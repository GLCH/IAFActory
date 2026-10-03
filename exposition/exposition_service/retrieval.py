"""Recuperation des donnees enregistrees (Neo4j, lecture seule) : voie GRAPHE (concepts, definitions,
hierarchie, entites, attributs, relations) et voie RAG (passages de documents). Deterministe : aucune
decision ici n'est prise par un modele.

Perimetre de visibilite (`scope`) : "official" (viewer) = classes officielles et classes d'exemple
importees ; "all" (creator) = tout sauf les coquilles de classes fusionnees."""
from __future__ import annotations

import math

from .config import settings
from .text import content_terms, cypher_folded, fold, tokens

_OFFICIAL = "NOT coalesce(c.status, '') = 'provisoire' AND NOT coalesce(c.status, '') STARTS WITH 'fusionnee'"
_ALL = "NOT coalesce(c.status, '') STARTS WITH 'fusionnee'"
_ENTITY_META = {"class_id", "name", "type"}


def scope_filter(scope: str) -> str:
    return _ALL if scope == "all" else _OFFICIAL


def matched_terms(text: str, terms: list[str], split_camel: bool = True) -> list[str]:
    """Termes de la question qui sont des mots entiers de `text` (jetons replies, pluriel simple)."""
    present = set(tokens(text, split_camel=split_camel))
    return [t for t in terms if t in present]


def coverage(text: str, terms: list[str]) -> float:
    return len(matched_terms(text, terms, split_camel=False)) / len(terms) if terms else 0.0


def _enough(text: str, terms: list[str]) -> bool:
    needed = max(1, math.ceil(settings.min_term_coverage * len(terms)))
    return len(matched_terms(text, terms, split_camel=False)) >= needed


def find_concepts(session, terms: list[str], query_fold: str, scope: str) -> list[dict]:
    """Concepts dont le LIBELLE contient un terme (mot entier) ou dont la definition en contient un
    (correspondance plus faible, `via` = "definition")."""
    rows = session.run(
        "MATCH (c:DocumentClass)-[:HAS_CONCEPT]->(k:Concept) WHERE " + scope_filter(scope) + " AND ANY(t IN $terms WHERE "
        + cypher_folded("k.label") + " CONTAINS t OR " + cypher_folded("k.definition") + " CONTAINS t) "
        "RETURN c.id AS class_id, c.name AS class_name, k.label AS label, k.definition AS definition LIMIT 300",
        terms=terms,
    )
    found = []
    for row in rows:
        by_label = matched_terms(row["label"], terms)
        phrase = len(fold(row["label"])) >= 3 and fold(row["label"]) in query_fold
        by_definition = matched_terms(row["definition"] or "", terms, split_camel=False)
        if not (by_label or phrase or by_definition):
            continue
        found.append({**dict(row), "terms": by_label or by_definition, "via": "libelle" if (by_label or phrase) else "definition"})
    found.sort(key=lambda r: (r["via"] != "libelle", -len(r["terms"]), len(r["label"])))
    return found[: settings.max_matches]


def concept_hierarchy(session, class_id: str, label: str) -> dict:
    row = session.run(
        "MATCH (k:Concept {class_id: $cid, label: $label}) "
        "OPTIONAL MATCH (k)-[:SUBCLASS_OF]->(p:Concept) OPTIONAL MATCH (ch:Concept)-[:SUBCLASS_OF]->(k) "
        "RETURN collect(DISTINCT p.label) AS parents, collect(DISTINCT ch.label) AS children", cid=class_id, label=label,
    ).single()
    return {"parents": [p for p in row["parents"] if p], "children": [c for c in row["children"] if c]} if row else {"parents": [], "children": []}


def find_entities(session, terms: list[str], scope: str) -> list[dict]:
    rows = session.run(
        "MATCH (e:Entity) WHERE ANY(t IN $terms WHERE " + cypher_folded("e.name") + " CONTAINS t) "
        "MATCH (c:DocumentClass {id: e.class_id}) WHERE " + scope_filter(scope) + " "
        "RETURN c.id AS class_id, c.name AS class_name, e.name AS name, e.type AS type, properties(e) AS props LIMIT 200",
        terms=terms,
    )
    found = []
    for row in rows:
        hit = matched_terms(row["name"], terms)
        if not hit:
            continue
        attributes = {k: v for k, v in (row["props"] or {}).items() if k not in _ENTITY_META and v not in (None, "")}
        found.append({
            "class_id": row["class_id"], "class_name": row["class_name"], "name": row["name"], "type": row["type"],
            "attributes": attributes, "terms": hit,
        })
    found.sort(key=lambda r: (-len(r["terms"]), len(r["name"])))
    return found[: settings.max_matches]


def entity_relations(session, class_id: str, name: str, limit: int = 8) -> list[dict]:
    rows = session.run(
        "MATCH (e:Entity {class_id: $cid, name: $name})-[r:REL]-(o:Entity) "
        "RETURN startNode(r).name AS subject, r.type AS relation, endNode(r).name AS object LIMIT $limit",
        cid=class_id, name=name, limit=limit,
    )
    return [dict(r) for r in rows if r["relation"]]


_PASSAGE_RETURN = (
    "RETURN chunk.text AS text, el.label AS section, d.filename AS document, c.id AS class_id, c.name AS class_name"
)
_PASSAGE_JOIN = (
    "MATCH (el:StructElement)-[:HAS_CHUNK]->(chunk) "
    "MATCH (d:Document)-[:HAS_ELEMENT]->(:StructElement)-[:CHILD*0..]->(el) "
    "MATCH (d)-[:IN_CLASS]->(c:DocumentClass) "
)


def entity_passages(session, class_id: str, name: str, scope: str, limit: int = 2) -> list[dict]:
    """Passages qui MENTIONNENT une entite : le pont entre la voie graphe et la voie RAG."""
    rows = session.run(
        "MATCH (e:Entity {class_id: $cid, name: $name})<-[:MENTIONS]-(chunk:Chunk) " + _PASSAGE_JOIN
        + "WHERE " + scope_filter(scope) + " " + _PASSAGE_RETURN + " LIMIT $limit",
        cid=class_id, name=name, limit=limit,
    )
    return [dict(r) for r in rows]


def vector_passages(session, vector: list[float], terms: list[str], scope: str) -> list[dict]:
    """Voie RAG : candidats par similarite vectorielle, puis filtre LEXICAL (le score vectoriel ne
    discrimine pas la pertinence, voir config) : un passage doit contenir assez de termes de la question."""
    rows = session.run(
        "CALL db.index.vector.queryNodes('chunkEmbeddings', $k, $vector) YIELD node AS chunk, score "
        + _PASSAGE_JOIN + "WHERE " + scope_filter(scope) + " " + _PASSAGE_RETURN + ", score ORDER BY score DESC",
        k=settings.vector_candidates, vector=vector,
    )
    kept = []
    for row in rows:
        if _enough(row["text"], terms):
            kept.append({**dict(row), "coverage": coverage(row["text"], terms)})
    kept.sort(key=lambda r: (-r["coverage"], -r["score"]))
    return kept


def documents_matching(session, terms: list[str], scope: str) -> list[dict]:
    rows = session.run(
        "MATCH (d:Document)-[:IN_CLASS]->(c:DocumentClass) WHERE " + scope_filter(scope) + " AND ANY(t IN $terms WHERE "
        + cypher_folded("d.filename") + " CONTAINS t OR " + cypher_folded("d.title") + " CONTAINS t) "
        "RETURN c.id AS class_id, c.name AS class_name, d.filename AS filename, d.title AS title LIMIT 100", terms=terms,
    )
    found = []
    for row in rows:
        hit = matched_terms(f"{row['filename'] or ''} {row['title'] or ''}", terms)
        if hit:
            found.append({**dict(row), "terms": hit})
    return found[: settings.max_matches]


def best_covering(concepts: list[dict], entities: list[dict], documents: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Ne garde que les correspondances qui couvrent le PLUS de termes de la question (egalites conservees).
    Sans cela, « trou noir » ramenait aussi tout ce qui contient seulement « noir » ou « trou » ; avec trois
    termes dont un seul est connu (« vin accompagne fromage »), les correspondances a un terme restent."""
    best = max((len(x["terms"]) for group in (concepts, entities, documents) for x in group), default=0)
    if best == 0:
        return concepts, entities, documents
    keep = lambda group: [x for x in group if len(x["terms"]) == best]  # noqa: E731
    return keep(concepts), keep(entities), keep(documents)


def terms_of(query: str) -> tuple[list[str], str]:
    return content_terms(query), fold(query)
