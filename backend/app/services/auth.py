"""Authentication: local users, opaque sessions and idempotent admin seed."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT, Settings
from app.core.security import hash_password, hash_token, new_session_token, verify_password
from app.db.models import AuthSession, User
from app.domain.enums import Role
from app.services import audit
from app.services.audit import Actor

log = logging.getLogger(__name__)


@dataclass
class AuthenticatedUser:
    id: uuid.UUID
    username: str
    display_name: str
    role: str
    session_id: uuid.UUID
    client_addr: str | None = None

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER_ADMIN.value

    @property
    def actor(self) -> Actor:
        return Actor(user_id=self.id, username=self.username, session_id=self.session_id, client_addr=self.client_addr)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def seed_reference_data(db: Session) -> None:
    seed_dir = REPO_ROOT / "database" / "seed"
    for sql_file in sorted(seed_dir.glob("*.sql")):
        raw = db.connection().connection.dbapi_connection
        with raw.cursor() as cur:
            cur.execute(sql_file.read_text(encoding="utf-8"))


def seed_initial_admin(db: Session, settings: Settings) -> bool:
    """Create the initial owner_admin once. Returns True when created."""
    username = settings.initial_admin_username.strip()
    existing = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if existing is not None:
        return False
    user = User(
        username=username,
        display_name="Yönetici",
        password_hash=hash_password(settings.initial_admin_password.get_secret_value()),
        role=Role.OWNER_ADMIN.value,
        is_active=True,
    )
    db.add(user)
    db.flush()
    audit.record(
        db,
        Actor.system(),
        "user.seeded",
        target_type="user",
        target_id=user.id,
        details={"username": username, "role": user.role},
    )
    log.info("İlk yönetici hesabı oluşturuldu: %s (owner_admin)", username)
    return True


def login(
    db: Session,
    settings: Settings,
    username: str,
    password: str,
    *,
    user_agent: str | None,
    client_addr: str | None,
) -> tuple[AuthenticatedUser, str] | None:
    user = db.execute(select(User).where(User.username == username.strip())).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        audit.record_independent(
            Actor(user_id=user.id if user else None, username=username[:120], client_addr=client_addr),
            "auth.login",
            outcome="failure",
            target_type="user",
            target_id=user.id if user else None,
            details={"reason": "invalid_credentials" if user is None or user.is_active else "inactive_user"},
        )
        return None
    token = new_session_token()
    sess = AuthSession(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=_now() + timedelta(hours=settings.session_ttl_hours),
        user_agent=(user_agent or "")[:300],
        client_addr=client_addr,
    )
    db.add(sess)
    db.flush()
    au = AuthenticatedUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        session_id=sess.id,
        client_addr=client_addr,
    )
    audit.record(db, au.actor, "auth.login", target_type="session", target_id=sess.id)
    return au, token


def resolve_session(db: Session, settings: Settings, token: str | None, client_addr: str | None) -> AuthenticatedUser | None:
    if not token:
        return None
    row = db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == hash_token(token))
    ).first()
    if row is None:
        return None
    sess, user = row
    now = _now()
    if sess.revoked_at is not None or not user.is_active:
        return None
    if sess.expires_at <= now:
        sess.revoked_at = now
        audit.record(
            db,
            Actor(user_id=user.id, username=user.username, session_id=sess.id, client_addr=client_addr),
            "auth.session_expired",
            outcome="info",
            target_type="session",
            target_id=sess.id,
        )
        db.commit()
        return None
    # sliding expiry, updated at most once per minute to limit writes
    if (now - sess.last_seen_at).total_seconds() > 60:
        sess.last_seen_at = now
        sess.expires_at = now + timedelta(hours=settings.session_ttl_hours)
        db.commit()
    return AuthenticatedUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        session_id=sess.id,
        client_addr=client_addr,
    )


def logout(db: Session, user: AuthenticatedUser) -> None:
    sess = db.get(AuthSession, user.session_id)
    if sess is not None and sess.revoked_at is None:
        sess.revoked_at = _now()
    audit.record(db, user.actor, "auth.logout", target_type="session", target_id=user.session_id)
    db.commit()


def ensure_schema_ready(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1 FROM users LIMIT 1"))
        return True
    except Exception:
        db.rollback()
        return False
