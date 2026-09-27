"""Etape agent/session simplifiee du PoC : embarque la question, recherche
vectorielle dans Neo4j, recupere le contexte (chunk + section + entites
mentionnees), demande une reponse sourcee au modele local.

Usage : python poc/ask.py "question ?"
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests
from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import EMBEDDING_MODEL, GATEWAY_KEY, GATEWAY_URL, NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from llm import chat

TOP_K = 3


def embed(text: str) -> list[float]:
    r = requests.post(
        f"{GATEWAY_URL}/v1/embeddings",
        headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
        json={"model": EMBEDDING_MODEL, "input": text},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


def main(question: str) -> None:
    t0 = time.time()
    vector = embed(question)

    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:
        hits = list(session.run(
            "CALL db.index.vector.queryNodes('chunkEmbeddings', $k, $vector) "
            "YIELD node AS chunk, score "
            "MATCH (el:StructElement)-[:HAS_CHUNK]->(chunk) "
            "MATCH (d:Document)-[:HAS_ELEMENT]->(:StructElement)-[:CHILD*0..]->(el) "
            "OPTIONAL MATCH (chunk)-[:MENTIONS]->(e:Entity) "
            "RETURN chunk.text AS text, el.label AS section, d.title AS doc, score, "
            "       collect(DISTINCT e.name) AS entities",
            k=TOP_K, vector=vector,
        ))
    driver.close()

    if not hits:
        print("aucun chunk trouve (index vide ? lancer poc/ingest.py d'abord)")
        return

    context_blocks = []
    for h in hits:
        ents = f" [entites mentionnees : {', '.join(h['entities'])}]" if h["entities"] else ""
        context_blocks.append(f"- ({h['doc']} / section \"{h['section']}\", score {h['score']:.3f}){ents}\n  {h['text']}")
    context = "\n".join(context_blocks)

    print("--- passages retrouves ---")
    print(context)

    prompt = (
        f"Contexte extrait de documents :\n{context}\n\n"
        f"Question : {question}\n\n"
        "Reponds uniquement a partir du contexte ci-dessus. Cite la section source entre "
        "parentheses. Si le contexte ne permet pas de repondre, dis-le explicitement."
    )
    answer = chat(prompt, system="Tu es un agent qui repond de facon precise et sourcee, sans inventer.")
    dt = time.time() - t0
    print("\n--- reponse de l'agent ---")
    print(answer)
    print(f"\n(latence totale : {dt:.1f} s)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit('usage: python poc/ask.py "question ?"')
    main(sys.argv[1])
