"""EPIC-IAF-E17 US17.5 (2026-10-02) : reduction de l'ontologie d'une classe en
eliminant les concepts les MOINS COMMUNS (specifiques a peu de documents).

Demande explicite : "On peut reduire l'ontologie en eliminant les noeuds les
moins communs (specifique a peu de documents)."

Le support d'un concept = nombre de documents DISTINCTS qui le mentionnent
(`(:Document)-[:MENTIONS_CONCEPT]->(:Concept)`, ecrit a l'ingestion reelle).
Trois groupes :
- **proteges** : support 0 - concepts jamais mentionnes par un document, donc
  IMPORTES depuis une ontologie (Vin, C2SIM) ou saisis par le creator ; les
  retirer videraient l'ontologie, ils ne sont jamais des candidats ;
- **retirables** : 0 < support < `min_support` (defaut
  `settings.ontology_reduction_min_support`) ;
- **conserves** : support >= `min_support`.
La reduction n'est autorisee que si la classe a au moins
`settings.official_class_min_documents` documents (avec 1 ou 2 documents,
presque tous les concepts seraient "rares")."""
from __future__ import annotations

from . import ontology
from .config import settings


def plan_reduction(session, class_id: str, min_support: int | None = None) -> dict:
    min_support = settings.ontology_reduction_min_support if min_support is None else max(1, min_support)
    documents = session.run(
        "MATCH (:DocumentClass {id: $cid})<-[:IN_CLASS]-(d:Document) RETURN count(DISTINCT d) AS n", cid=class_id,
    ).single()["n"]
    rows = list(session.run(
        "MATCH (:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(concept:Concept) "
        "OPTIONAL MATCH (d:Document)-[:MENTIONS_CONCEPT]->(concept) "
        "RETURN concept.label AS label, concept.uri AS uri, count(DISTINCT d) AS support ORDER BY support, label",
        cid=class_id,
    ))
    protected = [r["label"] for r in rows if r["support"] == 0]
    removable = [
        {"label": r["label"], "support": r["support"], "uri": r["uri"]} for r in rows if 0 < r["support"] < min_support
    ]
    kept = [r["label"] for r in rows if r["support"] >= min_support]
    allowed = documents >= settings.official_class_min_documents
    return {
        "class_id": class_id, "documents": documents, "min_support": min_support, "allowed": allowed,
        "required_documents": settings.official_class_min_documents,
        "removable": removable if allowed else [], "protected": protected, "kept": kept,
    }


def apply_reduction(session, class_id: str, min_support: int | None = None) -> int:
    """Recalcule le plan (jamais une liste fournie par l'appelant) puis retire
    les concepts retirables : noeud Neo4j (relations SUBCLASS_OF/mentions
    comprises) ET classe OWL du graphe Fuseki. Renvoie le nombre retire."""
    plan = plan_reduction(session, class_id, min_support)
    removed = 0
    for item in plan["removable"]:
        session.run(
            "MATCH (:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(concept:Concept {label: $label}) DETACH DELETE concept",
            cid=class_id, label=item["label"],
        )
        try:
            ontology.delete_concept(class_id, item["label"], item.get("uri"))
        except Exception:
            pass  # Fuseki indisponible : le concept disparait de Neo4j, a resynchroniser (meme principe que les suppressions existantes)
        removed += 1
    return removed
