"""EPIC-IAF-E17 : seuil de densite, plan de reduction, ontologies structurelles
a la fusion. Les calculs purs sont testes sans service ; la fusion RDF et le
plan de reduction utilisent le VRAI Fuseki / Neo4j (memes conventions que
test_structure_matcher.py : Docker demarre), sur des classes jetables
(prefixe `e17-test-`) nettoyees a la fin."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import class_lifecycle, class_reduction, ontology  # noqa: E402
from app.config import settings  # noqa: E402
from app.graph import get_driver  # noqa: E402


def test_density_threshold_uses_floor_without_enough_pairs():
    threshold, mean, std, n = class_lifecycle.density_threshold([0.9, 0.8], floor=0.5, k=1.0, min_pairs=3)
    assert (threshold, n) == (0.5, 2)


def test_density_threshold_adapts_to_ambient_similarity():
    # classes toutes proches les unes des autres (densite haute) : le seuil monte
    # au-dessus du plancher, une paire "moyennement proche" ne fusionne plus.
    high_density = [0.80, 0.82, 0.78, 0.81]
    threshold, mean, std, n = class_lifecycle.density_threshold(high_density, floor=0.5, k=1.0, min_pairs=3)
    assert threshold > 0.80 and mean == pytest.approx(0.8025)
    # une paire qui se detache d'une densite basse depasse le seuil
    low_density = [0.05, 0.10, 0.08, 0.92]
    threshold, *_ = class_lifecycle.density_threshold(low_density, floor=0.5, k=1.0, min_pairs=3)
    assert 0.92 > threshold >= 0.5


def test_density_threshold_never_below_floor():
    threshold, *_ = class_lifecycle.density_threshold([0.1, 0.1, 0.1, 0.1], floor=0.5, k=1.0, min_pairs=3)
    assert threshold == 0.5


@pytest.fixture()
def throwaway_class():
    class_id = f"e17-test-{uuid.uuid4().hex[:8]}"
    driver = get_driver()
    with driver.session() as session:
        session.run("CREATE (:DocumentClass {id: $cid, name: 'Provisoire - test', status: 'provisoire'})", cid=class_id)
    yield class_id
    with driver.session() as session:
        session.run("MATCH (n) WHERE n.class_id = $cid OR (n:DocumentClass AND n.id = $cid) DETACH DELETE n", cid=class_id)
        session.run("MATCH (d:Document) WHERE d.sha256 STARTS WITH $p DETACH DELETE d", p=class_id)
    try:
        ontology.delete_class_ontology(class_id)
    except Exception:
        pass


def _add_concepts_and_docs(class_id: str, mentions: dict[str, int], n_docs: int) -> None:
    with get_driver().session() as session:
        for i in range(n_docs):
            session.run(
                "MATCH (c:DocumentClass {id: $cid}) CREATE (d:Document {sha256: $sha}) CREATE (d)-[:IN_CLASS]->(c)",
                cid=class_id, sha=f"{class_id}-doc{i}",
            )
        for label, support in mentions.items():
            session.run(
                "MATCH (c:DocumentClass {id: $cid}) CREATE (c)-[:HAS_CONCEPT]->(k:Concept {label: $label, class_id: $cid})",
                cid=class_id, label=label,
            )
            for i in range(support):
                session.run(
                    "MATCH (d:Document {sha256: $sha}), (k:Concept {label: $label, class_id: $cid}) "
                    "CREATE (d)-[:MENTIONS_CONCEPT {term: $label}]->(k)",
                    sha=f"{class_id}-doc{i}", label=label, cid=class_id,
                )


def test_reduction_plan_protects_imported_concepts_and_flags_rare_ones(throwaway_class):
    _add_concepts_and_docs(throwaway_class, {"commun": 3, "rare": 1, "importe": 0}, n_docs=3)
    with get_driver().session() as session:
        plan = class_reduction.plan_reduction(session, throwaway_class, min_support=2)
    assert plan["allowed"] is True
    assert [c["label"] for c in plan["removable"]] == ["rare"]
    assert plan["protected"] == ["importe"]
    assert plan["kept"] == ["commun"]


def test_reduction_refused_below_official_document_count(throwaway_class):
    _add_concepts_and_docs(throwaway_class, {"rare": 1}, n_docs=max(1, settings.official_class_min_documents - 1))
    with get_driver().session() as session:
        plan = class_reduction.plan_reduction(session, throwaway_class, min_support=2)
        assert plan["allowed"] is False and plan["removable"] == []
        assert class_reduction.apply_reduction(session, throwaway_class, 2) == 0


def test_reduction_apply_removes_only_rare_concepts(throwaway_class):
    _add_concepts_and_docs(throwaway_class, {"commun": 3, "rare": 1, "importe": 0}, n_docs=3)
    with get_driver().session() as session:
        assert class_reduction.apply_reduction(session, throwaway_class, 2) == 1
        left = {r["l"] for r in session.run(
            "MATCH (:DocumentClass {id: $cid})-[:HAS_CONCEPT]->(k:Concept) RETURN k.label AS l", cid=throwaway_class)}
    assert left == {"commun", "importe"}


def test_merge_keeps_both_structural_ontologies_with_target_as_subject():
    source, target = f"e17-test-{uuid.uuid4().hex[:8]}", f"e17-test-{uuid.uuid4().hex[:8]}"
    try:
        ontology.ensure_class_accepts_structure(source, "urn:iaf:ns:structure-word")
        ontology.ensure_class_accepts_structure(source, "urn:iaf:ns:structure-pdf")
        ontology.ensure_class_accepts_structure(target, "urn:iaf:ns:structure-word")  # identique a l'une de la source
        ontology.merge_class_ontology(source, target)
        assert sorted(ontology.list_accepted_structures(target)) == [
            "urn:iaf:ns:structure-pdf", "urn:iaf:ns:structure-word",
        ]  # union : la commune n'apparait qu'une fois, la differente est conservee
        assert ontology.list_accepted_structures(source) == []
    finally:
        for c in (source, target):
            ontology.delete_class_ontology(c)


def test_delete_concept_removes_every_uri_carrying_the_label_after_a_merge():
    # Apres une fusion, le graphe garde UNE URI par classe d'origine pour un meme
    # libelle (Neo4j n'a qu'un noeud) : supprimer le concept doit toutes les retirer
    # (bug reel trouve en appliquant la reduction d'ontologie sur une classe fusionnee).
    source, target = f"e17-test-{uuid.uuid4().hex[:8]}", f"e17-test-{uuid.uuid4().hex[:8]}"
    try:
        ontology.ensure_concept(source, "Planete", "fr")
        ontology.ensure_concept(target, "Planete", "fr")
        ontology.ensure_concept(target, "Galaxie", "fr")
        ontology.merge_class_ontology(source, target)
        assert len([c for c in ontology.list_concepts(target) if c["label"] == "Planete"]) == 2
        ontology.delete_concept(target, "Planete")
        assert [c["label"] for c in ontology.list_concepts(target)] == ["Galaxie"]
    finally:
        for c in (source, target):
            ontology.delete_class_ontology(c)
