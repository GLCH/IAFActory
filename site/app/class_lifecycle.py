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
from .pipeline import OFFICIAL_CLASS_FILTER, _concept_set_similarity, _profile_similarity

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


def promotion_decision(n_documents: int, n_concepts: int) -> tuple[bool, str | None]:
    """Une classe provisoire devient officielle (donc cible de reconnaissance) quand elle a assez de
    documents ET assez de concepts. Constat reel du 2026-10-03 : trois notices d'archives d'1 a 3
    concepts ont fusionne en une classe de 3 concepts, promue a 3 documents ; une cible aussi pauvre
    fausserait la reconnaissance (semantique gonflee). La promotion est alors differee, pas refusee :
    la classe grossit tant que de nouveaux documents l'enrichissent. Renvoie (pret, motif du report)."""
    if n_documents < settings.official_class_min_documents:
        return False, None
    if n_concepts < settings.recognition_min_concepts:
        return False, (
            f"{n_documents} document(s) mais {n_concepts} concept(s) : minimum {settings.recognition_min_concepts} "
            "pour entrer dans la reconnaissance"
        )
    return True, None


def same_evidence_tier(n_concepts_a: int, n_concepts_b: int) -> bool:
    """Deux classes sont comparables si elles sont toutes deux au moins aussi riches que
    `recognition_min_concepts`, ou toutes deux plus pauvres."""
    minimum = settings.recognition_min_concepts
    return (n_concepts_a >= minimum) == (n_concepts_b >= minimum)


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


def rescore_class_members(session, class_id: str) -> int:
    """Recalcule le score (structure, semantique, combine) des documents entres dans la classe
    par FUSION ou creation, face au RESTE de la classe (les autres documents, le document
    evalue est exclu pour ne pas se comparer a lui-meme).

    Constat du 2026-10-02 (question de l'utilisateur : deux documents d'astrophysique affiches a 0 %
    de semantique dans la classe astronomie) : `IN_CLASS.semantic_score` gardait le score calcule AU
    DEPOT, face aux AUTRES classes officielles (la classe astronomie n'existait pas encore comme
    officielle), donc 0 % apres la fusion alors que les concepts des documents sont bien agreges dans
    la classe. L'ancien score est conserve dans `deposit_*`. Les documents reconnus normalement
    (methode score_combine_structure_semantique) gardent leur score : il compare deja a CETTE classe.
    Renvoie le nombre de documents recalcules."""
    rows = list(session.run(
        "MATCH (d:Document)-[r:IN_CLASS]->(c:DocumentClass {id: $cid}) "
        "OPTIONAL MATCH (d)-[:MENTIONS_CONCEPT]->(k:Concept) WHERE k.embedding IS NOT NULL "
        "RETURN d.sha256 AS sha, r.method AS method, r.score AS score, r.structural_score AS st, r.semantic_score AS se, "
        "       d.profile_section AS section, d.profile_paragraph AS paragraph, d.profile_table AS tbl, "
        "       d.profile_equation AS equation, d.profile_citation_density AS citation, "
        "       collect(DISTINCT {label: k.label, embedding: k.embedding}) AS concepts", cid=class_id,
    ))
    members = []
    for r in rows:
        profile = None
        if r["section"] is not None:
            profile = {
                "Section": r["section"], "Paragraph": r["paragraph"] or 0.0, "Table": r["tbl"] or 0.0,
                "Equation": r["equation"] or 0.0, "citation_density": r["citation"] or 0.0,
            }
        concepts = [(c["label"], c["embedding"]) for c in r["concepts"] if c["label"] and c["embedding"]]
        members.append({"row": r, "profile": profile, "concepts": concepts})
    rescored = 0
    for member in members:
        row = member["row"]
        if row["method"] == "score_combine_structure_semantique":
            continue
        others = [m for m in members if m is not member]
        if not others:
            continue
        other_concepts: dict[str, list[float]] = {}
        for m in others:
            for label, embedding in m["concepts"]:
                other_concepts.setdefault(label, embedding)
        semantic = _concept_set_similarity(member["concepts"], list(other_concepts.items()))
        other_profiles = [m["profile"] for m in others if m["profile"]]
        if member["profile"] and other_profiles:
            average = {k: sum(p[k] for p in other_profiles) / len(other_profiles) for k in other_profiles[0]}
            structural = _profile_similarity(member["profile"], average)
        else:
            structural = 0.0
        session.run(
            "MATCH (d:Document {sha256: $sha})-[r:IN_CLASS]->(c:DocumentClass {id: $cid}) "
            "SET r.deposit_score = coalesce(r.deposit_score, r.score), "
            "    r.deposit_structural_score = coalesce(r.deposit_structural_score, r.structural_score), "
            "    r.deposit_semantic_score = coalesce(r.deposit_semantic_score, r.semantic_score), "
            "    r.deposit_method = coalesce(r.deposit_method, r.method), "
            "    r.structural_score = $st, r.semantic_score = $se, r.score = $sc, r.method = 'fusion_de_classe_reevalue'",
            sha=row["sha"], cid=class_id, st=structural, se=semantic, sc=(structural + semantic) / 2,
        )
        rescored += 1
    return rescored


def _structurally_compatible(class_id: str, official_ids: list[str]) -> list[str]:
    """Reduit les classes officielles testees semantiquement a celles qui acceptent une des
    ontologies structurelles de la classe provisoire (meme principe que le pre-filtre
    structurel du pipeline : tester peu d'ontologies semantiques quand elles seront nombreuses).
    Degradation gracieuse : si la classe n'a aucune ontologie structurelle liee, si aucune
    officielle n'en partage une, ou si Fuseki est indisponible, toutes les officielles sont testees."""
    from . import ontology
    try:
        shared: set[str] = set()
        for structure_uri in ontology.list_accepted_structures(class_id):
            shared.update(ontology.classes_accepting_structure(structure_uri))
    except Exception:
        return official_ids
    compatible = [c for c in official_ids if c in shared]
    return compatible or official_ids


def _try_absorb_into_official(session, db: Session, class_id: str, trace) -> str | None:
    """Une classe provisoire qui n'a plus de voisine provisoire proche est
    comparee aux classes OFFICIELLES (meme formule combinee structurel +
    semantique et meme seuil que la reconnaissance d'un document, US7.4) :
    rattachee a la meilleure si elle atteint le seuil. Couvre le cas reel
    constate le 2026-10-02 : un document d'astronomie reste isole parce que sa
    classe voisine est devenue officielle entre-temps (une classe provisoire
    ne fusionne jamais avec une autre provisoire devenue officielle). Renvoie
    l'id de la classe officielle, ou None.

    Nombre minimal de concepts (2026-10-03) : une classe provisoire de moins de
    `recognition_min_concepts` concepts ne prouve rien (semantique gonflee), elle n'est pas comparee."""
    n_concepts = len(class_merge._class_profile_and_concepts(session, class_id)[1])
    if n_concepts < settings.recognition_min_concepts:
        trace(
            "Rattachement refuse (concepts insuffisants)",
            f"{class_id[:8]} : {n_concepts} concept(s), minimum {settings.recognition_min_concepts}",
        )
        return None
    officials = [r["id"] for r in session.run(
        "MATCH (c:DocumentClass) WHERE " + OFFICIAL_CLASS_FILTER + " RETURN c.id AS id"
    )]
    officials = _structurally_compatible(class_id, officials)
    # Porte semantique (2026-10-02) : la structure restreint les candidates, elle ne decide pas ;
    # une officielle qui passe la porte est preferee a une autre au score combine plus haut.
    best_id, best, best_semantic, best_key = None, 0.0, 0.0, (False, 0.0)
    for official in officials:
        _structural, semantic, combined = class_merge.class_similarity(session, class_id, official)
        key = (semantic >= settings.recognition_min_semantic, combined)
        if combined > 0.0 and key > best_key:
            best_id, best, best_semantic, best_key = official, combined, semantic, key
    if best_id is None or best < settings.recognition_threshold:
        return None
    if best_semantic < settings.recognition_min_semantic:
        trace(
            "Rattachement refuse (porte semantique)",
            f"{class_id[:8]} : combine {best:.0%} >= seuil {settings.recognition_threshold:.0%} mais "
            f"semantique {best_semantic:.0%} < porte {settings.recognition_min_semantic:.0%}",
        )
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
                # Meme niveau de preuve (2026-10-03) : une classe de moins de `recognition_min_concepts`
                # concepts ne fusionne qu'avec une classe tout aussi pauvre, et inversement ; la
                # similarite entre tailles tres differentes est mecaniquement gonflee.
                comparable = [o for o in others if same_evidence_tier(len(concepts[class_id]), len(concepts[o]))]
                if not comparable:
                    trace(
                        "Similarite avec les classes inconnues (US17.2)",
                        f"aucune classe de meme niveau de preuve ({len(concepts[class_id])} concept(s), "
                        f"minimum {settings.recognition_min_concepts})",
                    )
                    break
                best_id = max(comparable, key=lambda o: similarity[(class_id, o)])
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

            if summary["merged"]:
                rescore_class_members(session, class_id)  # les scores de depot ne valent plus apres fusion
            status, _name, docs = _class_status_and_docs(session, class_id)
            if status == "provisoire":
                absorbed = _try_absorb_into_official(session, db, class_id, trace)
                if absorbed:
                    summary["absorbed_into"] = absorbed
                    rescore_class_members(session, absorbed)
                    return summary
            n_class_concepts = len(class_merge._class_profile_and_concepts(session, class_id)[1]) if status == "provisoire" else 0
            ready, deferral = promotion_decision(docs, n_class_concepts)
            if status == "provisoire" and not ready and deferral:
                trace("Promotion differee (US17.4)", deferral)
            if status == "provisoire" and ready:
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
            elif status == "provisoire" and not deferral:
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
