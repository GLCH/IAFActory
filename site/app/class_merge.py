"""IAF-E7 US7.6 (etendue le 2026-09-28) : distance entre CLASSES (meme score
combine structurel+semantique que US7.4, applique cette fois a deux classes
plutot qu'a un document et une classe) et fusion automatique ou suggeree
selon deux seuils reglables (PlatformSettings).

Limite assumee : les entites/relations extraites (Entity.class_id, IAF-E7
US7.5) ne sont PAS migrees vers la classe cible lors d'une fusion - seuls les
documents (Postgres + IN_CLASS) et les concepts (HAS_CONCEPT) le sont. Une
classe fusionnee garde donc ses entites propres, invisibles depuis l'ecran de
la classe cible. A corriger si le besoin se confirme."""
from __future__ import annotations

from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from . import ontology
from .db import SessionLocal
from .graph import get_driver
from .models import Document, PlatformSettings
from .pipeline import STRUCT_KINDS, _normalize_label, _profile_similarity


def get_platform_settings(db: Session) -> PlatformSettings:
    row = db.get(PlatformSettings, 1)
    if row is None:
        row = PlatformSettings(id=1)
        db.add(row)
        db.commit()
    return row


def _class_profile_and_concepts(session, class_id: str) -> tuple[dict | None, set[str]]:
    profile_row = session.run(
        "MATCH (c:DocumentClass {id: $cid})<-[:IN_CLASS]-(d:Document) "
        "RETURN avg(d.profile_section) AS section, avg(d.profile_paragraph) AS paragraph, "
        "       avg(d.profile_table) AS table, avg(d.profile_equation) AS equation",
        cid=class_id,
    ).single()
    profile = None
    if profile_row and profile_row["section"] is not None:
        # avg(d.profile_equation) est null si aucun document membre n'a ce
        # champ (documents ingeres avant son ajout, 2026-09-28) - 0.0 par
        # defaut plutot qu'un None qui casserait _profile_similarity.
        profile = {
            "Section": profile_row["section"], "Paragraph": profile_row["paragraph"],
            "Table": profile_row["table"], "Equation": profile_row["equation"] or 0.0,
        }
    concept_row = session.run(
        "MATCH (c:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(concept:Concept) RETURN collect(concept.label) AS labels",
        cid=class_id,
    ).single()
    labels = concept_row["labels"] if concept_row else []
    return profile, {_normalize_label(l) for l in labels if l}


def class_similarity(session, class_a: str, class_b: str) -> tuple[float, float, float]:
    """Meme formule que IAF-E7 US7.4 (pipeline._find_best_class), appliquee a
    deux classes existantes plutot qu'a un document candidat."""
    profile_a, concepts_a = _class_profile_and_concepts(session, class_a)
    profile_b, concepts_b = _class_profile_and_concepts(session, class_b)
    structural = _profile_similarity(profile_a, profile_b) if profile_a and profile_b else 0.0
    union = concepts_a | concepts_b
    semantic = len(concepts_a & concepts_b) / len(union) if union else 0.0
    return structural, semantic, (structural + semantic) / 2


def merge_classes(session, db: Session, source_id: str, target_id: str) -> None:
    """Fusionne `source_id` DANS `target_id` (la classe la plus petite - par
    nombre de documents - vers la plus grande ; egalite -> ordre alphabetique
    d'id pour un resultat deterministe)."""
    counts = {
        r["id"]: r["n"] for r in session.run(
            "MATCH (c:DocumentClass) WHERE c.id IN $ids "
            "OPTIONAL MATCH (c)<-[:IN_CLASS]-(d:Document) RETURN c.id AS id, count(d) AS n",
            ids=[source_id, target_id],
        )
    }
    if counts.get(source_id, 0) > counts.get(target_id, 0) or (
        counts.get(source_id, 0) == counts.get(target_id, 0) and source_id < target_id
    ):
        source_id, target_id = target_id, source_id

    target_row = session.run("MATCH (c:DocumentClass {id: $id}) RETURN c.name AS name", id=target_id).single()
    if target_row is None:
        return
    target_name = target_row["name"]

    db.execute(
        sa_update(Document)
        .where(Document.neo4j_class_id == source_id)
        .values(neo4j_class_id=target_id, class_name=target_name)
    )
    db.commit()

    session.run(
        "MATCH (s:DocumentClass {id: $source})<-[r:IN_CLASS]-(d:Document) "
        "MATCH (t:DocumentClass {id: $target}) "
        "CREATE (d)-[r2:IN_CLASS]->(t) SET r2 = properties(r) "
        "DELETE r",
        source=source_id, target=target_id,
    )
    # Concepts : fusionnes par libelle (pas de doublon dans la classe cible) ;
    # les mentions (MENTIONS_CONCEPT) sont redirigees vers le concept fusionne.
    session.run(
        "MATCH (s:DocumentClass {id: $source})-[:HAS_CONCEPT]->(sc:Concept) "
        "MATCH (t:DocumentClass {id: $target}) "
        "MERGE (t)-[:HAS_CONCEPT]->(tc:Concept {label: sc.label, class_id: $target}) "
        "ON CREATE SET tc.uri = sc.uri, tc.definition = sc.definition, tc.translation_en = sc.translation_en "
        "WITH sc, tc "
        "OPTIONAL MATCH (doc:Document)-[m:MENTIONS_CONCEPT]->(sc) "
        "FOREACH (_ IN CASE WHEN doc IS NOT NULL THEN [1] ELSE [] END | "
        "  MERGE (doc)-[:MENTIONS_CONCEPT {term: m.term}]->(tc)) "
        "WITH sc "
        "DETACH DELETE sc",
        source=source_id, target=target_id,
    )
    session.run(
        "MATCH (s:DocumentClass {id: $source}) SET s.status = 'fusionnee_dans:' + $target",
        source=source_id, target=target_id,
    )
    session.run(
        "MATCH (a:DocumentClass {id: $a})-[r:SIMILAR_TO]-(b:DocumentClass {id: $b}) SET r.status = 'fusionnee'",
        a=source_id, b=target_id,
    )
    ontology.merge_class_ontology(source_id, target_id)


def check_and_act_on_class(new_class_id: str) -> None:
    """A appeler apres qu'un document ait ete rattache/a cree `new_class_id`
    (worker.py). Compare a toutes les autres classes actives ; fusionne
    automatiquement (si active) ou cree une suggestion (IAF-E7 US7.6
    etendue)."""
    driver = get_driver()
    db = SessionLocal()
    try:
        platform = get_platform_settings(db)
        with driver.session() as session:
            other_ids = [
                r["id"] for r in session.run(
                    "MATCH (c:DocumentClass) "
                    "WHERE c.id <> $cid AND NOT coalesce(c.status, '') STARTS WITH 'fusionnee' "
                    "RETURN c.id AS id",
                    cid=new_class_id,
                )
            ]
            best_id, best_combined, best_structural, best_semantic = None, 0.0, 0.0, 0.0
            for other_id in other_ids:
                structural, semantic, combined = class_similarity(session, new_class_id, other_id)
                if combined > best_combined:
                    best_id, best_combined, best_structural, best_semantic = other_id, combined, structural, semantic
            if best_id is None:
                return

            if platform.auto_merge_enabled and best_combined >= platform.auto_merge_threshold:
                merge_classes(session, db, new_class_id, best_id)
            elif best_combined >= platform.suggest_merge_threshold:
                session.run(
                    "MATCH (a:DocumentClass {id: $a}), (b:DocumentClass {id: $b}) "
                    "MERGE (a)-[r:SIMILAR_TO]-(b) "
                    "SET r.score = $score, r.structural_score = $struct, r.semantic_score = $sem, "
                    "    r.status = 'suggested'",
                    a=new_class_id, b=best_id, score=best_combined,
                    struct=best_structural, sem=best_semantic,
                )
    finally:
        db.close()
