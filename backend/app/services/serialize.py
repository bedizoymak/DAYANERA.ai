"""Stable JSON shapes shared by the REST API and the chat stream."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Conversation, Document, DocumentVersion, Message, MessageAttachment, MessageSource
from app.domain.enums import ANSWER_MODE_LABELS_TR, AnswerMode

_PUBLIC_META = ("calculation_id", "calc_type", "calc_status", "mismatch", "detail_open", "notice", "reason",
                "sources_for_message_id", "draft_used", "attachments_pending", "note_filename", "memory_item_id",
                "kind", "document_count", "verified_document_count", "refusal", "timings_ms", "best_score", "min_score")


def source_to_dict(s: MessageSource) -> dict[str, Any]:
    return {
        "rank": s.rank, "document_id": str(s.document_id), "version_id": str(s.version_id),
        "version_number": s.version_number, "document_title": s.document_title, "standard_code": s.standard_code,
        "page_number": s.page_number, "locator": s.locator, "excerpt": s.excerpt, "score": round(s.score, 4),
        "confidence_status": s.confidence_status, "cited": s.cited,
    }


def message_to_dict(db: Session, m: Message) -> dict[str, Any]:
    atts = db.execute(
        select(MessageAttachment, Document, DocumentVersion)
        .join(Document, Document.id == MessageAttachment.document_id)
        .outerjoin(DocumentVersion, DocumentVersion.id == MessageAttachment.version_id)
        .where(MessageAttachment.message_id == m.id)
    ).all()
    sources = []
    if m.show_sources:
        rows = db.execute(select(MessageSource).where(MessageSource.message_id == m.id).order_by(MessageSource.rank)).scalars()
        sources = [source_to_dict(s) for s in rows]
    meta = m.metadata_ or {}
    mode = m.answer_mode
    return {
        "id": str(m.id), "conversation_id": str(m.conversation_id), "role": m.role, "content": m.content,
        "answer_mode": mode, "answer_mode_label": ANSWER_MODE_LABELS_TR.get(AnswerMode(mode)) if mode else None,
        "status": m.status, "error_code": m.error_code, "show_sources": m.show_sources, "sources": sources,
        "attachments": [
            {"document_id": str(d.id), "filename": d.original_filename, "status": d.status,
             "ingestion_status": v.ingestion_status if v else None, "mime_type": v.mime_type if v else None}
            for _a, d, v in atts
        ],
        "metadata": {k: meta[k] for k in _PUBLIC_META if k in meta},
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "model": m.model, "latency_ms": m.latency_ms,
    }


def conversation_to_dict(c: Conversation) -> dict[str, Any]:
    return {"id": str(c.id), "title": c.title, "status": c.status, "owner_id": str(c.owner_id),
            "created_at": c.created_at.isoformat(), "updated_at": c.updated_at.isoformat()}
