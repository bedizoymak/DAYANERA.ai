"""Git hygiene: private runtime data is ignored and no secrets are tracked."""
from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from conftest import REPO, ROOT_ENV

pytestmark = pytest.mark.skipif(not (REPO / ".git").exists() or not shutil.which("git"), reason="Git deposu yok")

MUST_IGNORE = [".env", ".env.production", "data/documents/x.pdf", "data/document-versions/a/v0001.pdf", "data/logs/b.log",
               "agent-notes/2026-09-26_x.md", "IMPLEMENTATION_REPORT.md", "app.log", "postgres-data/base",
               "pgdata/base", "backend/__pycache__/m.pyc", ".pytest_cache/v", "backend/.venv/pyvenv.cfg",
               "frontend/node_modules/react/index.js", "frontend/dist/index.html", "coverage/lcov.info",
               "iso booklets/ISO 53.pdf", "data/backups/dump.sql", "repositories/x/README.md"]
MUST_TRACK = [".env.example", "README.md", "docker-compose.yml", "backend/requirements.lock.txt",
              "frontend/package-lock.json", "database/migrations/sql/0001_initial.sql", "scripts/start-local.ps1"]


def _ignored(path: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", "--no-index", "--", path], cwd=REPO).returncode == 0


@pytest.mark.parametrize("path", MUST_IGNORE)
def test_private_runtime_paths_are_ignored(path):
    assert _ignored(path), f"{path} Git tarafından yok sayılmalı"


@pytest.mark.parametrize("path", MUST_TRACK)
def test_source_artifacts_are_not_ignored(path):
    assert not _ignored(path), f"{path} izlenebilir olmalı"


def test_env_example_has_placeholders_only():
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    real_pw = ROOT_ENV.get("POSTGRES_PASSWORD", "")
    if real_pw and real_pw != "change-local-password":
        assert real_pw not in text
    assert re.search(r"^POSTGRES_PASSWORD=change-local-password$", text, re.M)
    for key in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        assert re.search(rf"^{key}=$", text, re.M), f"{key} boş yer tutucu olmalı"


def test_preflight_script_passes():
    exe = shutil.which("pwsh") or shutil.which("powershell")
    if not exe:
        pytest.skip("PowerShell yok")
    r = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(REPO / "scripts" / "git-check-private-data.ps1")], cwd=REPO, capture_output=True, timeout=300)
    out = r.stdout.decode("utf-8", "replace")
    assert r.returncode == 0, out[-3000:]
    assert "GECTI" in out
