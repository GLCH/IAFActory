"""Client MCP du site <-> VRAI service d'exposition (serveur uvicorn en fil, SDK `mcp` des deux cotes, VRAI Neo4j).
Verifie le contrat de bout en bout : jeton, roles, forme des resultats, indisponibilite."""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "exposition"))

from app.config import settings  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.services import exposition_client as exposition  # noqa: E402
from exposition_service import server  # noqa: E402

TOKEN = "jeton-site-exposition-test"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def service(monkeypatch):
    port = _free_port()
    instance = uvicorn.Server(uvicorn.Config(server.build_app(TOKEN), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    monkeypatch.setattr(settings, "exposition_url", f"http://127.0.0.1:{port}/mcp")
    monkeypatch.setattr(settings, "exposition_token", TOKEN)
    yield port
    instance.should_exit = True
    thread.join(timeout=5)


def test_roles_are_mapped_and_admin_is_a_viewer():
    assert exposition.exposition_role(User(role=Role.creator)) == "creator"
    assert exposition.exposition_role(User(role=Role.viewer)) == "viewer"
    assert exposition.exposition_role(User(role=Role.admin)) == "viewer"


def test_list_classes_and_unknown_question_over_real_mcp(service):
    classes = exposition.list_classes("viewer")
    assert isinstance(classes, list) and all(c["status"] != "provisoire" for c in classes)
    answer = exposition.ask_knowledge("recette quantique de fondue zzzqqqx", "viewer")
    assert answer["status"] == "inconnu" and answer["answer"] is None and "aucune information" in answer["ignorance"]


def test_get_class_none_and_unknown_role_error_over_real_mcp(service):
    assert exposition.get_class("n-existe-pas", "creator") is None
    with pytest.raises(exposition.ExpositionError, match="caller_role"):
        exposition.list_classes("admin")


def test_a_wrong_token_is_refused_without_leaking_it(service, monkeypatch):
    monkeypatch.setattr(settings, "exposition_token", "mauvais-jeton-secret")
    with pytest.raises(exposition.ExpositionError) as raised:
        exposition.list_classes("viewer")
    assert "mauvais-jeton-secret" not in str(raised.value)


def test_missing_token_and_unreachable_service_give_clear_errors(monkeypatch):
    monkeypatch.setattr(settings, "exposition_token", "")
    with pytest.raises(exposition.ExpositionError, match="EXPOSITION_TOKEN"):
        exposition.list_classes("viewer")
    monkeypatch.setattr(settings, "exposition_token", TOKEN)
    monkeypatch.setattr(settings, "exposition_url", f"http://127.0.0.1:{_free_port()}/mcp")  # rien n'ecoute
    with pytest.raises(exposition.ExpositionError, match="injoignable"):
        exposition.list_classes("viewer")
