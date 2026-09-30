"""Importe une ontologie semantique EXTERNE reelle (fichier RDF/OWL local,
deja telecharge) comme nouvelle classe de test, pour tester le generateur de
documents d'exemple (IAF-E16 US16.6). Demande explicite du 2026-09-30 :
"trouve des ontologies sémantiques sur internet [...] On va tester la
génération".

Usage : python scripts/import_semantic_ontology.py <fichier.rdf> "<nom de la classe>" [--format xml] [--language en]

Le fichier doit deja etre present localement (ce script ne telecharge rien -
voir docs/epics/EPIC-IAF-E16-generation-documents-exemple.md US16.6 pour la
source utilisee, ontologies/semantic-examples/wine.rdf, l'ontologie Wine du
W3C OWL Guide, https://www.w3.org/TR/owl-guide/wine.rdf).
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rdflib  # noqa: E402

from app.graph import get_driver  # noqa: E402
from app.ontology_import import extract_ontology_material, write_ontology_material  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rdf_file", help="chemin local vers le fichier RDF/OWL deja telecharge")
    parser.add_argument("class_name", help="nom donne a la nouvelle classe de test")
    parser.add_argument("--format", default="xml", help="format rdflib (xml, turtle, n3, ...) - defaut: xml")
    parser.add_argument("--language", default="en", help="langue du materiau importe (US16.1) - defaut: en")
    args = parser.parse_args()

    rdf_path = Path(args.rdf_file)
    if not rdf_path.exists():
        raise SystemExit(f"fichier introuvable : {rdf_path}")

    graph = rdflib.Graph()
    graph.parse(str(rdf_path), format=args.format)
    material = extract_ontology_material(graph, source=str(rdf_path))

    print(f"ontologie lue : {len(graph)} triples RDF")
    print(f"  concepts (owl:Class) : {len(material.concepts)}")
    print(f"  entites (individus)  : {len(material.entities)}")
    print(f"  relations (ObjectProperty) : {len(material.relations)}")
    print(f"  attributs (DatatypeProperty) : {len(material.attributes)}")

    if not material.entities and not material.concepts:
        raise SystemExit("aucun concept ni individu extrait - fichier/format incorrect ?")

    class_id = str(uuid.uuid4())
    driver = get_driver()
    with driver.session() as session:
        counts = write_ontology_material(session, class_id, args.class_name, material, language=args.language)

    print(f"\nclasse de test creee : {class_id} ({args.class_name!r})")
    print(f"ecrit dans Neo4j : {counts}")
    print(f"\na utiliser dans un fichier de config pour generate_documents.py : class_id: \"{class_id}\"")


if __name__ == "__main__":
    main()
