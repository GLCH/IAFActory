"""EPIC-IAF-E17 (2026-10-02) : cycle de vie des classes INCONNUES - fusion des
classes provisoires par DENSITE de similarite semantique (US17.2), fusion des
ontologies structurelles (US17.3, dans class_merge/ontology), promotion en
classe OFFICIELLE au-dela d'un seuil de documents (US17.4).

Demande explicite : "On calcule une distance de similarite semantique avec les
autres documents inconnus. Lorsque la similarite augmente (par rapport a une
densite de similarite) on merge les deux classes [...] Lorsque la classe
inconnue passe un seuil de nombre de documents integres, la classe devient
officielle et est integree au pipeline pour reconnaissance."

Appele par worker.py apres chaque ingestion. Tous les parametres (plancher,
k, paires minimales, seuil de promotion) sont NON calibres (US7.7) et
viennent de `settings` - voir docs/epics/EPIC-IAF-E17-classes-inconnues-cycle-de-vie.md."""
from __future__ import annotations

import statistics
import uuid
from datetime import datetime, timezone

from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from . import class_merge
from .config import settings
from .db import SessionLocal
from .graph import get_driver
from .models import Document, DocumentStatus, PipelineStep
from .pipeline import OFFICIAL_CLASS_FILTER, _concept_set_similarity

PROVISIONAL_PREFIX = "Provisoire - "
MAX_MERGE_ROUNDS = 5


def density_threshold(
    pair_similarities: list[float], floor: float | None = None, k: float | None = None, min_pairs: int | None = None,
) -> tuple[float, float, float, int]:
    """Seuil adaptatif de fusion : `max(plancher, moyenne + k * ecart-type)` des
    similarites semantiques entre PAIRES de classes provisoires - une paire ne
    fusionne que si elle se detache de la densite ambiante, pas seulement si
    elle depasse un seuil absolu. Moins de `min_pairs` paires : densite non
    estimable, le plancher seul s'applique. Renvoie (seuil, moyenne,
    ecart-type, nombre de paires)."""
    floor = settings.merge_similarity_floor if floor is None else floor
    k = settings.merge_density_k if k is None else k
    min_pairs = settings.merge_density_min_pairs if min_pairs is None else min_pairs
    n = len(pair_similarities)
    if n < min_pairs:
        return floor, 0.0, 0.0, n
    mean = statistics.fmean(pair_similarities)
    std = statistics.pstdev(pair_similarities)
    return max(floor, mean + k * std), mean, std, n


def _provisional_ids(session) -> list[str]:
    return [r["id"] for r in session.run(
        "MATCH (c:DocumentClass) WHERE coalesce(c.status, '') = 'provisoire' RETURN c.id AS id ORDER BY c.id"
    )]


def _class_status_and_docs(session, class_id: str) -> tuple[str | None, str | None, int]:
    row = session.run(
        "MATCH (c:DocumentClass {id: $cid}) OPTIONAL MATCH (c)<-[:IN_CLASS]-(d:Document) "
        "RETURN c.status AS status, c.name AS name, count(DISTINCT d) AS docs", cid=class_id,
    ).single()
    if row is None or row["name"] is None:
        return None, None, 0
    return row["status"], row["name"], row["docs"]


def promote_class(session, db: Session, class_id: str) -> str | None:
    """US17.4 : classe provisoire -> OFFICIELLE (entre dans la reconnaissance).
    Retire le prefixe "Provisoire - " du nom ; les documents Postgres passent a
    `recognized` (aucune nouvelle valeur d'enum, donc aucune migration). Renvoie
    le nouveau nom, ou None si la classe n'existe pas / n'est pas provisoire."""
    status, name, _docs = _class_status_and_docs(session, class_id)
    if name is None or status != "provisoire":
        return None
    new_name = name[len(PROVISIONAL_PREFIX):] if name.startswith(PROVISIONAL_PREFIX) else name
    session.run(
        "MATCH (c:DocumentClass {id: $cid}) SET c.status = 'officielle', c.name = $name, c.promoted_at = datetime()",
        cid=class_id, name=new_name,
    )
    db.execute(
        sa_update(Document).where(Document.neo4j_class_id == class_id)
        .values(status=DocumentStatus.recognized, class_name=new_name)
    )
    db.commit()
    return new_name


def _try_absorb_into_official(session, db: Session, class_id: str, trace) -> str | None:
    """Une classe provisoire qui n'a plus de voisine provisoire proche est
    comparee aux classes OFFICIELLES (meme formule combinee structurel +
    semantique et meme seuil que la reconnaissance d'un document, US7.4) :
    rattachee a la meilleure si elle atteint le seuil. Couvre le cas reel
    constate le 2026-10-02 : un document d'astronomie reste isole parce que sa
    classe voisine est devenue officielle entre-temps (une classe provisoire
    ne fusionne jamais avec une autre provisoire devenue officielle). Renvoie
    l'id de la classe officielle, ou None."""
    officials = [r["id"] for r in session.run(
        "MATCH (c:DocumentClass) WHERE " + OFFICIAL_CLASS_FILTER + " RETURN c.id AS id"
    )]
    best_id, best = None, 0.0
    for official in officials:
        _structural, _semantic, combined = class_merge.class_similarity(session, class_id, official)
        if combined > best:
            best_id, best = official, combined
    if best_id is None or best < settings.recognition_threshold:
        return None
    merged = class_merge.merge_classes(session, db, class_id, best_id)
    if merged is None:
        return None
    # Les documents de la classe absorbee entrent dans une classe officielle : ils sont
    # "reconnus", comme a la promotion (constate au rejeu du 2026-10-02 : un document
    # absorbe restait `provisional` dans une classe officielle).
    db.execute(
        sa_update(Document).where(Document.neo4j_class_id == merged[1])
        .values(status=DocumentStatus.recognized)
    )
    db.commit()
    trace(
        "Rattachement a une classe officielle (US17.4)",
        f"similarite {best:.0%} >= seuil de reconnaissance {settings.recognition_threshold:.0%} : "
        f"{merged[0][:8]} -> {merged[1][:8]}",
    )
    return merged[1]


def run_after_ingest(class_id: str, run_id: uuid.UUID | None = None) -> dict:
    """Apres l'ingestion d'un document : classe provisoire -> fusion par
    densite de similarite puis promotion eventuelle ; classe officielle ->
    comportement US7.6 inchange (suggestions de fusion). Chaque decision est
    tracee comme etape du pipeline (`PipelineStep`, phase "Cycle de vie") si
    `run_id` est fourni. Renvoie un resume (pour les tests et les traces)."""
    summary: dict = {"merged": [], "promoted": None, "threshold": None}
    db = SessionLocal()

    def trace(label: str, detail: str) -> None:
        if run_id is None:
            return
        try:
            db.add(PipelineStep(run_id=run_id, phase="Cycle de vie", label=label, detail=detail))
            db.commit()
        except Exception:
            db.rollback()

    try:
        with get_driver().session() as session:
            status, _name, _docs = _class_status_and_docs(session, class_id)
            if status != "provisoire":
                return summary  # classe officielle : voir class_merge.check_and_act_on_class (appele par worker.py)

            for _ in range(MAX_MERGE_ROUNDS):
                provisional = _provisional_ids(session)
                others = [c for c in provisional if c != class_id]
                if not others:
                    break
                concepts = {
                    c: class_merge._class_profile_and_concepts(session, c)[1] for c in provisional
                }
                similarity: dict[tuple[str, str], float] = {}
                for i, a in enumerate(provisional):
                    for b in provisional[i + 1:]:
                        similarity[(a, b)] = similarity[(b, a)] = _concept_set_similarity(concepts[a], concepts[b])
                pair_values = [v for (a, b), v in similarity.items() if a < b]
                threshold, mean, std, n_pairs = density_threshold(pair_values)
                best_id = max(others, key=lambda o: similarity[(class_id, o)])
                best_sim = similarity[(class_id, best_id)]
                summary["threshold"] = threshold
                trace(
                    "Similarite avec les classes inconnues (US17.2)",
                    f"meilleure {best_sim:.0%} vs seuil de densite {threshold:.0%} "
                    f"({n_pairs} paire(s), moyenne {mean:.0%}, ecart-type {std:.0%})",
                )
                if best_sim <= threshold:
                    break
                same_structures = _structures_equal(class_id, best_id)
                merged = class_merge.merge_classes(session, db, class_id, best_id)
                if merged is None:
                    break
                source, target = merged
                summary["merged"].append((source, target))
                trace(
                    "Fusion de classes inconnues (US17.2)",
                    f"{source[:8]} -> {target[:8]} (similarite {best_sim:.0%}) ; ontologies structurelles "
                    + ("identiques, fusionnees" if same_structures else "differentes, toutes conservees (US17.3)"),
                )
                class_id = target  # la classe survivante peut encore fusionner au tour suivant

            status, _name, docs = _class_status_and_docs(session, class_id)
            if status == "provisoire":
                absorbed = _try_absorb_into_official(session, db, class_id, trace)
                if absorbed:
                    summary["absorbed_into"] = absorbed
                    return summary
            if status == "provisoire" and docs >= settings.official_class_min_documents:
                new_name = promote_class(session, db, class_id)
                if new_name:
                    summary["promoted"] = class_id
                    trace(
                        "Promotion en classe officielle (US17.4)",
                        f"'{new_name}' : {docs} document(s) >= seuil {settings.official_class_min_documents} - "
                        "la classe entre dans la reconnaissance",
                    )
                    # Les autres classes provisoires isolees peuvent maintenant
                    # etre proches de cette nouvelle classe officielle.
                    for other in _provisional_ids(session):
                        _try_absorb_into_official(session, db, other, lambda *a: None)
            elif status == "provisoire":
                trace(
                    "Classe provisoire (US17.4)",
                    f"{docs}/{settings.official_class_min_documents} document(s) avant promotion",
                )
    finally:
        db.close()
    return summary


def reevaluate_provisional_classes() -> dict:
    """Rejoue le cycle de vie (fusion par densite, promotion) sur TOUTES les
    classes provisoires, une a une - utile apres un changement de seuil ou
    pour rattraper des classes laissees isolees. Renvoie le nombre de fusions et
    de promotions."""
    with get_driver().session() as session:
        ids = _provisional_ids(session)
    merges, promotions = 0, 0
    for class_id in ids:
        with get_driver().session() as session:
            status, _name, _docs = _class_status_and_docs(session, class_id)
        if status != "provisoire":
            continue  # deja fusionnee ou promue par un tour precedent de cette boucle
        result = run_after_ingest(class_id)
        merges += len(result["merged"])
        promotions += 1 if result["promoted"] else 0
    return {"merges": merges, "promotions": promotions}


def _structures_equal(class_a: str, class_b: str) -> bool:
    """US17.3 : les deux classes acceptent-elles exactement les memes
    ontologies structurelles ? (sert la trace ; la fusion RDF, elle, est
    toujours une UNION, voir ontology.merge_class_ontology)."""
    from . import ontology
    try:
        return set(ontology.list_accepted_structures(class_a)) == set(ontology.list_accepted_structures(class_b))
    except Exception:
        return False
