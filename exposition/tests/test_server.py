"""Serveur MCP du service d'exposition, de bout en bout : VRAI serveur (uvicorn, Streamable HTTP), VRAI client MCP
(SDK `mcp`), VRAI Neo4j. Verifie le jeton Bearer, les roles et la forme des resultats."""
from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx2
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from exposition_service import server
from exposition_service.store import get_driver

TOKEN = "jeton-de-test-exposition"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def running():
    port = _free_port()
    config = uvicorn.Config(server.build_app(TOKEN), host="127.0.0.1", port=port, log_level="warning")
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    instance.should_exit = True
    thread.join(timeout=5)


async def _call(base: str, tool: str, arguments: dict, token: str = TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx2.AsyncClient(headers=headers, timeout=30) as http:
        async with streamable_http_client(f"{base}/mcp", http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                if tool == "__list__":
                    return await session.list_tools()
                return await session.call_tool(tool, arguments)


def call(base, tool, arguments, token=TOKEN):
    return asyncio.run(_call(base, tool, arguments, token))


@pytest.fixture()
def provisional_class():
    cid = f"e19-test-srv-{int(time.time() * 1000)}"
    with get_driver().session() as session:
        session.run("CREATE (:DocumentClass {id: $c, name: $n, status: 'provisoire'})", c=cid, n=f"Provisoire srv {cid}")
    yield {"id": cid, "name": f"Provisoire srv {cid}"}
    with get_driver().session() as session:
        session.run("MATCH (c:DocumentClass {id: $c}) DETACH DELETE c", c=cid)


def test_the_server_refuses_to_start_without_a_token():
    with pytest.raises(RuntimeError, match="EXPOSITION_TOKEN"):
        server.build_app("")


def test_health_check_is_open_but_the_mcp_endpoint_needs_the_token(running):
    assert httpx2.get(f"{running}/healthz").status_code == 200
    assert httpx2.post(f"{running}/mcp", json={}).status_code == 401
    assert httpx2.post(f"{running}/mcp", json={}, headers={"Authorization": "Bearer faux"}).status_code == 401


def test_a_client_without_or_with_a_wrong_token_cannot_use_any_tool(running):
    for token in ("", "faux"):
        with pytest.raises(BaseException):
            call(running, "list_classes", {"caller_role": "creator"}, token=token)


def test_the_four_tools_are_published(running):
    tools = call(running, "__list__", {})
    assert {t.name for t in tools.tools} == {"ask_knowledge", "search_knowledge", "list_classes", "get_class"}


def test_roles_restrict_what_is_visible_through_the_protocol(running, provisional_class):
    viewer = call(running, "list_classes", {"caller_role": "viewer"}).structured_content["result"]
    creator = call(running, "list_classes", {"caller_role": "creator"}).structured_content["result"]
    assert provisional_class["name"] not in {c["name"] for c in viewer}
    assert provisional_class["name"] in {c["name"] for c in creator}
    assert all(c["status"] != "provisoire" for c in viewer)


def test_an_unknown_role_is_refused(running):
    for role in ("admin", "root", ""):
        result = call(running, "list_classes", {"caller_role": role})
        assert result.is_error and "caller_role" in result.content[0].text


def test_get_class_returns_the_detail_or_nothing(running, provisional_class):
    hidden = call(running, "get_class", {"class_id": provisional_class["id"], "caller_role": "viewer"})
    assert not hidden.is_error and not hidden.structured_content.get("result")
    shown = call(running, "get_class", {"class_id": provisional_class["id"], "caller_role": "creator"})
    assert shown.structured_content["result"]["name"] == provisional_class["name"]


def test_ask_knowledge_admits_ignorance_over_the_protocol_without_the_model(running):
    result = call(running, "ask_knowledge", {"question": "recette quantique de fondue zzzqqqx", "caller_role": "viewer"})
    data = result.structured_content
    assert data["status"] == "inconnu" and data["answer"] is None and "aucune information" in data["ignorance"]
    assert data["evidence"] == []
