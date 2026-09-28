"""US5.1 / US5.7 : l'admin cree tous les comptes (creator ET viewer, decide
le 2026-09-27) ; aucune inscription libre. US5.6 : la creation d'un AUTRE
admin n'est volontairement pas exposee ici (seul scripts/create_admin.py le
permet, hors API) pour eviter qu'une faille dans cette page ne permette de
fabriquer un admin depuis le web."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import require_role
from ..models import Role, User
from ..security import MIN_PASSWORD_LENGTH, hash_password

router = APIRouter(prefix="/admin", dependencies=[Depends(require_role(Role.admin))])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

CREATABLE_ROLES = (Role.viewer, Role.creator)


@router.get("/users/new")
def new_user_form(request: Request, user: User = Depends(require_role(Role.admin))):
    return templates.TemplateResponse(request, "admin_new_user.html", {"error": None, "user": user})


@router.post("/users/new")
def new_user_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    db: Session = Depends(get_db),
    current: User = Depends(require_role(Role.admin)),
):
    email = email.strip().lower()
    ctx = {"error": None, "email": email, "role": role, "user": current}

    if role not in {r.value for r in CREATABLE_ROLES}:
        ctx["error"] = "role invalide"
        return templates.TemplateResponse(request, "admin_new_user.html", ctx, status_code=422)
    if len(password) < MIN_PASSWORD_LENGTH:
        ctx["error"] = f"mot de passe trop court ({MIN_PASSWORD_LENGTH} caracteres minimum)"
        return templates.TemplateResponse(request, "admin_new_user.html", ctx, status_code=422)

    new_user = User(email=email, hashed_password=hash_password(password), role=Role(role))
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        ctx["error"] = f"{email} existe deja"
        return templates.TemplateResponse(request, "admin_new_user.html", ctx, status_code=409)

    return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)


_LIST_ERRORS = {
    "self": "vous ne pouvez pas vous supprimer vous-meme.",
    "admin": "la suppression d'un admin n'est pas possible depuis cette page.",
}


@router.get("/users")
def list_users(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    users = list(db.scalars(select(User).order_by(User.created_at.desc())))
    error = _LIST_ERRORS.get(request.query_params.get("error", ""))
    return templates.TemplateResponse(
        request, "admin_users.html", {"users": users, "user": user, "current_user_id": user.id, "error": error}
    )


@router.post("/users/{user_id}/delete")
def delete_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current: User = Depends(require_role(Role.admin)),
):
    if user_id == current.id:
        return RedirectResponse("/admin/users?error=self", status_code=status.HTTP_303_SEE_OTHER)
    target = db.get(User, user_id)
    if target is not None:
        if target.role == Role.admin:
            # Pas de suppression d'admin depuis cette page (US5.6 : la gestion
            # des admins reste hors API pour l'instant). Evite un verrou si un
            # admin supprime tous les autres admins par erreur.
            return RedirectResponse("/admin/users?error=admin", status_code=status.HTTP_303_SEE_OTHER)
        db.delete(target)
        db.commit()
    return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)
