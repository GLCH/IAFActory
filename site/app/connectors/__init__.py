"""Connecteurs vers des sources externes (EPIC-IAF-E18). Catalogue FERME : un type absent de
`CATALOG` ne peut pas etre declare (E9 US9.1). Chaque type expose `validate_config(config)` et
`fetch_files(config, secret, limit)` ; toute requete sortante passe par `app.net_guard`.

Etat (2026-10-03, decision de l'utilisateur) :
- `google_drive` RETIRE (pas assez mature) : code, routes, parametres et tests supprimes.
- `openric_api` EN PAUSE (`enabled: False`) : le code est conserve mais aucun connecteur de ce type ne
  peut etre cree, active ni synchronise tant que "comment et quand acceder aux donnees" n'est pas
  defini. Pour le rouvrir : passer `enabled` a True ici, apres decision."""
from __future__ import annotations

from dataclasses import dataclass

from . import openric
from .base import ConnectorError, RemoteFile

CATALOG = {
    "openric_api": {
        "label": "API OpenRiC (RiC-O)",
        "description": "Notices d'archives d'une API REST OpenRiC (JSON-LD fonde sur RiC-O), converties en documents Markdown. Lecture seule, sans identifiant.",
        "module": openric,
        "enabled": False,
        "pause_reason": "en pause : les modalites d'acces aux donnees (comment et quand) restent a definir",
    },
}


@dataclass
class SyncResult:
    files: list[RemoteFile]
    errors: list[str]


def catalog_choices() -> list[tuple[str, str, str, bool, str]]:
    """(cle, libelle, description, actif, motif de pause) pour l'ecran de creation."""
    return [
        (key, value["label"], value["description"], value["enabled"], value.get("pause_reason", ""))
        for key, value in CATALOG.items()
    ]


def is_enabled(connector_type: str) -> bool:
    return bool(CATALOG.get(connector_type, {}).get("enabled"))


__all__ = ["CATALOG", "ConnectorError", "RemoteFile", "SyncResult", "catalog_choices", "is_enabled"]
