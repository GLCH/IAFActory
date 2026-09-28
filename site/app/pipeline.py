"""Pipeline d'ingestion, reconnaissance et structuration (US3.1, US3.2, US3.4,
US7.4, US7.5). Premiere version reelle, deliberement simplifiee par rapport a
la conception complete de IAF-E7 :

- reconnaissance a un seul signal (similarite cosinus entre l'embedding moyen
  du document et le centroïde de chaque classe existante), pas la cascade
  MinHash/LSH + embeddings + domaines + arbitrage LLM de US7.2/US7.3 ;
- seuil `settings.recognition_threshold` fixe, non calibre sur un jeu annote
  (US7.7 reste a faire) ;
- analyse structurelle reelle pour .docx, .pdf et .pptx (US3.1), mais tres
  inegale entre eux : .docx distingue vraiment titres/paragraphes/tableaux
  (styles Word) ; .pptx distingue diapositive/paragraphe/tableau (structure
  native du format) ; .pdf est le plus grossier - pdfplumber ne donne aucun
  signal de titre fiable, donc chaque PAGE est une section et chaque LIGNE de
  texte un paragraphe, sans reconstruction de paragraphes multi-lignes
  (pdf_struct.py). Aucun format n'utilise Docling (US3.8 reste a evaluer) ;
- pas d'ontologie semantique RDF versionnee dans Fuseki (US3.3) : le schema
  induit (types d'entites, relations, attributs) vit uniquement dans Neo4j,
  comme dans le PoC.

Aucun de ces raccourcis n'est cache : chaque document ingere porte sa methode
et son score dans le graphe (relation IN_CLASS), et les ecrans creator
affichent le statut reel (docs/epics/EPIC-IAF-E3-graph-rag.md, US3.5).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import docx_struct, pdf_struct, pptx_struct
from .config import settings
from .graph import chat_json, cosine_similarity, embed, get_driver
from .models import DocumentStatus
from .struct_element import flatten

PARSERS = {".docx": docx_struct.parse, ".pdf": pdf_struct.parse, ".pptx": pptx_struct.parse}

EXTRACTION_SYSTEM = (
    "Tu extrais des entites et relations d'un extrait de document technique. "
    "Reponds UNIQUEMENT en JSON valide, sans texte autour, au format exact : "
    '{"entities":[{"name":"...","type":"..."}],'
    '"attributes":[{"entity":"...","key":"...","value":"..."}],'
    '"relations":[{"source":"...","relation":"...","target":"..."}]}. '
    "N'invente aucune valeur absente du texte. Renvoie des listes vides si rien de pertinent."
)


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
    recognition_score: float = 0.0
    recognition_method: str = ""
    warnings: list[str] = field(default_factory=list)


def _ensure_vector_index(session, dimensions: int) -> None:
    session.run(
        "CREATE VECTOR INDEX chunkEmbeddings IF NOT EXISTS "
        "FOR (c:Chunk) ON c.embedding "
        "OPTIONS {indexConfig: {`vector.dimensions`: $dims, `vector.similarity_function`: 'cosine'}}",
        dims=dimensions,
    )


def _find_best_class(session, doc_embedding: list[float]) -> tuple[str | None, str | None, float]:
    """Classe existante la plus proche par similarite cosinus entre
    l'embedding moyen du document candidat et le centroide (moyenne des
    embeddings de chunks) des documents deja rattaches a chaque classe."""
    rows = list(session.run(
        "MATCH (c:DocumentClass) "
        "OPTIONAL MATCH (c)<-[:IN_CLASS]-(:Document)-[:HAS_ELEMENT]->(top:StructElement) "
        "OPTIONAL MATCH (top)-[:CHILD*0..]->(:StructElement)-[:HAS_CHUNK]->(chunk:Chunk) "
        "RETURN c.id AS id, c.name AS name, collect(DISTINCT chunk.embedding) AS embeddings"
    ))
    best_id = best_name = None
    best_score = 0.0
    for row in rows:
        embeddings = [e for e in row["embeddings"] if e]
        if not embeddings:
            continue
        dims = len(embeddings[0])
        centroid = [sum(e[i] for e in embeddings) / len(embeddings) for i in range(dims)]
        score = cosine_similarity(doc_embedding, centroid)
        if score > best_score:
            best_score = score
            best_id, best_name = row["id"], row["name"]
    return best_id, best_name, best_score


def ingest_document(sha256: str, filename: str, stored_path: Path) -> IngestResult:
    """Fait tourner le document a travers I (structure + chunks + embeddings),
    R (rattachement a une classe existante ou creation), et S (extraction
    d'entites/relations). Ecrit dans Neo4j ; ne touche pas Postgres (l'appelant
    reporte le resultat sur la ligne `Document`). `stored_path` est le fichier
    deja ecrit sur le volume (US3.1 : conserve sur volume avec empreinte
    sha256), analyse en place."""
    suffix = stored_path.suffix.lower()
    parse = PARSERS.get(suffix)
    if parse is None:
        raise UnsupportedFormat(f"format non pris en charge : {suffix or 'sans extension'}")

    metadata, root = parse(str(stored_path))
    elements = flatten(root)

    driver = get_driver()
    warnings: list[str] = []
    with driver.session() as session:
        session.run(
            "MERGE (d:Document {sha256: $sha256}) "
            "SET d.title = $title, d.author = $author, d.subject = $subject, "
            "    d.filename = $filename, d.status = 'ingere', d.ingested_at = datetime()",
            sha256=sha256, title=metadata.get("title"), author=metadata.get("author"),
            subject=metadata.get("subject"), filename=filename,
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
            n_chunks += 1

        if embed_dims:
            _ensure_vector_index(session, embed_dims)

        if not chunk_embeddings:
            raise ValueError("document sans texte extrait (page blanche, ou contenu non textuel)")
        dims = len(chunk_embeddings[0])
        doc_embedding = [sum(v[i] for v in chunk_embeddings) / len(chunk_embeddings) for i in range(dims)]

        recognized_id, recognized_name, score = _find_best_class(session, doc_embedding)

        if recognized_id is not None and score >= settings.recognition_threshold:
            class_id, class_name, status = recognized_id, recognized_name, DocumentStatus.recognized
            method = "cosinus_embedding_moyen_document"
            session.run(
                "MATCH (d:Document {sha256: $sha256}), (c:DocumentClass {id: $cid}) "
                "MERGE (d)-[r:IN_CLASS]->(c) "
                "SET r.score = $score, r.method = $method",
                sha256=sha256, cid=class_id, score=score, method=method,
            )
        else:
            class_id = str(uuid.uuid4())
            class_name = f"Provisoire - {metadata.get('title') or filename}"
            status = DocumentStatus.provisional
            method = "aucune_classe_proche_creation_provisoire"
            session.run(
                "MATCH (d:Document {sha256: $sha256}) "
                "CREATE (c:DocumentClass {id: $cid, name: $name, status: 'provisoire', created_at: datetime()}) "
                "CREATE (d)-[:IN_CLASS {score: $score, method: $method}]->(c)",
                sha256=sha256, cid=class_id, name=class_name, score=score, method=method,
            )

        n_entities = 0
        for chunk_id, text, section in chunk_rows:
            try:
                extracted = chat_json(
                    f"Extrait (section \"{section}\") :\n{text}",
                    model=settings.extraction_model,
                    system=EXTRACTION_SYSTEM,
                )
            except Exception as exc:  # un chunk qui echoue n'invalide pas le document (US3.4)
                warnings.append(f"extraction ratee pour un chunk ({section!r}) : {exc}")
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

    return IngestResult(
        status=status,
        neo4j_class_id=class_id,
        class_name=class_name,
        chunk_count=n_chunks,
        entity_count=n_entities,
        recognition_score=score,
        recognition_method=method,
        warnings=warnings,
    )
