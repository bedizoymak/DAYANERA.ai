"""Owner-only user and data-scope administration (scaffold for future members)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_dep, require_owner
from app.api.schemas import KnowledgeAreaOut, ScopeIn, ScopeOut, UserCreateIn, UserOut, UserUpdateIn
from app.core.security import hash_password
from app.db.models import AuthSession, Conversation, Document, KnowledgeArea, User, UserScope
from app.services import audit
from app.services.auth import AuthenticatedUser

router = APIRouter(tags=["users"])


def _u(u: User) -> dict:
    return {"id": str(u.id), "username": u.username, "display_name": u.display_name, "role": u.role,
            "is_active": u.is_active}


def _uuid(v: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError as exc:
        raise HTTPException(404, "Bulunamadı.") from exc


@router.get("/users", response_model=list[UserOut], summary="Kullanıcılar (sahip)")
def list_users(user: AuthenticatedUser = Depends(require_owner), db: Session = Depends(db_dep)) -> list[dict]:
    return [_u(u) for u in db.execute(select(User).order_by(User.created_at)).scalars()]


@router.post("/users", response_model=UserOut, status_code=201, summary="Kullanıcı oluştur (sahip)")
def create_user(body: UserCreateIn, user: AuthenticatedUser = Depends(require_owner), db: Session = Depends(db_dep)) -> dict:
    u = User(username=body.username, display_name=body.display_name, password_hash=hash_password(body.password),
             role=body.role, is_active=True, created_by=user.id)
    db.add(u)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Bu kullanıcı adı zaten var.") from exc
    audit.record(db, user.actor, "user.create", target_type="user", target_id=u.id,
                 details={"username": u.username, "role": u.role})
    db.commit()
    return _u(u)


@router.patch("/users/{user_id}", response_model=UserOut, summary="Kullanıcı / rol güncelle (sahip)")
def update_user(user_id: str, body: UserUpdateIn, user: AuthenticatedUser = Depends(require_owner),
                db: Session = Depends(db_dep)) -> dict:
    u = db.get(User, _uuid(user_id))
    if u is None:
        raise HTTPException(404, "Kullanıcı bulunamadı.")
    changes: dict = {}
    if body.display_name is not None:
        u.display_name = body.display_name
        changes["display_name"] = True
    if body.role is not None and body.role != u.role:
        if u.id == user.id and body.role != "owner_admin":
            raise HTTPException(409, "Kendi owner_admin rolünüzü kaldıramazsınız.")
        changes["role"] = {"from": u.role, "to": body.role}
        u.role = body.role
    if body.is_active is not None and body.is_active != u.is_active:
        if u.id == user.id and not body.is_active:
            raise HTTPException(409, "Kendi hesabınızı devre dışı bırakamazsınız.")
        changes["is_active"] = body.is_active
        u.is_active = body.is_active
    if body.password is not None:
        u.password_hash = hash_password(body.password)
        changes["password_reset"] = True
        for s in db.execute(select(AuthSession).where(AuthSession.user_id == u.id, AuthSession.revoked_at.is_(None))).scalars():
            s.revoked_at = datetime.now(timezone.utc)
    u.updated_at = datetime.now(timezone.utc)
    audit.record(db, user.actor, "role.change" if "role" in changes else "user.update", target_type="user",
                 target_id=u.id, details=changes)
    db.commit()
    return _u(u)


def _scope_label(db: Session, s: UserScope) -> str | None:
    model = {"knowledge_area": KnowledgeArea, "document": Document, "conversation": Conversation}[s.scope_type]
    obj = db.get(model, s.scope_id)
    if obj is None:
        return None
    return getattr(obj, "name", None) or getattr(obj, "title", None)


@router.get("/users/{user_id}/scopes", response_model=list[ScopeOut], summary="Kullanıcı kapsamları (sahip)")
def list_scopes(user_id: str, user: AuthenticatedUser = Depends(require_owner), db: Session = Depends(db_dep)) -> list[dict]:
    rows = db.execute(select(UserScope).where(UserScope.user_id == _uuid(user_id), UserScope.revoked_at.is_(None))).scalars()
    return [{"id": str(s.id), "scope_type": s.scope_type, "scope_id": str(s.scope_id), "label": _scope_label(db, s),
             "granted_at": s.granted_at.isoformat()} for s in rows]


@router.post("/users/{user_id}/scopes", response_model=ScopeOut, status_code=201, summary="Kapsam ata (sahip)")
def grant_scope(user_id: str, body: ScopeIn, user: AuthenticatedUser = Depends(require_owner),
                db: Session = Depends(db_dep)) -> dict:
    target = db.get(User, _uuid(user_id))
    if target is None:
        raise HTTPException(404, "Kullanıcı bulunamadı.")
    sid = _uuid(body.scope_id)
    model = {"knowledge_area": KnowledgeArea, "document": Document, "conversation": Conversation}[body.scope_type]
    if db.get(model, sid) is None:
        raise HTTPException(404, "Kapsam nesnesi bulunamadı.")
    s = UserScope(user_id=target.id, scope_type=body.scope_type, scope_id=sid, granted_by=user.id)
    db.add(s)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Bu kapsam zaten atanmış.") from exc
    audit.record(db, user.actor, "scope.grant", target_type="user", target_id=target.id,
                 details={"scope_type": body.scope_type, "scope_id": str(sid)})
    db.commit()
    return {"id": str(s.id), "scope_type": s.scope_type, "scope_id": str(s.scope_id), "label": _scope_label(db, s),
            "granted_at": s.granted_at.isoformat()}


@router.delete("/users/{user_id}/scopes/{scope_id}", status_code=204, summary="Kapsamı kaldır (sahip)")
def revoke_scope(user_id: str, scope_id: str, user: AuthenticatedUser = Depends(require_owner),
                 db: Session = Depends(db_dep)) -> None:
    s = db.get(UserScope, _uuid(scope_id))
    if s is None or s.user_id != _uuid(user_id) or s.revoked_at is not None:
        raise HTTPException(404, "Kapsam bulunamadı.")
    s.revoked_at = datetime.now(timezone.utc)
    s.revoked_by = user.id
    audit.record(db, user.actor, "scope.revoke", target_type="user", target_id=s.user_id,
                 details={"scope_type": s.scope_type, "scope_id": str(s.scope_id)})
    db.commit()


@router.get("/knowledge-areas", response_model=list[KnowledgeAreaOut], summary="Bilgi alanları")
def areas(user: AuthenticatedUser = Depends(current_user), db: Session = Depends(db_dep)) -> list[dict]:
    return [{"id": str(a.id), "slug": a.slug, "name": a.name, "description": a.description,
             "is_verified_corpus": a.is_verified_corpus} for a in db.execute(select(KnowledgeArea)).scalars()]
