"""Auth/session behaviour, audit of login events, secret hygiene."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from conftest import CSRF, TEST_ADMIN_PASSWORD, audit_rows

PW = TEST_ADMIN_PASSWORD


def test_seeded_admin_is_owner_and_seed_is_idempotent(admin, settings):
    from app.db.models import User
    from app.db.session import session_scope
    from app.services.auth import seed_initial_admin

    assert admin.user["role"] == "owner_admin"
    with session_scope() as db:
        assert seed_initial_admin(db, settings) is False
        assert len(db.execute(select(User).where(User.username == "admin")).scalars().all()) == 1


def test_login_sets_httponly_strict_cookie_and_never_returns_password(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": PW}, headers=CSRF)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    body = r.text
    assert PW not in body and "password" not in body.lower()
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["username"] == "admin"


def test_failed_login_is_audited_without_password(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "yanlis-parola-xyz"}, headers=CSRF)
    assert r.status_code == 401
    assert r.json()["detail"] == "Kullanıcı adı veya parola hatalı."
    rows = audit_rows("auth.login", outcome="failure")
    assert rows, "failed login must be audited"
    assert "yanlis-parola-xyz" not in json.dumps(rows)


def test_successful_login_and_logout_are_audited_and_logout_revokes(app):
    from fastapi.testclient import TestClient

    c = TestClient(app, base_url="http://127.0.0.1")
    before = len(audit_rows("auth.login", outcome="success"))
    assert c.post("/api/v1/auth/login", json={"username": "admin", "password": PW}, headers=CSRF).status_code == 200
    assert len(audit_rows("auth.login", outcome="success")) == before + 1
    assert c.post("/api/v1/auth/logout", headers=CSRF).status_code == 204
    assert c.get("/api/v1/auth/me").status_code == 401
    assert audit_rows("auth.logout")
    for row in audit_rows("auth.login") + audit_rows("auth.logout"):
        assert PW not in json.dumps(row["details"])


def test_unauthenticated_requests_are_rejected(app):
    from fastapi.testclient import TestClient

    c = TestClient(app, base_url="http://127.0.0.1")
    assert c.get("/api/v1/conversations").status_code == 401
    assert c.get("/api/v1/documents").status_code == 401
    # a forged/invalid cookie is rejected and audited
    c.cookies.set("dayanera_session", "forged-token")
    assert c.get("/api/v1/documents").status_code == 401
    assert audit_rows("access.unauthenticated")


def test_expired_session_is_rejected_and_audited(app):
    from fastapi.testclient import TestClient

    from app.db.models import AuthSession
    from app.db.session import session_scope

    c = TestClient(app, base_url="http://127.0.0.1")
    c.post("/api/v1/auth/login", json={"username": "admin", "password": PW}, headers=CSRF)
    with session_scope() as db:
        sid = db.execute(select(AuthSession.id).order_by(AuthSession.created_at.desc()).limit(1)).scalar()
        db.execute(update(AuthSession).where(AuthSession.id == sid)
                   .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    assert c.get("/api/v1/auth/me").status_code == 401
    assert audit_rows("auth.session_expired")


def test_csrf_header_required_for_state_changes(admin):
    r = admin.c.post("/api/v1/conversations", json={})  # no CSRF header
    assert r.status_code == 403
    assert "CSRF" in r.json()["detail"]
    assert admin.post("/conversations", {}).status_code == 201


def test_session_token_is_stored_hashed(admin):
    from app.db.models import AuthSession
    from app.db.session import session_scope

    token = admin.c.cookies.get("dayanera_session")
    with session_scope() as db:
        hashes = db.execute(select(AuthSession.token_hash)).scalars().all()
    assert token not in hashes
    assert all(len(h) == 64 for h in hashes)


def test_password_hashing_roundtrip():
    from app.core.security import hash_password, verify_password

    h = hash_password("ornek-parola")
    assert h.startswith("pbkdf2_sha256$") and "ornek-parola" not in h
    assert verify_password("ornek-parola", h) and not verify_password("ornek-parola-x", h)
    assert not verify_password("ornek-parola", "garbage")
