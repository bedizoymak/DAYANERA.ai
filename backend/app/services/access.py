"""Role and data-scope authorization.

owner_admin sees everything. member sees only conversations, documents and
knowledge areas explicitly assigned via user_scopes (plus their own
conversations and uploads).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Conversation, Document, UserScope
from app.services.auth import AuthenticatedUser


@dataclass
class ScopeSet:
    is_owner: bool
    user_id: uuid.UUID
    area_ids: set[uuid.UUID] = field(default_factory=set)
    document_ids: set[uuid.UUID] = field(default_factory=set)
    conversation_ids: set[uuid.UUID] = field(default_factory=set)

    def sql_filter(self, doc_alias: str = "d") -> tuple[str, dict]:
        """Return a parameterized SQL predicate restricting documents to scope."""
        if self.is_owner:
            return "TRUE", {}
        params = {
            "scope_user": self.user_id,
            "scope_areas": list(self.area_ids) or [uuid.UUID(int=0)],
            "scope_docs": list(self.document_ids) or [uuid.UUID(int=0)],
        }
        clause = (
            f"({doc_alias}.owner_id = :scope_user OR {doc_alias}.knowledge_area_id = ANY(:scope_areas) "
            f"OR {doc_alias}.id = ANY(:scope_docs))"
        )
        return clause, params


def load_scopes(db: Session, user: AuthenticatedUser) -> ScopeSet:
    s = ScopeSet(is_owner=user.is_owner, user_id=user.id)
    if user.is_owner:
        return s
    rows = db.execute(
        select(UserScope.scope_type, UserScope.scope_id).where(
            UserScope.user_id == user.id, UserScope.revoked_at.is_(None)
        )
    ).all()
    for scope_type, scope_id in rows:
        if scope_type == "knowledge_area":
            s.area_ids.add(scope_id)
        elif scope_type == "document":
            s.document_ids.add(scope_id)
        elif scope_type == "conversation":
            s.conversation_ids.add(scope_id)
    return s


def can_view_conversation(scopes: ScopeSet, conv: Conversation) -> bool:
    return scopes.is_owner or conv.owner_id == scopes.user_id or conv.id in scopes.conversation_ids


def can_write_conversation(scopes: ScopeSet, conv: Conversation) -> bool:
    return scopes.is_owner or conv.owner_id == scopes.user_id


def can_view_document(scopes: ScopeSet, doc: Document) -> bool:
    if scopes.is_owner:
        return True
    return (
        doc.owner_id == scopes.user_id
        or (doc.knowledge_area_id is not None and doc.knowledge_area_id in scopes.area_ids)
        or doc.id in scopes.document_ids
    )
