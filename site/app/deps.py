from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .db import get_db
from .models import Role, User
from .security import SESSION_COOKIE, read_session_token


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Refuse l'anonyme (403 sur toute route protegee, cf. US5.2 : 'toute
    route protegee refuse l'anonyme'). Le role vient de la base, jamais du
    cookie ni d'un en-tete client."""
    token = request.cookies.get(SESSION_COOKIE)
    user_id = read_session_token(token) if token else None
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "connexion requise")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session invalide")
    return user


def require_role(*roles: Role) -> Callable[[User], User]:
    def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "role insuffisant")
        return user

    return _check
