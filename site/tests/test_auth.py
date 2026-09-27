"""Tests US5.2 : refus de l'anonyme, role serveur, role non falsifiable."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.security import hash_password, make_session_token  # noqa: E402


class _FakeDB:
    def __init__(self, users: dict[uuid.UUID, User]):
        self.users = users

    def get(self, model, key):
        return self.users.get(key)


@pytest.fixture
def client(monkeypatch):
    user_id = uuid.uuid4()
    # is_active=True explicite : le defaut de colonne SQLAlchemy ne s'applique
    # qu'au flush dans une vraie session, pas a la construction Python nue
    # utilisee ici pour ce faux depot.
    user = User(id=user_id, email="v@example.com", hashed_password=hash_password("x" * 12),
                role=Role.viewer, is_active=True)
    fake_db = _FakeDB({user_id: user})

    from app import deps

    def fake_get_db():
        yield fake_db

    app.dependency_overrides[deps.get_db] = fake_get_db
    yield TestClient(app), user_id
    app.dependency_overrides.clear()


def test_dashboard_refuses_anonymous(client):
    c, _ = client
    r = c.get("/", follow_redirects=False)
    assert r.status_code in (303, 401)


def test_dashboard_accepts_valid_session(client):
    c, user_id = client
    token = make_session_token(user_id)
    c.cookies.set("iaf_session", token)
    r = c.get("/")
    assert r.status_code == 200
    assert "viewer" in r.text


def test_tampered_role_in_cookie_has_no_effect(client):
    """Le role vient de la base, pas du cookie : falsifier le cookie ne change
    rien puisqu'il ne contient que l'id utilisateur, pas le role."""
    c, user_id = client
    token = make_session_token(user_id)
    c.cookies.set("iaf_session", token + "tampered")
    r = c.get("/", follow_redirects=False)
    assert r.status_code in (303, 401)
