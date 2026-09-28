"""US5.1 / US5.7 : l'admin cree tous les comptes (creator ET viewer, decide
le 2026-09-27) ; aucune inscription libre. US5.6 : la creation d'un AUTRE
admin n'est volontairement pas exposee ici (seul scripts/create_admin.py le
permet, hors API) pour eviter qu'une faille dans cette page ne permette de
fabriquer un admin depuis le web."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import ontology
from ..archive import build_archive
from ..config import settings
from ..db import get_db
from ..deps import require_role
from ..graph import get_driver
from ..models import Document, PipelineRun, PipelineStep, Role, User
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


@router.get("/archive")
def archive_form(request: Request, user: User = Depends(require_role(Role.admin))):
    """Ajoute le 2026-09-29, demande explicite. Archive (export lecture
    seule, .zip) et suppression complete (irreversible, confirmation par mot
    tape - une simple confirm() JS n'a pas semble suffisante pour une action
    qui efface TOUT, contrairement a US3.20 objet par objet)."""
    error = request.query_params.get("error")
    done = request.query_params.get("done")
    return templates.TemplateResponse(
        request, "admin_archive.html", {"user": user, "error": error, "done": done},
    )


@router.post("/archive/download")
def archive_download(db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    content = build_archive(db)
    filename = f"iafactory-archive-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(
        content=content, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/wipe")
def wipe_all_route(
    confirm: str = Form(...), db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin)),
):
    """Irreversible - demande explicite ("tu peux vider completement le
    service de tous les documents, les classes, et tout le reste"). Ne
    touche PAS aux comptes utilisateurs (User) : "tout le reste" est compris
    ici comme le CONTENU (documents, classes, concepts, entites, taxonomies,
    executions de pipeline), pas la gestion des comptes (US5.x, un sujet
    distinct) - a confirmer si une portee plus large etait voulue."""
    if confirm != "EFFACER TOUT":
        return RedirectResponse("/admin/archive?error=confirm", status_code=status.HTTP_303_SEE_OTHER)

    driver = get_driver()
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")

    if settings.documents_dir.exists():
        for path in settings.documents_dir.iterdir():
            if path.is_file():
                path.unlink()

    # Ordre des DELETE Core (execute(delete(...))) explicite, plutot que
    # db.delete(obj) un par un : evite le bug de flush deja rencontre le
    # 2026-09-28 (aucune relationship() ORM declaree entre ces tables).
    db.execute(sa_delete(PipelineStep))
    db.execute(sa_delete(PipelineRun))
    db.execute(sa_delete(Document))
    db.commit()

    try:
        ontology.wipe_all()
    except Exception:
        pass

    return RedirectResponse("/admin/archive?done=1", status_code=status.HTTP_303_SEE_OTHER)
