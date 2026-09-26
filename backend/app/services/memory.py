"""Persistent, scoped, searchable memory.

Raw chats and attachments are never replaced: memory items are additional
structured records (facts, preferences, confirmed values, summaries, source
links). Updates create a new item and mark the old one ``superseded``.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import MemoryItem
from app.services import audit
from app.services.access import ScopeSet
from app.services.auth import AuthenticatedUser

MEMORY_CMD = re.compile(r"^\s*(?:bunu\s+)?(?:hatırla|hatirla|not\s+al|not\s+et|unutma|kaydet)\s*[:,\-–]\s*(.+)$",
                        re.IGNORECASE | re.DOTALL)
PREFERENCE_HINT = re.compile(r"(tercih|istiyorum|isterim|bana\s+\w+\s+(?:yaz|ver|göster)|her\s+zaman|asla)", re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_memory_command(text_: str) -> str | None:
    m = MEMORY_CMD.match(text_)
    return m.group(1).strip() if m else None


def create_item(db: Session, user: AuthenticatedUser, *, kind: str, title: str, content: str, status: str,
                structured: dict[str, Any] | None = None, visibility: str = "private",
                source_conversation_id=None, source_message_id=None, source_document_id=None,
                source_version_id=None, source_extracted_value_id=None, owner_id=None) -> MemoryItem:
    item = MemoryItem(
        owner_id=owner_id or user.id, visibility=visibility, kind=kind, title=title[:300], content=content,
        structured=structured or {}, status=status, source_conversation_id=source_conversation_id,
        source_message_id=source_message_id, source_document_id=source_document_id,
        source_version_id=source_version_id, source_extracted_value_id=source_extracted_value_id,
        created_by=user.id,
        confirmed_by=user.id if status == "user_confirmed" else None,
        confirmed_at=_now() if status == "user_confirmed" else None,
    )
    db.add(item)
    db.flush()
    audit.record(db, user.actor, "memory.create", target_type="memory_item", target_id=item.id,
                 details={"kind": kind, "status": status, "title": item.title})
    return item


def supersede_item(db: Session, user: AuthenticatedUser, old: MemoryItem, *, title: str | None, content: str | None,
                   structured: dict | None, status: str | None) -> MemoryItem:
    new = MemoryItem(
        owner_id=old.owner_id, visibility=old.visibility, kind=old.kind, title=(title or old.title)[:300],
        content=content if content is not None else old.content,
        structured=structured if structured is not None else old.structured,
        status=status or old.status, source_conversation_id=old.source_conversation_id,
        source_message_id=old.source_message_id, source_document_id=old.source_document_id,
        source_version_id=old.source_version_id, source_extracted_value_id=old.source_extracted_value_id,
        supersedes_id=old.id, created_by=user.id,
        confirmed_by=user.id if (status or old.status) == "user_confirmed" else None,
        confirmed_at=_now() if (status or old.status) == "user_confirmed" else None,
    )
    db.add(new)
    db.flush()
    old.status = "superseded"
    old.superseded_by_id = new.id
    old.updated_at = _now()
    audit.record(db, user.actor, "memory.update", target_type="memory_item", target_id=new.id,
                 details={"supersedes": str(old.id), "status": new.status})
    return new


def set_status(db: Session, user: AuthenticatedUser, item: MemoryItem, status: str) -> None:
    prev = item.status
    item.status = status
    item.updated_at = _now()
    if status == "user_confirmed":
        item.confirmed_by, item.confirmed_at = user.id, _now()
    audit.record(db, user.actor, "memory.status_change", target_type="memory_item", target_id=item.id,
                 details={"from": prev, "to": status})


def visible_items_query(scopes: ScopeSet):
    q = select(MemoryItem)
    if not scopes.is_owner:
        q = q.where((MemoryItem.owner_id == scopes.user_id) | (MemoryItem.visibility == "shared"))
    return q


def search(db: Session, scopes: ScopeSet, q: str, limit: int = 30) -> dict[str, list[dict]]:
    """Search memory items and raw chat messages the user may see."""
    words = [w for w in re.findall(r"[\wçğıöşüÇĞİÖŞÜ]+", q.lower()) if len(w) > 1][:8]
    if not words:
        return {"memory": [], "messages": []}
    tsq = " & ".join(w + ":*" for w in words)
    mem_scope = "TRUE" if scopes.is_owner else "(m.owner_id = :uid OR m.visibility = 'shared')"
    mem_rows = db.execute(text(f"""
        SELECT m.id, m.kind, m.title, m.content, m.status, m.created_at, m.owner_id,
               ts_rank(m.tsv, to_tsquery('simple', :q)) AS rank
        FROM memory_items m
        WHERE m.tsv @@ to_tsquery('simple', :q) AND m.status <> 'deleted' AND {mem_scope}
        ORDER BY rank DESC, m.created_at DESC LIMIT :lim"""), {"q": tsq, "uid": scopes.user_id, "lim": limit}).all()
    conv_scope = "TRUE" if scopes.is_owner else "(c.owner_id = :uid OR c.id = ANY(:convs))"
    msg_rows = db.execute(text(f"""
        SELECT msg.id, msg.conversation_id, c.title, msg.role, msg.content, msg.answer_mode, msg.created_at,
               ts_rank(msg.tsv, to_tsquery('simple', :q)) AS rank
        FROM messages msg JOIN conversations c ON c.id = msg.conversation_id
        WHERE msg.tsv @@ to_tsquery('simple', :q) AND {conv_scope}
        ORDER BY rank DESC, msg.created_at DESC LIMIT :lim"""),
        {"q": tsq, "uid": scopes.user_id, "convs": list(scopes.conversation_ids) or [uuid.UUID(int=0)], "lim": limit}).all()
    return {
        "memory": [{"id": str(r.id), "kind": r.kind, "title": r.title, "content": r.content[:500], "status": r.status,
                    "created_at": r.created_at.isoformat()} for r in mem_rows],
        "messages": [{"id": str(r.id), "conversation_id": str(r.conversation_id), "conversation_title": r.title,
                      "role": r.role, "content": r.content[:500], "answer_mode": r.answer_mode,
                      "created_at": r.created_at.isoformat()} for r in msg_rows],
    }


def relevant_notes(db: Session, user: AuthenticatedUser, text_: str, limit: int = 6) -> list[str]:
    """Confirmed facts/preferences to give the general-chat model as unverified context."""
    items = db.execute(
        select(MemoryItem).where(MemoryItem.owner_id == user.id, MemoryItem.status == "user_confirmed",
                                 MemoryItem.kind.in_(("fact", "preference", "technical_value")))
        .order_by(MemoryItem.created_at.desc()).limit(50)
    ).scalars().all()
    words = {w for w in re.findall(r"\w+", text_.lower()) if len(w) > 3}
    scored = []
    for it in items:
        overlap = len(words & set(re.findall(r"\w+", (it.title + " " + it.content).lower())))
        scored.append((it.kind == "preference", overlap, it.created_at, it))
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return [f"{it.content}" for *_rest, it in scored[:limit]]
