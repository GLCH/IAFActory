"""Serveur MCP du service d'exposition (Streamable HTTP, SDK `mcp` 2.3.0, EPIC-IAF-E19, ADR 0009).

Outils (contrat typé, schéma publié par le protocole) :
- `ask_knowledge(question, caller_role)` : recherche + réponse ANCRÉE sur les données enregistrées, avec
  preuves numérotées, voie (graphe/rag) et aveu d'ignorance quand les données manquent ;
- `search_knowledge(query, caller_role)` : correspondances seules (concepts, entités, documents), sans modèle ;
- `list_classes(caller_role)`, `get_class(class_id, caller_role)` : navigation.

Sécurité : (1) jeton Bearer obligatoire sur tout appel MCP (`EXPOSITION_TOKEN`), comparé en temps constant ;
le serveur refuse de démarrer sans jeton ; (2) `caller_role` est le rôle de l'appelant tel que le site l'a
authentifié, et seuls `viewer` et `creator` sont acceptés : un viewer ne voit que les classes officielles.
Le rôle n'est pas prouvé cryptographiquement : le jeton prouve que l'appelant est le site, qui reste
responsable de ne transmettre que le rôle réel de l'utilisateur connecté (limite documentée, ADR 0009).
Le service n'écrit jamais dans les magasins."""
from __future__ import annotations

import hmac

import uvicorn
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from . import answer, knowledge
from .config import settings
from .models import Answer, ClassDetail, ClassSummary

ROLE_SCOPES = {"viewer": "official", "creator": "all"}

mcp = MCPServer(
    "iaf-exposition",
    instructions=(
        "Acces aux connaissances d'IAFActory. `ask_knowledge` repond uniquement a partir des donnees "
        "enregistrees et dit explicitement quand le systeme ne sait pas."
    ),
)


def scope_for(caller_role: str) -> str:
    if caller_role not in ROLE_SCOPES:
        # ToolError = echec anticipe : le message atteint l'appelant (une autre exception est masquee par le SDK)
        raise ToolError("caller_role inconnu : 'viewer' ou 'creator' attendu")
    return ROLE_SCOPES[caller_role]


@mcp.tool()
def ask_knowledge(question: str, caller_role: str) -> Answer:
    """Retourne le savoir extrait du systeme pour une question : donnees du graphe semantique (concepts,
    definitions, hierarchie, attributs, relations) et/ou passages de documents (RAG), avec une reponse et une
    explication fondees UNIQUEMENT sur ces donnees, chaque phrase citant ses sources [n]. Sans donnee, ou avec
    un simple terme sans detail, le statut vaut inconnu ou terme_sans_detail et `ignorance` le dit."""
    return answer.ask(question, scope_for(caller_role))


@mcp.tool()
def search_knowledge(query: str, caller_role: str) -> Answer:
    """Correspondances seules (concepts, entites, documents) pour un texte, sans reponse redigee."""
    return answer.search_matches(query, scope_for(caller_role))


@mcp.tool()
def list_classes(caller_role: str) -> list[ClassSummary]:
    """Classes de documents visibles pour ce role, avec leurs effectifs."""
    return knowledge.list_classes(scope_for(caller_role))


@mcp.tool()
def get_class(class_id: str, caller_role: str) -> ClassDetail | None:
    """Detail d'une classe visible : concepts avec definitions, types d'entites, documents. None sinon."""
    return knowledge.class_detail(class_id, scope_for(caller_role))


class BearerAuth(BaseHTTPMiddleware):
    """Refuse tout appel sans `Authorization: Bearer <EXPOSITION_TOKEN>` (sauf /healthz)."""

    def __init__(self, app, token: str):
        super().__init__(app)
        self._expected = f"Bearer {token}".encode("utf-8")

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        supplied = request.headers.get("authorization", "").encode("utf-8")
        if not hmac.compare_digest(supplied, self._expected):
            return PlainTextResponse("jeton absent ou invalide", status_code=401)
        return await call_next(request)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request):
    return PlainTextResponse("ok")


def build_app(token: str | None = None):
    token = settings.exposition_token if token is None else token
    if not token:
        raise RuntimeError("EXPOSITION_TOKEN absent : le service d'exposition refuse de demarrer sans jeton")
    # La protection contre le "DNS rebinding" du SDK ne vaut que pour un serveur local lie a localhost ;
    # ici le service n'est joignable que depuis le reseau interne Docker (Host = nom du service) et protege
    # par le jeton.
    app = mcp.streamable_http_app(
        host="0.0.0.0", stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    app.add_middleware(BearerAuth, token=token)
    return app


def main() -> None:
    uvicorn.run(build_app(), host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
