"""Roles, data scopes, failed-authorization auditing and audit immutability."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from conftest import audit_rows


@pytest.mark.parametrize("method,path", [
    ("get", "/users"), ("post", "/users"), ("post", "/ingestion/scan"), ("post", "/ingestion/reindex-all"),
    ("get", "/ingestion/jobs"), ("get", "/integrations/supabase/status"),
])
def test_member_cannot_use_owner_endpoints_and_denial_is_audited(member_factory, method, path):
    m = member_factory()
    before = len(audit_rows("access.denied"))
    r = getattr(m, method)(path) if method == "get" else m.post(path, {})
    assert r.status_code == 403
    assert r.json()["detail"] == "Bu işlem için yetkiniz yok."
    rows = audit_rows("access.denied")
    assert len(rows) == before + 1 and rows[-1]["actor"] == m.user["username"] and rows[-1]["outcome"] == "denied"


def test_member_sees_only_assigned_documents(admin, member_factory, process_jobs):
    doc = admin.upload("/documents/upload", "yonetici.txt", "gizli ölçüm raporu".encode("utf-8"), "text/plain").json()
    process_jobs()
    m = member_factory()
    assert doc["id"] not in [d["id"] for d in m.get("/documents").json()["items"]]
    assert m.get(f"/documents/{doc['id']}").status_code == 403
    assert audit_rows("access.denied", target_id=doc["id"])
    scoped = member_factory([("document", doc["id"])])
    assert doc["id"] in [d["id"] for d in scoped.get("/documents").json()["items"]]
    assert scoped.get(f"/documents/{doc['id']}").status_code == 200
    # members cannot delete documents they do not own
    assert scoped.delete(f"/documents/{doc['id']}", {"reason": "x"}).status_code == 403


def test_member_conversation_scope(admin, member_factory):
    cid = admin.post("/conversations", {}).json()["id"]
    m = member_factory()
    assert m.get(f"/conversations/{cid}/messages").status_code == 403
    shared = member_factory([("conversation", cid)])
    assert shared.get(f"/conversations/{cid}/messages").status_code == 200
    assert shared.post(f"/conversations/{cid}/messages", {"content": "yazabilir miyim?"}).status_code == 403  # read-only share


def test_owner_sees_all_audit_rows_member_sees_own(admin, member_factory):
    m = member_factory()
    m.post("/conversations", {})
    own = m.get("/audit?limit=500").json()["items"]
    assert own and all(e["actor_username"] == m.user["username"] for e in own)
    everything = admin.get("/audit?limit=1000").json()
    assert everything["total"] > len(own)
    assert {e["actor_username"] for e in everything["items"]} - {m.user["username"]}
    filtered = admin.get("/audit?event_type=auth.login&outcome=success").json()["items"]
    assert filtered and all(e["event_type"] == "auth.login" and e["outcome"] == "success" for e in filtered)


def test_role_and_scope_changes_are_audited(admin, member_factory):
    m = member_factory()
    assert admin.patch(f"/users/{m.id}", {"role": "owner_admin"}).json()["role"] == "owner_admin"
    assert audit_rows("role.change", target_id=m.id)
    area = admin.get("/knowledge-areas").json()[0]
    s = admin.post(f"/users/{m.id}/scopes", {"scope_type": "knowledge_area", "scope_id": area["id"]}).json()
    assert audit_rows("scope.grant", target_id=m.id)
    assert admin.delete(f"/users/{m.id}/scopes/{s['id']}").status_code == 204
    assert audit_rows("scope.revoke", target_id=m.id)
    assert admin.patch(f"/users/{admin.user['id']}", {"role": "member"}).status_code == 409


def test_password_reset_revokes_sessions(admin, member_factory):
    m = member_factory()
    assert m.get("/auth/me").status_code == 200
    admin.patch(f"/users/{m.id}", {"password": "yeni-parola-456"})
    assert m.get("/auth/me").status_code == 401


def test_audit_log_is_append_only(admin):
    from app.db.session import get_engine

    assert audit_rows("auth.login")
    eng = get_engine()
    for stmt in ("UPDATE audit_events SET outcome = 'success'", "DELETE FROM audit_events", "TRUNCATE audit_events"):
        with eng.connect() as conn:
            with pytest.raises(Exception) as exc:
                conn.execute(text(stmt))
            assert "append-only" in str(exc.value)
            conn.rollback()


def test_audit_details_are_sanitized():
    from app.services.audit import sanitize

    out = sanitize({"password": "1234", "nested": {"api_key": "sk-x", "ok": 1}, "session_token": "t"})
    assert out == {"password": "[redacted]", "nested": {"api_key": "[redacted]", "ok": 1}, "session_token": "[redacted]"}
