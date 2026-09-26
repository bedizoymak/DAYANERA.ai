"""Audit log: owner sees all rows; other users see only their own actions."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_dep
from app.db.models import AuditEvent
from app.services.auth import AuthenticatedUser

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", summary="Denetim kayıtları (filtrelenebilir)")
def list_events(event_type: str | None = None, actor: str | None = None, outcome: str | None = None,
                target_id: str | None = None, since: datetime | None = None, until: datetime | None = None,
                limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                user: AuthenticatedUser = Depends(current_user), db: Session = Depends(db_dep)) -> dict:
    q = select(AuditEvent)
    if not user.is_owner:
        q = q.where(AuditEvent.actor_user_id == user.id)
    if event_type:
        q = q.where(AuditEvent.event_type.ilike(f"{event_type}%"))
    if actor:
        q = q.where(AuditEvent.actor_username.ilike(f"%{actor}%"))
    if outcome:
        q = q.where(AuditEvent.outcome == outcome)
    if target_id:
        q = q.where(AuditEvent.target_id == target_id)
    if since:
        q = q.where(AuditEvent.occurred_at >= since)
    if until:
        q = q.where(AuditEvent.occurred_at <= until)
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar()
    rows = db.execute(q.order_by(AuditEvent.id.desc()).limit(limit).offset(offset)).scalars()
    return {"total": total, "items": [
        {"id": e.id, "occurred_at": e.occurred_at.isoformat(), "actor_user_id": str(e.actor_user_id) if e.actor_user_id else None,
         "actor_username": e.actor_username, "event_type": e.event_type, "target_type": e.target_type,
         "target_id": e.target_id, "outcome": e.outcome, "client_addr": e.client_addr, "details": e.details}
        for e in rows]}


@router.get("/event-types", summary="Kayıtlı olay türleri")
def event_types(user: AuthenticatedUser = Depends(current_user), db: Session = Depends(db_dep)) -> list[str]:
    q = select(AuditEvent.event_type).distinct()
    if not user.is_owner:
        q = q.where(AuditEvent.actor_user_id == user.id)
    return sorted(db.execute(q).scalars())
