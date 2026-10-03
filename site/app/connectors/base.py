"""Types communs des connecteurs (EPIC-IAF-E18)."""
from __future__ import annotations

import re
from dataclasses import dataclass


class ConnectorError(Exception):
    """Erreur d'un connecteur (configuration, authentification, reponse inattendue). Le message ne
    contient jamais de secret."""


@dataclass
class RemoteFile:
    """Un fichier rapporte par une source externe, destine a `_save_and_enqueue` (meme chemin et
    memes controles que le depot manuel)."""

    filename: str
    content: bytes
    content_type: str
    source_id: str


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename(name: str, default: str = "document") -> str:
    """Nom de fichier sans separateur de chemin ni caractere special (le nom vient d'une source non fiable)."""
    cleaned = _UNSAFE.sub("-", name.replace("\\", "/").split("/")[-1]).strip("-.")
    return cleaned[:120] or default
