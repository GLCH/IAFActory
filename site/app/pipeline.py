"""Pipeline d'ingestion, reconnaissance et structuration (US3.1, US3.2, US3.4,
IAF-E7 US7.1/US7.4/US7.5). Deuxieme version reelle (2026-09-28), suite a une
mesure reelle qui a montre que le signal unique (cosinus sur l'embedding
moyen du document, seuil 0.75) ne discrimine pas : 5 documents topiquement
distincts rattaches a la meme classe, un score a 0.993.

Reconnaissance a deux signaux maintenant, decide par l'utilisateur le
2026-09-28 :
- **structurel** : profil de proportions (Section/Paragraph/Table/Equation,
  "Equation" ajoute le 2026-09-28 - addendum US3.15, discrimine les documents
  scientifiques) du document compare a la moyenne des documents deja dans
  chaque classe ;
- **semantique** : vocabulaire extrait par une etape LLM DEDIEE (US7.1,
  distincte de l'extraction d'entites par chunk ci-dessous), chaque terme
  associe a un concept induit par le LLM, compare par recouvrement (Jaccard)
  au vocabulaire deja connu de chaque classe. Les concepts sont ecrits comme
  de vraies classes OWL dans Fuseki (ontology.py, US7.5) - pas seulement des
  etiquettes Neo4j comme avant.

Le score combine (moyenne des deux) doit depasser `settings.recognition_threshold`
(0.90, decide le 2026-09-28) pour rattacher a une classe EXISTANTE. Ce seuil
runtime n'est PAS une preuve de qualite : le banc de mesure hors echantillon
(US7.7, precision/rappel sur un jeu annote) reste a faire - voir
docs/epics/EPIC-IAF-E7-classification-documents.md.

Simplifications restantes, non cachees :
- pas de MinHash/LSH ni de pre-filtrage SEMANTIQUE par domaines (US7.2
  original) : si le pre-filtrage STRUCTUREL ci-dessous ne restreint rien, le
  document candidat est compare a TOUTES les classes existantes, comme avant ;
- **Mise a jour 2026-10-01 (IAF-125) : pre-filtrage STRUCTUREL branche** -
  `_structural_prefilter()` (app/structure_matcher.py, axiomes de cardinalite
  OWL2 lus dans Fuseki, contraints par format de fichier) restreint
  `_find_best_class` aux classes dont l'ontologie structurelle correspond
  reellement a la forme du document (score >= `STRUCTURAL_PREFILTER_THRESHOLD`,
  0.75, NON calibre). Degradation TOUJOURS gracieuse vers "toutes les
  classes" sinon - jamais une exception qui ferait echouer l'ingestion ;
- pas d'arbitrage LLM sur les correspondances ambigues (US7.3) ;
- analyse structurelle tres inegale entre .docx/.pptx (reelle) et .pdf
  (grossiere, page = section, ligne = paragraphe - pdf_struct.py) ;
- le vocabulaire est extrait sur un extrait du document (les premiers
  chunks, borne en taille), pas le texte integral (cout/latence sur le
  modele local).

**Revision du 2026-09-29** (demande explicite : vocabulaire trop pauvre,
score semantique toujours a 0%, pas de normalisation, tout traite en
francais) :
- score semantique : Jaccard sur libelles EXACTS remplace par une
  correspondance souple par EMBEDDING (_concept_set_similarity) - le
  Jaccard exact restait quasiment toujours a 0% en pratique (mesure reelle,
  deux documents proches n'inventent jamais le meme libelle mot pour mot) ;
- vocabulaire : jusqu'a 150 termes (etait 10-20), le LLM recoit les
  concepts DEJA CONNUS de toutes les classes pour les reutiliser plutot que
  d'en inventer des doublons proches ;
- normalisation (_TypeNormalizer) : types d'entites (par classe), relations
  et attributs (globaux, US7.9), et concepts (par classe) reutilisent un
  libelle deja connu si un nouveau lui est assez proche par embedding ;
- langue : detectee par le LLM (etape vocabulaire), utilisee pour le tag
  RDF des concepts (ontology.ensure_concept) - etait TOUJOURS "@fr", meme
  pour un document en anglais (bug reel corrige) - et pour instruire
  l'extraction par chunk d'ecrire dans la langue source, jamais traduite.
Non fait dans cette revision (voir docs/epics/EPIC-IAF-E7-classification-documents.md
US7.10) : construction explicite d'un corpus a partir de N documents
similaires en un seul lot (le rattachement reste incrementiel, document par
document) - voir class_merge.batch_cluster_classes() pour un premier pas
(regroupement de classes provisoires DEJA creees, pas la creation initiale).
"""
from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import docx_struct, latex_struct, markdown_struct, ontology, pdf_struct, pptx_struct, structure_matcher
from .config import settings
from .graph import chat_json, cosine_similarity, embed, get_driver
from .models import DocumentStatus
from .struct_element import StructElement, flatten

PARSERS = {
    ".docx": docx_struct.parse,
    ".pdf": pdf_struct.parse,
    ".pptx": pptx_struct.parse,
    ".md": markdown_struct.parse,
    ".tex": latex_struct.parse,
}

STRUCT_KINDS = ("Section", "Paragraph", "Table", "Equation")

# EPIC-IAF-E17 US17.1 (2026-10-02) : la reconnaissance (US7.4) ne compare un
# document qu'aux classes OFFICIELLES - ni provisoires (leur sort se decide par
# fusion, voir class_lifecycle.py) ni deja fusionnees dans une autre. Une classe
# importee (`exemple-importe`) ou promue (`officielle`) est officielle.
OFFICIAL_CLASS_FILTER = "NOT coalesce(c.status, '') = 'provisoire' AND NOT coalesce(c.status, '') STARTS WITH 'fusionnee'"

# Seuil de similarite cosinus au-dela duquel deux libelles de type (entite,
# relation, attribut ou concept) sont consideres comme le MEME type - ajoute
# le 2026-09-29 (demande explicite : normaliser pour reduire le nombre
# d'ontologies quasi-doublons entre documents). NON calibre (meme prudence
# que le reste du projet, US7.7).
NORMALIZE_MATCH_THRESHOLD = 0.85

# Seuil de similarite cosinus pour le score SEMANTIQUE (US7.4) - remplace le
# 2026-09-29 le recouvrement Jaccard sur libelles EXACTS, qui restait
# quasiment toujours a 0% en pratique (constate reellement : deux documents
# proches inducent des libelles de concept jamais identiques mot pour mot,
# ex. "Fonction de perte" vs "Perte", donc leur intersection de chaines etait
# vide). NON calibre.
CONCEPT_MATCH_THRESHOLD = 0.80

EXTRACTION_SYSTEM = (
    "Tu extrais des entites et relations d'un extrait de document technique. "
    "Ecris les noms, types et valeurs extraits DANS LA LANGUE DU DOCUMENT SOURCE, sans jamais "
    "traduire (demande explicite : tout traitement reste dans la langue du document). "
    "Reponds UNIQUEMENT en JSON valide, sans texte autour, au format exact : "
    '{"entities":[{"name":"...","type":"..."}],'
    '"attributes":[{"entity":"...","key":"...","value":"..."}],'
    '"relations":[{"source":"...","relation":"...","target":"..."}]}. '
    "N'invente aucune valeur absente du texte. Renvoie des listes vides si rien de pertinent."
)

# IAF-E7 US7.1 : etape dediee, distincte de l'extraction d'entites ci-dessus.
# Vocabulaire = termes du domaine (pas des entites nommees precises), chacun
# associe au concept (classe OWL) qu'il represente.
# Revise le 2026-09-29 (demande explicite) : jusqu'a 150 termes (etait 10-20,
# jugee trop pauvre), reutilisation des concepts DEJA CONNUS du corpus
# (fournis en entree, cf. extract_vocabulary) pour reduire les doublons
# proches plutot que d'en creer un nouveau a chaque fois, et langue du
# document detectee et respectee (pas de traduction imposee en francais).
VOCABULARY_SYSTEM = (
    "Tu extrais le vocabulaire technique d'un document, aussi exhaustif que raisonnable "
    "(jusqu'a 150 termes les plus significatifs, pas des entites nommees precises comme un nom de "
    "societe). Le document peut relever de n'importe quel domaine (scientifique, historique, "
    "technique, mathematique, ou autre) - ne privilegie aucun domaine par defaut. Pour chaque terme, "
    "donne le concept general auquel il appartient (un ou deux mots). Si un des concepts DEJA CONNUS "
    "fournis en debut de message s'applique, REUTILISE-LE A L'IDENTIQUE plutot que d'en creer un "
    "proche (ex. ne cree pas \"Materiaux\" si \"Materiau\" existe deja) - cela reduit le nombre de "
    "concepts redondants entre documents. Detecte la langue du document et ecris TOUS les termes et "
    "concepts DANS CETTE LANGUE, jamais traduits. Reponds UNIQUEMENT en JSON valide, sans texte "
    'autour, au format exact : {"language":"code ISO 639-1 (ex. fr, en, de)",'
    '"vocabulary":[{"term":"...","concept":"..."}]}.'
)
VOCABULARY_TEXT_LIMIT = 12000  # releve de 4000 le 2026-09-29 : plus de texte source pour ~150 termes au lieu de 10-20
KNOWN_CONCEPTS_LIMIT = 200  # borne le prompt (concepts deja connus, toutes classes confondues)


class UnsupportedFormat(ValueError):
    """Format accepte par US3.1 mais dont l'analyse structurelle n'est pas
    encore implementee (US3.8)."""


@dataclass
class IngestResult:
    status: DocumentStatus
    neo4j_class_id: str | None = None
    class_name: str | None = None
    chunk_count: int = 0
    entity_count: int = 0
    concept_count: int = 0
    recognition_score: float = 0.0
    structural_score: float = 0.0
    semantic_score: float = 0.0
    recognition_method: str = ""
    warnings: list[str] = field(default_factory=list)


def _normalize_label(label: str) -> str:
    """Normalisation grossiere pour comparer des libelles induits par un LLM
    (casse, accents) - pas une resolution de synonymes (US7.3, hors scope
    ici)."""
    decomposed = unicodedata.normalize("NFKD", label.strip().lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def extract_vocabulary(
    text: str, model: str, known_concepts: list[str] | None = None,
) -> tuple[list[dict], str | None]:
    """IAF-E7 US7.1. Renvoie (liste de {"term", "concept"}, langue detectee ou
    None) ; vocabulaire vide (pas d'exception) si l'appel echoue - un
    vocabulaire absent ne doit pas invalider le document (meme principe que
    US3.4 pour l'extraction par chunk). `known_concepts` (ajoute 2026-09-29) :
    libelles de concepts deja connus (toutes classes confondues), fournis au
    LLM pour qu'il les reutilise au lieu d'inventer des doublons proches."""
    excerpt = text[:VOCABULARY_TEXT_LIMIT]
    prompt = excerpt
    if known_concepts:
        prompt = f"Concepts deja connus (reutilise-les si pertinent) : {', '.join(known_concepts)}\n\n{excerpt}"
    try:
        # max_tokens releve a 8000 (2026-09-29, etait 3000) : jusqu'a 150
        # termes + concept en JSON, plus les jetons de "raisonnement" internes
        # de Gemini 2.5 (deja documente ailleurs dans ce fichier), depasse
        # largement 3000 - latence plus elevee, non mesuree finement, cout
        # assume en echange d'un vocabulaire moins pauvre (demande explicite).
        result = chat_json(prompt, model=model, system=VOCABULARY_SYSTEM, timeout=150, max_tokens=8000)
    except Exception:
        return [], None
    vocabulary = []
    for item in result.get("vocabulary", []):
        term, concept = item.get("term"), item.get("concept")
        if term and concept:
            vocabulary.append({"term": term, "concept": concept})
    language = result.get("language")
    return vocabulary, language if isinstance(language, str) and language.strip() else None


class _TypeNormalizer:
    """Ajoute le 2026-09-29 (demande explicite : normaliser les types
    d'entites/relations/attributs et les concepts pour reduire le nombre
    d'ontologies quasi-doublons). Reutilise un libelle deja connu si un
    nouveau lui est assez proche par embedding (`NORMALIZE_MATCH_THRESHOLD`),
    au lieu de creer un synonyme ; sinon l'ajoute a son propre cache pour que
    les occurrences suivantes DANS LE MEME document s'y comparent aussi."""

    def __init__(self, known_labels: list[str], seeded: list[tuple[str, list[float]]] | None = None):
        self._labels: list[str] = []
        self._vectors: list[list[float]] = []
        for label in known_labels:
            if label:
                self._add(label)
        # EPIC-IAF-E17 US17.1 : vocabulaire d'un corpus officiel proche, AVEC ses
        # embeddings deja stockes (aucun recalcul).
        for label, vector in seeded or []:
            if label and label not in self._labels:
                self._labels.append(label)
                self._vectors.append(vector)

    def _add(self, label: str) -> None:
        self._labels.append(label)
        self._vectors.append(embed(label))

    def normalize(self, raw: str, precomputed_vector: list[float] | None = None) -> str:
        """`precomputed_vector` (ajoute le 2026-09-29 suite a une question de
        l'utilisateur qui a mis le doigt dessus) : evite de reembedder `raw`
        s'il a deja ete embedde ailleurs dans le pipeline pour un autre usage
        (ex. les concepts, deja embeddes une fois pour le score semantique de
        reconnaissance avant meme de savoir dans quelle classe ils finiront -
        _find_best_class ci-dessous - puis reembeddes ICI en double sans ce
        parametre, un gaspillage reel corrige)."""
        if not raw:
            return raw
        raw_normalized = _normalize_label(raw)
        for label in self._labels:
            if _normalize_label(label) == raw_normalized:
                return label
        vector = precomputed_vector if precomputed_vector is not None else embed(raw)
        best_label, best_score = None, 0.0
        for label, known_vector in zip(self._labels, self._vectors):
            score = cosine_similarity(vector, known_vector)
            if score > best_score:
                best_label, best_score = label, score
        if best_label is not None and best_score >= NORMALIZE_MATCH_THRESHOLD:
            return best_label
        self._labels.append(raw)
        self._vectors.append(vector)
        return raw

    def vector_for(self, label: str) -> list[float] | None:
        try:
            return self._vectors[self._labels.index(label)]
        except ValueError:
            return None


def _concept_set_similarity(a: list[tuple[str, list[float]]], b: list[tuple[str, list[float]]]) -> float:
    """Remplace le 2026-09-29 le recouvrement Jaccard sur libelles EXACTS
    (voir CONCEPT_MATCH_THRESHOLD ci-dessus pour la mesure reelle qui a
    motive ce changement). Correspondance souple par embedding, symetrique :
    proportion de `a` ayant un match dans `b`, et inversement, moyennees."""
    if not a or not b:
        return 0.0
    matched_a = sum(1 for _, va in a if any(cosine_similarity(va, vb) >= CONCEPT_MATCH_THRESHOLD for _, vb in b))
    matched_b = sum(1 for _, vb in b if any(cosine_similarity(va, vb) >= CONCEPT_MATCH_THRESHOLD for _, va in a))
    return (matched_a / len(a) + matched_b / len(b)) / 2


def _structural_profile(elements: list[StructElement], citation_count: int = 0) -> dict[str, float]:
    """`citation_count` ajoute le 2026-09-29 (demande explicite : les
    citations font partie de "l'organisation du contenu" a matcher
    structurellement) - densite (citations par element), PAS une proportion
    de plus dans STRUCT_KINDS (qui doivent sommer a 1) : une densite est une
    grandeur differente, geree a part par _profile_similarity ci-dessous.
    Vient de latex_struct.py (metadata["citation_count"]) ; 0 pour les autres
    formats (pas de notion de citation dans .docx/.pdf/.pptx/.md)."""
    counts = {kind: 0 for kind in STRUCT_KINDS}
    for elem in elements:
        if elem.kind in counts:
            counts[elem.kind] += 1
    total = sum(counts.values())
    profile = {kind: 0.0 for kind in STRUCT_KINDS} if total == 0 else {kind: n / total for kind, n in counts.items()}
    profile["citation_density"] = citation_count / total if total else 0.0
    return profile


def _profile_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """1 - distance L1 / 2 sur les proportions STRUCT_KINDS (bornee a [0, 1],
    simple et explicable plutot qu'une mesure plus sophistiquee non calibree,
    US7.7 tranchera), combinee a la similarite de densite de citations
    (ajoutee le 2026-09-29, echelle arbitraire de 5 citations/element = tres
    dense - NON calibree comme le reste). Ponderation 80/20 : les proportions
    STRUCT_KINDS restent le signal principal, la densite de citations un
    signal d'appoint (absente pour la plupart des formats)."""
    l1 = sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in STRUCT_KINDS)
    kind_similarity = max(0.0, 1.0 - l1 / 2)
    citation_diff = abs(a.get("citation_density", 0.0) - b.get("citation_density", 0.0))
    citation_similarity = max(0.0, 1.0 - citation_diff / 5.0)
    return 0.8 * kind_similarity + 0.2 * citation_similarity


def _ensure_vector_index(session, dimensions: int) -> None:
    session.run(
        "CREATE VECTOR INDEX chunkEmbeddings IF NOT EXISTS "
        "FOR (c:Chunk) ON c.embedding "
        "OPTIONS {indexConfig: {`vector.dimensions`: $dims, `vector.similarity_function`: 'cosine'}}",
        dims=dimensions,
    )


def recognition_verdict(has_class: bool, combined: float, semantic: float, n_concepts: int) -> tuple[bool, str]:
    """Decision de reconnaissance (EPIC-IAF-E17, E18) : (rattache a la meilleure classe ?, methode).
    Trois conditions cumulees : score combine >= `recognition_threshold` ; semantique >=
    `recognition_min_semantic` (la structure seule ne suffit pas) ; au moins
    `recognition_min_concepts` concepts dans le document (mesure du 2026-10-03 : des notices de 1 a 3
    concepts obtenaient jusqu'a 55 % de semantique face a une classe, la moitie « document dans classe »
    de la mesure valant 100 % des qu'un concept correspond). La methode explique pourquoi un document
    n'est pas rattache alors que son score combine suffisait."""
    if not has_class:
        return False, "score_combine_insuffisant_creation_provisoire"
    if combined < settings.recognition_threshold:
        return False, "score_combine_insuffisant_creation_provisoire"
    if n_concepts < settings.recognition_min_concepts:
        return False, "concepts_insuffisants_creation_provisoire"
    if semantic < settings.recognition_min_semantic:
        return False, "porte_semantique_creation_provisoire"
    return True, "score_combine_structure_semantique"


def _find_best_class(
    session, struct_profile: dict[str, float], concept_vectors: list[tuple[str, list[float]]],
    candidate_class_ids: list[str] | None = None, structural_prefilter_score: float | None = None,
) -> tuple[str | None, str | None, float, float, float]:
    """IAF-E7 US7.4 : score combine (structurel + semantique) contre chaque
    classe existante. Une classe sans document membre (profil structurel
    indefini) ou sans concept connu obtient un score nul sur l'axe
    correspondant - une classe toute neuve ne "reconnait" donc jamais un
    nouveau document par accident.

    `concept_vectors` : liste de (libelle, embedding) du document candidat -
    remplace le 2026-09-29 le `set[str]` de libelles normalises (voir
    _concept_set_similarity : le score semantique restait quasiment toujours
    a 0%, mesure reellement, car deux documents proches n'inventent jamais le
    MEME libelle mot pour mot).

    `candidate_class_ids` (US7.2-structurel, IAF-125, 2026-10-01) :
    pre-filtrage par ontologie structurelle (app/structure_matcher.py) - quand
    fourni ET non vide, ne compare le document qu'a CES classes (la reponse
    de _structural_prefilter ci-dessous) au lieu de TOUTES. `None` ou liste
    vide = comportement INCHANGE (toutes les classes) - degradation
    gracieuse si aucune ontologie structurelle ne s'applique au format, si
    aucune classe ne l'accepte encore, ou si Fuseki est indisponible.

    `structural_prefilter_score` (bug reel trouve le 2026-10-01, pas suppose :
    l'utilisateur a depose un PDF reel sur une classe fraichement creee par
    import RDF - "il a trouve l'ontologie structurelle candidate (c'est bien)
    [...] score_combine_insuffisant" - score structurel ET semantique EXACTEMENT
    a 0.0) : une classe SANS AUCUN document membre encore (import RDF via
    create_class.py, jamais une vraie ingestion) a toujours `profiles` vide,
    donc structural_score=0.0 QUEL QUE SOIT le document - meme un document qui
    satisfait PARFAITEMENT ses axiomes structurels (le pre-filtre l'a deja
    confirme en l'incluant dans candidate_class_ids) ne peut jamais etre
    reconnu, cercle vicieux (il faudrait deja un document reconnu pour que le
    suivant puisse l'etre). Pour une classe SANS profil reel ET presente dans
    `candidate_class_ids` (donc deja validee par les axiomes de cardinalite),
    utilise le score du pre-filtre structurel comme repli au lieu de 0.0 - un
    score structurel EXPLICITEMENT ancre sur l'ontologie plutot que sur une
    moyenne de documents qui n'existe pas encore. Les classes AVEC des
    documents membres continuent d'utiliser leur profil moyen reel, inchange."""
    if candidate_class_ids:
        rows = list(session.run(
            "MATCH (c:DocumentClass) WHERE c.id IN $ids AND " + OFFICIAL_CLASS_FILTER + " "
            "OPTIONAL MATCH (c)<-[:IN_CLASS]-(d:Document) "
            "OPTIONAL MATCH (c)-[:HAS_CONCEPT]->(concept:Concept) "
            "RETURN c.id AS id, c.name AS name, "
            "       collect(DISTINCT {section: d.profile_section, paragraph: d.profile_paragraph, "
            "                         table: d.profile_table, equation: d.profile_equation, "
            "                         citation: d.profile_citation_density}) AS profiles, "
            "       collect(DISTINCT CASE WHEN concept.embedding IS NOT NULL "
            "                             THEN {label: concept.label, embedding: concept.embedding} END) AS concepts",
            ids=candidate_class_ids,
        ))
    else:
        rows = list(session.run(
            "MATCH (c:DocumentClass) WHERE " + OFFICIAL_CLASS_FILTER + " "
            "OPTIONAL MATCH (c)<-[:IN_CLASS]-(d:Document) "
            "OPTIONAL MATCH (c)-[:HAS_CONCEPT]->(concept:Concept) "
            "RETURN c.id AS id, c.name AS name, "
            "       collect(DISTINCT {section: d.profile_section, paragraph: d.profile_paragraph, "
            "                         table: d.profile_table, equation: d.profile_equation, "
            "                         citation: d.profile_citation_density}) AS profiles, "
            "       collect(DISTINCT CASE WHEN concept.embedding IS NOT NULL "
            "                             THEN {label: concept.label, embedding: concept.embedding} END) AS concepts"
        ))
    best = (None, None, 0.0, 0.0, 0.0)
    # (passe la porte semantique, score combine) : une classe qui passe la porte est toujours
    # preferee a une qui ne la passe pas, meme si son score combine est plus bas (2026-10-02).
    best_key = (False, 0.0)
    for row in rows:
        profiles = [p for p in row["profiles"] if p.get("section") is not None]
        if profiles:
            # p[kind.lower()] peut etre None pour "equation"/"citation" sur un
            # document ingere avant leur ajout, au sein d'une classe qui a par
            # ailleurs des documents plus recents - 0.0 par defaut.
            avg_profile = {
                kind: sum(p[kind.lower()] or 0.0 for p in profiles) / len(profiles) for kind in STRUCT_KINDS
            }
            avg_profile["citation_density"] = sum(p.get("citation") or 0.0 for p in profiles) / len(profiles)
            structural_score = _profile_similarity(struct_profile, avg_profile)
        elif candidate_class_ids and row["id"] in candidate_class_ids and structural_prefilter_score is not None:
            # Repli IAF-125 : pas de profil reel (classe sans document membre),
            # mais deja validee par les axiomes structurels du pre-filtre.
            structural_score = structural_prefilter_score
        else:
            structural_score = 0.0

        class_concepts = [(c["label"], c["embedding"]) for c in row["concepts"] if c is not None]
        semantic_score = _concept_set_similarity(class_concepts, concept_vectors)

        combined = (structural_score + semantic_score) / 2
        key = (semantic_score >= settings.recognition_min_semantic, combined)
        if combined > 0.0 and key > best_key:
            best_key = key
            best = (row["id"], row["name"], structural_score, semantic_score, combined)
    return best


def _search_known_corpora(
    session, concept_vectors: list[tuple[str, list[float]]], limit: int = 3,
) -> list[dict]:
    """EPIC-IAF-E17 US17.1 : etape "recherche de corpus matchant le document"
    pour un document NON reconnu. Classe les corpus OFFICIELS par similarite
    SEMANTIQUE (meme mesure que le score de reconnaissance, `_concept_set_
    similarity`, sur les embeddings de concepts deja stockes) et renvoie les
    `limit` plus proches avec leur vocabulaire - sert a rattacher des liens
    NEAR_CORPUS et a amorcer le normaliseur de concepts de la classe
    provisoire (l'ontologie agnostique reprend les libelles deja connus)."""
    rows = list(session.run(
        "MATCH (c:DocumentClass) WHERE " + OFFICIAL_CLASS_FILTER + " "
        "OPTIONAL MATCH (c)-[:HAS_CONCEPT]->(concept:Concept) WHERE concept.embedding IS NOT NULL "
        "RETURN c.id AS id, c.name AS name, collect({label: concept.label, embedding: concept.embedding}) AS concepts"
    ))
    ranked = []
    for row in rows:
        concepts = [(c["label"], c["embedding"]) for c in row["concepts"] if c["label"]]
        score = _concept_set_similarity(concepts, concept_vectors)
        ranked.append({"id": row["id"], "name": row["name"], "semantic_score": score, "concepts": concepts})
    ranked.sort(key=lambda r: r["semantic_score"], reverse=True)
    return [r for r in ranked[:limit] if r["semantic_score"] > 0]


# US7.2-structurel (IAF-125, 2026-10-01) : score minimal (fraction d'axiomes
# de cardinalite satisfaits, app/structure_matcher.py) pour qu'un match
# structurel restreigne la recherche de classe - NON calibre (comme le reste
# du projet, US7.7), choisi pour exiger que la PLUPART des axiomes d'une
# forme soient respectes (ex. 3 sur 4 pour la forme Word) avant de l'utiliser
# comme filtre, jamais sur un match faible/ambigu.
STRUCTURAL_PREFILTER_THRESHOLD = 0.75


def _structural_prefilter(
    root_children: list[StructElement], suffix: str,
) -> tuple[list[str] | None, float | None, str | None]:
    """IAF-125 : restreint `_find_best_class` aux classes dont une ontologie
    structurelle ACCEPTEE correspond bien a la forme reelle du document
    (demande explicite : "utiliser les ontologies structurelles [...] pour
    pre filtrer la classe des documents"). Degradation TOUJOURS gracieuse
    (jamais une exception qui ferait echouer l'ingestion, meme principe que
    le reste de ce module) : renvoie (None, None) (= pas de restriction,
    comportement identique a avant le 2026-10-01) si le format n'a pas
    d'ontologie structurelle applicable, si aucun match n'atteint le seuil,
    si aucune classe n'accepte encore l'ontologie la mieux matchee, ou si
    Fuseki est indisponible.

    Renvoie aussi le SCORE du meilleur match (2026-10-01, bug reel trouve en
    testant - voir le docstring de _find_best_class) : sert de repli pour
    structural_score quand la classe candidate n'a encore aucun document
    membre pour calculer un profil reel."""
    doc_format = suffix.lstrip(".").lower()
    try:
        matches = structure_matcher.match_structural_ontologies(root_children, doc_format)
    except Exception:
        return None, None, None
    if not matches or matches[0].score < STRUCTURAL_PREFILTER_THRESHOLD:
        return None, None, None
    # EPIC-IAF-E17 US17.3 : l'URI du meilleur match sert aussi a lier une classe
    # PROVISOIRE a son ontologie structurelle (meme quand aucune classe ne
    # l'accepte encore, `candidate_ids` est alors None).
    try:
        candidate_ids = ontology.classes_accepting_structure(matches[0].ontology_uri)
    except Exception:
        return None, None, matches[0].ontology_uri
    return (candidate_ids or None), matches[0].score, matches[0].ontology_uri


def _write_concepts(
    session, class_id: str, document_sha256: str, vocabulary: list[dict],
    language: str | None, concept_normalizer: "_TypeNormalizer",
    precomputed_vectors: dict[str, list[float]] | None = None,
) -> int:
    """Ecrit chaque concept induit comme classe OWL dans Fuseki (US7.5) et
    son rattachement dans Neo4j (lecture rapide pour les ecrans du site,
    US3.14). Un concept qui echoue a s'ecrire dans Fuseki ne bloque pas les
    autres (meme principe que l'extraction par chunk, US3.4).

    Ajoute le 2026-09-29 : `concept_normalizer` reutilise un libelle DEJA
    CONNU de cette classe si le nouveau lui est assez proche par embedding
    (reduit les concepts quasi-doublons DANS une classe, complementaire du
    `known_concepts` passe a extract_vocabulary qui vise plutot a reduire les
    doublons ENTRE classes) ; l'embedding de chaque libelle final est stocke
    sur le noeud Concept pour que _find_best_class (US7.4) et
    class_merge.class_similarity (US7.6) le comparent sans le recalculer.

    `precomputed_vectors` (ajoute suite a une question de l'utilisateur qui a
    releve le doublon) : les concepts ont deja ete embeddes une fois dans
    ingest_document() pour construire `concept_vectors` (score semantique de
    reconnaissance, AVANT de savoir dans quelle classe ils finiraient) -
    reutilise ces vecteurs ici au lieu de les recalculer pour chaque concept."""
    written = 0
    seen_labels: set[str] = set()
    for item in vocabulary:
        raw_concept = item["concept"]
        label = concept_normalizer.normalize(raw_concept, (precomputed_vectors or {}).get(raw_concept))
        normalized = _normalize_label(label)
        if normalized in seen_labels:
            continue
        seen_labels.add(normalized)
        try:
            uri = ontology.ensure_concept(class_id, label, language or "fr")
        except Exception:
            continue  # Fuseki indisponible ou erreur reseau : le concept reste absent, pas de silence en amont (log gateway)
        vector = concept_normalizer.vector_for(label)
        session.run(
            "MERGE (c:DocumentClass {id: $class_id}) "
            "MERGE (concept:Concept {label: $label, class_id: $class_id}) "
            "SET concept.uri = $uri, concept.embedding = $embedding "
            "MERGE (c)-[:HAS_CONCEPT]->(concept) "
            "WITH concept "
            "MATCH (d:Document {sha256: $sha256}) "
            "MERGE (d)-[:MENTIONS_CONCEPT {term: $term}]->(concept)",
            class_id=class_id, label=label, uri=uri, embedding=vector,
            sha256=document_sha256, term=item["term"],
        )
        written += 1
    return written


def ingest_document(
    sha256: str, filename: str, stored_path: Path,
    on_step: Callable[[str, str, str | None], None] | None = None,
) -> IngestResult:
    """Fait tourner le document a travers I (structure + chunks + embeddings),
    R (rattachement a une classe existante ou creation, score combine
    structurel + semantique), et S (extraction d'entites/relations par chunk
    pour le graph RAG). Ecrit dans Neo4j et Fuseki ; ne touche pas Postgres
    (l'appelant reporte le resultat sur la ligne `Document`).

    `on_step(phase, label, detail)` : rappel optionnel (ajoute le 2026-09-28,
    demande explicite de "plus d'information sur quelle etape a fait quelque
    chose") pour tracer la progression reelle - worker.py l'utilise pour
    ecrire des lignes `PipelineStep` (Postgres), pipeline.py ne connait pas
    Postgres (separation deja en place, cf. le commentaire ci-dessus). Un
    echec du rappel ne doit jamais faire echouer l'ingestion elle-meme."""
    def step(phase: str, label: str, detail: str | None = None) -> None:
        if on_step is not None:
            try:
                on_step(phase, label, detail)
            except Exception:
                pass

    suffix = stored_path.suffix.lower()
    parse = PARSERS.get(suffix)
    if parse is None:
        raise UnsupportedFormat(f"format non pris en charge : {suffix or 'sans extension'}")

    step("Ingestion", "Analyse structurelle", filename)
    metadata, root = parse(str(stored_path))
    # US18.6 : pages transcrites par OCR (pdf_struct.py) - trace et avertissements pour le creator.
    ocr_warnings: list[str] = []
    if metadata.get("ocr_pages") or metadata.get("ocr_errors") or metadata.get("ocr_skipped_pages"):
        n_ok = len(metadata.get("ocr_pages", []))
        n_err = len(metadata.get("ocr_errors", []))
        n_skip = metadata.get("ocr_skipped_pages", 0)
        step(
            "Ingestion", "OCR (US18.6)",
            f"{n_ok} page(s) transcrite(s)" + (f", {n_err} en echec" if n_err else "")
            + (f", {n_skip} au-dela du plafond" if n_skip else ""),
        )
        ocr_warnings.append(f"OCR : {n_ok} page(s) transcrite(s) par modele de vision, transcription non verifiee (US18.6)")
        if n_skip:
            ocr_warnings.append(f"OCR : {n_skip} page(s) ignoree(s), plafond de {settings.ocr_max_pages} pages atteint")
        ocr_warnings.extend(metadata.get("ocr_errors", []))
        if metadata.get("ocr_truncated_pages"):
            ocr_warnings.append(f"OCR : transcription incomplete (reponse coupee) pour les pages {metadata['ocr_truncated_pages']}")
    elements = flatten(root)
    # "citation_count" ajoute le 2026-09-29 dans latex_struct.py uniquement
    # (organisation du contenu LaTeX - citations, cf. module docstring) ; 0
    # par defaut pour les formats qui ne le renseignent pas.
    struct_profile = _structural_profile(elements, metadata.get("citation_count", 0))

    # US7.2-structurel (IAF-125) : pre-filtre les classes candidates par
    # ontologie structurelle AVANT le score combine (_find_best_class) -
    # None si aucune restriction ne s'applique (voir _structural_prefilter).
    candidate_class_ids, structural_prefilter_score, structural_ontology_uri = _structural_prefilter(
        root.children, suffix,
    )
    step(
        "Ingestion", "Pre-filtrage structurel",
        f"{len(candidate_class_ids)} classe(s) candidate(s) (score {structural_prefilter_score:.0%})"
        if candidate_class_ids else "aucune restriction",
    )

    driver = get_driver()
    warnings: list[str] = list(ocr_warnings)
    with driver.session() as session:
        session.run(
            "MERGE (d:Document {sha256: $sha256}) "
            "SET d.title = $title, d.author = $author, d.subject = $subject, "
            "    d.filename = $filename, d.status = 'ingere', d.ingested_at = datetime(), "
            "    d.profile_section = $section, d.profile_paragraph = $paragraph, d.profile_table = $table, "
            "    d.profile_equation = $equation, d.profile_citation_density = $citation_density",
            sha256=sha256, title=metadata.get("title"), author=metadata.get("author"),
            subject=metadata.get("subject"), filename=filename,
            section=struct_profile["Section"], paragraph=struct_profile["Paragraph"], table=struct_profile["Table"],
            equation=struct_profile["Equation"], citation_density=struct_profile["citation_density"],
        )

        def write_element(elem, parent_neo_id):
            session.run(
                "MATCH (d:Document {sha256: $sha256}) "
                "CREATE (e:StructElement {id: $id, kind: $kind, label: $label, level: $level, position: $position}) "
                "CREATE (d)-[:HAS_ELEMENT]->(e)",
                sha256=sha256, id=elem.id, kind=elem.kind, label=elem.label,
                level=elem.level, position=elem.position,
            )
            if parent_neo_id:
                session.run(
                    "MATCH (p:StructElement {id: $parent_id}), (e:StructElement {id: $id}) "
                    "CREATE (p)-[:CHILD]->(e)",
                    parent_id=parent_neo_id, id=elem.id,
                )
            for child in elem.children:
                write_element(child, elem.id)

        for top in root.children:
            write_element(top, None)

        chunk_embeddings: list[list[float]] = []
        n_chunks = 0
        embed_dims = None
        chunk_rows: list[tuple[str, str, str]] = []  # (chunk_id, text, section)
        text_parts: list[str] = []
        for elem in elements:
            if not elem.text:
                continue
            vector = embed(elem.text)
            embed_dims = embed_dims or len(vector)
            chunk_id = str(uuid.uuid4())
            session.run(
                "MATCH (e:StructElement {id: $eid}) "
                "CREATE (c:Chunk {id: $cid, text: $text, embedding: $embedding}) "
                "CREATE (e)-[:HAS_CHUNK]->(c)",
                eid=elem.id, cid=chunk_id, text=elem.text, embedding=vector,
            )
            chunk_embeddings.append(vector)
            chunk_rows.append((chunk_id, elem.text, elem.label))
            text_parts.append(elem.text)
            n_chunks += 1

        if embed_dims:
            _ensure_vector_index(session, embed_dims)

        if not chunk_embeddings:
            raise ValueError("document sans texte extrait (page blanche, ou contenu non textuel)")

        step("Ingestion", "Decoupage et embeddings", f"{n_chunks} chunks")

        # IAF-E7 US7.1 : etape dediee d'extraction de vocabulaire, sur un
        # extrait du document (pas chunk par chunk comme l'extraction
        # d'entites ci-dessous). "known_concepts" (2026-09-29) : concepts deja
        # induits TOUTES CLASSES confondues, fournis au LLM pour qu'il les
        # reutilise plutot que d'en inventer des doublons proches - reduit le
        # nombre d'ontologies representant une connaissance deja connue
        # (demande explicite).
        known_concepts = [
            r["label"] for r in session.run(
                "MATCH (:DocumentClass)-[:HAS_CONCEPT]->(concept:Concept) "
                "RETURN DISTINCT concept.label AS label LIMIT $limit",
                limit=KNOWN_CONCEPTS_LIMIT,
            ) if r["label"]
        ]
        vocabulary, language = extract_vocabulary(
            "\n".join(text_parts), settings.extraction_model, known_concepts=known_concepts,
        )
        if not vocabulary:
            warnings.append("extraction de vocabulaire vide ou ratee (US7.1) : score semantique a 0")
        step(
            "Ingestion", "Extraction du vocabulaire (US7.1)",
            f"{len(vocabulary)} termes, langue {language or 'inconnue'}" if vocabulary else "echec ou vide",
        )

        # Concepts induits pour CE document, embeddes une fois (2026-09-29) -
        # remplace le recouvrement Jaccard sur libelles exacts (voir
        # _concept_set_similarity), qui restait quasiment toujours a 0%
        # (mesure reelle). Deduplique par libelle normalise avant d'embedder
        # (evite d'embedder 2 fois "Algorithme" si plusieurs termes y menent).
        seen_concept_labels: set[str] = set()
        concept_vectors: list[tuple[str, list[float]]] = []
        for v in vocabulary:
            normalized = _normalize_label(v["concept"])
            if normalized in seen_concept_labels:
                continue
            seen_concept_labels.add(normalized)
            concept_vectors.append((v["concept"], embed(v["concept"])))

        recognized_id, recognized_name, structural_score, semantic_score, combined = _find_best_class(
            session, struct_profile, concept_vectors, candidate_class_ids, structural_prefilter_score,
        )

        # Porte semantique (2026-10-02) : la structure seule ne suffit jamais a reconnaitre ;
        # nombre minimal de concepts (2026-10-03) : un document trop pauvre en vocabulaire ne prouve rien.
        accepted, method = recognition_verdict(recognized_id is not None, combined, semantic_score, len(concept_vectors))
        if accepted:
            class_id, class_name, status = recognized_id, recognized_name, DocumentStatus.recognized
            session.run(
                "MATCH (d:Document {sha256: $sha256}), (c:DocumentClass {id: $cid}) "
                "MERGE (d)-[r:IN_CLASS]->(c) "
                "SET r.score = $score, r.structural_score = $struct_score, "
                "    r.semantic_score = $sem_score, r.method = $method",
                sha256=sha256, cid=class_id, score=combined,
                struct_score=structural_score, sem_score=semantic_score, method=method,
            )
        else:
            class_id = str(uuid.uuid4())
            class_name = f"Provisoire - {metadata.get('title') or filename}"
            status = DocumentStatus.provisional
            session.run(
                "MATCH (d:Document {sha256: $sha256}) "
                "CREATE (c:DocumentClass {id: $cid, name: $name, status: 'provisoire', created_at: datetime()}) "
                "CREATE (d)-[:IN_CLASS {score: $score, structural_score: $struct_score, "
                "                       semantic_score: $sem_score, method: $method}]->(c)",
                sha256=sha256, cid=class_id, name=class_name, score=combined,
                struct_score=structural_score, sem_score=semantic_score, method=method,
            )
            # US17.3 : une classe provisoire est liee a l'ontologie structurelle
            # du meilleur match de son document (aucune classe ne l'acceptait
            # peut-etre encore) - la fusion de classes reunira ces liens.
            if structural_ontology_uri:
                try:
                    ontology.ensure_class_accepts_structure(class_id, structural_ontology_uri)
                except Exception:
                    pass  # Fuseki indisponible : la classe reste sans ontologie structurelle liee, pas bloquant
        step(
            "Ingestion", "Reconnaissance de classe (US7.4)",
            f"{method} -> {class_name} ({combined:.0%}) [structure {structural_score:.0%}, "
            f"semantique {semantic_score:.0%}, porte semantique {settings.recognition_min_semantic:.0%}, "
            f"{len(concept_vectors)} concept(s), minimum {settings.recognition_min_concepts}]",
        )
        session.run("MATCH (d:Document {sha256: $sha256}) SET d.language = $language", sha256=sha256, language=language)

        # US17.1 : document NON reconnu -> recherche de corpus officiels proches
        # (liens NEAR_CORPUS) et amorcage du vocabulaire de l'ontologie agnostique.
        seed_pairs: list[tuple[str, list[float]]] = []
        if status == DocumentStatus.provisional:
            near = _search_known_corpora(session, concept_vectors)
            for item in near:
                session.run(
                    "MATCH (p:DocumentClass {id: $pid}), (o:DocumentClass {id: $oid}) "
                    "MERGE (p)-[r:NEAR_CORPUS]->(o) SET r.semantic_score = $score",
                    pid=class_id, oid=item["id"], score=item["semantic_score"],
                )
            if near and near[0]["semantic_score"] >= settings.corpus_seed_min_similarity:
                seed_pairs = near[0]["concepts"]
            step(
                "Ingestion", "Recherche de corpus proches (US17.1)",
                ", ".join(f"{i['name']} ({i['semantic_score']:.0%})" for i in near) + (
                    f" - vocabulaire de '{near[0]['name']}' repris ({len(seed_pairs)} concepts)" if seed_pairs else ""
                ) if near else "aucun corpus officiel proche",
            )

        # Normalisation des concepts/types/attributs/relations (2026-09-29,
        # demande explicite : "reduire le nombre d'ontologies" en comparant
        # aux termes deja connus). Un normaliseur par categorie, initialise
        # avec ce qui est DEJA connu (concepts : par CLASSE, comme les
        # concepts eux-memes ; types d'entites : par CLASSE, comme Entity.type ;
        # relations/attributs : GLOBAUX, comme leurs proprietes OWL, US7.9).
        known_class_concepts = [
            r["label"] for r in session.run(
                "MATCH (c:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(concept:Concept) RETURN DISTINCT concept.label AS label",
                cid=class_id,
            ) if r["label"]
        ]
        concept_normalizer = _TypeNormalizer(known_class_concepts, seeded=seed_pairs)
        known_entity_types = [
            r["type"] for r in session.run(
                "MATCH (e:Entity {class_id: $cid}) RETURN DISTINCT e.type AS type", cid=class_id,
            ) if r["type"]
        ]
        entity_type_normalizer = _TypeNormalizer(known_entity_types)
        try:
            relation_normalizer = _TypeNormalizer(ontology.list_properties("owl:ObjectProperty"))
            attribute_normalizer = _TypeNormalizer(ontology.list_properties("owl:DatatypeProperty"))
        except Exception:
            # Fuseki indisponible : normalisation reduite au cache du document
            # en cours plutot que d'echouer l'ingestion (US3.4).
            relation_normalizer = _TypeNormalizer([])
            attribute_normalizer = _TypeNormalizer([])

        # dict(concept_vectors) : un seul vecteur par libelle unique (deja
        # deduplique en amont) - reutilise pour eviter de reembedder deux fois
        # le meme concept brut (voir _write_concepts ci-dessus).
        n_concepts = _write_concepts(
            session, class_id, sha256, vocabulary, language, concept_normalizer,
            precomputed_vectors=dict(concept_vectors),
        )
        step("Structuration", "Ecriture ontologie OWL (concepts, US7.5)", f"{n_concepts} concepts")

        n_entities = 0
        seen_relation_types: set[str] = set()
        seen_attribute_keys: set[str] = set()
        for i, (chunk_id, text, section) in enumerate(chunk_rows, start=1):
            try:
                extracted = chat_json(
                    f"Extrait (section \"{section}\") :\n{text}",
                    model=settings.extraction_model,
                    system=EXTRACTION_SYSTEM,
                    max_tokens=4000,  # meme raison que extract_vocabulary ci-dessus ; releve encore une fois (3000
                    # encore insuffisant sur 2 chunks/8 mesures reellement le 2026-09-28)
                )
            except Exception as exc:  # un chunk qui echoue n'invalide pas le document (US3.4)
                warnings.append(f"extraction ratee pour un chunk ({section!r}) : {exc}")
                step("Structuration", f"Extraction entites/relations - chunk {i}/{len(chunk_rows)}", "echec")
                continue

            for ent in extracted.get("entities", []):
                name, etype = ent.get("name"), ent.get("type", "Autre")
                if not name:
                    continue
                etype = entity_type_normalizer.normalize(etype)
                session.run(
                    "MERGE (e:Entity {name: $name, class_id: $class_id}) "
                    "SET e.type = $type "
                    "WITH e MATCH (c:Chunk {id: $chunk_id}) MERGE (c)-[:MENTIONS]->(e)",
                    name=name, type=etype, class_id=class_id, chunk_id=chunk_id,
                )
                n_entities += 1

            for attr in extracted.get("attributes", []):
                ent_name, key, value = attr.get("entity"), attr.get("key"), attr.get("value")
                if not (ent_name and key):
                    continue
                key = attribute_normalizer.normalize(key)
                session.run(
                    "MATCH (e:Entity {name: $name, class_id: $class_id}) "
                    "CALL apoc.create.setProperty(e, $key, $value) YIELD node RETURN node",
                    name=ent_name, class_id=class_id, key=key, value=str(value),
                )
                if key not in seen_attribute_keys:  # IAF-E7 US7.9 : owl:DatatypeProperty global
                    seen_attribute_keys.add(key)
                    try:
                        ontology.ensure_attribute_property(key)
                    except Exception:
                        pass  # Fuseki indisponible : l'attribut reste dans Neo4j, pas de silence en amont (US3.4)

            for rel in extracted.get("relations", []):
                src, rtype, tgt = rel.get("source"), rel.get("relation"), rel.get("target")
                if not (src and rtype and tgt):
                    continue
                rtype = relation_normalizer.normalize(rtype)
                session.run(
                    "MATCH (s:Entity {name: $src, class_id: $class_id}), "
                    "(t:Entity {name: $tgt, class_id: $class_id}) "
                    "MERGE (s)-[r:REL {type: $rtype}]->(t)",
                    src=src, tgt=tgt, rtype=rtype, class_id=class_id,
                )
                if rtype not in seen_relation_types:  # IAF-E7 US7.9 : owl:ObjectProperty global
                    seen_relation_types.add(rtype)
                    try:
                        ontology.ensure_relation_property(rtype)
                    except Exception:
                        pass

            step(
                "Structuration", f"Extraction entites/relations - chunk {i}/{len(chunk_rows)}",
                f"section {section!r}",
            )

        step(
            "Structuration", "Extraction entites/relations terminee",
            f"{n_entities} entites, {len(seen_relation_types)} types de relation, {len(seen_attribute_keys)} attributs",
        )

    return IngestResult(
        status=status,
        neo4j_class_id=class_id,
        class_name=class_name,
        chunk_count=n_chunks,
        entity_count=n_entities,
        concept_count=n_concepts,
        recognition_score=combined,
        structural_score=structural_score,
        semantic_score=semantic_score,
        recognition_method=method,
        warnings=warnings,
    )
