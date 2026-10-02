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
from rdflib.collection import Collection
from rdflib.namespace import OWL, RDF, RDFS

from .graph import embed


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
    # 2026-10-02 : hierarchie REELLE des classes (enfant, parent) - `rdfs:subClassOf`
    # entre classes NOMMEES et membres NOMMES d'un `owl:intersectionOf`
    # d'equivalence (A = B ET C implique A sous-classe de B et de C). Perdue a
    # l'import avant ce jour (seuls les libelles etaient ecrits, hierarchie
    # plate) - sert a construire une taxonomie profonde sans l'inventer.
    subclass_of: list[tuple[str, str]] = field(default_factory=list)
    # 2026-10-02 : definition REELLE (rdfs:comment) des classes nommees quand
    # le fichier en porte une (C2SIM : 171 sur 172 ; Wine : 1 sur 74) - jamais
    # inventee, jamais completee par le LLM (US3.17).
    concept_definitions: dict[str, str] = field(default_factory=dict)


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

    subclass_edges: set[tuple[str, str]] = set()
    for cls in classes:
        for parent in graph.objects(cls, RDFS.subClassOf):
            if isinstance(parent, rdflib.URIRef) and parent in classes and parent != cls:
                subclass_edges.add((_local_name(cls), _local_name(parent)))
        for equivalent in graph.objects(cls, OWL.equivalentClass):
            for member_list in graph.objects(equivalent, OWL.intersectionOf):
                for member in Collection(graph, member_list):
                    if isinstance(member, rdflib.URIRef) and member in classes and member != cls:
                        subclass_edges.add((_local_name(cls), _local_name(member)))

    concept_definitions: dict[str, str] = {}
    for cls in classes:
        comment = next((str(c).strip() for c in graph.objects(cls, RDFS.comment) if str(c).strip()), None)
        if comment:
            concept_definitions[_local_name(cls)] = " ".join(comment.split())

    return ImportedOntology(
        source=source, concepts=concepts, entities=entities, relations=relations, attributes=attributes,
        subclass_of=sorted(subclass_edges), concept_definitions=concept_definitions,
    )


def write_subclass_edges(session, class_id: str, edges: list[tuple[str, str]]) -> int:
    """2026-10-02 : `(enfant)-[:SUBCLASS_OF]->(parent)` entre Concept d'une
    classe (idempotent, MERGE) - lecture rapide pour la taxonomie
    (taxonomy_builder.py) ; l'ecriture RDF correspondante (`rdfs:subClassOf`
    dans le graphe Fuseki de la classe, source de verite) est faite par
    ontology.ensure_subclass depuis les scripts d'import."""
    n = 0
    for child, parent in edges:
        session.run(
            "MATCH (c:Concept {label: $child, class_id: $cid}), (p:Concept {label: $parent, class_id: $cid}) "
            "MERGE (c)-[:SUBCLASS_OF]->(p)",
            child=child, parent=parent, cid=class_id,
        )
        n += 1
    return n


def write_ontology_material(session, class_id: str, class_name: str, material: ImportedOntology, language: str = "en") -> dict:
    """Ecrit le materiau extrait dans Neo4j avec EXACTEMENT le meme schema
    que le pipeline d'ingestion reel (MERGE DocumentClass/Concept/Entity/REL
    - mêmes noms de labels/proprietes que pipeline.py) pour que
    doc_generator.load_class_material() n'ait besoin d'aucun cas particulier.
    N'ecrit PAS Fuseki (US16.6 : reste dans le perimetre deja decide pour
    IAF-E16 - Neo4j uniquement, voir doc_generator.py). Renvoie un compte par
    type d'objet ecrit, pour un rapport honnete cote CLI.

    Bug reel trouve le 2026-10-01 en testant la reconnaissance (pas en
    relisant le code) : l'utilisateur a depose un vrai PDF sur la classe
    "Vin" (creee par ce module) en esperant qu'il soit reconnu - score
    semantique 0.0 EXACT, pas juste faible. Cause racine : CONTRAIREMENT a ce
    que dit le paragraphe ci-dessus, cette fonction ne posait PAS
    `concept.embedding` (seul `pipeline.py:_write_concepts`, utilise a
    l'ingestion REELLE d'un document, le fait) - `_find_best_class` (US7.4)
    ignore tout concept sans embedding (`WHERE concept.embedding IS NOT
    NULL`), donc une classe seedee par import RDF etait semantiquement
    INVISIBLE a la reconnaissance, quel que soit le document depose. Corrige
    en embeddant chaque libelle de concept ici aussi, exactement comme a
    l'ingestion reelle."""
    session.run(
        "MERGE (c:DocumentClass {id: $cid}) "
        "SET c.name = $name, c.status = 'exemple-importe', c.source = $source, c.created_at = coalesce(c.created_at, datetime())",
        cid=class_id, name=class_name, source=material.source,
    )

    for label in material.concepts:
        try:
            vector = embed(label)
        except Exception:
            vector = None  # passerelle LLM indisponible : le concept reste ecrit sans embedding, pas bloquant
        session.run(
            "MATCH (c:DocumentClass {id: $cid}) "
            "MERGE (concept:Concept {label: $label, class_id: $cid}) "
            "SET concept.embedding = $embedding "
            "MERGE (c)-[:HAS_CONCEPT]->(concept)",
            cid=class_id, label=label, embedding=vector,
        )

    write_subclass_edges(session, class_id, material.subclass_of)
    for label, definition in material.concept_definitions.items():
        session.run(
            "MATCH (c:Concept {label: $label, class_id: $cid}) SET c.definition = $definition",
            label=label, cid=class_id, definition=definition,
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
