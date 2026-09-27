"""Etape I (ingestion) simplifiee du PoC : lit un .docx, en tire un squelette
structurel et des chunks, les vectorise via la passerelle LLM (alias
iaf-embedding -> Ollama nomic-embed-text), et ecrit tout dans Neo4j.

Usage : python poc/ingest.py <chemin_du_docx>
"""
from __future__ import annotations

import hashlib
import sys
import time
import uuid
from pathlib import Path

import requests
from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import EMBEDDING_MODEL, GATEWAY_KEY, GATEWAY_URL, NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from docx_struct import flatten, parse


def embed(text: str) -> list[float]:
    r = requests.post(
        f"{GATEWAY_URL}/v1/embeddings",
        headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
        json={"model": EMBEDDING_MODEL, "input": text},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def ensure_vector_index(session, dimensions: int) -> None:
    session.run(
        "CREATE VECTOR INDEX chunkEmbeddings IF NOT EXISTS "
        "FOR (c:Chunk) ON c.embedding "
        "OPTIONS {indexConfig: {`vector.dimensions`: $dims, `vector.similarity_function`: 'cosine'}}",
        dims=dimensions,
    )


def main(path_str: str) -> None:
    path = Path(path_str)
    raw = path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()

    document, root = parse(str(path))
    core = document.core_properties
    doc_id = str(uuid.uuid4())

    elements = flatten(root)
    print(f"squelette : {len(elements)} elements (sections, paragraphes, tableaux)")

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    t0 = time.time()
    n_chunks = 0
    embed_dims = None
    with driver.session() as session:
        session.run(
            "MERGE (d:Document {sha256: $sha256}) "
            "SET d.id = $id, d.title = $title, d.author = $author, d.subject = $subject, "
            "    d.source_path = $path, d.status = 'ingere', d.ingested_at = datetime()",
            sha256=sha256, id=doc_id, title=core.title, author=core.author,
            subject=core.subject, path=str(path),
        )

        # ids en memoire python -> ids stables cote Neo4j ; parent_id=None pour les
        # enfants directs du Document.
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
            n_chunks += 1

        if embed_dims:
            ensure_vector_index(session, embed_dims)

    driver.close()
    dt = time.time() - t0
    print(f"document {doc_id} (sha256 {sha256[:12]}...) : {n_chunks} chunks embarques "
          f"(dimension {embed_dims}), ecrits en {dt:.1f} s")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python poc/ingest.py <chemin_du_docx>")
    main(sys.argv[1])
