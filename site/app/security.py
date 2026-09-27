"""Hachage de mot de passe (Argon2id, recommandation OWASP verifiee le
2026-09-27) et cookies de session signes (itsdangerous). Le role n'est JAMAIS
lu depuis le cookie : le cookie ne porte que l'id utilisateur, le role est
relu en base a chaque requete (ADR 0007, decision explicite anti-falsification)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

_hasher = PasswordHasher()  # parametres par defaut de argon2-cffi (t=3, m=64Mo, p=... verifies OWASP)
_serializer = URLSafeTimedSerializer(settings.session_secret, salt="iaf-session")

SESSION_COOKIE = "iaf_session"
MIN_PASSWORD_LENGTH = 12  # meme regle que scripts/create_admin.py, a garder alignee


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except VerifyMismatchError:
        return False


def make_session_token(user_id: uuid.UUID) -> str:
    return _serializer.dumps({"uid": str(user_id)})


def read_session_token(token: str) -> uuid.UUID | None:
    max_age = int(timedelta(hours=settings.session_ttl_hours).total_seconds())
    try:
        data = _serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    try:
        return uuid.UUID(data["uid"])
    except (KeyError, ValueError, TypeError):
        return None


def session_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
