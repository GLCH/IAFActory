from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import User
from ..security import SESSION_COOKIE, make_session_token, verify_password

router = APIRouter()
# Chemin absolu derive du fichier, pas du repertoire courant du process (le
# lanceur peut demarrer uvicorn depuis la racine du depot via --app-dir).
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    # Meme message que l'utilisateur existe ou non : n'indique pas quels
    # comptes existent (US5.2 ne le precise pas explicitement, mesure de
    # prudence standard).
    if user is None or not user.is_active or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            request, "login.html", {"error": "identifiants incorrects"}, status_code=status.HTTP_401_UNAUTHORIZED
        )
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        make_session_token(user.id),
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        # secure=True desactive volontairement : le site tourne en http local
        # pour l'instant (US5.2 ADR 0007). A activer avant tout deploiement.
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response
