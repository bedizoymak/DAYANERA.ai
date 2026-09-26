"""FastAPI dependencies: settings, DB session, authentication, authorization."""
from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import SESSION_COOKIE
from app.db.session import get_db
from app.inference.base import LLMProvider
from app.inference.registry import get_provider
from app.services import audit
from app.services.access import ScopeSet, load_scopes
from app.services.audit import Actor
from app.services.auth import AuthenticatedUser, resolve_session


def settings_dep() -> Settings:
    return get_settings()


def db_dep() -> Iterator[Session]:
    yield from get_db()


def provider_dep() -> LLMProvider:
    return get_provider()


def client_addr(request: Request) -> str | None:
    return request.client.host if request.client else None


def current_user(request: Request, db: Session = Depends(db_dep), settings: Settings = Depends(settings_dep)) -> AuthenticatedUser:
    token = request.cookies.get(SESSION_COOKIE)
    user = resolve_session(db, settings, token, client_addr(request))
    if user is None:
        if token:
            audit.record_independent(Actor(user_id=None, username=None, client_addr=client_addr(request)),
                                     "access.unauthenticated", outcome="denied", target_type="endpoint",
                                     target_id=f"{request.method} {request.url.path}",
                                     details={"reason": "invalid_or_expired_session"})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Oturum açmanız gerekiyor.")
    return user


def require_owner(request: Request, user: AuthenticatedUser = Depends(current_user)) -> AuthenticatedUser:
    if not user.is_owner:
        deny(user, request, "owner_only")
    return user


def deny(user: AuthenticatedUser, request: Request, reason: str, target_id: str | None = None) -> None:
    audit.record_independent(user.actor, "access.denied", outcome="denied", target_type="endpoint",
                             target_id=target_id or f"{request.method} {request.url.path}", details={"reason": reason})
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Bu işlem için yetkiniz yok.")


def scopes_dep(user: AuthenticatedUser = Depends(current_user), db: Session = Depends(db_dep)) -> ScopeSet:
    return load_scopes(db, user)
