"""Etapes R (reconnaissance) et S (structuration) simplifiees du PoC.

Simplification assumee (voir poc/README.md) : un seul document connu ->
toujours "non reconnu" au premier passage -> classe provisoire creee
directement (etape C), sans cascade de comparaison (IAF-E7), sans ecriture
d'ontologie RDF dans Fuseki. L'extraction semantique (etape S) appelle le
modele local par chunk, pour garder la provenance (quel chunk a produit
quelle entite).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from llm import chat_json

EXTRACTION_SYSTEM = (
    "Tu extrais des entites et relations d'un extrait de fiche technique. "
    "Reponds UNIQUEMENT en JSON valide, sans texte autour, au format exact : "
    '{"entities":[{"name":"...","type":"..."}],'
    '"attributes":[{"entity":"...","key":"...","value":"..."}],'
    '"relations":[{"source":"...","relation":"...","target":"..."}]}. '
    "Types d'entite suggeres : Materiau, Norme, Fournisseur, Organisation, Caracteristique. "
    "N'invente aucune valeur absente du texte. Renvoie des listes vides si rien de pertinent."
)


def main() -> None:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as session:
        doc = session.run(
            "MATCH (d:Document) RETURN d.sha256 AS sha256, d.title AS title "
            "ORDER BY d.ingested_at DESC LIMIT 1"
        ).single()
        if not doc:
            raise SystemExit("aucun document ingere : lancer poc/ingest.py d'abord")
        sha256, title = doc["sha256"], doc["title"]

        existing_class = session.run(
            "MATCH (d:Document {sha256: $sha256})-[:IN_CLASS]->(c:DocumentClass) "
            "RETURN c.id AS id LIMIT 1", sha256=sha256,
        ).single()
        if existing_class:
            class_id = existing_class["id"]
            print(f"document deja rattache a la classe {class_id} (relance = restructuration)")
        else:
            class_id = str(uuid.uuid4())
            session.run(
                "MATCH (d:Document {sha256: $sha256}) "
                "CREATE (c:DocumentClass {id: $cid, name: $name, status: 'provisoire', created_at: datetime()}) "
                "CREATE (d)-[:IN_CLASS {method: 'unique_document_connu', note: "
                "'seul document connu au moment de la creation, aucune comparaison effectuee'}]->(c) "
                "SET d.status = 'classe_creee'",
                sha256=sha256, cid=class_id, name=f"Provisoire - {title}",
            )
            print(f"aucune classe existante : classe provisoire {class_id} creee")

        chunks = list(session.run(
            "MATCH (d:Document {sha256: $sha256})-[:HAS_ELEMENT]->(:StructElement)-[:CHILD*0..]->(el:StructElement)"
            "-[:HAS_CHUNK]->(c:Chunk) "
            "RETURN DISTINCT c.id AS id, c.text AS text, el.label AS section",
            sha256=sha256,
        ))
        print(f"{len(chunks)} chunks a structurer")

        n_entities = n_rel = n_attr = 0
        for row in chunks:
            chunk_id, text, section = row["id"], row["text"], row["section"]
            try:
                extracted = chat_json(f"Extrait (section \"{section}\") :\n{text}", system=EXTRACTION_SYSTEM)
            except Exception as exc:  # modele local peu fiable : on ne casse pas tout le lot
                print(f"  chunk {chunk_id[:8]} ({section}) : extraction ratee ({exc})")
                continue

            for ent in extracted.get("entities", []):
                name, etype = ent.get("name"), ent.get("type", "Autre")
                if not name:
                    continue
                session.run(
                    "MERGE (e:Entity {name: $name, class_id: $class_id}) "
                    "SET e.type = $type "
                    "WITH e "
                    "MATCH (c:Chunk {id: $chunk_id}) "
                    "MERGE (c)-[:MENTIONS]->(e)",
                    name=name, type=etype, class_id=class_id, chunk_id=chunk_id,
                )
                n_entities += 1

            for attr in extracted.get("attributes", []):
                ent_name, key, value = attr.get("entity"), attr.get("key"), attr.get("value")
                if not (ent_name and key):
                    continue
                session.run(
                    "MATCH (e:Entity {name: $name, class_id: $class_id}) "
                    "CALL apoc.create.setProperty(e, $key, $value) YIELD node "
                    "RETURN node",
                    name=ent_name, class_id=class_id, key=key, value=value,
                )
                n_attr += 1

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
                n_rel += 1

    driver.close()
    print(f"structuration terminee : {n_entities} liens entite-chunk, {n_attr} attributs, {n_rel} relations")


if __name__ == "__main__":
    main()
