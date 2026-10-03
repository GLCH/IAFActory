"""EPIC-IAF-E18 : secrets chiffres, garde reseau, connecteur OpenRiC (reponses SIMULEES, type en pause
en production depuis le 2026-10-03), cycle de vie brouillon -> en attente -> approuve et audit. Google
Drive a ete retire (pas assez mature). Les tests de routes utilisent le VRAI Postgres (tables creees par
alembic) avec des comptes jetables supprimes a la fin ; la mise en file d'ingestion est remplacee
par un espion pour ne rien deposer dans le pipeline."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete as sa_delete, select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import net_guard, secret_store  # noqa: E402
from app.config import settings  # noqa: E402
from app.connectors import CATALOG, openric  # noqa: E402
from app.connectors.base import ConnectorError, RemoteFile, safe_filename  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Connector, ConnectorEvent, ConnectorStatus, Role, User  # noqa: E402
from app.security import hash_password, make_session_token  # noqa: E402

SENTINEL = "SENTINELLE-123-ne-doit-jamais-apparaitre"


@pytest.fixture()
def master_key(monkeypatch):
    key = secret_store.generate_master_key()
    monkeypatch.setattr(settings, "connector_master_key", key)
    monkeypatch.setattr(settings, "connector_master_key_file", "")
    return key


# --- secrets -----------------------------------------------------------------------------------

def test_secret_roundtrip_and_blob_does_not_contain_plaintext(master_key):
    blob = secret_store.encrypt_secret(SENTINEL, aad=b"connecteur-1")
    assert SENTINEL.encode() not in blob
    assert secret_store.decrypt_secret(blob, aad=b"connecteur-1") == SENTINEL


def test_secret_is_bound_to_its_connector(master_key):
    blob = secret_store.encrypt_secret(SENTINEL, aad=b"connecteur-1")
    with pytest.raises(secret_store.SecretStoreError):
        secret_store.decrypt_secret(blob, aad=b"connecteur-2")


def test_secret_tampering_and_other_master_key_are_refused(master_key, monkeypatch):
    blob = bytearray(secret_store.encrypt_secret(SENTINEL))
    blob[-1] ^= 1
    with pytest.raises(secret_store.SecretStoreError):
        secret_store.decrypt_secret(bytes(blob))
    good = secret_store.encrypt_secret(SENTINEL)
    monkeypatch.setattr(settings, "connector_master_key", secret_store.generate_master_key())
    with pytest.raises(secret_store.SecretStoreError):
        secret_store.decrypt_secret(good)


def test_secret_store_refuses_without_or_with_invalid_master_key(monkeypatch):
    monkeypatch.setattr(settings, "connector_master_key", "")
    monkeypatch.setattr(settings, "connector_master_key_file", "")
    assert not secret_store.master_key_available()
    with pytest.raises(secret_store.SecretStoreError, match="cle maitre absente"):
        secret_store.encrypt_secret("x")
    monkeypatch.setattr(settings, "connector_master_key", "AAAA")
    with pytest.raises(secret_store.SecretStoreError, match="32 octets"):
        secret_store.encrypt_secret("x")


def test_master_key_can_come_from_a_mounted_file(monkeypatch, tmp_path):
    key_file = tmp_path / "master.key"
    key_file.write_text(secret_store.generate_master_key(), encoding="utf-8")
    monkeypatch.setattr(settings, "connector_master_key", "")
    monkeypatch.setattr(settings, "connector_master_key_file", str(key_file))
    assert secret_store.decrypt_secret(secret_store.encrypt_secret("ok")) == "ok"


# --- garde reseau ------------------------------------------------------------------------------

def _resolve_to(monkeypatch, *addresses):
    monkeypatch.setattr(net_guard.socket, "getaddrinfo", lambda host, port, **kw: [(2, 1, 6, "", (a, port)) for a in addresses])


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "172.16.0.1", "::1", "::ffff:127.0.0.1", "0.0.0.0"])
def test_guard_refuses_non_public_addresses(monkeypatch, address):
    _resolve_to(monkeypatch, address)
    with pytest.raises(net_guard.NetworkRefused):
        net_guard.validate_url("https://exemple.test/x", {"exemple.test"})


def test_guard_refuses_if_any_resolved_address_is_private(monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34", "10.1.2.3")
    with pytest.raises(net_guard.NetworkRefused):
        net_guard.validate_url("https://exemple.test/x", {"exemple.test"})


def test_guard_refuses_http_and_hosts_outside_the_allowlist(monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    with pytest.raises(net_guard.NetworkRefused, match="https"):
        net_guard.validate_url("http://exemple.test/x", {"exemple.test"})
    with pytest.raises(net_guard.NetworkRefused, match="liste blanche"):
        net_guard.validate_url("https://autre.test/x", {"exemple.test"})
    assert net_guard.validate_url("https://exemple.test/x", {"exemple.test"}) == "exemple.test"


class _FakeResponse:
    def __init__(self, status_code=200, headers=None, body=b"{}"):
        self.status_code, self.headers, self._body = status_code, headers or {}, body

    def iter_content(self, chunk_size=65536):
        yield self._body

    def close(self):
        pass


def test_guard_revalidates_every_redirect(monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    monkeypatch.setattr(net_guard.requests, "request", lambda *a, **k: _FakeResponse(302, {"Location": "http://127.0.0.1:7474/"}))
    with pytest.raises(net_guard.NetworkRefused, match="https"):
        net_guard.guarded_request("GET", "https://exemple.test/x", {"exemple.test"})
    monkeypatch.setattr(net_guard.requests, "request", lambda *a, **k: _FakeResponse(302, {"Location": "https://interne.test/"}))
    with pytest.raises(net_guard.NetworkRefused, match="liste blanche"):
        net_guard.guarded_request("GET", "https://exemple.test/x", {"exemple.test"})


def test_guard_limits_redirect_hops_and_response_size(monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    monkeypatch.setattr(net_guard.requests, "request", lambda *a, **k: _FakeResponse(302, {"Location": "https://exemple.test/loop"}))
    with pytest.raises(net_guard.NetworkRefused, match="redirections"):
        net_guard.guarded_request("GET", "https://exemple.test/x", {"exemple.test"})
    monkeypatch.setattr(net_guard.requests, "request", lambda *a, **k: _FakeResponse(200, {}, b"x" * 100))
    with pytest.raises(net_guard.NetworkRefused, match="volumineuse"):
        net_guard.guarded_request("GET", "https://exemple.test/x", {"exemple.test"}, max_bytes=10)
    response = net_guard.guarded_request("GET", "https://exemple.test/x", {"exemple.test"})
    assert response.ok and len(response.content) == 100


# --- OpenRiC -----------------------------------------------------------------------------------

# Reponse reelle de GET /records/title-of-object sur l'instance de reference (2026-10-03), abregee.
OPENRIC_RECORD = {
    "@id": "https://ric.theahg.co.za/informationobject/title-of-object", "@type": "https://www.ica.org/standards/RiC/ontology#Record",
    "rico:type": "Record", "rico:title": "Title of object", "rico:identifier": "2025-11-24/146", "openricx:description": "Description",
    "rico:hasExtent": {"@type": "rico:Extent", "rico:hasExtentType": "1x2x3"},
    "rico:hasOrHadInstantiation": [{"@type": "rico:Instantiation", "rico:identifier": "marble_statue_ultra_high_res.tiff",
        "openricx:hasMimeType": "image/tiff", "rico:hasExtent": {"@type": "rico:Extent", "rico:quantity": 75504986, "rico:hasExtentType": "bytes"}}],
}


def test_openric_record_becomes_a_markdown_document():
    markdown = openric.record_to_markdown(OPENRIC_RECORD, fallback_title="title-of-object")
    assert markdown.startswith("# Title of object")
    assert "Identifiant : 2025-11-24/146" in markdown and "## Description" in markdown and "image/tiff" in markdown


def test_openric_config_requires_https():
    assert openric.validate_config({"base_url": "https://ric.theahg.co.za/api/ric/v1/"}) == {"base_url": "https://ric.theahg.co.za/api/ric/v1"}
    with pytest.raises(ConnectorError):
        openric.validate_config({"base_url": "http://ric.theahg.co.za/api"})


def test_openric_fetch_files_lists_then_reads_each_record(monkeypatch):
    calls = []

    def fake_request(method, url, hosts, **kwargs):
        calls.append((url, hosts))
        if url.endswith("/records"):
            body = {"ric:items": [{"@id": "https://x/informationobject/title-of-object"}, {"@id": "https://x/informationobject/casse"}]}
        elif url.endswith("/title-of-object"):
            body = OPENRIC_RECORD
        else:
            return net_guard.GuardedResponse(500, {}, b"", url)
        return net_guard.GuardedResponse(200, {}, json.dumps(body).encode(), url)

    monkeypatch.setattr(openric, "guarded_request", fake_request)
    files, errors = openric.fetch_files({"base_url": "https://ric.theahg.co.za/api/ric/v1"}, None, 5)
    assert [f.filename for f in files] == ["title-of-object.md"] and files[0].content_type == "text/markdown"
    assert errors == ["casse : HTTP 500"]  # une notice en echec n'interrompt pas la synchronisation
    assert all(hosts == {"ric.theahg.co.za"} for _url, hosts in calls)


def test_openric_network_refusal_is_reported_without_leaking(monkeypatch):
    def refuse(*a, **k):
        raise net_guard.NetworkRefused("adresse non publique refusee : 10.0.0.1")

    monkeypatch.setattr(openric, "guarded_request", refuse)
    with pytest.raises(ConnectorError, match="garde reseau"):
        openric.fetch_files({"base_url": "https://ric.theahg.co.za/api/ric/v1"}, None, 2)


def test_safe_filename_strips_paths_and_special_characters():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("a b\\c:d.docx") == "c-d.docx"
    assert safe_filename("???") == "document"


# --- routes : etats, droits, audit ----------------------------------------------------------------

@pytest.fixture()
def world(master_key, monkeypatch):
    """Deux creators et un admin jetables dans le vrai Postgres ; ingestion remplacee par un espion."""
    from app.routers import connectors as router_module

    deposited = []
    monkeypatch.setitem(CATALOG["openric_api"], "enabled", True)  # type en pause en production, ouvert pour tester le cycle de vie
    monkeypatch.setattr(router_module, "_save_and_enqueue", lambda raw, name, ctype, user, db: deposited.append(name))
    db = SessionLocal()
    tag = uuid.uuid4().hex[:8]
    users = {}
    for role, key in ((Role.creator, "a"), (Role.creator, "b"), (Role.admin, "admin")):
        user = User(email=f"e18-{key}-{tag}@iafactory.test", hashed_password=hash_password("x" * 14), role=role, is_active=True)
        db.add(user)
        users[key] = user
    db.commit()

    def client_for(key):
        client = TestClient(app, follow_redirects=False)
        client.cookies.set("iaf_session", make_session_token(users[key].id))
        return client

    yield {"db": db, "users": users, "client": client_for, "deposited": deposited}
    ids = [c.id for c in db.scalars(select(Connector).where(Connector.owner_id.in_([u.id for u in users.values()])))]
    db.execute(sa_delete(ConnectorEvent).where(ConnectorEvent.connector_id.in_(ids)))
    db.execute(sa_delete(Connector).where(Connector.owner_id.in_([u.id for u in users.values()])))
    for user in users.values():
        db.delete(user)
    db.commit()
    db.close()


def _create_openric(world, who="a"):
    response = world["client"](who).post("/creator/connectors", data={
        "type": "openric_api", "name": "Archives test", "base_url": "https://ric.theahg.co.za/api/ric/v1",
    })
    assert response.status_code == 303
    return uuid.UUID(response.headers["location"].rsplit("/", 1)[-1])


def test_unapproved_connector_cannot_sync_and_only_its_owner_sees_it(world):
    connector_id = _create_openric(world)
    assert world["client"]("a").post(f"/creator/connectors/{connector_id}/sync").status_code == 409  # brouillon
    assert world["client"]("b").get(f"/creator/connectors/{connector_id}").status_code == 404
    assert world["client"]("b").post(f"/creator/connectors/{connector_id}/sync").status_code == 404
    assert world["client"]("a").post(f"/creator/connectors/{connector_id}/request-activation").status_code == 303
    assert world["client"]("a").post(f"/creator/connectors/{connector_id}/sync").status_code == 409  # en attente
    assert world["client"]("a").post(f"/creator/connectors/{connector_id}/request-activation").status_code == 409


def test_only_an_admin_decides_and_a_rejection_needs_a_reason(world):
    connector_id = _create_openric(world)
    world["client"]("a").post(f"/creator/connectors/{connector_id}/request-activation")
    assert world["client"]("a").post(f"/admin/connectors/{connector_id}/decide", data={"decision": "approve"}).status_code == 403
    assert world["client"]("admin").post(f"/admin/connectors/{connector_id}/decide", data={"decision": "reject"}).status_code == 422
    assert world["client"]("admin").post(f"/admin/connectors/{connector_id}/decide", data={"decision": "reject", "reason": "source non fiable"}).status_code == 303
    world["db"].expire_all()
    connector = world["db"].get(Connector, connector_id)
    assert connector.status == ConnectorStatus.rejete and connector.decision_reason == "source non fiable"
    # un connecteur rejete peut etre redemande, et une nouvelle decision est exigee
    assert world["client"]("a").post(f"/creator/connectors/{connector_id}/request-activation").status_code == 303
    assert world["client"]("admin").post(f"/admin/connectors/{connector_id}/decide", data={"decision": "approve"}).status_code == 303
    assert world["client"]("admin").post(f"/admin/connectors/{connector_id}/decide", data={"decision": "approve"}).status_code == 409


def test_approved_connector_syncs_through_the_deposit_path_and_is_audited(world, monkeypatch):
    connector_id = _create_openric(world)
    world["client"]("a").post(f"/creator/connectors/{connector_id}/request-activation")
    world["client"]("admin").post(f"/admin/connectors/{connector_id}/decide", data={"decision": "approve"})
    monkeypatch.setattr(
        openric, "fetch_files",
        lambda config, secret, limit: ([RemoteFile("n1.md", b"# t\n\ncorps", "text/markdown", "id1")], ["avertissement"]),
    )
    assert world["client"]("a").post(f"/creator/connectors/{connector_id}/sync").status_code == 303
    assert world["deposited"] == ["n1.md"]
    world["db"].expire_all()
    actions = [e.action for e in world["db"].scalars(select(ConnectorEvent).where(ConnectorEvent.connector_id == connector_id))]
    assert {"created", "activation_requested", "approved", "sync"} <= set(actions)
    assert "1 document(s) mis en file" in world["db"].get(Connector, connector_id).last_sync_summary


def test_secret_is_never_displayed_and_revocation_destroys_it(world):
    connector_id = _create_openric(world)
    connector = world["db"].get(Connector, connector_id)
    connector.secret_encrypted = secret_store.encrypt_secret(SENTINEL, aad=str(connector_id).encode())
    connector.status = ConnectorStatus.approuve
    world["db"].commit()
    page = world["client"]("a").get(f"/creator/connectors/{connector_id}")
    assert page.status_code == 200 and "defini (jamais affiche)" in page.text and SENTINEL not in page.text
    assert world["client"]("a").post(f"/creator/connectors/{connector_id}/revoke").status_code == 303
    world["db"].expire_all()
    connector = world["db"].get(Connector, connector_id)
    assert connector.secret_encrypted is None and connector.status == ConnectorStatus.brouillon
    events = " ".join(f"{e.action} {e.detail}" for e in world["db"].scalars(select(ConnectorEvent).where(ConnectorEvent.connector_id == connector_id)))
    assert SENTINEL not in events and "revoked" in events




def test_guard_identifies_the_client_with_a_descriptive_user_agent(monkeypatch):
    _resolve_to(monkeypatch, "93.184.216.34")
    seen = {}

    def spy(method, url, **kwargs):
        seen.update(kwargs["headers"])
        return _FakeResponse(200, {}, b"{}")

    monkeypatch.setattr(net_guard.requests, "request", spy)
    net_guard.guarded_request("GET", "https://exemple.test/x", {"exemple.test"}, headers={"Accept": "application/json"})
    assert seen["User-Agent"].startswith("IAFActory-connector/") and seen["Accept"] == "application/json"


def test_paused_openric_type_cannot_be_created_requested_approved_or_synced(world, monkeypatch):
    """Decision du 2026-10-03 : OpenRiC en pause, aucun acces aux donnees tant que le comment et le quand
    ne sont pas definis. Le catalogue de production est teste tel quel (enabled False)."""
    connector_id = _create_openric(world)  # cree alors que le type est ouvert par la fixture
    monkeypatch.setitem(CATALOG["openric_api"], "enabled", False)
    refused = world["client"]("a").post("/creator/connectors", data={
        "type": "openric_api", "name": "x", "base_url": "https://ric.theahg.co.za/api/ric/v1"})
    assert refused.status_code == 422 and "en pause" in refused.text
    assert world["client"]("a").post(f"/creator/connectors/{connector_id}/request-activation").status_code == 409
    connector = world["db"].get(Connector, connector_id)
    connector.status = ConnectorStatus.en_attente
    world["db"].commit()
    assert world["client"]("admin").post(f"/admin/connectors/{connector_id}/decide", data={"decision": "approve"}).status_code == 409
    connector.status = ConnectorStatus.approuve
    world["db"].commit()
    called = []
    monkeypatch.setattr(openric, "fetch_files", lambda *a, **k: called.append(1) or ([], []))
    assert world["client"]("a").post(f"/creator/connectors/{connector_id}/sync").status_code == 409
    assert called == [] and world["deposited"] == []  # aucune requete vers la source


def test_production_catalog_has_no_google_drive_and_openric_is_paused():
    from app.connectors import CATALOG as production, is_enabled

    assert "google_drive" not in production
    assert "openric_api" in production and not is_enabled("openric_api")


def test_connectors_are_reached_from_the_settings_page_not_the_navbar(world):
    _create_openric(world)
    settings_page = world["client"]("a").get("/creator/settings")
    assert settings_page.status_code == 200 and "Archives test" in settings_page.text and "/creator/connectors" in settings_page.text
    dashboard = world["client"]("a").get("/")
    assert 'href="/creator/connectors"' not in dashboard.text  # plus de lien direct dans la barre de navigation
