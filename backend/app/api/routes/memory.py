"""Memory search and user-confirmed facts."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_dep, deny, scopes_dep
from app.api.schemas import MemoryIn, MemoryPatchIn
from app.db.models import MemoryItem
from app.services import audit, memory
from app.services.access import ScopeSet
from app.services.auth import AuthenticatedUser

router = APIRouter(prefix="/memory", tags=["memory"])


def _item(m: MemoryItem) -> dict:
    return {"id": str(m.id), "owner_id": str(m.owner_id), "visibility": m.visibility, "kind": m.kind,
            "title": m.title, "content": m.content, "structured": m.structured, "status": m.status,
            "source_conversation_id": str(m.source_conversation_id) if m.source_conversation_id else None,
            "source_message_id": str(m.source_message_id) if m.source_message_id else None,
            "source_document_id": str(m.source_document_id) if m.source_document_id else None,
            "source_version_id": str(m.source_version_id) if m.source_version_id else None,
            "source_extracted_value_id": str(m.source_extracted_value_id) if m.source_extracted_value_id else None,
            "supersedes_id": str(m.supersedes_id) if m.supersedes_id else None,
            "superseded_by_id": str(m.superseded_by_id) if m.superseded_by_id else None,
            "confirmed_at": m.confirmed_at.isoformat() if m.confirmed_at else None,
            "created_at": m.created_at.isoformat(), "updated_at": m.updated_at.isoformat()}


def _get(db: Session, item_id: str, user: AuthenticatedUser, request: Request, write: bool = False) -> MemoryItem:
    try:
        m = db.get(MemoryItem, uuid.UUID(item_id))
    except ValueError as exc:
        raise HTTPException(404, "Bulunamadı.") from exc
    if m is None:
        raise HTTPException(404, "Hafıza kaydı bulunamadı.")
    allowed = user.is_owner or m.owner_id == user.id or (not write and m.visibility == "shared")
    if not allowed:
        deny(user, request, "memory_scope", target_id=str(m.id))
    return m


@router.get("/search", summary="Hafıza ve sohbet geçmişinde ara")
def search(q: str = Query(min_length=1, max_length=300), user: AuthenticatedUser = Depends(current_user),
           scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    res = memory.search(db, scopes, q)
    audit.record(db, user.actor, "memory.search", target_type="memory", details={"q_chars": len(q),
                 "hits": len(res["memory"]) + len(res["messages"])})
    db.commit()
    return res


@router.get("/items", summary="Hafıza kayıtları")
def items(status: str | None = None, kind: str | None = None, limit: int = Query(200, le=1000),
          user: AuthenticatedUser = Depends(current_user), scopes: ScopeSet = Depends(scopes_dep),
          db: Session = Depends(db_dep)) -> list[dict]:
    q = memory.visible_items_query(scopes)
    if status:
        q = q.where(MemoryItem.status == status)
    if kind:
        q = q.where(MemoryItem.kind == kind)
    return [_item(m) for m in db.execute(q.order_by(MemoryItem.created_at.desc()).limit(limit)).scalars()]


@router.post("/items", status_code=201, summary="Hafıza kaydı ekle (açık kullanıcı bilgisi)")
def create(body: MemoryIn, user: AuthenticatedUser = Depends(current_user), db: Session = Depends(db_dep)) -> dict:
    m = memory.create_item(db, user, kind=body.kind, title=body.title, content=body.content, status=body.status,
                           structured=body.structured, visibility=body.visibility)
    db.commit()
    return _item(m)


@router.patch("/items/{item_id}", summary="Hafıza kaydını güncelle (eski kayıt 'superseded' olarak korunur)")
def patch(item_id: str, body: MemoryPatchIn, request: Request, user: AuthenticatedUser = Depends(current_user),
          db: Session = Depends(db_dep)) -> dict:
    m = _get(db, item_id, user, request, write=True)
    if m.status in ("superseded", "deleted"):
        raise HTTPException(409, "Tarihsel kayıt değiştirilemez.")
    if body.title is None and body.content is None and body.structured is None and body.status:
        memory.set_status(db, user, m, body.status)
        db.commit()
        return _item(m)
    new = memory.supersede_item(db, user, m, title=body.title, content=body.content, structured=body.structured,
                                status=body.status)
    db.commit()
    return _item(new)


@router.get("/items/{item_id}/history", summary="Kaydın sürüm zinciri")
def history(item_id: str, request: Request, user: AuthenticatedUser = Depends(current_user),
            db: Session = Depends(db_dep)) -> list[dict]:
    m = _get(db, item_id, user, request)
    chain = [m]
    cur = m
    while cur.supersedes_id:
        cur = db.get(MemoryItem, cur.supersedes_id)
        chain.append(cur)
    cur = m
    while cur.superseded_by_id:
        cur = db.get(MemoryItem, cur.superseded_by_id)
        chain.insert(0, cur)
    return [_item(x) for x in chain]
