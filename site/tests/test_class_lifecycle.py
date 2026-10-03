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


def _official_class(class_id: str, profile_section: float | None, concepts: list[tuple[str, list[float]]]) -> None:
    """Classe OFFICIELLE jetable : 0 ou 1 document au profil donne (structure) et des concepts
    avec embedding de dimension 3 (semantique)."""
    with get_driver().session() as session:
        session.run("CREATE (:DocumentClass {id: $cid, name: $cid, status: 'officielle'})", cid=class_id)
        if profile_section is not None:
            session.run(
                "MATCH (c:DocumentClass {id: $cid}) CREATE (d:Document {sha256: $sha, profile_section: $p, "
                "profile_paragraph: $q, profile_table: 0.0, profile_equation: 0.0, profile_citation_density: 0.0}) "
                "CREATE (d)-[:IN_CLASS]->(c)",
                cid=class_id, sha=f"{class_id}-doc", p=profile_section, q=1.0 - profile_section,
            )
        for label, embedding in concepts:
            session.run(
                "MATCH (c:DocumentClass {id: $cid}) CREATE (c)-[:HAS_CONCEPT]->(:Concept "
                "{label: $label, class_id: $cid, embedding: $emb})",
                cid=class_id, label=label, emb=embedding,
            )


def _drop_class(class_id: str) -> None:
    with get_driver().session() as session:
        session.run("MATCH (n) WHERE n.class_id = $cid OR (n:DocumentClass AND n.id = $cid) DETACH DELETE n", cid=class_id)
        session.run("MATCH (d:Document) WHERE d.sha256 STARTS WITH $p DETACH DELETE d", p=class_id)


def test_semantic_gate_prefers_a_class_that_passes_it_over_a_structure_only_match():
    from app.pipeline import _find_best_class

    structure_only, semantic = f"e17-test-{uuid.uuid4().hex[:8]}", f"e17-test-{uuid.uuid4().hex[:8]}"
    # meme profil structurel que le document (structure 1.0) mais vocabulaire orthogonal (semantique 0)
    _official_class(structure_only, 1.0, [("autre sujet", [0.0, 1.0, 0.0])])
    # aucun document membre (structure 0) mais vocabulaire proche (semantique 0.75, combine 0.375 < 0.5)
    _official_class(semantic, None, [("sujet", [1.0, 0.0, 0.0]), ("voisin", [0.0, 1.0, 0.0])])
    profile = {"Section": 1.0, "Paragraph": 0.0, "Table": 0.0, "Equation": 0.0, "citation_density": 0.0}
    try:
        with get_driver().session() as session:
            class_id, _name, structural, sem, combined = _find_best_class(
                session, profile, [("sujet du document", [1.0, 0.0, 0.0])], [structure_only, semantic],
            )
        assert class_id == semantic and sem >= settings.recognition_min_semantic
        # sans la porte, la classe "structure seule" l'emporterait (0.5 > 0.375)
        assert combined < 0.5
    finally:
        _drop_class(structure_only)
        _drop_class(semantic)


def test_semantic_gate_reports_the_structure_only_class_when_nothing_passes_it():
    from app.pipeline import _find_best_class

    structure_only = f"e17-test-{uuid.uuid4().hex[:8]}"
    _official_class(structure_only, 1.0, [("autre sujet", [0.0, 1.0, 0.0])])
    profile = {"Section": 1.0, "Paragraph": 0.0, "Table": 0.0, "Equation": 0.0, "citation_density": 0.0}
    try:
        with get_driver().session() as session:
            class_id, _name, structural, sem, combined = _find_best_class(
                session, profile, [("sujet du document", [1.0, 0.0, 0.0])], [structure_only],
            )
        # la classe est rapportee (pour la trace) mais sa semantique est sous la porte : le pipeline refusera
        assert class_id == structure_only
        assert sem < settings.recognition_min_semantic and combined == pytest.approx(0.5)
    finally:
        _drop_class(structure_only)


def test_structural_compatibility_falls_back_to_all_officials_without_structure_link(throwaway_class):
    assert class_lifecycle._structurally_compatible(throwaway_class, ["a", "b"]) == ["a", "b"]


def test_rescore_replaces_the_stale_deposit_score_of_documents_that_joined_by_merge():
    class_id = f"e17-test-{uuid.uuid4().hex[:8]}"
    with get_driver().session() as session:
        session.run("CREATE (:DocumentClass {id: $cid, name: $cid, status: 'officielle'})", cid=class_id)
        for name, label in (("a", "etoile"), ("b", "galaxie")):
            session.run(
                "MATCH (c:DocumentClass {id: $cid}) "
                "CREATE (d:Document {sha256: $sha, profile_section: 0.5, profile_paragraph: 0.5, profile_table: 0.0, "
                "                    profile_equation: 0.0, profile_citation_density: 0.0}) "
                "CREATE (d)-[:IN_CLASS {score: 0.29, structural_score: 0.58, semantic_score: 0.0, "
                "                       method: 'score_combine_insuffisant_creation_provisoire'}]->(c) "
                "CREATE (c)-[:HAS_CONCEPT]->(k:Concept {label: $label, class_id: $cid, embedding: [1.0, 0.0, 0.0]}) "
                "CREATE (d)-[:MENTIONS_CONCEPT {term: $label}]->(k)",
                cid=class_id, sha=f"{class_id}-{name}", label=label,
            )
        # un document reconnu normalement garde son score
        session.run(
            "MATCH (c:DocumentClass {id: $cid}) CREATE (d:Document {sha256: $sha}) "
            "CREATE (d)-[:IN_CLASS {score: 0.7, structural_score: 0.9, semantic_score: 0.5, "
            "                       method: 'score_combine_structure_semantique'}]->(c)", cid=class_id, sha=f"{class_id}-c",
        )
    try:
        with get_driver().session() as session:
            assert class_lifecycle.rescore_class_members(session, class_id) == 2
            rows = {r["sha"]: r for r in session.run(
                "MATCH (d:Document)-[r:IN_CLASS]->(:DocumentClass {id: $cid}) "
                "RETURN d.sha256 AS sha, r.semantic_score AS se, r.deposit_semantic_score AS dep, r.method AS m", cid=class_id,
            )}
        # concepts de a et b (vecteurs identiques) se retrouvent chez l'autre : la semantique n'est plus 0 %
        assert rows[f"{class_id}-a"]["se"] == pytest.approx(1.0) and rows[f"{class_id}-a"]["dep"] == 0.0
        assert rows[f"{class_id}-a"]["m"] == "fusion_de_classe_reevalue"
        assert rows[f"{class_id}-c"]["se"] == 0.5 and rows[f"{class_id}-c"]["m"] == "score_combine_structure_semantique"
    finally:
        _drop_class(class_id)


def test_recognition_verdict_needs_enough_concepts_even_with_a_high_score(monkeypatch):
    from app.pipeline import recognition_verdict

    monkeypatch.setattr(settings, "recognition_min_concepts", 4)
    # cas reel du 2026-10-03 : notice d'un seul concept, semantique 51 % face a Vin, score combine suffisant
    assert recognition_verdict(True, 0.57, 0.51, 1) == (False, "concepts_insuffisants_creation_provisoire")
    assert recognition_verdict(True, 0.68, 0.43, 3) == (False, "concepts_insuffisants_creation_provisoire")
    assert recognition_verdict(True, 0.68, 0.43, 4) == (True, "score_combine_structure_semantique")
    # les autres refus gardent leur motif propre
    assert recognition_verdict(True, 0.50, 0.05, 20) == (False, "porte_semantique_creation_provisoire")
    assert recognition_verdict(True, 0.30, 0.40, 20) == (False, "score_combine_insuffisant_creation_provisoire")
    assert recognition_verdict(False, 0.0, 0.0, 20) == (False, "score_combine_insuffisant_creation_provisoire")


def test_same_evidence_tier_separates_poor_and_rich_classes(monkeypatch):
    monkeypatch.setattr(settings, "recognition_min_concepts", 4)
    assert class_lifecycle.same_evidence_tier(2, 3) and class_lifecycle.same_evidence_tier(4, 30)
    assert not class_lifecycle.same_evidence_tier(3, 4) and not class_lifecycle.same_evidence_tier(1, 25)


def test_a_provisional_class_below_the_minimum_is_never_compared_to_official_classes(throwaway_class, monkeypatch):
    _add_concepts_and_docs(throwaway_class, {"donnee": 1, "mesure": 1}, n_docs=1)
    monkeypatch.setattr(settings, "recognition_min_concepts", 4)

    def must_not_be_called(*args, **kwargs):
        raise AssertionError("une classe trop pauvre ne doit pas etre comparee")

    monkeypatch.setattr(class_lifecycle.class_merge, "class_similarity", must_not_be_called)
    traces = []
    with get_driver().session() as session:
        # les concepts du helper n'ont pas d'embedding : la classe est donc a 0 concept comparable
        assert class_lifecycle._try_absorb_into_official(session, None, throwaway_class, lambda *a: traces.append(a)) is None
    assert traces and "concepts insuffisants" in traces[0][0]


def test_promotion_is_deferred_until_the_class_has_enough_concepts(monkeypatch):
    monkeypatch.setattr(settings, "official_class_min_documents", 3)
    monkeypatch.setattr(settings, "recognition_min_concepts", 4)
    assert class_lifecycle.promotion_decision(2, 30) == (False, None)  # pas assez de documents : rien a signaler
    ready, reason = class_lifecycle.promotion_decision(3, 3)  # cas reel du 2026-10-03 : 3 notices, 3 concepts
    assert not ready and "3 concept(s)" in reason and "minimum 4" in reason
    assert class_lifecycle.promotion_decision(3, 4) == (True, None)
