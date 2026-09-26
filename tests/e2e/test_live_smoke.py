"""LIVE end-to-end smoke test against the running local stack.

Requires: scripts/start-local.ps1 (backend 127.0.0.1:8000, PostgreSQL, Ollama
with the configured Qwen model) and the real ISO 53 PDF in "iso booklets".
Run:  backend\\.venv\\Scripts\\python -m pytest tests/e2e -m live -k main
      (restart the stack)  ... -k persistence

Path: login -> ingest ISO PDF -> indexed/active -> verified question ->
"kaynak ver" -> citation -> calculation -> engine result -> audit events.
Negative: unsupported question -> exact refusal; draft OCR value rejected.
"""
from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.live

BASE = os.environ.get("DAYANERA_E2E_BASE", "http://127.0.0.1:8000/api/v1")
REPO = Path(__file__).resolve().parents[2]
STATE = REPO / "data" / "run" / "e2e-state.json"
REFUSAL = "Bu kaynak setinde doğrulayamadım"
H = {"X-DAYANERA-CSRF": "1"}


def _env_admin() -> tuple[str, str]:
    env = {}
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env.get("INITIAL_ADMIN_USERNAME", "admin"), env.get("INITIAL_ADMIN_PASSWORD", "")


@pytest.fixture(scope="module")
def api():
    c = httpx.Client(base_url=BASE, timeout=1200, trust_env=False)
    try:
        c.get("/health").raise_for_status()
    except Exception:
        pytest.skip("Canlı yığın çalışmıyor (scripts/start-local.ps1)")
    user, pw = _env_admin()
    r = c.post("/auth/login", json={"username": user, "password": pw}, headers=H)
    assert r.status_code == 200, r.text
    yield c
    c.close()


def _say(api, conv, text):
    t0 = time.time()
    r = api.post(f"/conversations/{conv}/messages", json={"content": text}, headers=H)
    assert r.status_code == 200, r.text
    msg = r.json()["assistant_message"]
    print(f"\n[{time.time() - t0:5.0f}s] {text!r} -> mode={msg['answer_mode']} :: {msg['content'][:200]!r}")
    return msg


def _wait_jobs(api, timeout=1800):
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = api.get("/ingestion/jobs?limit=500").json()
        if not [j for j in jobs if j["status"] in ("queued", "running")]:
            return
        time.sleep(3)
    raise AssertionError("ingestion jobs did not finish")


def test_main_smoke_path(api):
    ready = api.get("/readiness").json()
    assert ready["ready"] and ready["ollama"]["reachable"] and ready["ollama"]["model_available"], ready

    # --- ingest an ISO PDF (watcher scan + explicit reindex of ISO 53) -> indexed / active
    api.post("/ingestion/scan", headers=H).raise_for_status()
    docs = api.get("/documents", params={"q": "ISO 53", "limit": 50}).json()["items"]
    iso53 = next(d for d in docs if d["standard_code"] == "ISO 53:1998")
    api.post(f"/documents/{iso53['id']}/reindex", headers=H).raise_for_status()
    _wait_jobs(api)
    iso53 = api.get(f"/documents/{iso53['id']}").json()
    assert iso53["status"] == "active" and iso53["is_verified_corpus"]
    assert iso53["current_version"]["ingestion_status"] == "indexed" and iso53["current_version"]["is_active"]

    conv = api.post("/conversations", json={"title": "E2E duman testi"}, headers=H).json()["id"]
    general = _say(api, conv, "Merhaba! Tek cümleyle kendini tanıtır mısın?")
    assert general["answer_mode"] == "general" and general["status"] == "complete" and general["content"]

    # --- verified question (sources hidden by default)
    ans = _say(api, conv, "ISO 53 standart temel kremayer profilinde basınç açısı nedir?")
    assert ans["answer_mode"] == "verified_source", ans
    assert "20" in ans["content"]
    assert ans["show_sources"] is False and ans["sources"] == []

    # --- explicit "kaynak ver" -> citation appears
    src = _say(api, conv, "kaynak ver")
    assert src["show_sources"] is True
    assert src["sources"] and all(s["standard_code"] == "ISO 53:1998" for s in src["sources"])
    assert all(s["confidence_status"] in ("verified_source", "user_confirmed") for s in src["sources"])

    # --- calculation -> deterministic engine result (+ Qwen draft comparison)
    calc = _say(api, conv, "z=20, m=2 mm dişli geometrisini hesapla")
    assert calc["answer_mode"] == "calculation"
    assert "**40 mm**" in calc["content"] and "**44 mm**" in calc["content"]
    detail = api.get(f"/messages/{calc['id']}/calculation").json()
    assert detail["status"] == "ok" and detail["comparison"]["performed"] is True
    out = {o["key"]: o["value"] for o in detail["result"]["outputs"]}
    assert out["d"] == pytest.approx(40) and out["d_f"] == pytest.approx(35)
    if detail["mismatch"]:
        assert "UYUŞMADI" in calc["content"]

    # --- audit events exist for every step
    def has(event, **params):
        items = api.get("/audit", params={"event_type": event, "limit": 200, **params}).json()["items"]
        return items

    assert has("auth.login", outcome="success")
    assert has("document.reindex") and has("ingestion.completed")
    assert has("source.used") and has("sources.revealed") and has("calculation.run")
    assert [e for e in has("chat.message") if e["target_id"] == conv]

    # --- negative: unsupported / unverifiable technical question -> exact phrase
    neg = _say(api, conv, "ISO 6336'ya göre diş dibi gerilmesi için hangi güvenlik katsayısı önerilir?")
    assert neg["content"] == REFUSAL and neg["answer_mode"] == "unverified"

    # --- negative: draft OCR value rejected from calculation before confirmation
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 220), "white")
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 44)
    except OSError:
        font = ImageFont.load_default()
    ImageDraw.Draw(img).text((20, 60), "m = 2.5 mm", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    up = api.post("/attachments", files={"file": ("e2e-cizim.png", buf.getvalue(), "image/png")}, headers=H).json()
    _wait_jobs(api)
    vals = api.get("/extractions/values", params={"document_id": up["id"]}).json()["items"]
    ev = next(v for v in vals if v["label"] == "m")
    assert ev["status"] == "draft_extraction"
    blocked = api.post("/calculations", headers=H, json={
        "calc_type": "cylindrical_gear_geometry",
        "inputs": {"z": {"value": 20}, "m_n": {"value": 2.5, "unit": "mm", "provenance": "extracted_value", "ref_id": ev["id"]}},
    }).json()
    assert blocked["status"] == "invalid_input"
    assert any(d["code"] == "draft_input_blocked" for d in blocked["result"]["diagnostics"])

    STATE.parent.mkdir(parents=True, exist_ok=True)
    msgs = api.get(f"/conversations/{conv}/messages").json()
    STATE.write_text(json.dumps({"conversation_id": conv, "message_count": len(msgs), "iso53_id": iso53["id"],
                                 "attachment_id": up["id"], "calculation_id": detail["id"]}), encoding="utf-8")


def test_persistence_after_restart(api):
    if not STATE.exists():
        pytest.skip("Önce ana duman testi çalıştırılmalı")
    st = json.loads(STATE.read_text(encoding="utf-8"))
    msgs = api.get(f"/conversations/{st['conversation_id']}/messages").json()
    assert len(msgs) >= st["message_count"]
    assert any(m["answer_mode"] == "verified_source" for m in msgs)
    assert api.get(f"/documents/{st['iso53_id']}").json()["status"] == "active"
    assert api.get(f"/documents/{st['attachment_id']}").json()["current_version"]["ingestion_status"] == "indexed"
    assert api.get(f"/calculations/{st['calculation_id']}").json()["status"] == "ok"
    assert api.get("/audit", params={"event_type": "system.startup"}).json()["total"] >= 2


def test_step2_inventory_is_fast_and_llm_free(api):
    """Step 2 Order E: T2 returns the standards list without the LLM in < 2 s."""
    conv = api.post("/conversations", json={"title": "Step 2 envanter"}, headers=H).json()["id"]
    t0 = time.perf_counter()
    msg = _say(api, conv, "peki elinde hangi ISO standartları var, listeler misin?")
    assert time.perf_counter() - t0 < 2.0
    assert msg["metadata"]["kind"] == "corpus_inventory" and msg["model"] is None
    assert "ISO 53:1998" in msg["content"] and "ISO 21771:2007" in msg["content"]


def test_step2_missing_standard_refusal_is_fast(api):
    conv = api.post("/conversations", json={"title": "Step 2 eksik standart"}, headers=H).json()["id"]
    t0 = time.perf_counter()
    msg = _say(api, conv, "ISO 2768 m sınıfı 30-120 mm tolerans ne? kaynak ver")
    assert time.perf_counter() - t0 < 5.0
    assert msg["content"] == REFUSAL and msg["metadata"]["refusal"]["codes_missing"] == ["ISO 2768"]
