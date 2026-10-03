"""Chiffrement par enveloppe des secrets de connecteurs (EPIC-IAF-E18 US18.2, ADR 0003 point 1).

Une cle de donnees AES-256 aleatoire par secret chiffre le secret (AES-GCM) ; elle est elle-meme
chiffree (AES-GCM) par la cle MAITRE lue depuis un secret monte (`CONNECTOR_MASTER_KEY_FILE`,
prefere) ou la variable `CONNECTOR_MASTER_KEY` (32 octets en base64 urlsafe). Bibliotheque :
`cryptography` (AESGCM, deja presente comme dependance de pdfminer.six, declaree explicitement
dans pyproject.toml). Le secret est lie a son connecteur par les donnees associees (AAD) : un
blob copie d'un connecteur a un autre ne se dechiffre pas.

Format du blob : b"IAFS1" | nonce_cle(12) | cle_de_donnees_chiffree(48) | nonce(12) | secret_chiffre.
Limites v1 : pas de rotation de la cle maitre (la changer rend les secrets illisibles), pas de
coffre externe ; l'interface reste `encrypt_secret`/`decrypt_secret` pour en changer plus tard."""
from __future__ import annotations

import base64
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings

MAGIC = b"IAFS1"
_KEY_AAD = b"iaf-connector-data-key"


class SecretStoreError(RuntimeError):
    """Cle maitre absente ou invalide, ou blob illisible. Le message ne contient jamais de secret."""


def _master_key() -> bytes:
    raw = ""
    if settings.connector_master_key_file:
        try:
            raw = Path(settings.connector_master_key_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SecretStoreError(f"fichier de cle maitre illisible ({type(exc).__name__})") from None
    elif settings.connector_master_key:
        raw = settings.connector_master_key.strip()
    if not raw:
        raise SecretStoreError(
            "cle maitre absente : definir CONNECTOR_MASTER_KEY_FILE (ou CONNECTOR_MASTER_KEY) avec 32 octets en base64 urlsafe"
        )
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except Exception:
        raise SecretStoreError("cle maitre invalide : base64 urlsafe attendu") from None
    if len(key) != 32:
        raise SecretStoreError(f"cle maitre invalide : 32 octets attendus, {len(key)} recus")
    return key


def master_key_available() -> bool:
    try:
        _master_key()
        return True
    except SecretStoreError:
        return False


def generate_master_key() -> str:
    """Nouvelle cle maitre (base64 urlsafe) a placer dans un secret monte ; jamais journalisee par le site."""
    return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode("ascii")


def encrypt_secret(plaintext: str, aad: bytes = b"") -> bytes:
    master = AESGCM(_master_key())
    data_key = AESGCM.generate_key(bit_length=256)
    key_nonce, nonce = os.urandom(12), os.urandom(12)
    wrapped = master.encrypt(key_nonce, data_key, _KEY_AAD)
    ciphertext = AESGCM(data_key).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return MAGIC + key_nonce + wrapped + nonce + ciphertext


def decrypt_secret(blob: bytes, aad: bytes = b"") -> str:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 12 + 48 + 12 + 16:
        raise SecretStoreError("secret illisible : format inconnu")
    body = blob[len(MAGIC):]
    key_nonce, wrapped, nonce, ciphertext = body[:12], body[12:60], body[60:72], body[72:]
    try:
        data_key = AESGCM(_master_key()).decrypt(key_nonce, wrapped, _KEY_AAD)
        return AESGCM(data_key).decrypt(nonce, ciphertext, aad).decode("utf-8")
    except InvalidTag:
        raise SecretStoreError("secret illisible : cle maitre differente, connecteur different ou donnees alterees") from None
