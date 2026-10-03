"""Client MCP du service d'exposition (EPIC-IAF-E19, ADR 0009, decisions de l'utilisateur du 2026-10-03 :
MCP, services separes). Le site n'ouvre plus Neo4j pour lire les connaissances : il appelle ces fonctions.

Chaque appel ouvre une session MCP (Streamable HTTP, jeton Bearer `EXPOSITION_TOKEN`), appelle un outil et la
referme : simple et sans etat, adapte au rythme d'une page. Le site transmet le ROLE de l'utilisateur connecte
(`viewer` ou `creator`) ; l'administrateur est traite comme un viewer (classes officielles seulement).

Les routes du site sont synchrones (executees dans un fil sans boucle asyncio) : `asyncio.run` est donc sur."""
from __future__ import annotations

import asyncio

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from ..config import settings
from ..models import Role, User

CALL_TIMEOUT_SECONDS = 120  # une reponse enrichie appelle un modele (quelques secondes, plus si la passerelle est lente)


class ExpositionError(RuntimeError):
    """Le service d'exposition a refuse l'appel (echec anticipe) ou est injoignable. Message sans secret."""


def exposition_role(user: User) -> str:
    return "creator" if user.role == Role.creator else "viewer"


async def _call_async(tool: str, arguments: dict):
    if not settings.exposition_token:
        raise ExpositionError("jeton du service d'exposition absent (EXPOSITION_TOKEN) : service non configure")
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {settings.exposition_token}"}, timeout=CALL_TIMEOUT_SECONDS) as http:
        async with streamable_http_client(settings.exposition_url, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                return await session.call_tool(tool, arguments)


def _call(tool: str, arguments: dict):
    try:
        result = asyncio.run(_call_async(tool, arguments))
    except ExpositionError:
        raise
    except BaseException as exc:  # ExceptionGroup du transport, refus de connexion, jeton refuse (401)...
        raise ExpositionError(f"service d'exposition injoignable ou refuse ({type(exc).__name__})") from None
    if result.is_error:
        text = result.content[0].text if result.content else "erreur"
        raise ExpositionError(f"le service d'exposition a refuse l'appel : {text}")
    return result.structured_content


def ask_knowledge(question: str, role: str) -> dict:
    """Reponse ancree sur les donnees enregistrees (voir exposition_service.answer) : dict de `Answer`."""
    return _call("ask_knowledge", {"question": question, "caller_role": role})


def search_knowledge(query: str, role: str) -> dict:
    return _call("search_knowledge", {"query": query, "caller_role": role})


def list_classes(role: str) -> list[dict]:
    return (_call("list_classes", {"caller_role": role}) or {}).get("result", [])


def get_class(class_id: str, role: str) -> dict | None:
    return (_call("get_class", {"class_id": class_id, "caller_role": role}) or {}).get("result")
