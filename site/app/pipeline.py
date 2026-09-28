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
"""
from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import docx_struct, latex_struct, markdown_struct, ontology, pdf_struct, pptx_struct
from .config import settings
from .graph import chat_json, embed, get_driver
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

EXTRACTION_SYSTEM = (
    "Tu extrais des entites et relations d'un extrait de document technique. "
    "Reponds UNIQUEMENT en JSON valide, sans texte autour, au format exact : "
    '{"entities":[{"name":"...","type":"..."}],'
    '"attributes":[{"entity":"...","key":"...","value":"..."}],'
    '"relations":[{"source":"...","relation":"...","target":"..."}]}. '
    "N'invente aucune valeur absente du texte. Renvoie des listes vides si rien de pertinent."
)

# IAF-E7 US7.1 : etape dediee, distincte de l'extraction d'entites ci-dessus.
# Vocabulaire = termes du domaine (pas des entites nommees precises), chacun
# associe au concept (classe OWL) qu'il represente.
VOCABULARY_SYSTEM = (
    "Tu extrais le vocabulaire technique d'un document (10 a 20 termes les plus significatifs, "
    "pas des entites nommees precises comme un nom de societe). Le document peut relever de "
    "n'importe quel domaine technique, y compris mathematique ou machine learning (ex: fonction "
    "de perte, gradient, notation mathematique, algorithme, hyperparametre) - ne privilegie pas "
    "un domaine industriel par defaut. Pour chaque terme, donne le concept general auquel il "
    "appartient (un ou deux mots, en francais, ex: Materiau, Norme, Fonction de perte, Algorithme, "
    "Notation mathematique, Fournisseur). Reponds UNIQUEMENT en JSON valide, sans texte autour, au "
    'format exact : {"vocabulary":[{"term":"...","concept":"..."}]}.'
)
VOCABULARY_TEXT_LIMIT = 4000  # caracteres ; borne le cout/latence sur le modele local


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


def extract_vocabulary(text: str, model: str) -> list[dict]:
    """IAF-E7 US7.1. Renvoie une liste de {"term", "concept"} ; liste vide
    (pas d'exception) si l'appel echoue - un vocabulaire absent ne doit pas
    invalider le document (meme principe que US3.4 pour l'extraction par
    chunk)."""
    excerpt = text[:VOCABULARY_TEXT_LIMIT]
    try:
        # max_tokens releve a 3000 (addendum US3.15, 2026-09-28) : constate
        # reellement tronque a 1200 (defaut de chat_json) sur un document
        # scientifique reel - 10-20 termes + concept + jetons de raisonnement
        # Gemini 2.5 depassent largement 1200.
        result = chat_json(excerpt, model=model, system=VOCABULARY_SYSTEM, timeout=90, max_tokens=3000)
    except Exception:
        return []
    vocabulary = []
    for item in result.get("vocabulary", []):
        term, concept = item.get("term"), item.get("concept")
        if term and concept:
            vocabulary.append({"term": term, "concept": concept})
    return vocabulary


def _structural_profile(elements: list[StructElement]) -> dict[str, float]:
    counts = {kind: 0 for kind in STRUCT_KINDS}
    for elem in elements:
        if elem.kind in counts:
            counts[elem.kind] += 1
    total = sum(counts.values())
    if total == 0:
        return {kind: 0.0 for kind in STRUCT_KINDS}
    return {kind: n / total for kind, n in counts.items()}


def _profile_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """1 - distance L1 / 2 : les deux profils sont des distributions de
    proportions (somme 1), la distance L1 entre deux distributions est bornee
    a [0, 2], donc cette similarite est bornee a [0, 1]. Simple et explicable
    plutot qu'une mesure plus sophistiquee non calibree (US7.7 tranchera)."""
    l1 = sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in STRUCT_KINDS)
    return max(0.0, 1.0 - l1 / 2)


def _ensure_vector_index(session, dimensions: int) -> None:
    session.run(
        "CREATE VECTOR INDEX chunkEmbeddings IF NOT EXISTS "
        "FOR (c:Chunk) ON c.embedding "
        "OPTIONS {indexConfig: {`vector.dimensions`: $dims, `vector.similarity_function`: 'cosine'}}",
        dims=dimensions,
    )


def _find_best_class(
    session, struct_profile: dict[str, float], concept_labels: set[str],
) -> tuple[str | None, str | None, float, float, float]:
    """IAF-E7 US7.4 : score combine (structurel + semantique) contre chaque
    classe existante. Une classe sans document membre (profil structurel
    indefini) ou sans concept connu obtient un score nul sur l'axe
    correspondant - une classe toute neuve ne "reconnait" donc jamais un
    nouveau document par accident."""
    rows = list(session.run(
        "MATCH (c:DocumentClass) "
        "OPTIONAL MATCH (c)<-[:IN_CLASS]-(d:Document) "
        "OPTIONAL MATCH (c)-[:HAS_CONCEPT]->(concept:Concept) "
        "RETURN c.id AS id, c.name AS name, "
        "       collect(DISTINCT {section: d.profile_section, paragraph: d.profile_paragraph, "
        "                         table: d.profile_table, equation: d.profile_equation}) AS profiles, "
        "       collect(DISTINCT concept.label) AS concepts"
    ))
    best = (None, None, 0.0, 0.0, 0.0)
    best_combined = 0.0
    for row in rows:
        profiles = [p for p in row["profiles"] if p.get("section") is not None]
        if profiles:
            # p[kind.lower()] peut etre None pour "equation" sur un document
            # ingere avant son ajout (2026-09-28) au sein d'une classe qui a
            # par ailleurs des documents plus recents - 0.0 par defaut.
            avg_profile = {
                kind: sum(p[kind.lower()] or 0.0 for p in profiles) / len(profiles) for kind in STRUCT_KINDS
            }
            structural_score = _profile_similarity(struct_profile, avg_profile)
        else:
            structural_score = 0.0

        class_concepts = {_normalize_label(c) for c in row["concepts"] if c}
        union = class_concepts | concept_labels
        semantic_score = len(class_concepts & concept_labels) / len(union) if union else 0.0

        combined = (structural_score + semantic_score) / 2
        if combined > best_combined:
            best_combined = combined
            best = (row["id"], row["name"], structural_score, semantic_score, combined)
    return best


def _write_concepts(session, class_id: str, document_sha256: str, vocabulary: list[dict]) -> int:
    """Ecrit chaque concept induit comme classe OWL dans Fuseki (US7.5) et
    son rattachement dans Neo4j (lecture rapide pour les ecrans du site,
    US3.14). Un concept qui echoue a s'ecrire dans Fuseki ne bloque pas les
    autres (meme principe que l'extraction par chunk, US3.4)."""
    written = 0
    seen_labels: set[str] = set()
    for item in vocabulary:
        label = item["concept"]
        normalized = _normalize_label(label)
        if normalized in seen_labels:
            continue
        seen_labels.add(normalized)
        try:
            uri = ontology.ensure_concept(class_id, label)
        except Exception:
            continue  # Fuseki indisponible ou erreur reseau : le concept reste absent, pas de silence en amont (log gateway)
        session.run(
            "MERGE (c:DocumentClass {id: $class_id}) "
            "MERGE (concept:Concept {label: $label, class_id: $class_id}) "
            "SET concept.uri = $uri "
            "MERGE (c)-[:HAS_CONCEPT]->(concept) "
            "WITH concept "
            "MATCH (d:Document {sha256: $sha256}) "
            "MERGE (d)-[:MENTIONS_CONCEPT {term: $term}]->(concept)",
            class_id=class_id, label=label, uri=uri, sha256=document_sha256, term=item["term"],
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
    struct_profile = _structural_profile(elements)

    driver = get_driver()
    warnings: list[str] = []
    with driver.session() as session:
        session.run(
            "MERGE (d:Document {sha256: $sha256}) "
            "SET d.title = $title, d.author = $author, d.subject = $subject, "
            "    d.filename = $filename, d.status = 'ingere', d.ingested_at = datetime(), "
            "    d.profile_section = $section, d.profile_paragraph = $paragraph, d.profile_table = $table, "
            "    d.profile_equation = $equation",
            sha256=sha256, title=metadata.get("title"), author=metadata.get("author"),
            subject=metadata.get("subject"), filename=filename,
            section=struct_profile["Section"], paragraph=struct_profile["Paragraph"], table=struct_profile["Table"],
            equation=struct_profile["Equation"],
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
        # d'entites ci-dessous).
        vocabulary = extract_vocabulary("\n".join(text_parts), settings.extraction_model)
        if not vocabulary:
            warnings.append("extraction de vocabulaire vide ou ratee (US7.1) : score semantique a 0")
        concept_labels = {_normalize_label(v["concept"]) for v in vocabulary}
        step("Ingestion", "Extraction du vocabulaire (US7.1)", f"{len(vocabulary)} termes" if vocabulary else "echec ou vide")

        recognized_id, recognized_name, structural_score, semantic_score, combined = _find_best_class(
            session, struct_profile, concept_labels,
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

        n_concepts = _write_concepts(session, class_id, sha256, vocabulary)
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
