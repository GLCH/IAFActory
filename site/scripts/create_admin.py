"""Cree le tout premier compte admin (bootstrap : US5.1/US5.7 reservent toute
creation de compte a un admin existant, donc le tout premier doit venir d'ici,
hors API, jamais expose en HTTP).

Usage : python scripts/create_admin.py <email> puis saisir le mot de passe.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.security import MIN_PASSWORD_LENGTH, hash_password  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python scripts/create_admin.py <email>")
    email = sys.argv[1].strip().lower()
    password = getpass.getpass("Mot de passe : ")
    confirm = getpass.getpass("Confirmer : ")
    if password != confirm:
        raise SystemExit("les mots de passe ne correspondent pas")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"mot de passe trop court ({MIN_PASSWORD_LENGTH} caracteres minimum)")

    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)):
            raise SystemExit(f"{email} existe deja")
        user = User(email=email, hashed_password=hash_password(password), role=Role.admin)
        db.add(user)
        db.commit()
        print(f"admin cree : {email} (id {user.id})")


if __name__ == "__main__":
    main()
