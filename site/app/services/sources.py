"""Sources qui alimentent le service : connecteurs APPROUVES, visibles de tous les roles (donnees du site,
Postgres). N'expose ni configuration, ni secret, ni proprietaire."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors import CATALOG
from ..models import Connector, ConnectorStatus


def active_connectors(db: Session) -> list[dict]:
    rows = db.scalars(select(Connector).where(Connector.status == ConnectorStatus.approuve).order_by(Connector.name))
    return [
        {
            "name": c.name, "type": CATALOG.get(c.type, {}).get("label", c.type),
            "last_sync_at": c.last_sync_at, "last_sync_summary": c.last_sync_summary,
        }
        for c in rows
    ]
