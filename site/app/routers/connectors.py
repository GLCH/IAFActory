"""Connecteurs vers des sources externes (EPIC-IAF-E18 US18.2).

Cycle de vie decide en E9 (US5.6, 2026-09-27) : brouillon -> en attente (demande du creator) ->
approuve ou rejete (admin). Un connecteur non approuve renvoie 409 a toute synchronisation. Un
creator ne voit que SES connecteurs (404 sinon). Les secrets sont en ecriture seule : aucune page ne
les affiche. Chaque action est journalisee dans `connector_events` (ajout seul). Execution dans le
processus du site (isolation conteneur US9.3 non faite, voir l'epic E18).

Acces depuis le site (2026-10-03) : la gestion se fait depuis la page Parametres du creator ; les
connecteurs approuves sont visibles de tous les roles sur la page des connaissances. Un type en
pause (`CATALOG[...]["enabled"] is False`) ne peut ni etre cree, ni demande, ni synchronise."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import secret_store
from ..config import settings
from ..connectors import CATALOG, ConnectorError, catalog_choices, is_enabled
from ..db import get_db
from ..deps import require_role
from ..models import Connector, ConnectorEvent, ConnectorStatus, Role, User
from .documents import _save_and_enqueue

router = APIRouter(prefix="/creator/connectors")
admin_router = APIRouter(prefix="/admin/connectors", dependencies=[Depends(require_role(Role.admin))])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _audit(db: Session, connector: Connector, actor_id: uuid.UUID | None, action: str, detail: str | None = None) -> None:
    db.add(ConnectorEvent(
        connector_id=connector.id, connector_name=connector.name, actor_id=actor_id, action=action,
        detail=(detail or "")[:500] or None,
    ))


def _owned(db: Session, connector_id: uuid.UUID, user: User) -> Connector:
    connector = db.get(Connector, connector_id)
    if connector is None or connector.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connecteur introuvable")
    return connector


def _secret_aad(connector: Connector) -> bytes:
    return str(connector.id).encode("ascii")


def _detail_context(db: Session, connector: Connector, user: User, **extra) -> dict:
    events = list(db.scalars(
        select(ConnectorEvent).where(ConnectorEvent.connector_id == connector.id).order_by(ConnectorEvent.at.desc()).limit(20)
    ))
    entry = CATALOG.get(connector.type, {"label": connector.type, "description": "", "enabled": False, "pause_reason": "type retire du catalogue"})
    ctx = {
        "connector": connector, "events": events, "user": user, "catalog_entry": entry,
        "secret_defined": connector.secret_encrypted is not None, "active_page": "creator_settings",
        "max_items": settings.connector_max_items_per_sync, "error": None, "message": None,
    }
    ctx.update(extra)
    return ctx


def _list_context(db: Session, user: User, error: str | None = None) -> dict:
    connectors = list(db.scalars(select(Connector).where(Connector.owner_id == user.id).order_by(Connector.created_at.desc())))
    return {
        "connectors": connectors, "catalog": catalog_choices(), "catalog_map": CATALOG, "user": user, "error": error,
        "active_page": "creator_settings",
    }


@router.get("")
def list_connectors(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.creator))):
    return templates.TemplateResponse(request, "creator_connectors.html", _list_context(db, user))


@router.post("")
def create_connector(
    request: Request, type: str = Form(...), name: str = Form(...), base_url: str = Form(""),
    db: Session = Depends(get_db), user: User = Depends(require_role(Role.creator)),
):
    def fail(message: str):
        return templates.TemplateResponse(request, "creator_connectors.html", _list_context(db, user, message), status_code=422)

    if type not in CATALOG:
        return fail("type de connecteur inconnu (catalogue ferme)")
    if not is_enabled(type):
        return fail(f"type de connecteur {CATALOG[type]['pause_reason']}")
    name = name.strip()
    if not name or len(name) > 120:
        return fail("nom requis (120 caracteres maximum)")
    try:
        config = CATALOG[type]["module"].validate_config({"base_url": base_url})
    except ConnectorError as exc:
        return fail(str(exc))
    connector = Connector(owner_id=user.id, type=type, name=name, config=config, status=ConnectorStatus.brouillon)
    db.add(connector)
    db.flush()
    _audit(db, connector, user.id, "created", f"type {type}")
    db.commit()
    return RedirectResponse(f"/creator/connectors/{connector.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{connector_id}")
def connector_detail(
    request: Request, connector_id: uuid.UUID, error: str = "", message: str = "",
    db: Session = Depends(get_db), user: User = Depends(require_role(Role.creator)),
):
    connector = _owned(db, connector_id, user)
    return templates.TemplateResponse(
        request, "creator_connector_detail.html", _detail_context(db, connector, user, error=error or None, message=message or None),
    )


@router.post("/{connector_id}/request-activation")
def request_activation(connector_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_role(Role.creator))):
    connector = _owned(db, connector_id, user)
    if not is_enabled(connector.type):
        raise HTTPException(status.HTTP_409_CONFLICT, "type de connecteur en pause ou retire")
    if connector.status not in (ConnectorStatus.brouillon, ConnectorStatus.rejete):
        raise HTTPException(status.HTTP_409_CONFLICT, "activation deja demandee ou connecteur deja approuve")
    connector.status = ConnectorStatus.en_attente
    connector.decision_reason = None
    _audit(db, connector, user.id, "activation_requested")
    db.commit()
    return RedirectResponse(f"/creator/connectors/{connector.id}?message=demande+envoyee+a+l%27admin", status_code=303)


@router.post("/{connector_id}/sync")
def sync_connector(
    request: Request, connector_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_role(Role.creator)),
):
    """Synchronisation en lecture seule : les fichiers rapportes entrent par `_save_and_enqueue` (memes
    controles que le depot manuel : format, taille, sha256, doublons). Inline, plafonnee a
    `connector_max_items_per_sync` elements."""
    connector = _owned(db, connector_id, user)
    if not is_enabled(connector.type):
        raise HTTPException(status.HTTP_409_CONFLICT, "type de connecteur en pause ou retire : aucun acces aux donnees")
    if connector.status != ConnectorStatus.approuve:
        raise HTTPException(status.HTTP_409_CONFLICT, "connecteur non approuve par l'admin")
    module = CATALOG[connector.type]["module"]
    try:
        secret = (
            secret_store.decrypt_secret(connector.secret_encrypted, aad=_secret_aad(connector))
            if connector.secret_encrypted else None
        )
        files, errors = module.fetch_files(connector.config, secret, settings.connector_max_items_per_sync)
    except (ConnectorError, secret_store.SecretStoreError) as exc:
        connector.last_sync_at = datetime.now(timezone.utc)
        connector.last_sync_summary = f"echec : {exc}"[:500]
        _audit(db, connector, user.id, "sync_failed", str(exc))
        db.commit()
        return templates.TemplateResponse(
            request, "creator_connector_detail.html", _detail_context(db, connector, user, error=str(exc)), status_code=502,
        )
    accepted = duplicates = rejected = 0
    for remote in files:
        problem = _save_and_enqueue(remote.content, remote.filename, remote.content_type, user, db)
        if problem is None:
            accepted += 1
        elif "deja present" in problem:
            duplicates += 1
        else:
            rejected += 1
            errors.append(f"{remote.filename} : {problem}")
    summary = f"{accepted} document(s) mis en file, {duplicates} deja present(s), {rejected} refuse(s)"
    if errors:
        summary += f", {len(errors)} avertissement(s) : " + " | ".join(errors[:3])
    connector.last_sync_at = datetime.now(timezone.utc)
    connector.last_sync_summary = summary[:500]
    _audit(db, connector, user.id, "sync", summary)
    db.commit()
    return RedirectResponse(f"/creator/connectors/{connector.id}?message=synchronisation+terminee", status_code=303)


@router.post("/{connector_id}/revoke")
def revoke_connector(connector_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_role(Role.creator))):
    """Revocation immediate (E9 US9.6) : le secret est detruit et le connecteur repasse a `brouillon`
    (une nouvelle approbation est necessaire avant tout usage)."""
    connector = _owned(db, connector_id, user)
    connector.secret_encrypted = None
    connector.status = ConnectorStatus.brouillon
    connector.decision_reason = None
    _audit(db, connector, user.id, "revoked", "secret detruit, retour a brouillon")
    db.commit()
    return RedirectResponse(f"/creator/connectors/{connector.id}?message=connecteur+revoque", status_code=303)


@router.post("/{connector_id}/delete")
def delete_connector(connector_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(require_role(Role.creator))):
    connector = _owned(db, connector_id, user)
    _audit(db, connector, user.id, "deleted", "connecteur et secret detruits")
    db.delete(connector)
    db.commit()
    return RedirectResponse("/creator/connectors", status_code=status.HTTP_303_SEE_OTHER)


@admin_router.get("")
def admin_list(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    rows = db.execute(
        select(Connector, User.email).join(User, User.id == Connector.owner_id).order_by(Connector.created_at.desc())
    ).all()
    return templates.TemplateResponse(request, "admin_connectors.html", {
        "rows": rows, "catalog_map": CATALOG, "user": user, "error": None,
    })


@admin_router.post("/{connector_id}/decide")
def admin_decide(
    request: Request, connector_id: uuid.UUID, decision: str = Form(...), reason: str = Form(""),
    db: Session = Depends(get_db), admin: User = Depends(require_role(Role.admin)),
):
    connector = db.get(Connector, connector_id)
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connecteur introuvable")
    if connector.status != ConnectorStatus.en_attente:
        raise HTTPException(status.HTTP_409_CONFLICT, "aucune demande en attente pour ce connecteur")
    reason = reason.strip()
    if decision == "approve":
        if not is_enabled(connector.type):
            raise HTTPException(status.HTTP_409_CONFLICT, "type de connecteur en pause ou retire : approbation impossible")
        connector.status = ConnectorStatus.approuve
    elif decision == "reject":
        if not reason:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "un rejet doit etre motive")
        connector.status = ConnectorStatus.rejete
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "decision inconnue")
    connector.decided_by, connector.decided_at, connector.decision_reason = admin.id, datetime.now(timezone.utc), reason or None
    _audit(db, connector, admin.id, "approved" if decision == "approve" else "rejected", reason or None)
    db.commit()
    return RedirectResponse("/admin/connectors", status_code=status.HTTP_303_SEE_OTHER)
