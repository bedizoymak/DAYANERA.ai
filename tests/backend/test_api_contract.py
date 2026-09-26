"""DAYANERA Core API contract: OpenAPI is generated and covers every required group."""
from __future__ import annotations

import json

from conftest import REPO

REQUIRED = [
    "/api/v1/auth/login", "/api/v1/auth/me", "/api/v1/health", "/api/v1/readiness", "/api/v1/system/status",
    "/api/v1/conversations", "/api/v1/conversations/{conv_id}/messages", "/api/v1/conversations/{conv_id}/messages/stream",
    "/api/v1/attachments", "/api/v1/documents", "/api/v1/documents/{doc_id}/versions", "/api/v1/ingestion/reindex-all",
    "/api/v1/documents/{doc_id}/reindex", "/api/v1/retrieval/search", "/api/v1/messages/{message_id}/sources",
    "/api/v1/extractions/values/{value_id}/confirm", "/api/v1/calculations", "/api/v1/memory/search",
    "/api/v1/memory/items", "/api/v1/audit", "/api/v1/users", "/api/v1/users/{user_id}/scopes", "/api/v1/notes",
    "/api/v1/providers/status", "/api/v1/integrations/supabase/status",
]


def test_openapi_is_generated_and_complete(client):
    spec = client.get("/api/v1/openapi.json").json()
    assert spec["info"]["title"] == "DAYANERA Core API"
    missing = [p for p in REQUIRED if p not in spec["paths"]]
    assert not missing, missing
    assert "MessageOut" in spec["components"]["schemas"] and "CalcOut" in spec["components"]["schemas"]


def test_exported_openapi_document_is_current(client):
    exported = REPO / "docs" / "openapi.json"
    assert exported.exists(), "run: python -m app.cli export-openapi"
    live = client.get("/api/v1/openapi.json").json()
    assert sorted(json.loads(exported.read_text(encoding="utf-8"))["paths"]) == sorted(live["paths"])


def test_health_and_readiness_are_public_and_secret_free(client, settings):
    h = client.get("/api/v1/health").json()
    assert h["status"] == "ok"
    r = client.get("/api/v1/readiness").json()
    assert r["database"]["ok"] and r["database"]["migration_revision"] == "0001_initial"
    body = json.dumps(r)
    assert settings.postgres_password.get_secret_value() not in body


def test_system_status_hides_owner_sections_from_members(admin, member_factory):
    s = admin.get("/system/status").json()
    assert {"storage", "supabase", "providers"} <= set(s)
    assert s["app"]["localhost_only"] is True
    m = member_factory().get("/system/status").json()
    assert not ({"storage", "supabase", "providers"} & set(m))
