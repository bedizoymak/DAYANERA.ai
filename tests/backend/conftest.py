"""Shared fixtures for backend unit/integration tests.

Integration tests run against a *temporary* database (``dayanera_test``) in
the local Docker PostgreSQL, a temporary project root (tmp dir) and a
deterministic fake LLM provider. The real ``dayanera`` database, the real
corpus folder and Ollama are never modified by these tests.
"""
from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app.core.config import Settings  # noqa: E402


def _read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


ROOT_ENV = _read_env(REPO / ".env")
BASE_DB_URL = os.environ.get("DAYANERA_TEST_BASE_DATABASE_URL") or ROOT_ENV.get(
    "DATABASE_URL", "postgresql+psycopg://dayanera:change-local-password@127.0.0.1:54329/dayanera")
TEST_DB = "dayanera_test"
TEST_ADMIN_PASSWORD = "test-admin-parola-9"  # test database only; never the beta credential


def _db_url(name: str) -> str:
    head, _ = BASE_DB_URL.rsplit("/", 1)
    return f"{head}/{name}"


FONT_CANDIDATES = [r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
FONT = next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)


def make_pdf(path: Path, pages: list[str]) -> Path:
    """Create a real, text-layer PDF (one string per page)."""
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        lines = text.count("\n") + 1
        height = max(842, 20 * lines + 100)  # tall page for one-token-per-line tables
        page = doc.new_page(width=595, height=height)
        kwargs = {"fontsize": 9}
        if FONT:
            kwargs.update(fontname="tf", fontfile=FONT)
        rc = page.insert_textbox(pymupdf.Rect(36, 36, 559, height - 36), text, **kwargs)
        assert rc >= 0, "text did not fit on the synthetic page"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def project_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("dayanera_proj")
    (root / "iso booklets").mkdir()
    return root


@pytest.fixture(scope="session")
def test_settings_env(project_root: Path):
    """Environment for the app under test (env vars override the root .env)."""
    env = {
        "DATABASE_URL": _db_url(TEST_DB),
        "PROJECT_ROOT": str(project_root),
        "ISO_BOOKLETS_PATH": str(project_root / "iso booklets"),
        "DATA_ROOT": str(project_root / "data"),
        "AGENT_NOTES_PATH": str(project_root / "agent-notes"),
        "EXTRA_WATCH_ROOTS": "",
        "WATCHER_ENABLED": "false",
        "DISABLE_BACKGROUND_WORKERS": "true",
        "CALC_LLM_COMPARE": "true",
        "SUMMARY_EVERY_N_MESSAGES": "0",
        "INITIAL_ADMIN_USERNAME": "admin",
        "INITIAL_ADMIN_PASSWORD": TEST_ADMIN_PASSWORD,
        "TRUSTED_HOSTS_EXTRA": "testserver",
        "SUPABASE_ENABLED": "false",
        "OPENAI_ENABLED": "false",
        "ANTHROPIC_ENABLED": "false",
        "DRAFT_VALUE_MAX_PAGES": "12",
        # reuse the locally fetched speech model (read-only) if present
        "WHISPER_MODEL_PATH": str(REPO / "data" / "models" / "faster-whisper-small"),
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    from app.core.config import reset_settings_cache

    reset_settings_cache()
    yield env
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    reset_settings_cache()


@pytest.fixture(scope="session")
def database(test_settings_env):
    """Create a fresh temporary database and apply the real migrations."""
    import psycopg

    admin_url = _db_url("postgres").replace("postgresql+psycopg://", "postgresql://")
    try:
        conn = psycopg.connect(admin_url, autocommit=True, connect_timeout=5)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Yerel PostgreSQL erişilemiyor (docker compose up -d postgres): {exc}")
    with conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    from app.cli import cmd_migrate
    from app.db.session import init_engine

    cmd_migrate()
    init_engine(test_settings_env["DATABASE_URL"])
    yield test_settings_env["DATABASE_URL"]


@pytest.fixture(scope="session")
def settings(database) -> Settings:
    from app.core.config import get_settings

    s = get_settings()
    assert "dayanera_test" in s.database_url, "tests must never touch the real database"
    return s


class FakeProvider:
    """Deterministic stand-in for the local Qwen model."""

    name = "local_ollama"
    enabled = True
    model = "fake-qwen"

    def __init__(self):
        self.responder: Callable[[list], str] = self.default
        self.fail_with: Exception | None = None
        self.calls: list[list] = []
        self.options: list = []  # GenerationOptions of each call (Step 2 budget checks)

    @staticmethod
    def default(messages) -> str:
        system = messages[0].content
        if "Doğrulanmış kaynak cevabı" in system:
            return "Bu kaynak setinde doğrulayamadım"
        if "SADECE JSON" in system and "outputs" in system:
            return '{"outputs": {}}'
        if "SADECE JSON" in system:
            return '{"calc_type": "unsupported"}'
        return "Merhaba! Ben DAYANERA.ai, yerel mühendislik asistanınızım."

    def chat(self, messages, options=None):
        from app.inference.base import LLMResult

        self.calls.append(messages)
        self.options.append(options)
        if self.fail_with:
            raise self.fail_with
        return LLMResult(content=self.responder(messages), provider=self.name, model=self.model, latency_ms=5)

    def stream_chat(self, messages, options=None):
        self.calls.append(messages)
        if self.fail_with:
            raise self.fail_with
        text = self.responder(messages)
        for i in range(0, len(text), 7):
            yield text[i:i + 7]

    def health(self):
        from app.inference.base import ProviderHealth

        return ProviderHealth(name=self.name, reachable=self.fail_with is None, model=self.model,
                              model_available=self.fail_with is None, version="fake")


@pytest.fixture(scope="session")
def fake_llm() -> FakeProvider:
    return FakeProvider()


@pytest.fixture(scope="session")
def app(settings, fake_llm):
    from app.inference.registry import set_provider
    from app.main import create_app

    set_provider(fake_llm)
    application = create_app()
    yield application
    set_provider(None)


@pytest.fixture(scope="session")
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_fake(request):
    if "fake_llm" in request.fixturenames:
        fl = request.getfixturevalue("fake_llm")
        fl.responder = fl.default
        fl.fail_with = None
        fl.calls.clear()
        fl.options.clear()
    yield


CSRF = {"X-DAYANERA-CSRF": "1"}


class ApiUser:
    """A logged-in API session (own cookie jar)."""

    def __init__(self, app, username: str, password: str):
        from fastapi.testclient import TestClient

        self.c = TestClient(app, base_url="http://127.0.0.1")
        r = self.c.post("/api/v1/auth/login", json={"username": username, "password": password}, headers=CSRF)
        assert r.status_code == 200, r.text
        self.user = r.json()

    def get(self, path, **kw):
        return self.c.get("/api/v1" + path, **kw)

    def post(self, path, json=None, **kw):
        return self.c.post("/api/v1" + path, json=json if json is not None else {}, headers={**CSRF, **kw.pop("headers", {})}, **kw)

    def patch(self, path, json, **kw):
        return self.c.patch("/api/v1" + path, json=json, headers=CSRF, **kw)

    def delete(self, path, json=None, **kw):
        return self.c.request("DELETE", "/api/v1" + path, json=json, headers=CSRF, **kw)

    def upload(self, path, name: str, data: bytes, mime="application/octet-stream"):
        return self.c.post("/api/v1" + path, files={"file": (name, data, mime)}, headers=CSRF)


@pytest.fixture(scope="session")
def admin(client, app) -> ApiUser:
    return ApiUser(app, "admin", TEST_ADMIN_PASSWORD)


@pytest.fixture()
def member_factory(admin, app):
    def make(scopes: list[tuple[str, str]] | None = None) -> ApiUser:
        name = f"uye{uuid.uuid4().hex[:8]}"
        r = admin.post("/users", {"username": name, "display_name": "Üye", "password": "uye-parola-123", "role": "member"})
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        for st, sid in scopes or []:
            assert admin.post(f"/users/{uid}/scopes", {"scope_type": st, "scope_id": sid}).status_code == 201
        u = ApiUser(app, name, "uye-parola-123")
        u.id = uid
        return u

    return make


def approve_all(note: str = "test fixture: approved after the automated quality gates") -> list[str]:
    """Approve every approvable active version of verified-corpus documents through the REAL
    approval service (as the first active owner_admin). Returns the approved standard codes."""
    from sqlalchemy import select

    from app.db.models import Document, DocumentVersion, KnowledgeArea, User
    from app.db.session import session_scope
    from app.services import corpus

    approved = []
    with session_scope() as db:
        owner = db.execute(select(User).where(User.role == "owner_admin", User.is_active.is_(True))
                           .order_by(User.created_at)).scalars().first()
        if owner is None:
            return approved
        reviewer = corpus.Reviewer(owner.id, owner.username)
        rows = db.execute(
            select(DocumentVersion, Document).join(Document, Document.current_version_id == DocumentVersion.id)
            .join(KnowledgeArea, KnowledgeArea.id == Document.knowledge_area_id)
            .where(KnowledgeArea.is_verified_corpus.is_(True), Document.status == "active",
                   DocumentVersion.ingestion_status == "indexed",
                   DocumentVersion.corpus_status.in_(corpus.APPROVABLE))).all()
        for ver, doc in rows:
            corpus.approve(db, ver.id, reviewer, note)
            approved.append(doc.standard_code or doc.title)
    return approved


def run_jobs(settings, verify: bool = True) -> int:
    """Process queued ingestion jobs; by default approve the results (see ``approve_all``).
    Tests of the approval gate itself pass ``verify=False``."""
    from app.ingestion.jobs import run_pending

    n = run_pending(settings)
    if verify:
        approve_all()
    return n


@pytest.fixture()
def process_jobs(settings):
    return lambda: run_jobs(settings)


def audit_rows(event_type: str, **filters) -> list:
    from sqlalchemy import select

    from app.db.models import AuditEvent
    from app.db.session import session_scope

    with session_scope() as db:
        q = select(AuditEvent).where(AuditEvent.event_type == event_type)
        for k, v in filters.items():
            q = q.where(getattr(AuditEvent, k) == v)
        rows = db.execute(q.order_by(AuditEvent.id)).scalars().all()
        return [{"id": r.id, "actor": r.actor_username, "outcome": r.outcome, "target_id": r.target_id,
                 "details": r.details} for r in rows]
