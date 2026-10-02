"""Tests IAF-E16 US16.6 : extraction d'une ontologie RDF/OWL externe reelle
(pure, sans Neo4j - voir app/ontology_import.py). Utilise le vrai fichier
telecharge (ontologies/semantic-examples/wine.rdf, l'ontologie Wine du W3C
OWL Guide) plutot qu'un graphe RDF ecrit a la main, pour verifier
l'extraction contre un fichier reel, pas un cas jouet invente pour
l'occasion."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import rdflib

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ontology_import import extract_ontology_material  # noqa: E402

WINE_RDF = Path(__file__).resolve().parent.parent.parent / "ontologies" / "semantic-examples" / "wine.rdf"


@pytest.fixture(scope="module")
def wine_graph() -> rdflib.Graph:
    if not WINE_RDF.exists():
        pytest.skip(f"fichier de test non present : {WINE_RDF}")
    g = rdflib.Graph()
    g.parse(str(WINE_RDF), format="xml")
    return g


def test_extracts_real_classes_as_concepts(wine_graph):
    material = extract_ontology_material(wine_graph, source="wine.rdf")
    assert "Wine" in material.concepts
    assert "Winery" in material.concepts
    assert len(material.concepts) > 50  # ontologie Wine reelle : 101 owl:Class mesurees le 2026-09-30


def test_extracts_real_individuals_with_their_type(wine_graph):
    material = extract_ontology_material(wine_graph, source="wine.rdf")
    entities_by_name = {e["name"]: e["type"] for e in material.entities}
    assert entities_by_name.get("BancroftChardonnay") == "Chardonnay"


def test_extracts_real_object_property_assertions_between_individuals(wine_graph):
    material = extract_ontology_material(wine_graph, source="wine.rdf")
    assert ("BancroftChardonnay", "hasMaker", "Bancroft") in material.relations


def test_extracts_real_datatype_property_assertion(wine_graph):
    material = extract_ontology_material(wine_graph, source="wine.rdf")
    assert ("Year1998", "yearValue", "1998") in material.attributes


def test_extraction_never_fabricates_entities_outside_the_graph(wine_graph):
    material = extract_ontology_material(wine_graph, source="wine.rdf")
    all_names = {e["name"] for e in material.entities}
    assert "ThisWineDoesNotExist" not in all_names


def test_extraction_excludes_anonymous_blank_node_classes(wine_graph):
    # Bug reel trouve en testant : les expressions de restriction OWL
    # anonymes (owl:Restriction, ex. "hasColor some Color") sont aussi des
    # owl:Class mais sans nom stable - ne doivent jamais devenir un concept
    # ou une entite au libelle illisible (identifiant blank node genere).
    material = extract_ontology_material(wine_graph, source="wine.rdf")
    # 74 owl:Class NOMMEES (rdflib.URIRef) mesurees reellement sur ce fichier
    # le 2026-09-30, contre 101 owl:Class au total (27 sont des blank nodes
    # anonymes, exclues).
    assert len(material.concepts) == 74
    assert not any(c.startswith("N") and len(c) == 33 for c in material.concepts)
    assert not any(e["name"].startswith("N") and len(e["name"]) == 33 for e in material.entities)


def test_extracts_real_subclass_hierarchy_between_named_classes(wine_graph):
    # 2026-10-02 : hierarchie REELLE (rdfs:subClassOf entre classes nommees,
    # mesure : 13 relations dans wine.rdf - la plupart des classes Wine sont
    # definies par des restrictions anonymes, pas par un parent nomme).
    material = extract_ontology_material(wine_graph, source="wine.rdf")
    edges = set(material.subclass_of)
    assert ("DessertWine", "Wine") in edges
    assert ("Sauternes", "Bordeaux") in edges
    assert ("WineBody", "WineTaste") in edges
    assert all(child in material.concepts and parent in material.concepts for child, parent in edges)


def test_concept_definitions_only_come_from_real_comments(wine_graph):
    material = extract_ontology_material(wine_graph, source="wine.rdf")
    assert len(material.concept_definitions) <= len(material.concepts)
    assert set(material.concept_definitions) <= set(material.concepts)
