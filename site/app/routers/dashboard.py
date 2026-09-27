from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from ..deps import get_current_user
from ..models import User

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/")
def dashboard(request: Request, user: User = Depends(get_current_user)):
    # Squelette seulement : les sections par role (projets, agents, comptes a
    # creer...) arrivent avec les epics correspondants (E4, E5, E9...).
    return templates.TemplateResponse(request, "dashboard.html", {"user": user})
