"""EPIC-IAF-E19 : pages communes de connaissances et de questions (viewer et creator). Le SERVICE D'EXPOSITION est
remplace par un faux client (la logique de recherche et d'ancrage est testee dans exposition/tests, contre un vrai
Neo4j) : ici on verifie ce que le SITE fait de la reponse : roles transmis, panneaux de reponse et d'ignorance,
indisponibilite du service, connecteurs approuves visibles. VRAI Postgres, comptes jetables."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete as sa_delete

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Connector, ConnectorEvent, ConnectorStatus, Role, User  # noqa: E402
from app.security import hash_password, make_session_token  # noqa: E402
from app.services import exposition_client as exposition  # noqa: E402

CLASSES = [
    {"id": "c-off", "name": "Astronomie", "status": "officielle", "documents": 5, "concepts": 32, "entities": 100},
    {"id": "c-prov", "name": "Provisoire - droit", "status": "provisoire", "documents": 1, "concepts": 5, "entities": 3},
]
EVIDENCE = [
    {"n": 1, "kind": "definition", "route": "graphe", "text": "Trou noir : objet de densite extreme.",
     "source": {"class_id": "c-off", "class_name": "Astronomie", "document": None, "section": None}},
    {"n": 2, "kind": "passage", "route": "rag", "text": "Un trou noir est defini par son horizon.",
     "source": {"class_id": "c-off", "class_name": "Astronomie", "document": "astro-1.docx", "section": "Introduction"}},
]
ANSWERED = {
    "query": "trou noir", "status": "repondu", "answer": "Un trou noir est un objet tres dense [1].", "explanation": "Defini par son horizon [2].",
    "limits": "Aucune donnee enregistree sur : masse.", "ignorance": None, "evidence": EVIDENCE, "routes": ["graphe", "rag"],
    "matches": [{"kind": "concept", "label": "Trou noir", "detail": "objet dense", "class_id": "c-off", "class_name": "Astronomie"}],
    "terms": ["trou", "noir"], "enrichment": "llm", "notes": [],
}
UNKNOWN = {"query": "orion", "status": "inconnu", "answer": None, "explanation": None, "limits": None,
           "ignorance": "Le système n'a aucune information enregistrée sur « orion ».", "evidence": [], "routes": [],
           "matches": [], "terms": ["orion"], "enrichment": "non_applicable", "notes": []}


@pytest.fixture()
def fake_exposition(monkeypatch):
    """Faux service d'exposition : enregistre les appels (outil, role) et renvoie des reponses programmables."""
    state = {"calls": [], "answer": ANSWERED, "fail": None, "detail": {"id": "c-off", "name": "Astronomie", "status": "officielle",
             "concepts": [{"label": "Trou noir", "definition": "objet dense", "support": 2}],
             "entity_types": [{"type": "objet celeste", "count": 3, "examples": ["Soleil"]}],
             "documents": [{"filename": "astro-1.docx", "title": None, "language": "fr", "ingested_at": "2026-10-03T10:00"}]}}

    def guard(name, role):
        state["calls"].append((name, role))
        if state["fail"]:
            raise exposition.ExpositionError(state["fail"])

    def list_classes(role):
        guard("list_classes", role)
        return [c for c in CLASSES if role == "creator" or c["status"] != "provisoire"]

    def ask_knowledge(question, role):
        guard("ask_knowledge", role)
        return state["answer"]

    def get_class(class_id, role):
        guard("get_class", role)
        return state["detail"] if class_id == "c-off" else None

    monkeypatch.setattr(exposition, "list_classes", list_classes)
    monkeypatch.setattr(exposition, "ask_knowledge", ask_knowledge)
    monkeypatch.setattr(exposition, "get_class", get_class)
    return state


@pytest.fixture()
def accounts():
    db = SessionLocal()
    tag = uuid.uuid4().hex[:8]
    users = {}
    for role in (Role.viewer, Role.creator, Role.admin):
        user = User(email=f"e19-{role.value}-{tag}@iafactory.test", hashed_password=hash_password("x" * 14), role=role, is_active=True)
        db.add(user)
        users[role.value] = user
    db.commit()

    def client_for(role):
        client = TestClient(app, follow_redirects=False)
        client.cookies.set("iaf_session", make_session_token(users[role].id))
        return client

    yield {"db": db, "users": users, "client": client_for}
    ids = [u.id for u in users.values()]
    connector_ids = [c.id for c in db.query(Connector).filter(Connector.owner_id.in_(ids))]
    db.execute(sa_delete(ConnectorEvent).where(ConnectorEvent.connector_id.in_(connector_ids)))
    db.execute(sa_delete(Connector).where(Connector.owner_id.in_(ids)))
    for user in users.values():
        db.delete(user)
    db.commit()
    db.close()


def test_page_is_open_to_viewer_and_creator_but_not_to_admin_or_anonymous(accounts, fake_exposition):
    assert accounts["client"]("viewer").get("/knowledge").status_code == 200
    assert accounts["client"]("creator").get("/knowledge").status_code == 200
    assert accounts["client"]("admin").get("/knowledge").status_code == 403
    assert TestClient(app, follow_redirects=False).get("/knowledge").status_code in (401, 303)


def test_the_site_forwards_the_real_role_and_admin_is_treated_as_viewer(accounts, fake_exposition):
    accounts["client"]("viewer").get("/knowledge?q=trou+noir")
    accounts["client"]("creator").get("/knowledge?q=trou+noir")
    accounts["client"]("admin").post("/ask", data={"question": "trou noir"})
    roles = [role for name, role in fake_exposition["calls"] if name == "ask_knowledge"]
    assert roles == ["viewer", "creator", "viewer"]


def test_viewer_sees_official_classes_only_and_creator_the_provisional_ones_too(accounts, fake_exposition):
    assert "Provisoire - droit" not in accounts["client"]("viewer").get("/knowledge").text
    creator_page = accounts["client"]("creator").get("/knowledge").text
    assert "Astronomie" in creator_page and "Provisoire - droit" in creator_page


def test_an_answer_shows_the_extracted_knowledge_the_explanation_the_limits_and_every_source(accounts, fake_exposition):
    page = accounts["client"]("viewer").get("/knowledge?q=trou+noir").text
    assert "Savoir extrait du systeme" in page and "Un trou noir est un objet tres dense [1]." in page
    assert "Defini par son horizon [2]." in page and "Aucune donnee enregistree sur : masse." in page
    assert "graphe semantique" in page and "RAG (passages)" in page
    assert "astro-1.docx / Introduction" in page and "passage de document" in page
    assert "Correspondances" in page


def test_ignorance_is_shown_plainly_without_any_invented_answer(accounts, fake_exposition):
    fake_exposition["answer"] = UNKNOWN
    page = accounts["client"]("viewer").get("/knowledge?q=orion").text
    assert "Le systeme ne sait pas" in page and "aucune information enregistrée sur « orion »" in page
    assert "Savoir extrait du systeme" not in page and "Donnees utilisees" not in page


def test_insufficient_data_shows_the_neighbouring_data_as_not_answering(accounts, fake_exposition):
    fake_exposition["answer"] = {**ANSWERED, "status": "donnees_insuffisantes", "answer": None, "explanation": None,
                                 "ignorance": "Le système a des données voisines mais aucune ne répond.", "limits": "Aucune couleur n'est donnee."}
    page = accounts["client"]("viewer").get("/knowledge?q=couleur").text
    assert "Le systeme ne sait pas" in page and "Donnees voisines (ne repondent pas a la question)" in page
    assert "Aucune couleur n&#39;est donnee." in page and "Savoir extrait du systeme" not in page


def test_the_ask_page_uses_the_same_service_and_panel(accounts, fake_exposition):
    response = accounts["client"]("viewer").post("/ask", data={"question": "Qu'est-ce qu'un trou noir ?"})
    assert response.status_code == 200 and "Savoir extrait du systeme" in response.text
    assert accounts["client"]("viewer").post("/ask", data={"question": "  "}).status_code == 422
    fake_exposition["answer"] = UNKNOWN
    assert "Le systeme ne sait pas" in accounts["client"]("viewer").post("/ask", data={"question": "orion"}).text


def test_an_unavailable_service_gives_a_clear_503_and_the_page_still_renders(accounts, fake_exposition):
    fake_exposition["fail"] = "service d'exposition injoignable ou refuse (ConnectError)"
    page = accounts["client"]("viewer").get("/knowledge")
    assert page.status_code == 503 and "injoignable" in page.text and "Sources connectees" in page.text
    ask = accounts["client"]("viewer").post("/ask", data={"question": "trou noir"})
    assert ask.status_code == 503 and "injoignable" in ask.text
    assert accounts["client"]("viewer").get("/knowledge/classes/c-off").status_code == 503


def test_class_page_shows_definitions_and_only_creators_get_the_edit_link(accounts, fake_exposition):
    viewer = accounts["client"]("viewer").get("/knowledge/classes/c-off")
    creator = accounts["client"]("creator").get("/knowledge/classes/c-off")
    assert "objet dense" in viewer.text and "Soleil" in viewer.text and "astro-1.docx" in viewer.text
    assert "/creator/classes/c-off" not in viewer.text and "/creator/classes/c-off" in creator.text
    assert accounts["client"]("viewer").get("/knowledge/classes/inconnue").status_code == 404


def test_viewer_cannot_reach_connector_management(accounts, fake_exposition):
    assert accounts["client"]("viewer").get("/creator/connectors").status_code == 403
    assert accounts["client"]("viewer").get("/creator/settings").status_code == 403


def test_only_approved_connectors_are_listed_and_without_config_or_owner(accounts, fake_exposition):
    db, creator = accounts["db"], accounts["users"]["creator"]
    for name, status in (("Source approuvee", ConnectorStatus.approuve), ("Source brouillon", ConnectorStatus.brouillon),
                         ("Source en attente", ConnectorStatus.en_attente)):
        db.add(Connector(owner_id=creator.id, type="openric_api", name=name, status=status,
                         config={"base_url": "https://hote-secret-config.test/api"}))
    db.commit()
    page = accounts["client"]("viewer").get("/knowledge").text
    assert "Source approuvee" in page and "Source brouillon" not in page and "Source en attente" not in page
    assert "hote-secret-config.test" not in page and creator.email not in page
