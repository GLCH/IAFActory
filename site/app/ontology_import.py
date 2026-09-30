"""Import d'une ontologie semantique EXTERNE reelle (RDF/OWL, trouvee sur
internet) comme materiau de test pour le generateur de documents (IAF-E16
US16.6). Demande explicite du 2026-09-30 : "trouve des ontologies
sémantiques sur internet [...] On va tester la génération".

Ne fabrique rien : lit les classes (owl:Class), individus (rdf:type vers une
owl:Class), assertions de proprietes objet (ObjectProperty entre deux
individus) et UNE assertion de propriete de donnees (DatatypeProperty) d'un
graphe RDF deja charge par rdflib, et les fait correspondre au meme schema
Neo4j que celui ecrit par le pipeline d'ingestion reel (pipeline.py) :
- owl:Class -> Concept (HAS_CONCEPT sur une DocumentClass)
- individu -> Entity (name = identifiant local, type = libelle local de sa
  classe rdf:type)
- assertion d'ObjectProperty entre deux individus -> relation REL
- assertion de DatatypeProperty -> attribut Neo4j sur le noeud Entity

Ainsi `doc_generator.load_class_material()` (qui ne sait lire QUE ce schema
Neo4j, decision de cadrage US16.1) fonctionne sans aucune modification sur
une classe seedee depuis une ontologie externe, exactement comme sur une
classe peuplee par une vraie ingestion de document."""
from __future__ import annotations

from dataclasses import dataclass, field

import rdflib
from rdflib.namespace import OWL, RDF


def _local_name(uri) -> str:
    s = str(uri)
    return s.split("#")[-1] if "#" in s else s.rsplit("/", 1)[-1]


@dataclass
class ImportedOntology:
    source: str
    concepts: list[str]
    entities: list[dict]  # {"name": str, "type": str}
    relations: list[tuple[str, str, str]]  # (source_name, relation_label, target_name)
    attributes: list[tuple[str, str, str]]  # (entity_name, key, value)


def extract_ontology_material(graph: rdflib.Graph, source: str) -> ImportedOntology:
    """Extrait le materiau reel d'un graphe RDF deja parse par rdflib. Pure
    (aucun acces reseau/Neo4j ici) - testable a l'identique sans aucun
    service externe.

    Bug reel trouve en testant (2026-09-30, ontologie Wine reelle) : une
    partie des `owl:Class` d'un fichier OWL sont des noeuds ANONYMES
    (`rdflib.BNode`, ex. les expressions de restriction comme "hasColor
    some Color") - 27 sur 101 mesures ici. Sans filtre, ils produisaient des
    "concepts" et "entites" au libelle illisible (l'identifiant blank node
    genere par rdflib, ex. "N91da3f6843684bc59806d9a8a2d3ee7c") dans les
    documents generes. Le modele Entity/Concept de ce projet suppose toujours
    un nom STABLE et lisible (US3.4/US7.1) - jamais un identifiant anonyme -
    donc seuls les noeuds NOMMES (`rdflib.URIRef`) sont retenus ici."""
    classes = {c for c in graph.subjects(RDF.type, OWL.Class) if isinstance(c, rdflib.URIRef)}
    object_properties = {p for p in graph.subjects(RDF.type, OWL.ObjectProperty) if isinstance(p, rdflib.URIRef)}
    data_properties = {p for p in graph.subjects(RDF.type, OWL.DatatypeProperty) if isinstance(p, rdflib.URIRef)}
    non_individual = classes | object_properties | data_properties

    concepts = sorted({_local_name(c) for c in classes})

    individuals: dict[str, str] = {}  # local name -> type local name
    for s, t in graph.subject_objects(RDF.type):
        if not isinstance(s, rdflib.URIRef) or s in non_individual or t not in classes:
            continue
        individuals[_local_name(s)] = _local_name(t)

    relations: list[tuple[str, str, str]] = []
    for prop in object_properties:
        prop_label = _local_name(prop)
        for s, o in graph.subject_objects(prop):
            s_name, o_name = _local_name(s), _local_name(o)
            if s_name in individuals and o_name in individuals:
                relations.append((s_name, prop_label, o_name))
    relations.sort()

    attributes: list[tuple[str, str, str]] = []
    for prop in data_properties:
        prop_label = _local_name(prop)
        for s, o in graph.subject_objects(prop):
            s_name = _local_name(s)
            if s_name in individuals:
                attributes.append((s_name, prop_label, str(o)))
    attributes.sort()

    entities = [{"name": name, "type": etype} for name, etype in sorted(individuals.items())]

    return ImportedOntology(source=source, concepts=concepts, entities=entities, relations=relations, attributes=attributes)


def write_ontology_material(session, class_id: str, class_name: str, material: ImportedOntology, language: str = "en") -> dict:
    """Ecrit le materiau extrait dans Neo4j avec EXACTEMENT le meme schema
    que le pipeline d'ingestion reel (MERGE DocumentClass/Concept/Entity/REL
    - mêmes noms de labels/proprietes que pipeline.py) pour que
    doc_generator.load_class_material() n'ait besoin d'aucun cas particulier.
    N'ecrit PAS Fuseki (US16.6 : reste dans le perimetre deja decide pour
    IAF-E16 - Neo4j uniquement, voir doc_generator.py). Renvoie un compte par
    type d'objet ecrit, pour un rapport honnete cote CLI."""
    session.run(
        "MERGE (c:DocumentClass {id: $cid}) "
        "SET c.name = $name, c.status = 'exemple-importe', c.source = $source, c.created_at = coalesce(c.created_at, datetime())",
        cid=class_id, name=class_name, source=material.source,
    )

    for label in material.concepts:
        session.run(
            "MATCH (c:DocumentClass {id: $cid}) "
            "MERGE (concept:Concept {label: $label, class_id: $cid}) "
            "MERGE (c)-[:HAS_CONCEPT]->(concept)",
            cid=class_id, label=label,
        )

    for e in material.entities:
        session.run(
            "MERGE (e:Entity {name: $name, class_id: $cid}) SET e.type = $type",
            name=e["name"], type=e["type"], cid=class_id,
        )

    for s_name, rel_type, t_name in material.relations:
        session.run(
            "MATCH (s:Entity {name: $s, class_id: $cid}), (t:Entity {name: $t, class_id: $cid}) "
            "MERGE (s)-[r:REL {type: $rtype}]->(t)",
            s=s_name, t=t_name, rtype=rel_type, cid=class_id,
        )

    for entity_name, key, value in material.attributes:
        session.run(
            "MATCH (e:Entity {name: $name, class_id: $cid}) "
            "CALL apoc.create.setProperty(e, $key, $value) YIELD node RETURN node",
            name=entity_name, key=key, value=value, cid=class_id,
        )

    return {
        "concepts": len(material.concepts),
        "entities": len(material.entities),
        "relations": len(material.relations),
        "attributes": len(material.attributes),
    }
