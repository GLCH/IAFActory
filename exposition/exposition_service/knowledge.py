"""Navigation dans les connaissances : classes et detail d'une classe (lecture seule). Deplace du site
(`site/app/services/knowledge.py`) vers le service d'exposition le 2026-10-03 (ADR 0009, etape 3)."""
from __future__ import annotations

from .models import ClassDetail, ClassSummary, ConceptRow, DocumentRow, EntityTypeRow
from .retrieval import scope_filter
from .store import get_driver


def list_classes(scope: str = "official") -> list[ClassSummary]:
    with get_driver().session() as session:
        rows = session.run(
            "MATCH (c:DocumentClass) WHERE " + scope_filter(scope) + " "
            "OPTIONAL MATCH (c)<-[:IN_CLASS]-(d:Document) WITH c, count(DISTINCT d) AS documents "
            "OPTIONAL MATCH (c)-[:HAS_CONCEPT]->(k:Concept) WITH c, documents, count(DISTINCT k) AS concepts "
            "OPTIONAL MATCH (e:Entity {class_id: c.id}) "
            "RETURN c.id AS id, c.name AS name, coalesce(c.status, '') AS status, documents, concepts, "
            "       count(DISTINCT e) AS entities ORDER BY name"
        )
        return [ClassSummary(**dict(r)) for r in rows]


def class_detail(class_id: str, scope: str = "official") -> ClassDetail | None:
    """None si la classe n'existe pas ou n'est pas visible dans ce perimetre."""
    with get_driver().session() as session:
        head = session.run(
            "MATCH (c:DocumentClass {id: $cid}) WHERE " + scope_filter(scope) + " "
            "RETURN c.id AS id, c.name AS name, coalesce(c.status, '') AS status", cid=class_id,
        ).single()
        if head is None:
            return None
        concepts = [ConceptRow(**dict(r)) for r in session.run(
            "MATCH (c:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(k:Concept) "
            "OPTIONAL MATCH (d:Document)-[:MENTIONS_CONCEPT]->(k) "
            "RETURN k.label AS label, k.definition AS definition, count(DISTINCT d) AS support "
            "ORDER BY support DESC, label LIMIT 300", cid=class_id,
        )]
        entity_types = [EntityTypeRow(**dict(r)) for r in session.run(
            "MATCH (e:Entity {class_id: $cid}) "
            "RETURN coalesce(e.type, 'sans type') AS type, count(e) AS count, collect(e.name)[0..5] AS examples "
            "ORDER BY count DESC LIMIT 25", cid=class_id,
        )]
        documents = [DocumentRow(**dict(r)) for r in session.run(
            "MATCH (d:Document)-[:IN_CLASS]->(:DocumentClass {id: $cid}) "
            "RETURN d.filename AS filename, d.title AS title, d.language AS language, "
            "       toString(d.ingested_at) AS ingested_at ORDER BY filename", cid=class_id,
        )]
    return ClassDetail(**dict(head), concepts=concepts, entity_types=entity_types, documents=documents)
