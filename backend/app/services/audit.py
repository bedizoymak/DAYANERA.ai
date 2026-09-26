"""Audit service: append-only audit rows for every security-relevant action.

Success events are written in the caller's transaction (so they commit or
roll back together with the action). Denied/failed events are written in an
independent transaction so that they persist even when the request fails.
Details are sanitized: secret-like keys are never stored.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditEvent
from app.db.session import session_scope

log = logging.getLogger(__name__)

_SECRET_KEY_RE = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|cookie|authorization|credential)")


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID | None
    username: str | None
    session_id: uuid.UUID | None = None
    client_addr: str | None = None

    @staticmethod
    def system() -> "Actor":
        return Actor(user_id=None, username="system")


def sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _SECRET_KEY_RE.search(str(k)):
                out[str(k)] = "[redacted]"
            else:
                out[str(k)] = sanitize(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [sanitize(v, depth + 1) for v in list(value)[:200]]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str) and len(value) > 2000:
        return value[:2000] + "…"
    if value is None or isinstance(value, (int, float, bool, str)):
        return value
    return str(value)


def _event(
    actor: Actor,
    event_type: str,
    outcome: str,
    target_type: str | None,
    target_id: Any,
    details: dict[str, Any] | None,
) -> AuditEvent:
    return AuditEvent(
        actor_user_id=actor.user_id,
        actor_username=actor.username,
        session_id=actor.session_id,
        event_type=event_type,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        outcome=outcome,
        client_addr=actor.client_addr,
        details=sanitize(details or {}),
    )


def record(
    db: Session,
    actor: Actor,
    event_type: str,
    *,
    outcome: str = "success",
    target_type: str | None = None,
    target_id: Any = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Add an audit row to the caller's transaction."""
    db.add(_event(actor, event_type, outcome, target_type, target_id, details))


def record_independent(
    actor: Actor,
    event_type: str,
    *,
    outcome: str = "failure",
    target_type: str | None = None,
    target_id: Any = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Persist an audit row in its own transaction (used for denials/failures)."""
    try:
        with session_scope() as db:
            db.add(_event(actor, event_type, outcome, target_type, target_id, details))
    except Exception:  # pragma: no cover - database outage
        log.exception("Denetim kaydı yazılamadı: %s", event_type)
