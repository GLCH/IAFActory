"""Rattrape la hierarchie REELLE des classes (`rdfs:subClassOf`) pour une
classe deja importee depuis un fichier RDF/OWL, avant que l'import ne la
conserve (2026-10-02, ontology_import.subclass_of). Ecrit
`(:Concept)-[:SUBCLASS_OF]->(:Concept)` dans Neo4j et `rdfs:subClassOf` dans
le graphe Fuseki de la classe. Idempotent (MERGE / INSERT DATA).

Usage : python scripts/backfill_subclass_of.py <class_id> <fichier.rdf> [--format xml]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rdflib  # noqa: E402

from app import ontology  # noqa: E402
from app.graph import get_driver  # noqa: E402
from app.ontology_import import extract_ontology_material, write_subclass_edges  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("class_id")
    parser.add_argument("rdf_file")
    parser.add_argument("--format", default="xml")
    parser.add_argument("--language", default="en")
    args = parser.parse_args()

    graph = rdflib.Graph()
    graph.parse(args.rdf_file, format=args.format)
    material = extract_ontology_material(graph, source=args.rdf_file)
    print(f"{len(material.subclass_of)} relation(s) sous-classe/parent lue(s) dans {args.rdf_file}")

    with get_driver().session() as session:
        known = {r["label"] for r in session.run(
            "MATCH (:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(c:Concept) RETURN c.label AS label", cid=args.class_id,
        )}
        edges = [(c, p) for c, p in material.subclass_of if c in known and p in known]
        skipped = len(material.subclass_of) - len(edges)
        write_subclass_edges(session, args.class_id, edges)
    for child, parent in edges:
        ontology.ensure_subclass(args.class_id, child, parent)
    print(f"{len(edges)} relation(s) ecrite(s) (Neo4j + Fuseki), {skipped} ignoree(s) (concept absent de la classe)")

    n_def = 0
    with get_driver().session() as session:
        for label, definition in material.concept_definitions.items():
            if label not in known:
                continue
            session.run(
                "MATCH (c:Concept {label: $label, class_id: $cid}) SET c.definition = $definition",
                label=label, cid=args.class_id, definition=definition,
            )
            ontology.set_concept_definition(args.class_id, label, definition, language=args.language)
            n_def += 1
    print(f"{n_def} definition(s) reelle(s) (rdfs:comment) ecrite(s)")


if __name__ == "__main__":
    main()
