"""Calculation API/chat integration: sourced evidence, Qwen draft comparison,
draft OCR input rejection before confirmation, refusals."""
from __future__ import annotations

import io
import json
import time

import pytest

from app.domain.enums import REFUSAL_PHRASE
from conftest import audit_rows
from synthetic_corpus import write_all


@pytest.fixture(scope="module")
def calc_corpus(settings, admin):
    from app.ingestion.jobs import run_pending
    from app.ingestion.watcher import Watcher

    write_all(settings.iso_booklets_path / "calc")
    time.sleep(2.1)
    Watcher(settings).scan()
    run_pending(settings)
    types = {t["calc_type"]: t for t in admin.get("/calculations/types").json()}
    missing = {k: [r for r, ok in v["evidence_available"].items() if not ok] for k, v in types.items()}
    assert all(t["available"] for t in types.values()), missing
    yield types


def _draft_responder(outputs: dict):
    def respond(messages):
        system = messages[0].content
        if "TASLAK" in system and "outputs" in system:
            return json.dumps({"outputs": outputs})
        if "SADECE JSON" in system:
            return '{"calc_type": "unsupported"}'
        return "x"
    return respond


def test_calculation_api_returns_sourced_result(admin, calc_corpus):
    r = admin.post("/calculations", {"calc_type": "cylindrical_gear_geometry",
                                     "inputs": {"z": {"value": 20}, "m_n": {"value": 2, "unit": "mm"}}})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["status"] == "ok"
    outs = {o["key"]: o for o in c["result"]["outputs"]}
    assert outs["d"]["value"] == pytest.approx(40) and outs["d"]["unit"] == "mm"
    assert outs["d"]["formula_id"].startswith("ISO21771")
    ev = c["result"]["evidence"]
    assert {e["standard_code"] for e in ev} == {"ISO 21771:2007", "ISO 53:1998"}
    assert all(e["confidence_status"] == "verified_source" and e["page_number"] for e in ev)
    assert c["result"]["inputs"][0]["provenance"]["kind"] == "user_input"
    assert audit_rows("calculation.run", target_id=c["id"])


def test_calculation_invalid_unit_via_api(admin, calc_corpus):
    r = admin.post("/calculations", {"calc_type": "cylindrical_gear_geometry",
                                     "inputs": {"z": {"value": 20}, "m_n": {"value": 2, "unit": "kg"}}})
    assert r.json()["status"] == "invalid_input"
    assert r.json()["result"]["diagnostics"][0]["code"] == "invalid_unit"


def test_chat_calculation_mismatch_is_shown_and_audited(admin, fake_llm, calc_corpus):
    fake_llm.responder = _draft_responder({"d": 40.0, "d_a": 46.0, "d_f": 35.0})
    conv = admin.post("/conversations", {}).json()["id"]
    a = admin.post(f"/conversations/{conv}/messages", {"content": "z=20, m=2 mm dişli geometrisini hesapla"}).json()["assistant_message"]
    assert a["answer_mode"] == "calculation" and a["answer_mode_label"] == "Hesap sonucu"
    assert a["metadata"]["mismatch"] is True
    assert "UYUŞMADI" in a["content"] and "**44 mm**" in a["content"]  # engine value shown, not Qwen's 46
    detail = admin.get(f"/messages/{a['id']}/calculation").json()
    bad = [i for i in detail["comparison"]["items"] if i["status"] == "mismatch"]
    assert [i["key"] for i in bad] == ["d_a"]
    assert detail["result"]["trace"] and detail["result"]["evidence"]
    assert audit_rows("calculation.llm_mismatch", target_id=detail["id"])
    # sources are available only on explicit request
    assert a["show_sources"] is False and a["sources"] == []
    s = admin.post(f"/conversations/{conv}/messages", {"content": "kaynak ver"}).json()["assistant_message"]
    assert s["show_sources"] and {x["standard_code"] for x in s["sources"]} >= {"ISO 21771:2007"}


def test_chat_calculation_matching_draft(admin, fake_llm, calc_corpus):
    fake_llm.responder = _draft_responder({"IT": 5.0})
    conv = admin.post("/conversations", {}).json()["id"]
    a = admin.post(f"/conversations/{conv}/messages", {"content": "40 mm anma ölçüsü için IT6 kaç µm? ayrıntılı çözüm"}).json()["assistant_message"]
    assert a["answer_mode"] == "calculation"
    assert a["metadata"]["detail_open"] is True
    detail = admin.get(f"/messages/{a['id']}/calculation").json()
    assert detail["result"]["outputs"][0]["formula_id"] == "ISO286-1:2010 Tablo 1"


def test_calculation_works_without_llm(admin, fake_llm, calc_corpus):
    from app.inference.base import ProviderUnavailableError

    fake_llm.fail_with = ProviderUnavailableError("down")
    conv = admin.post("/conversations", {}).json()["id"]
    a = admin.post(f"/conversations/{conv}/messages", {"content": "z=25, m=3 mm hesapla"}).json()["assistant_message"]
    assert a["answer_mode"] == "calculation" and "**75 mm**" in a["content"]
    assert "Qwen taslağı alınamadı" in a["content"]


def test_unsupported_calculation_request_is_refused(admin, fake_llm, calc_corpus):
    fake_llm.responder = _draft_responder({})
    conv = admin.post("/conversations", {}).json()["id"]
    a = admin.post(f"/conversations/{conv}/messages", {"content": "Dişli mukavemet hesabını yap, tork 250 Nm"}).json()["assistant_message"]
    assert a["content"] == REFUSAL_PHRASE and a["answer_mode"] == "unverified"
    r = admin.post("/calculations", {"calc_type": "tooth_root_stress", "inputs": {}}).json()
    assert r["status"] == "refused" and r["result"]["message"] == REFUSAL_PHRASE


def _image(text: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 220), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 44)
    except OSError:
        font = ImageFont.load_default()
    d.text((20, 60), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.slow
def test_draft_ocr_value_rejected_until_confirmed(admin, calc_corpus, process_jobs):
    doc_id = admin.upload("/attachments", "resim-modul.png", _image("m = 2.5 mm"), "image/png").json()["id"]
    process_jobs()
    vals = admin.get(f"/extractions/values?document_id={doc_id}").json()["items"]
    ev = next(v for v in vals if v["label"] == "m")
    assert ev["status"] == "draft_extraction"
    body = {"calc_type": "cylindrical_gear_geometry",
            "inputs": {"z": {"value": 20}, "m_n": {"value": 2.5, "unit": "mm", "provenance": "extracted_value", "ref_id": ev["id"]}}}
    blocked = admin.post("/calculations", body).json()
    assert blocked["status"] == "invalid_input"
    assert any(d["code"] == "draft_input_blocked" for d in blocked["result"]["diagnostics"])
    assert blocked["result"]["outputs"] == []
    assert audit_rows("calculation.draft_input_rejected", target_id=blocked["id"])

    # authorized confirmation with an edited value; the confirmed value (not the client value) is used
    conf = admin.post(f"/extractions/values/{ev['id']}/confirm", {"value": 2.5, "unit": "mm", "note": "çizimle kontrol edildi"})
    assert conf.status_code == 200 and conf.json()["status"] == "user_confirmed"
    rows = audit_rows("extraction.confirm", target_id=ev["id"])
    assert rows and rows[0]["details"]["original"]["raw_text"] and rows[0]["details"]["version_id"]
    body["inputs"]["m_n"]["value"] = 999  # ignored: provenance value comes from the confirmed record
    ok = admin.post("/calculations", body).json()
    assert ok["status"] == "ok"
    assert {o["key"]: o["value"] for o in ok["result"]["outputs"]}["d"] == pytest.approx(50.0)
    prov = [i for i in ok["result"]["inputs"] if i["key"] == "m_n"][0]["provenance"]
    assert prov["kind"] == "extracted_value" and prov["status"] == "user_confirmed"
    # confirmed technical value is now persistent, user-confirmed memory
    mem = admin.get("/memory/items?kind=technical_value&status=user_confirmed").json()
    assert any(m["source_extracted_value_id"] == ev["id"] for m in mem)
    # double confirmation is rejected
    assert admin.post(f"/extractions/values/{ev['id']}/confirm", {}).status_code == 409


def test_calculation_refused_when_source_document_deleted(admin, calc_corpus):
    # every active ISO 1328-1 copy (other modules may have ingested the real one too)
    docs = [d for d in admin.get("/documents?limit=500&status=active").json()["items"]
            if d["standard_code"] == "ISO 1328-1:2013"]
    assert docs
    body = {"calc_type": "iso1328_flank_tolerance",
            "inputs": {"m_n": {"value": 3, "unit": "mm"}, "d": {"value": 120, "unit": "mm"}, "A": {"value": 6}}}
    assert admin.post("/calculations", body).json()["status"] == "ok"
    for doc in docs:
        assert admin.delete(f"/documents/{doc['id']}", {"reason": "kaynak kaldırma testi"}).status_code == 200
    r = admin.post("/calculations", body).json()
    assert r["status"] == "refused" and r["result"]["message"] == REFUSAL_PHRASE
    assert any(d["code"] == "missing_source" for d in r["result"]["diagnostics"])
    # owner restore brings it back after re-indexing
    for doc in docs:
        assert admin.post(f"/documents/{doc['id']}/restore").status_code == 200
    from app.ingestion.jobs import run_pending
    from app.core.config import get_settings

    run_pending(get_settings())
    assert admin.post("/calculations", body).json()["status"] == "ok"
