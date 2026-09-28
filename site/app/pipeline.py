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
- pas de MinHash/LSH ni de pre-filtrage par domaines (US7.2) : chaque
  document candidat est compare a TOUTES les classes existantes ;
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

from . import docx_struct, latex_struct, markdown_struct, ontology, pdf_struct, pptx_struct
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

    def __init__(self, known_labels: list[str]):
        self._labels: list[str] = []
        self._vectors: list[list[float]] = []
        for label in known_labels:
            if label:
                self._add(label)

    def _add(self, label: str) -> None:
        self._labels.append(label)
        self._vectors.append(embed(label))

    def normalize(self, raw: str) -> str:
        if not raw:
            return raw
        raw_normalized = _normalize_label(raw)
        for label in self._labels:
            if _normalize_label(label) == raw_normalized:
                return label
        vector = embed(raw)
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


def _find_best_class(
    session, struct_profile: dict[str, float], concept_vectors: list[tuple[str, list[float]]],
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
    MEME libelle mot pour mot)."""
    rows = list(session.run(
        "MATCH (c:DocumentClass) "
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
    best_combined = 0.0
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
        else:
            structural_score = 0.0

        class_concepts = [(c["label"], c["embedding"]) for c in row["concepts"] if c is not None]
        semantic_score = _concept_set_similarity(class_concepts, concept_vectors)

        combined = (structural_score + semantic_score) / 2
        if combined > best_combined:
            best_combined = combined
            best = (row["id"], row["name"], structural_score, semantic_score, combined)
    return best


def _write_concepts(
    session, class_id: str, document_sha256: str, vocabulary: list[dict],
    language: str | None, concept_normalizer: "_TypeNormalizer",
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
    class_merge.class_similarity (US7.6) le comparent sans le recalculer."""
    written = 0
    seen_labels: set[str] = set()
    for item in vocabulary:
        label = concept_normalizer.normalize(item["concept"])
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
    elements = flatten(root)
    # "citation_count" ajoute le 2026-09-29 dans latex_struct.py uniquement
    # (organisation du contenu LaTeX - citations, cf. module docstring) ; 0
    # par defaut pour les formats qui ne le renseignent pas.
    struct_profile = _structural_profile(elements, metadata.get("citation_count", 0))

    driver = get_driver()
    warnings: list[str] = []
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
            session, struct_profile, concept_vectors,
        )

        if recognized_id is not None and combined >= settings.recognition_threshold:
            class_id, class_name, status = recognized_id, recognized_name, DocumentStatus.recognized
            method = "score_combine_structure_semantique"
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
            method = "score_combine_insuffisant_creation_provisoire"
            session.run(
                "MATCH (d:Document {sha256: $sha256}) "
                "CREATE (c:DocumentClass {id: $cid, name: $name, status: 'provisoire', created_at: datetime()}) "
                "CREATE (d)-[:IN_CLASS {score: $score, structural_score: $struct_score, "
                "                       semantic_score: $sem_score, method: $method}]->(c)",
                sha256=sha256, cid=class_id, name=class_name, score=combined,
                struct_score=structural_score, sem_score=semantic_score, method=method,
            )
        step(
            "Ingestion", "Reconnaissance de classe (US7.4)",
            f"{method} -> {class_name} ({combined:.0%})",
        )
        session.run("MATCH (d:Document {sha256: $sha256}) SET d.language = $language", sha256=sha256, language=language)

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
        concept_normalizer = _TypeNormalizer(known_class_concepts)
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

        n_concepts = _write_concepts(session, class_id, sha256, vocabulary, language, concept_normalizer)
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
