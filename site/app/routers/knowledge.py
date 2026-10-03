"""Page COMMUNE d'acces aux connaissances (viewer et creator, 2026-10-03) : classes, concepts avec leurs
definitions, entites, documents, et une recherche qui retourne le SAVOIR EXTRAIT du systeme (graphe semantique
et/ou RAG), explique, ancre sur les donnees enregistrees, avec aveu d'ignorance quand les donnees manquent.

Toute lecture passe par le SERVICE D'EXPOSITION (client MCP, ADR 0009) : cette route n'ouvre aucun magasin. Un
viewer ne voit que les classes officielles ; un creator voit aussi les provisoires et les liens d'edition."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_role
from ..models import Role, User
from ..services import exposition_client as exposition
from ..services import sources

router = APIRouter(prefix="/knowledge")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("")
def knowledge_home(
    request: Request, q: str = "", db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.viewer, Role.creator)),
):
    role = exposition.exposition_role(user)
    classes, result, error, code = [], None, None, status.HTTP_200_OK
    try:
        classes = exposition.list_classes(role)
        result = exposition.ask_knowledge(q.strip(), role) if q.strip() else None
    except exposition.ExpositionError as exc:
        error, code = str(exc), status.HTTP_503_SERVICE_UNAVAILABLE
    return templates.TemplateResponse(request, "knowledge.html", {
        "user": user, "classes": classes, "connectors": sources.active_connectors(db), "result": result,
        "q": q.strip(), "error": error,
    }, status_code=code)


@router.get("/classes/{class_id}")
def knowledge_class(
    request: Request, class_id: str, user: User = Depends(require_role(Role.viewer, Role.creator)),
):
    try:
        detail = exposition.get_class(class_id, exposition.exposition_role(user))
    except exposition.ExpositionError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from None
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "classe introuvable")
    return templates.TemplateResponse(request, "knowledge_class.html", {"user": user, "c": detail})
