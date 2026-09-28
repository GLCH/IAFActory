"""Ecriture des concepts semantiques induits comme de vraies classes OWL dans
Fuseki (ADR 0001 : Fuseki = source de verite des ontologies RDF/OWL/SPARQL).
IAF-E7 US7.5 : "schema normalise publie comme ontologie semantique induite
dans un graphe nomme Fuseki" - deja specifie depuis le 2026-09-26, jamais
implemente avant ce tour (le pipeline ne touchait que Neo4j).

Un graphe nomme par classe documentaire : http://iafactory.local/classes/{id}.
Neo4j reste la lecture rapide pour les ecrans du site (pipeline.py,
routers/documents.py) ; Fuseki est la source de verite RDF, ecrite en
parallele, pas encore relue par le site (US3.14 : a faire si le besoin de
lire directement le SPARQL se confirme)."""
from __future__ import annotations

import re
import unicodedata
import uuid

import requests

from .config import settings

_PREFIXES = (
    "PREFIX owl: <http://www.w3.org/2002/07/owl#>\n"
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
)


def _slug(label: str) -> str:
    # Accents retires avant le filtre alphanumerique (sinon "e" accentue
    # produit un "-" disgracieux dans l'URI, ex. "syst-me" au lieu de
    # "systeme" - constate reellement au premier test, corrige aussitot).
    decomposed = unicodedata.normalize("NFKD", label.strip().lower())
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-")
    return slug or uuid.uuid4().hex[:8]


def class_graph_uri(class_id: str) -> str:
    return f"http://iafactory.local/classes/{class_id}"


def concept_uri(class_id: str, label: str) -> str:
    return f"{class_graph_uri(class_id)}#{_slug(label)}"


def ensure_concept(class_id: str, label: str) -> str:
    """Ecrit (idempotent : INSERT DATA d'un triple deja present est un
    no-op) le concept comme une classe OWL dans le graphe nomme de la classe
    documentaire. Renvoie l'URI du concept."""
    uri = concept_uri(class_id, label)
    graph = class_graph_uri(class_id)
    label_escaped = label.replace('"', '\\"')
    update = (
        f"{_PREFIXES}"
        f"INSERT DATA {{ GRAPH <{graph}> {{ <{uri}> a owl:Class ; rdfs:label \"{label_escaped}\"@fr . }} }}"
    )
    r = requests.post(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/update",
        data=update.encode("utf-8"),
        headers={"Content-Type": "application/sparql-update; charset=utf-8"},
        timeout=15,
    )
    r.raise_for_status()
    return uri


def set_concept_metadata(class_id: str, label: str, definition: str, translation_en: str) -> None:
    """US3.17 : definition (`rdfs:comment`) et traduction anglaise
    (`rdfs:label@en`) d'un concept, saisies par le creator - jamais induites
    par le LLM (US7.5 ne produit qu'un libelle francais). Remplace toute
    valeur precedente (DELETE puis INSERT, pas d'accumulation de doublons)."""
    uri = concept_uri(class_id, label)
    graph = class_graph_uri(class_id)
    # `DELETE WHERE` (forme raccourcie) ne supporte pas FILTER (erreur de
    # syntaxe SPARQL reelle rencontree ici) : forme complete DELETE/WHERE
    # pour la traduction, qui doit filtrer par langue.
    ops = [
        f"{_PREFIXES}DELETE WHERE {{ GRAPH <{graph}> {{ <{uri}> rdfs:comment ?c . }} }}",
        f"{_PREFIXES}DELETE {{ GRAPH <{graph}> {{ <{uri}> rdfs:label ?l . }} }} "
        f"WHERE {{ GRAPH <{graph}> {{ <{uri}> rdfs:label ?l . FILTER(lang(?l) = 'en') }} }}",
    ]
    inserts = []
    if definition:
        inserts.append(f'<{uri}> rdfs:comment "{definition.replace(chr(34), chr(92) + chr(34))}"@fr .')
    if translation_en:
        inserts.append(f'<{uri}> rdfs:label "{translation_en.replace(chr(34), chr(92) + chr(34))}"@en .')
    if inserts:
        ops.append(f"{_PREFIXES}INSERT DATA {{ GRAPH <{graph}> {{ {' '.join(inserts)} }} }}")

    r = requests.post(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/update",
        data=" ; ".join(ops).encode("utf-8"),
        headers={"Content-Type": "application/sparql-update; charset=utf-8"},
        timeout=15,
    )
    r.raise_for_status()


def list_concepts(class_id: str) -> list[dict]:
    """Concepts OWL deja ecrits pour cette classe (verification, pas encore
    utilise par les ecrans du site - voir US3.14)."""
    graph = class_graph_uri(class_id)
    query = (
        f"{_PREFIXES}"
        f"SELECT ?concept ?label WHERE {{ GRAPH <{graph}> {{ ?concept a owl:Class ; rdfs:label ?label . }} }}"
    )
    r = requests.get(
        f"{settings.fuseki_url}/{settings.fuseki_dataset}/sparql",
        params={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=15,
    )
    r.raise_for_status()
    bindings = r.json().get("results", {}).get("bindings", [])
    return [{"uri": b["concept"]["value"], "label": b["label"]["value"]} for b in bindings]
