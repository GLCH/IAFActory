"""Fusionne plusieurs fichiers RDF/OWL lies par des `owl:imports` (ex. une
ontologie "noyau" + ses extensions) en UN SEUL fichier autonome, sans
dependance externe. Demande explicite (2026-10-01) : "prend les ontologies et
fabrique une seule pour ne pas avoir de dependances".

Principe : chaque fichier est parse dans le MEME `rdflib.Graph` (l'union des
triples se fait naturellement, RDF est un ensemble de triples) ; les triples
`owl:imports` sont retires du resultat (l'import est maintenant inutile -
tout le contenu importe est deja physiquement present dans le meme graphe).
Ne fabrique AUCUN contenu : union stricte des triples reels des fichiers
donnes.

`--strip-named-individuals` (ajoute le 2026-10-01, demande explicite "il faut
retirer tous les namedIndividual") : retire TOUT individu declare
`owl:NamedIndividual` et TOUS ses triples (pas seulement le triple de
declaration) - produit un TBox pur (classes/proprietes seulement), coherent
avec la decision deja actee dans ce projet "TBox dans Jena, ABox dans Neo4j"
(l'ontologie agregee sert de SCHEMA, les instances reelles n'y ont pas leur
place).

Usage : python scripts/aggregate_ontology.py <sortie.rdf> <entree1.rdf> [<entree2.rdf> ...] [--strip-named-individuals]
"""
from __future__ import annotations

import sys
from pathlib import Path

import rdflib
from rdflib.namespace import OWL, RDF


def strip_named_individuals(graph: rdflib.Graph) -> int:
    individuals = {s for s in graph.subjects(RDF.type, OWL.NamedIndividual)}
    n_triples = 0
    for subject in individuals:
        for triple in list(graph.triples((subject, None, None))):
            graph.remove(triple)
            n_triples += 1
    return len(individuals), n_triples


def main() -> None:
    args = sys.argv[1:]
    strip_individuals = "--strip-named-individuals" in args
    args = [a for a in args if a != "--strip-named-individuals"]
    if len(args) < 2:
        raise SystemExit(
            "usage: python scripts/aggregate_ontology.py <sortie.rdf> <entree1.rdf> [...] [--strip-named-individuals]"
        )
    output_path = Path(args[0])
    input_paths = [Path(p) for p in args[1:]]

    graph = rdflib.Graph()
    for path in input_paths:
        if not path.exists():
            raise SystemExit(f"fichier introuvable : {path}")
        before = len(graph)
        graph.parse(str(path), format="xml")
        print(f"{path.name} : +{len(graph) - before} triple(s) (total {len(graph)})")

    n_imports = 0
    for s, p, o in list(graph.triples((None, OWL.imports, None))):
        graph.remove((s, p, o))
        n_imports += 1
    print(f"{n_imports} triple(s) owl:imports retire(s) (contenu deja fusionne, dependance devenue inutile)")

    if strip_individuals:
        n_individuals, n_triples = strip_named_individuals(graph)
        print(f"{n_individuals} individu(s) owl:NamedIndividual retire(s) ({n_triples} triple(s)) - TBox pur restant")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    graph.serialize(destination=str(output_path), format="pretty-xml")
    print(f"\nontologie agregee ecrite : {output_path} ({len(graph)} triples, {len(input_paths)} fichier(s) source)")


if __name__ == "__main__":
    main()
