"""Algorithme de reconnaissance de l'ontologie structurelle d'un document
(US7.2-structurel, demande explicite du 2026-10-01) : "Il faut un algorithme
qui permet de trouver l'ontologie structurelle d'un document [...] On va
contraindre cette recherche sur le type de document [...] on peut avoir des
contraintes, axiomes pour definir le nombre de structures attendues (max,
min, ...)".

Principe : classe chaque element structurel REEL du document (deja parse par
docx_struct/pdf_struct/latex_struct - struct_element.StructElement, le meme
arbre que pipeline.py utilise deja) en un ROLE connu d'une ontologie
structurelle candidate (par mot-cle sur le libelle d'une Section, ou par
`kind` pour Table/Equation), compte les occurrences reelles par role, verifie
si elles respectent les axiomes de cardinalite (min/max/exact) LUS REELLEMENT
dans Fuseki par SPARQL (pas recopies en dur en Python), calcule un score =
fraction des axiomes satisfaits. PAS un raisonneur OWL : les restrictions
sont lues puis verifiees par du code Python simple (meme principe deja
documente dans iaf-structure-livre.ttl : "les .ttl restent des vocabulaires,
pas des raisonneurs actifs").

Le FORMAT du document (extension) CONTRAINT la recherche
(`iafs:appliesToFormat`, ontologies/structure/iaf-structure-base.ttl) : un
.docx n'est jamais compare aux roles/axiomes de l'ontologie LaTeX, et
reciproquement - demande explicite, evite les faux-matches entre
vocabulaires non pertinents pour le format.

Limite honnete specifique au PDF (lue dans pdf_struct.py AVANT d'ecrire ce
module, documentee aussi dans iaf-structure-pdf.ttl) : le parseur PDF reel ne
detecte AUCUN titre de section (une PAGE entiere = une Section) - impossible
de distinguer "Section" de "Sous-section" par mot-cle sur un PDF quelconque,
et `iafp:Header` n'est structurellement jamais detectable par ce parseur.
Traite explicitement ci-dessous (JAMAIS une fausse certitude)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import requests

from .config import settings
from .struct_element import StructElement
from .struct_element import flatten as _flatten_one

_PREFIXES = (
    "PREFIX owl: <http://www.w3.org/2002/07/owl#>\n"
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
    "PREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n"
    "PREFIX iafs: <urn:iaf:ns:structure#>\n"
)

# Extension de fichier -> kind StructElement compte par mot-cle de LIBELLE
# (vs par `kind` direct, voir _read_axioms/_count_role). "Section" reste le
# kind reel pour tous les roles bases sur un titre, quel que soit le format.
_KEYWORD_MATCH_KIND = "Section"

# Les 2 roles PDF ou le matching par mot-cle est structurellement impossible
# avec le parseur reel (voir limite honnete ci-dessus) - traites a part.
_PDF_ANY_SECTION_ROLE_SUFFIX = "#PdfSection"
_PDF_UNMATCHABLE_ROLE_SUFFIX = "#Header"


def _run_query(query: str) -> list[dict]:
    r = requests.get(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/sparql",
        params={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("results", {}).get("bindings", [])


@dataclass
class RoleAxiom:
    role_uri: str
    role_label: str
    keywords: list[str]
    match_kind: str  # "Section" (par mot-cle), "Table", "Equation", "AnySection", "Unmatchable"
    exact: int | None
    min_count: int | None
    max_count: int | None


@dataclass
class StructureMatch:
    ontology_uri: str
    ontology_label: str
    graph_uri: str
    score: float  # fraction d'axiomes satisfaits, 0.0 a 1.0
    satisfied: list[str] = field(default_factory=list)
    violated: list[str] = field(default_factory=list)  # messages lisibles, compte reel vs attendu


def applicable_ontologies(doc_format: str) -> list[tuple[str, str, str]]:
    """Ontologies structurelles qui s'appliquent a ce format de fichier
    (`iafs:appliesToFormat`) - recherche SUR TOUS les graphes nommes (pas de
    liste fixee en dur cote Python : une nouvelle ontologie structurelle
    ajoutee a Fuseki est prise en compte automatiquement). Renvoie
    (ontology_uri, label, graph_uri)."""
    query = (
        f"{_PREFIXES}"
        f"SELECT ?ontology ?label ?graph WHERE {{ "
        f"  GRAPH ?graph {{ ?ontology a owl:Ontology ; iafs:appliesToFormat \"{doc_format}\" ; rdfs:label ?label . }} "
        f"}}"
    )
    bindings = _run_query(query)
    return [(b["ontology"]["value"], b["label"]["value"], b["graph"]["value"]) for b in bindings]


def _role_keywords(graph_uri: str, role_uri: str) -> list[str]:
    query = (
        f"{_PREFIXES}SELECT ?label WHERE {{ GRAPH <{graph_uri}> {{ "
        f"<{role_uri}> rdfs:label|skos:altLabel ?label . }} }}"
    )
    return [b["label"]["value"].lower() for b in _run_query(query)]


def _read_axioms(graph_uri: str) -> list[RoleAxiom]:
    """Lit les restrictions OWL2 a cardinalite qualifiee d'une ontologie
    structurelle (iafw:WordForm/iafp:PdfForm/iaftex:LatexForm - une classe
    rdfs:subClassOf iafs:Document portant des owl:Restriction sur
    iafs:hasPart, voir les .ttl). Determine la STRATEGIE de comptage (par
    kind direct ou par mot-cle de libelle) depuis le parent NOMME du role
    (rdfs:subClassOf iafs:Table/iafs:Formula/iafs:Section)."""
    query = (
        f"{_PREFIXES}"
        f"SELECT ?role ?parent ?exact ?min ?max WHERE {{ GRAPH <{graph_uri}> {{ "
        f"  ?form a owl:Class ; rdfs:subClassOf iafs:Document ; rdfs:subClassOf ?restriction . "
        f"  ?restriction a owl:Restriction ; owl:onProperty iafs:hasPart ; owl:onClass ?role . "
        f"  ?role rdfs:subClassOf ?parent . FILTER(isIRI(?parent)) "
        f"  OPTIONAL {{ ?restriction owl:qualifiedCardinality ?exact }} "
        f"  OPTIONAL {{ ?restriction owl:minQualifiedCardinality ?min }} "
        f"  OPTIONAL {{ ?restriction owl:maxQualifiedCardinality ?max }} "
        f"}} }}"
    )
    axioms = []
    for b in _run_query(query):
        role_uri = b["role"]["value"]
        parent = b["parent"]["value"]
        keywords = _role_keywords(graph_uri, role_uri)
        role_label = keywords[0] if keywords else role_uri.rsplit("#", 1)[-1]

        if role_uri.endswith(_PDF_UNMATCHABLE_ROLE_SUFFIX) and parent.endswith("#Section"):
            match_kind = "Unmatchable"
        elif role_uri.endswith(_PDF_ANY_SECTION_ROLE_SUFFIX) and parent.endswith("#Section"):
            match_kind = "AnySection"
        elif parent.endswith("#Table"):
            match_kind = "Table"
        elif parent.endswith("#Formula"):
            match_kind = "Equation"
        else:
            match_kind = _KEYWORD_MATCH_KIND

        axioms.append(RoleAxiom(
            role_uri=role_uri, role_label=role_label, keywords=keywords, match_kind=match_kind,
            exact=int(b["exact"]["value"]) if "exact" in b else None,
            min_count=int(b["min"]["value"]) if "min" in b else None,
            max_count=int(b["max"]["value"]) if "max" in b else None,
        ))
    return axioms


def _flatten(elements: list[StructElement]) -> list[StructElement]:
    """Reutilise struct_element.flatten() (deja utilise par pipeline.py pour
    les memes elements), applique a chaque enfant de la racine."""
    out: list[StructElement] = []
    for e in elements:
        out.extend(_flatten_one(e))
    return out


def _count_role(all_elements: list[StructElement], axiom: RoleAxiom) -> int:
    if axiom.match_kind == "Table":
        return sum(1 for e in all_elements if e.kind == "Table")
    if axiom.match_kind == "Equation":
        return sum(1 for e in all_elements if e.kind == "Equation")
    if axiom.match_kind == "AnySection":
        # Limite honnete du parseur PDF (page = Section, aucun titre detecte) :
        # toute Section compte pour ce role, le mot-cle n'a pas de sens ici.
        return sum(1 for e in all_elements if e.kind == "Section")
    if axiom.match_kind == "Unmatchable":
        # iafp:Header : jamais detectable par pdf_struct.py (pas de notion
        # d'en-tete de page). Compte honnetement 0, jamais une fausse
        # detection - satisfait quand meme un axiome max (0 <= max).
        return 0
    # "Section" par mot-cle : correspondance insensible a la casse sur le
    # libelle reel (meme principe que _BIBLIOGRAPHIC_TYPE_HINTS, doc_generator.py).
    return sum(
        1 for e in all_elements
        if e.kind == "Section" and any(kw in (e.label or "").lower() for kw in axiom.keywords)
    )


def match_structural_ontologies(root_children: list[StructElement], doc_format: str) -> list[StructureMatch]:
    """Point d'entree : `root_children` = `root.children` du StructElement
    deja produit par docx_struct/pdf_struct/latex_struct.parse() (pipeline.py
    l'a deja calcule pour l'ingestion - aucun reparsing ici), `doc_format` =
    extension SANS le point ("docx", "pdf", "tex"). Renvoie les ontologies
    structurelles applicables a ce format, triees par score decroissant
    (fraction d'axiomes de cardinalite satisfaits)."""
    all_elements = _flatten(root_children)
    results: list[StructureMatch] = []
    for ontology_uri, label, graph_uri in applicable_ontologies(doc_format):
        axioms = _read_axioms(graph_uri)
        if not axioms:
            continue
        satisfied: list[str] = []
        violated: list[str] = []
        for axiom in axioms:
            count = _count_role(all_elements, axiom)
            ok = True
            if axiom.exact is not None and count != axiom.exact:
                ok = False
            if axiom.min_count is not None and count < axiom.min_count:
                ok = False
            if axiom.max_count is not None and count > axiom.max_count:
                ok = False
            expected = (
                f"={axiom.exact}" if axiom.exact is not None else
                f"[{axiom.min_count or 0}..{axiom.max_count if axiom.max_count is not None else 'n'}]"
            )
            message = f"{axiom.role_label} : {count} reel(s), attendu {expected}"
            (satisfied if ok else violated).append(message)
        score = len(satisfied) / len(axioms)
        results.append(StructureMatch(
            ontology_uri=ontology_uri, ontology_label=label, graph_uri=graph_uri,
            score=score, satisfied=satisfied, violated=violated,
        ))
    results.sort(key=lambda m: m.score, reverse=True)
    return results
