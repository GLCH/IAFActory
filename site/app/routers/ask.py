"""US3.5 : le viewer (et le creator) posent une question. Depuis le 2026-10-03 (ADR 0009, decision de
l'utilisateur) la reponse vient du SERVICE D'EXPOSITION (client MCP) : savoir extrait du systeme, voie graphe
semantique et/ou RAG, reponse expliquee mais ancree sur les donnees enregistrees, aveu d'ignorance quand il n'y a
rien ou seulement un terme sans detail. Cette route n'ouvre aucun magasin et n'appelle plus de modele.

Historique : l'ancienne version renvoyait toujours les 5 passages les plus proches, meme hors sujet (le score
vectoriel ne discrimine pas la pertinence, mesure du 2026-10-03), sans tenir compte de la visibilite des classes."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.templating import Jinja2Templates

from ..deps import require_role
from ..models import Role, User
from ..services import exposition_client as exposition

router = APIRouter(dependencies=[Depends(require_role(Role.viewer, Role.creator, Role.admin))])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/ask")
def ask_form(request: Request, user: User = Depends(require_role(Role.viewer, Role.creator, Role.admin))):
    return templates.TemplateResponse(request, "viewer_ask.html", {"question": None, "result": None, "error": None, "user": user})


@router.post("/ask")
def ask_submit(
    request: Request, question: str = Form(...),
    user: User = Depends(require_role(Role.viewer, Role.creator, Role.admin)),
):
    question = question.strip()
    ctx = {"question": question, "result": None, "error": None, "user": user}
    if not question:
        ctx["error"] = "question vide"
        return templates.TemplateResponse(request, "viewer_ask.html", ctx, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)
    try:
        ctx["result"] = exposition.ask_knowledge(question, exposition.exposition_role(user))
    except exposition.ExpositionError as exc:
        ctx["error"] = str(exc)
        return templates.TemplateResponse(request, "viewer_ask.html", ctx, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    return templates.TemplateResponse(request, "viewer_ask.html", ctx)
