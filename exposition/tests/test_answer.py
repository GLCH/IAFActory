"""Reponse ancree sur les donnees enregistrees : VRAI Neo4j (graphe jetable, identifiants `e19-test-...`),
modele et embedding SIMULES. Verifie surtout les AVEUX D'IGNORANCE et l'interdiction d'inventer."""
from __future__ import annotations

import random
import uuid

import pytest

from exposition_service import answer, knowledge
from exposition_service.store import get_driver

DIM = 768  # dimension de l'index vectoriel `chunkEmbeddings`


def fake_embed(_text):
    return [1.0] + [0.0] * (DIM - 1)


def never_called(*_a, **_k):
    raise AssertionError("le modele ne doit pas etre appele sans donnee a expliquer")


@pytest.fixture()
def world():
    tag = "".join(random.choices("abcdefghjkmnpqrtuvwz", k=8))  # lettres seules : un jeton, sans pluriel
    tag2 = "".join(random.choices("abcdefghjkmnpqrtuvwz", k=8))  # jeton propre a la classe provisoire
    ids = {"official": f"e19-test-off-{tag}", "provisional": f"e19-test-prov-{tag}"}
    with get_driver().session() as session:
        session.run("CREATE (:DocumentClass {id: $a, name: $an, status: 'officielle'}), (:DocumentClass {id: $b, name: $bn, status: 'provisoire'})",
                    a=ids["official"], an=f"Classe off {tag}", b=ids["provisional"], bn=f"Classe prov {tag}")
        session.run(
            "MATCH (c:DocumentClass {id: $a}) "
            # concept avec definition et hierarchie
            "CREATE (c)-[:HAS_CONCEPT]->(n:Concept {label: $nebuleuse, class_id: $a, definition: $definition}) "
            "CREATE (c)-[:HAS_CONCEPT]->(p:Concept {label: $parent, class_id: $a}) CREATE (n)-[:SUBCLASS_OF]->(p) "
            # concept SANS aucun detail : un simple terme
            "CREATE (c)-[:HAS_CONCEPT]->(:Concept {label: $bare, class_id: $a}) "
            # entite avec attribut, relation, et passage qui la mentionne
            "CREATE (e:Entity {class_id: $a, name: $orion, type: 'Etoile', couleur: 'rouge'}) "
            "CREATE (b:Entity {class_id: $a, name: $beta, type: 'Etoile'}) CREATE (e)-[:REL {type: 'voisine de'}]->(b) "
            "CREATE (d:Document {sha256: $sha, filename: $fn, title: 'Doc'}) CREATE (d)-[:IN_CLASS]->(c) "
            "CREATE (d)-[:HAS_ELEMENT]->(el:StructElement {label: 'Observations'}) "
            "CREATE (el)-[:HAS_CHUNK]->(ch:Chunk {id: $chunk, text: $text}) CREATE (ch)-[:MENTIONS]->(e)",
            a=ids["official"], nebuleuse=f"Nebuleuse {tag}", parent=f"Objet diffus {tag}", bare=f"Quasar {tag}",
            definition=f"Nuage de gaz et de poussiere {tag} ou naissent des etoiles.",
            orion=f"Orion {tag}", beta=f"Betelgeuse {tag}", sha=f"{ids['official']}-doc", fn=f"obs-{tag}.docx", chunk=f"{ids['official']}-chunk",
            text=f"L'observation montre que Orion {tag} brille d'un rouge intense pres de la ceinture.",
        )
        session.run(
            "MATCH (c:DocumentClass {id: $b}) CREATE (c)-[:HAS_CONCEPT]->(:Concept {label: $label, class_id: $b, definition: $definition})",
            b=ids["provisional"], label=f"Protoetoile {tag2}", definition=f"Etoile en formation {tag2}.",
        )
    yield {"tag": tag, "tag2": tag2, "ids": ids}
    with get_driver().session() as session:
        for cid in ids.values():
            session.run("MATCH (n) WHERE n.class_id = $c OR (n:DocumentClass AND n.id = $c) DETACH DELETE n", c=cid)
            session.run("MATCH (d:Document) WHERE d.sha256 STARTS WITH $c DETACH DELETE d", c=cid)
            session.run("MATCH (ch:Chunk) WHERE ch.id STARTS WITH $c DETACH DELETE ch", c=cid)
        session.run("MATCH (el:StructElement {label: 'Observations'}) WHERE NOT ()-[:HAS_ELEMENT]->(el) DETACH DELETE el")


def cited_chat(prompt, system):
    """Modele simule honnete : reponse construite sur les donnees numerotees recues."""
    assert "Donnees enregistrees" in prompt and "[1]" in prompt
    return {"reponse": "Une nebuleuse est un nuage de gaz [1]. Elle abrite la naissance d'etoiles [1].", "explication": "Defini par la donnee [1].", "limites": ""}


def test_answer_uses_graph_data_definition_hierarchy_attribute_relation_and_passage(world):
    tag = world["tag"]
    result = answer.ask(f"Qu'est-ce qu'une nebuleuse {tag} ?", "official", embed_fn=fake_embed, chat_fn=cited_chat)
    assert result.status == "repondu" and result.enrichment == "llm"
    assert [e.kind for e in result.evidence if e.kind == "definition"] == ["definition"]
    assert any(e.kind == "hierarchie" and "Objet diffus" in e.text for e in result.evidence)
    assert "graphe" in result.routes
    assert "[1]" in result.answer and result.explanation


def test_answer_combines_graph_and_rag_through_an_entity_mentioned_in_a_passage(world):
    tag = world["tag"]
    result = answer.ask(f"Quelle est la couleur d'Orion {tag} ?", "official", embed_fn=fake_embed, chat_fn=cited_chat)
    kinds = {e.kind for e in result.evidence}
    assert {"attribut", "relation", "passage"} <= kinds and set(result.routes) == {"graphe", "rag"}
    attribute = next(e for e in result.evidence if e.kind == "attribut")
    assert "couleur = rouge" in attribute.text
    passage = next(e for e in result.evidence if e.kind == "passage")
    assert passage.source.document == f"obs-{tag}.docx" and passage.source.section == "Observations"


def test_nothing_known_admits_ignorance_without_calling_the_model(world):
    result = answer.ask("Quelle est la meilleure recette de fondue savoyarde quantique ?", "official", embed_fn=fake_embed, chat_fn=never_called)
    assert result.status == "inconnu" and result.evidence == [] and result.answer is None
    assert "aucune information" in result.ignorance


def test_a_bare_term_without_any_detail_admits_it_knows_only_the_term(world):
    tag = world["tag"]
    result = answer.ask(f"quasar {tag}", "official", embed_fn=fake_embed, chat_fn=never_called)
    assert result.status == "terme_sans_detail" and result.evidence == [] and result.answer is None
    assert f"Quasar {tag}" in result.ignorance and "aucun détail" in result.ignorance
    assert [m.kind for m in result.matches] == ["concept"]


def test_a_question_without_content_words_is_refused_as_too_vague(world):
    result = answer.ask("Qu'est-ce que c'est ?", "official", embed_fn=fake_embed, chat_fn=never_called)
    assert result.status == "requete_trop_courte" and "aucun terme exploitable" in result.ignorance


def test_ungrounded_sentences_are_removed_and_grouped_citations_are_accepted(world):
    tag = world["tag"]

    def chat(prompt, system):
        return {"reponse": "Les nebuleuses sont tres belles. Un nuage de gaz [1]. Autre fait invente [99]. Deux faits [1, 2].",
                "explication": "Sans citation.", "limites": ""}

    result = answer.ask(f"nebuleuse {tag}", "official", embed_fn=fake_embed, chat_fn=chat)
    assert result.answer == "Un nuage de gaz [1]. Deux faits [1, 2]."  # ni la phrase sans citation, ni la citation [99] inexistante
    assert result.explanation is None


def test_an_answer_with_no_valid_citation_is_discarded_for_the_raw_data(world):
    tag = world["tag"]
    result = answer.ask(f"nebuleuse {tag}", "official", embed_fn=fake_embed,
                        chat_fn=lambda p, s: {"reponse": "Reponse inventee sans aucune citation.", "explication": "", "limites": ""})
    assert result.status == "repondu" and result.enrichment == "deterministe"
    assert result.answer.startswith("Voici ce que le système a enregistré") and "inventee" not in result.answer
    assert any("ne citait pas les données" in n for n in result.notes)


def test_model_failure_still_returns_the_stored_data(world):
    tag = world["tag"]

    def broken(prompt, system):
        raise TimeoutError("passerelle")

    result = answer.ask(f"nebuleuse {tag}", "official", embed_fn=fake_embed, chat_fn=broken)
    assert result.status == "repondu" and result.enrichment == "deterministe" and result.evidence
    assert any("indisponible" in n for n in result.notes)


def test_a_model_that_says_the_data_do_not_answer_gives_a_clear_admission(world):
    tag = world["tag"]
    result = answer.ask(f"Quelle est la masse de la nebuleuse {tag} ?", "official", embed_fn=fake_embed,
                        chat_fn=lambda p, s: {"reponse": "", "explication": "", "limites": "Aucune masse n'est donnee."})
    assert result.status == "donnees_insuffisantes" and result.answer is None
    assert "préfère ne pas répondre" in result.ignorance and "Aucune masse" in result.limits and result.evidence


def test_terms_without_any_stored_data_are_reported_in_the_limits(world):
    tag = world["tag"]
    result = answer.ask(f"nebuleuse {tag} masse", "official", embed_fn=fake_embed, chat_fn=cited_chat)
    assert "Aucune donnée enregistrée sur : masse" in result.limits


def test_embedding_failure_keeps_the_graph_route_and_says_so(world):
    tag = world["tag"]

    def down(_text):
        raise ConnectionError("passerelle arretee")

    result = answer.ask(f"nebuleuse {tag}", "official", embed_fn=down, chat_fn=cited_chat)
    assert result.status == "repondu" and result.routes == ["graphe"]
    assert any("vectorielle indisponible" in n for n in result.notes)


def test_scope_viewer_never_sees_provisional_classes_creator_does(world):
    tag, tag2 = world["tag"], world["tag2"]
    viewer = answer.ask(f"protoetoile {tag2}", "official", embed_fn=fake_embed, chat_fn=never_called)
    creator = answer.ask(f"protoetoile {tag2}", "all", embed_fn=fake_embed, chat_fn=cited_chat)
    assert viewer.status == "inconnu" and creator.status == "repondu"
    assert knowledge.class_detail(world["ids"]["provisional"], "official") is None
    assert knowledge.class_detail(world["ids"]["provisional"], "all").name == f"Classe prov {tag}"
    assert f"Classe prov {tag}" not in {c.name for c in knowledge.list_classes("official")}


def test_search_matches_needs_no_model_and_finds_concepts_entities_and_documents(world):
    tag = world["tag"]
    result = answer.search_matches(f"orion {tag}", "official")
    assert result.status == "repondu" and [m.kind for m in result.matches] == ["entite"]
    assert answer.search_matches("zzzzqqqq", "official").status == "inconnu"


def test_accents_and_case_do_not_matter_in_the_query(world):
    tag = world["tag"]
    result = answer.search_matches(f"NÉBULEUSE {tag}", "official")
    assert [m.label for m in result.matches] == [f"Nebuleuse {tag}"]


def test_the_query_is_data_not_cypher(world):
    result = answer.ask("') MATCH (n) DETACH DELETE n //", "official", embed_fn=fake_embed, chat_fn=never_called)
    assert result.status in ("inconnu", "requete_trop_courte")
    assert knowledge.list_classes("all")  # rien n'a ete supprime
