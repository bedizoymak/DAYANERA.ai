"""Beta boundaries: localhost-only bindings, disabled online providers,
path containment, Supabase readiness without data transfer, redaction."""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from app.core.config import ConfigError, Settings
from conftest import REPO


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


@pytest.mark.parametrize("field", ["app_host", "frontend_host", "postgres_host"])
@pytest.mark.parametrize("value", ["0.0.0.0", "192.168.1.10", "::"])
def test_non_loopback_bindings_are_rejected(field, value):
    with pytest.raises(ConfigError):
        _settings(**{field: value})


def test_remote_database_and_ollama_are_rejected():
    with pytest.raises(ConfigError):
        _settings(database_url="postgresql+psycopg://u:p@db.example.com:5432/x")
    with pytest.raises(ConfigError):
        _settings(ollama_base_url="http://10.0.0.5:11434")
    with pytest.raises(ConfigError):
        _settings(ollama_base_url="https://api.example.com")


def test_online_providers_cannot_be_enabled_in_beta():
    with pytest.raises(ConfigError):
        _settings(openai_enabled=True)
    with pytest.raises(ConfigError):
        _settings(anthropic_enabled=True)


def test_disabled_provider_adapters_never_generate():
    from app.inference.base import ChatMessage, ProviderDisabledError
    from app.inference.disabled import ClaudeProvider, OpenAIProvider

    for cls in (OpenAIProvider, ClaudeProvider):
        p = cls(configured=True)
        assert p.enabled is False
        with pytest.raises(ProviderDisabledError):
            p.chat([ChatMessage("user", "x")])
        with pytest.raises(ProviderDisabledError):
            list(p.stream_chat([ChatMessage("user", "x")]))


def test_application_data_must_stay_below_project_root(tmp_path):
    (tmp_path / "proj").mkdir()
    s = _settings(project_root=tmp_path / "proj", data_root=tmp_path / "elsewhere",
                  agent_notes_path=tmp_path / "proj" / "agent-notes", iso_booklets_path=tmp_path / "proj" / "iso")
    with pytest.raises(ConfigError):
        s.validate_paths()
    ok = _settings(project_root=tmp_path / "proj", data_root=tmp_path / "proj" / "data",
                   agent_notes_path=tmp_path / "proj" / "agent-notes", iso_booklets_path=tmp_path / "proj" / "iso")
    warnings = ok.validate_paths()
    assert any("ISO_BOOKLETS_PATH" in w for w in warnings)  # missing folder -> precise instruction
    created = ok.ensure_runtime_dirs()
    assert all(str(p).startswith(str(tmp_path / "proj")) for p in created)
    assert not (tmp_path / "elsewhere").exists()


def test_ollama_provider_refuses_non_local_url_even_if_constructed(settings):
    from app.inference.base import ProviderError
    from app.inference.ollama import LocalOllamaProvider

    bad = settings.model_copy(update={"ollama_base_url": "http://example.com:11434"})
    with pytest.raises(ProviderError):
        LocalOllamaProvider(bad)


def test_ollama_unavailable_is_reported_as_provider_error(settings):
    from app.inference.base import ChatMessage, ProviderUnavailableError
    from app.inference.ollama import LocalOllamaProvider

    s = settings.model_copy(update={"ollama_base_url": "http://127.0.0.1:9"})
    p = LocalOllamaProvider(s)
    with pytest.raises(ProviderUnavailableError) as exc:
        p.chat([ChatMessage("user", "merhaba")])
    assert "Ollama" in exc.value.user_message
    h = p.health()
    assert h.reachable is False and h.model_available is False


def test_trusted_host_blocks_dns_rebinding(client):
    r = client.get("/api/v1/health", headers={"Host": "evil.example.com"})
    assert r.status_code == 400
    assert client.get("/api/v1/health").status_code == 200


def test_docker_compose_publishes_postgres_on_loopback_only():
    text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    ports = re.findall(r'-\s*"([^"]+):5432"', text)
    assert ports and all(p.startswith("127.0.0.1:") for p in ports)
    assert "0.0.0.0" not in text
    assert "ollama" not in text.lower().split("services:")[1].split("volumes:")[0] or "image: ollama" not in text


def test_scripts_and_vite_bind_loopback_only():
    vite = (REPO / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert "LOOPBACK" in vite and "strictPort: true" in vite
    start = (REPO / "scripts" / "start-local.ps1").read_text(encoding="utf-8")
    code = re.sub(r"<#.*?#>", "", start, flags=re.S)
    code_lines = [ln for ln in code.splitlines() if not ln.strip().startswith("#")]
    assert not [ln for ln in code_lines if "0.0.0.0" in ln], "start script must never bind 0.0.0.0"
    assert "Test-PortLoopbackOnly" in start and "--host', $feHost" in start
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    for key in ("APP_HOST", "FRONTEND_HOST", "POSTGRES_HOST"):
        assert re.search(rf"^{key}=127\.0\.0\.1$", example, re.M)
    assert re.search(r"^SUPABASE_ENABLED=false$", example, re.M)


def test_supabase_status_owner_only_and_reveals_no_secrets(admin, member_factory, settings, app):
    r = admin.get("/integrations/supabase/status")
    assert r.status_code == 200
    body = r.json()
    assert body["data_sent"] is False and body["sync_implemented"] is False and body["network_calls"] == "none"
    member = member_factory()
    assert member.get("/integrations/supabase/status").status_code == 403


def test_supabase_status_with_keys_configured_never_returns_them(settings):
    from app.services.system_status import supabase_status

    s = settings.model_copy(update={"supabase_enabled": False, "supabase_url": "https://proj.supabase.co",
                                    # fake key built by concatenation so the Git secret scanner stays strict
                                    "supabase_secret_key": type(settings.supabase_secret_key)("sb_" + "secret_TESTVALUE123")})
    out = supabase_status(s)
    assert out["url_configured"] and out["secret_key_configured"]
    assert "TESTVALUE" not in str(out) and "proj.supabase.co" not in str(out)
    assert out["data_sent"] is False


def test_provider_status_endpoint_has_no_keys(admin):
    r = admin.get("/providers/status")
    assert r.status_code == 200
    data = r.json()
    assert data["online_calls_allowed"] is False
    assert [p for p in data["providers"] if p["name"] != "local_ollama" and p["enabled"]] == []
    assert "api_key" not in r.text.lower()


def test_log_redaction_masks_secrets():
    from app.core.logging import RedactingFilter

    f = RedactingFilter(["1234", "s3cr3t-pg-password"])
    rec = logging.LogRecord("t", logging.INFO, __file__, 1,
                            "login admin password=1234 url=postgresql+psycopg://u:s3cr3t-pg-password@127.0.0.1/x", None, None)
    f.filter(rec)
    assert "1234" not in rec.msg and "s3cr3t-pg-password" not in rec.msg
    rec2 = logging.LogRecord("t", logging.INFO, __file__, 1, "IT7 12345 değeri", None, None)
    f.filter(rec2)
    assert rec2.msg == "IT7 12345 değeri"


def test_frontend_bundle_contains_no_secrets():
    dist = REPO / "frontend" / "dist"
    if not dist.exists():
        pytest.skip("frontend/dist yok (npm run build)")
    env = (REPO / ".env").read_text(encoding="utf-8") if (REPO / ".env").exists() else ""
    m = re.search(r"^POSTGRES_PASSWORD=(.+)$", env, re.M)
    secrets = [m.group(1).strip()] if m else []
    for f in Path(dist).rglob("*.js"):
        content = f.read_text(encoding="utf-8", errors="ignore")
        for s in secrets:
            assert s not in content
        assert "INITIAL_ADMIN_PASSWORD" not in content and "SUPABASE_SECRET_KEY" not in content
