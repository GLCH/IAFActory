"""POC jetable (hors epics officiels, voir poc/README.md) - exemple concret
de l'architecture demandee le 2026-09-30, DIFFERENTE de celle deja
implementee dans site/app/doc_generator.py (US16.1, decision "Neo4j
uniquement, pas de Fuseki pour la generation") :

- **TBox** (definitions de concepts/relations/attributs, PLUSIEURS libelles
  lexicaux par terme) dans Apache Jena/Fuseki - deja la source de verite des
  ontologies RDF/OWL/SPARQL du projet (ADR 0001, voir site/app/ontology.py),
  mais jamais relue par le generateur aujourd'hui.
- **ABox** (instances reelles - entites/relations extraites d'un document)
  dans Neo4j, referencant le TBox PAR URI (`type_uri`/`property_uri`) - PAS
  en copiant un libelle fige comme le fait le schema Neo4j actuel
  (Entity.type/REL.type SONT deja le libelle texte, pas une reference).
- **Variabilite lexicale** : generer une phrase pioche, a chaque fait, une
  variante lexicale du TBox (rdfs:label canonique + skos:altLabel - meme
  convention deja utilisee en production par
  site/app/ontology.py:write_taxonomy pour les synonymes de concepts) ET un
  gabarit de phrase, au lieu du gabarit FIXE unique de doc_generator.py
  actuel (voir son docstring : "prose MECANIQUE (gabarits), pas un texte
  naturel" - limite assumee que cet exemple propose de lever).

Ne modifie PAS le pipeline reel ni doc_generator.py : demonstration isolee,
sa propre classe Fuseki+Neo4j prefixee "poc-lexvar" (namespace dedie, ne
touche jamais aux vraies donnees).

Usage : python poc/lexical_variability_example.py
Prerequis : Docker (Neo4j + Fuseki) demarre, memes ports que .env racine.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import requests
from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import FUSEKI_DATASET, FUSEKI_URL, NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER  # noqa: E402

TBOX_GRAPH = "http://iafactory.local/example/poc-lexvar"

_PREFIXES = (
    "PREFIX owl: <http://www.w3.org/2002/07/owl#>\n"
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
    "PREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n"
)

# Terme local -> (type OWL, libelle canonique, variantes lexicales reelles).
# Ce sont les SEULES donnees "inventees" de cet exemple (un petit vocabulaire
# de demonstration) - tout le reste (ABox, phrases generees) en decoule
# mecaniquement, rien n'est fabrique par un LLM.
TBOX_TERMS = {
    "Winery": ("owl:Class", "domaine viticole", ["exploitation viticole", "propriete viticole"]),
    "Wine": ("owl:Class", "vin", ["cru", "breuvage"]),
    "locatedIn": ("owl:ObjectProperty", "est situe a", ["se trouve a", "est implante a"]),
    "madeFromGrape": (
        "owl:ObjectProperty", "est elabore a partir du cepage",
        ["est issu du cepage", "utilise le cepage"],
    ),
}

SENTENCE_TEMPLATES = [
    "{source} {relation} {target}.",
    "On observe que {source} {relation} {target}.",
    "Fait extrait du document : {source} {relation} {target}.",
]


def _uri(local: str) -> str:
    return f"{TBOX_GRAPH}#{local}"


def write_tbox() -> None:
    """TBox -> Fuseki (Jena). Un triple rdfs:label (canonique) + un triple
    skos:altLabel par variante - meme convention que ontology.py:write_taxonomy,
    pas une invention pour ce POC."""
    triples = []
    for local, (owl_type, label, alts) in TBOX_TERMS.items():
        uri = _uri(local)
        triples.append(f'<{uri}> a {owl_type} ; rdfs:label "{label}"@fr .')
        for alt in alts:
            triples.append(f'<{uri}> skos:altLabel "{alt}"@fr .')
    update = f"{_PREFIXES}INSERT DATA {{ GRAPH <{TBOX_GRAPH}> {{ {' '.join(triples)} }} }}"
    r = requests.post(
        f"{FUSEKI_URL}/{FUSEKI_DATASET}/update", data=update.encode("utf-8"),
        headers={"Content-Type": "application/sparql-update; charset=utf-8"}, timeout=15,
    )
    r.raise_for_status()
    print(f"TBox ecrit dans Fuseki (Jena), graphe {TBOX_GRAPH} : {len(TBOX_TERMS)} termes")


def lexical_variants(term_uri: str) -> list[str]:
    """La variabilite lexicale vient d'ICI - une vraie requete SPARQL sur le
    TBox Fuseki, pas d'une liste codee en dur cote generateur."""
    query = (
        f"{_PREFIXES}SELECT ?label WHERE {{ GRAPH <{TBOX_GRAPH}> {{ "
        f"<{term_uri}> rdfs:label|skos:altLabel ?label . }} }}"
    )
    r = requests.get(
        f"{FUSEKI_URL}/{FUSEKI_DATASET}/sparql", params={"query": query},
        headers={"Accept": "application/sparql-results+json"}, timeout=15,
    )
    r.raise_for_status()
    return [b["label"]["value"] for b in r.json()["results"]["bindings"]]


def write_abox(driver) -> None:
    """ABox -> Neo4j. DIFFERENCE cle avec le schema Neo4j actuel du pipeline
    (pipeline.py/ontology_import.py) : les noeuds/relations portent une
    reference `type_uri`/`property_uri` vers le TBox, ils ne copient PAS de
    libelle. Le libelle affiche est TOUJOURS resolu depuis Fuseki au moment
    de generer (voir `generate_sentence`), jamais fige dans Neo4j."""
    with driver.session() as s:
        s.run(
            "MERGE (w:PocLexvarEntity {name: 'Chateau Poc-Margaux'}) SET w.type_uri = $winery "
            "MERGE (v:PocLexvarEntity {name: 'Cabernet Sauvignon'}) SET v.type_uri = $wine "
            "MERGE (loc:PocLexvarEntity {name: 'Bordeaux'}) "
            "MERGE (w)-[:POC_REL {property_uri: $located}]->(loc) "
            "MERGE (v)-[:POC_REL {property_uri: $made_from}]->(w)",
            winery=_uri("Winery"), wine=_uri("Wine"), located=_uri("locatedIn"), made_from=_uri("madeFromGrape"),
        )
    print("ABox ecrite dans Neo4j (noeuds PocLexvarEntity, references par URI vers le TBox Fuseki)")


def cleanup(driver) -> None:
    with driver.session() as s:
        s.run("MATCH (n:PocLexvarEntity) DETACH DELETE n")
    update = f"{_PREFIXES}DROP GRAPH <{TBOX_GRAPH}>"
    requests.post(
        f"{FUSEKI_URL}/{FUSEKI_DATASET}/update", data=update.encode("utf-8"),
        headers={"Content-Type": "application/sparql-update; charset=utf-8"}, timeout=15,
    )
    print("nettoye (ABox Neo4j + TBox Fuseki de la demo supprimes)")


def generate_sentence(source: str, relation_uri: str, target: str, rng: random.Random) -> str:
    """Coeur de la demande : pour le MEME fait ABox, pioche une variante
    lexicale du TBox ET un gabarit de phrase - contrairement a
    doc_generator.py actuel qui applique TOUJOURS le meme gabarit fixe au
    libelle technique brut de la relation (voir son docstring : "prose
    MECANIQUE (gabarits), pas un texte naturel")."""
    relation_label = rng.choice(lexical_variants(relation_uri))
    template = rng.choice(SENTENCE_TEMPLATES)
    return template.format(source=source, relation=relation_label, target=target)


def main() -> None:
    write_tbox()
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    try:
        write_abox(driver)
        with driver.session() as s:
            facts = list(s.run(
                "MATCH (s:PocLexvarEntity)-[r:POC_REL]->(t:PocLexvarEntity) "
                "RETURN s.name AS s, r.property_uri AS p, t.name AS t ORDER BY s.name"
            ))

        print(f"\n{len(facts)} fait(s) ABox lus dans Neo4j (references par URI, pas par libelle) :")
        for fact in facts:
            print(f"  {fact['s']} -[{fact['p'].rsplit('#', 1)[-1]}]-> {fact['t']}")

        fact = facts[0]
        print(f"\nMEME fait ({fact['s']} / {fact['p'].rsplit('#', 1)[-1]} / {fact['t']}), genere 5 fois "
              "(5 graines differentes) - variabilite lexicale reelle, lue dans Fuseki a chaque appel :\n")
        for seed in (1, 2, 3, 4, 5):
            rng = random.Random(seed)
            print(f"  seed={seed} : {generate_sentence(fact['s'], fact['p'], fact['t'], rng)}")

        fixed_label = fact["p"].rsplit("#", 1)[-1]
        print(
            "\nPour comparaison, doc_generator.py actuel (US16.1) produirait TOUJOURS EXACTEMENT :\n"
            f'  "{fact["s"]} est en relation « {fixed_label} » avec {fact["t"]}."\n'
            "(un seul gabarit fixe, applique au libelle technique brut de la relation - "
            "jamais de TBox lu, jamais de variante)."
        )
    finally:
        cleanup(driver)
        driver.close()


if __name__ == "__main__":
    main()
