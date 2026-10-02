"""Cree une classe documentaire complete : ontologie(s) STRUCTURELLE(s) et UNE
ontologie SEMANTIQUE, toutes dans Jena/Fuseki (US3.11/US3.12, IAF-92/IAF-93),
plus le miroir Neo4j necessaire au reste du site (ADR 0001 : Neo4j reste la
lecture rapide pour la reconnaissance/generation, IAF-E7/IAF-E16).

Demande explicite (2026-09-30) : "on peut donc creer une classe 'Vin' avec 2
ontologies structurelles, 1 ontologie semantique (unique par classe) (toutes
dans Jena)". Generation de documents de test explicitement DIFFEREE (contenu
mecanique actuel juge "pas suffisamment bon" par l'utilisateur) - ce script
ne fait QUE creer la classe et ses ontologies, rien d'autre.

Ce que ce script fait reellement, dans l'ordre :
1. Charge le vocabulaire structurel COMMUN (ontologies/structure/*.ttl) dans
   Fuseki si pas deja fait - un seul exemplaire partage par toutes les
   classes, jamais duplique (app/ontology.py:load_turtle_file). Ces fichiers
   restaient statiques sur disque jusqu'ici (jamais chargee dans Fuseki,
   malgre US3.11/IAF-92 deja specifie depuis le 2026-09-26).
2. Extrait le materiau semantique reel d'un fichier RDF/OWL deja telecharge
   (meme extracteur que import_semantic_ontology.py, US16.6 - pas de
   duplication de logique) et l'ecrit :
   - dans Neo4j (DocumentClass/Concept/Entity/REL/attributs, miroir deja
     utilise par pipeline.py/doc_generator.py) ;
   - dans Fuseki, graphe nomme de la classe (owl:Class par concept,
     app/ontology.py:ensure_concept - MEME convention que l'ecriture reelle
     faite a l'ingestion d'un document, US7.5) - UNE seule ontologie
     semantique par classe (pas de notion de plusieurs ontologies
     semantiques : decision deja actee, US3.12/EPIC-IAF-E7).
3. Relie la classe (son graphe Fuseki) aux ontologies structurelles qu'elle
   ACCEPTE (`iafs:acceptsStructure`, repetable - US3.11 generalise le
   2026-09-30 pour accepter 0..n formes structurelles, pas une seule).

Usage :
  python scripts/create_class.py "Vin" ../ontologies/semantic-examples/wine.rdf \
      --structures base,livre --format xml --language en

Voir docs/epics/EPIC-IAF-E3-graph-rag.md US3.11 pour la decision de cadrage.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rdflib  # noqa: E402

from app import ontology  # noqa: E402
from app.graph import get_driver  # noqa: E402
from app.ontology_import import extract_ontology_material, write_ontology_material  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STRUCTURE_ONTOLOGIES = {
    # cle CLI -> (URI owl:Ontology, fichier .ttl source, graphe nomme Fuseki partage)
    "base": ("urn:iaf:ns:structure", REPO_ROOT / "ontologies/structure/iaf-structure-base.ttl", ontology.STRUCTURE_BASE_GRAPH),
    "livre": ("urn:iaf:ns:structure-livre", REPO_ROOT / "ontologies/structure/iaf-structure-livre.ttl", ontology.STRUCTURE_LIVRE_GRAPH),
    # 2026-10-01 (US7.2-structurel) : ontologies structurelles PAR FORMAT DE
    # FICHIER, avec axiomes de cardinalite, utilisees par app/structure_matcher.py.
    "word": ("urn:iaf:ns:structure-word", REPO_ROOT / "ontologies/structure/iaf-structure-word.ttl", ontology.STRUCTURE_WORD_GRAPH),
    "pdf": ("urn:iaf:ns:structure-pdf", REPO_ROOT / "ontologies/structure/iaf-structure-pdf.ttl", ontology.STRUCTURE_PDF_GRAPH),
    "latex": ("urn:iaf:ns:structure-latex", REPO_ROOT / "ontologies/structure/iaf-structure-latex.ttl", ontology.STRUCTURE_LATEX_GRAPH),
}


def ensure_structure_vocabularies_loaded(keys: list[str]) -> None:
    for key in keys:
        ontology_uri, ttl_path, graph_uri = STRUCTURE_ONTOLOGIES[key]
        ontology.load_turtle_file(graph_uri, ttl_path.read_bytes())
        print(f"  vocabulaire structurel '{key}' charge dans Fuseki (graphe {graph_uri})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("class_name", help="nom de la nouvelle classe documentaire (ex. 'Vin')")
    parser.add_argument("semantic_rdf_file", help="fichier RDF/OWL local deja telecharge (ontologie semantique)")
    parser.add_argument(
        "--structures", default="base,livre",
        help="formes structurelles acceptees par la classe, separees par des virgules "
             f"(disponibles : {', '.join(STRUCTURE_ONTOLOGIES)}) - defaut : base,livre",
    )
    parser.add_argument("--format", default="xml", help="format rdflib du fichier semantique (xml, turtle, n3...) - defaut: xml")
    parser.add_argument("--language", default="en", help="langue du materiau semantique importe - defaut: en")
    args = parser.parse_args()

    structure_keys = [k.strip() for k in args.structures.split(",") if k.strip()]
    unknown = set(structure_keys) - set(STRUCTURE_ONTOLOGIES)
    if unknown:
        raise SystemExit(f"structure(s) inconnue(s) : {sorted(unknown)} (disponibles : {sorted(STRUCTURE_ONTOLOGIES)})")
    if not structure_keys:
        raise SystemExit("au moins une ontologie structurelle est requise (--structures)")

    rdf_path = Path(args.semantic_rdf_file)
    if not rdf_path.exists():
        raise SystemExit(f"fichier semantique introuvable : {rdf_path}")

    print(f"1. vocabulaire(s) structurel(s) commun(s) ({', '.join(structure_keys)}) :")
    ensure_structure_vocabularies_loaded(structure_keys)

    print("\n2. materiau semantique :")
    graph = rdflib.Graph()
    graph.parse(str(rdf_path), format=args.format)
    material = extract_ontology_material(graph, source=str(rdf_path))
    print(f"  ontologie lue : {len(graph)} triples RDF - {len(material.concepts)} concepts, "
          f"{len(material.entities)} entites, {len(material.relations)} relations, "
          f"{len(material.attributes)} attributs")
    if not material.entities and not material.concepts:
        raise SystemExit("aucun concept ni individu extrait - fichier/format incorrect ?")

    class_id = str(uuid.uuid4())
    driver = get_driver()
    with driver.session() as session:
        counts = write_ontology_material(session, class_id, args.class_name, material, language=args.language)
    print(f"  Neo4j (miroir, ADR 0001) : {counts}")

    for label in material.concepts:
        ontology.ensure_concept(class_id, label, language=args.language)
    for child, parent in material.subclass_of:
        ontology.ensure_subclass(class_id, child, parent)
    for label, definition in material.concept_definitions.items():
        ontology.set_concept_definition(class_id, label, definition, language=args.language)
    print(f"  Fuseki (ontologie semantique, graphe {ontology.class_graph_uri(class_id)}) : "
          f"{len(material.concepts)} owl:Class et {len(material.subclass_of)} rdfs:subClassOf et {len(material.concept_definitions)} definition(s) ecrites")

    print("\n3. liaison classe -> ontologie(s) structurelle(s) :")
    for key in structure_keys:
        ontology_uri, _ttl_path, _graph_uri = STRUCTURE_ONTOLOGIES[key]
        ontology.ensure_class_accepts_structure(class_id, ontology_uri)
        print(f"  classe -> iafs:acceptsStructure -> <{ontology_uri}> ({key})")

    # Verification reelle (pas suppose) : relecture SPARQL directe.
    accepted = ontology.list_accepted_structures(class_id)
    fuseki_concepts = ontology.list_concepts(class_id)
    print(
        f"\nVerifie par relecture SPARQL directe : {len(accepted)} ontologie(s) structurelle(s) accepteee(s), "
        f"{len(fuseki_concepts)} concept(s) OWL dans le graphe semantique de la classe."
    )
    print(f"\nclasse creee : {class_id} ({args.class_name!r})")
    print(f"a utiliser dans un fichier de config pour generate_documents.py : class_id: \"{class_id}\"")


if __name__ == "__main__":
    main()
