"""Answer policy: verified answers only from active sources, exact refusal
phrase, citation opt-in, source-state filtering, provider failures."""
from __future__ import annotations

import io
import json
import re
import time

import pytest

from app.domain.enums import REFUSAL_PHRASE
from app.inference.base import ProviderUnavailableError
from conftest import audit_rows, make_pdf


def _scan_and_process(settings):
    from app.ingestion.jobs import run_pending
    from app.ingestion.watcher import Watcher

    time.sleep(2.1)
    Watcher(settings).scan()
    run_pending(settings)


def _grounded_responder(template: str):
    """Fake Qwen: answer with the first number found after `anchor` in the passages."""
    def respond(messages):
        system = messages[0].content
        if "Doğrulanmış kaynak cevabı" not in system:
            return "Genel yanıt."
        user = messages[-1].content
        m = re.search(r"pressure angle[^\d]*(\d+)°", user) or re.search(r"test load is (\d+) kN", user) \
            or re.search(r"backlash gauge class is (\d+)", user) or re.search(r"flange width is (\d+) mm", user)
        if not m:
            return REFUSAL_PHRASE
        return template.format(n=m.group(1))
    return respond


@pytest.fixture(scope="module")
def qa_corpus(settings):
    iso = settings.iso_booklets_path
    make_pdf(iso / "qa-iso8888.pdf", [
        "INTERNATIONAL STANDARD\nISO 8888:2020\nCylindrical gears - basic rack (synthetic test copy)",
        "ISO 8888:2020\n5.5 The flanks of the standard basic rack tooth profile are inclined at the pressure angle.\n"
        "The pressure angle of the standard basic rack tooth profile is 20°.",
    ])
    make_pdf(iso / "qa-iso7777.pdf", [
        "INTERNATIONAL STANDARD\nISO 7777:2021\nGear inspection gauges (synthetic test copy)",
        "ISO 7777:2021\nThe backlash gauge class is 4 for general inspection.",
    ])
    make_pdf(iso / "qa-iso6666.pdf", [
        "INTERNATIONAL STANDARD\nISO 6666:2019\nGear rig testing (synthetic test copy)",
        "ISO 6666:2019\nThe test load is 40 kN for the rig.",
    ])
    _scan_and_process(settings)
    yield


def _chat(user, text, conv_id=None):
    if conv_id is None:
        conv_id = user.post("/conversations", {}).json()["id"]
    r = user.post(f"/conversations/{conv_id}/messages", {"content": text})
    assert r.status_code == 200, r.text
    return conv_id, r.json()["assistant_message"]


def test_verified_answer_hides_sources_until_explicitly_requested(admin, fake_llm, qa_corpus):
    fake_llm.responder = _grounded_responder("Standart temel kremayer (basic rack) profilinde basınç açısı "
                                             "(pressure angle) {n}°'dir [S1].")
    conv, a = _chat(admin, "ISO 8888 standart temel kremayer profilinde basınç açısı nedir?")
    assert a["answer_mode"] == "verified_source"
    assert a["answer_mode_label"] == "Doğrulanmış kaynak cevabı"
    assert "20°" in a["content"] and "[S1]" not in a["content"]
    assert a["show_sources"] is False and a["sources"] == []  # suppressed by default
    assert audit_rows("source.used", target_id=a["id"])  # provenance retained internally

    _, s = _chat(admin, "kaynak ver", conv)
    assert s["show_sources"] is True
    assert s["sources"][0]["standard_code"] == "ISO 8888:2020"
    assert s["sources"][0]["locator"] == "s. 2"
    assert s["sources"][0]["confidence_status"] == "verified_source"
    assert "pressure angle" in s["sources"][0]["excerpt"]
    assert audit_rows("sources.revealed", target_id=s["id"])


def test_question_with_kaynak_ver_shows_sources_immediately(admin, fake_llm, qa_corpus):
    fake_llm.responder = _grounded_responder("Basınç açısı {n}° [S1].")
    _, a = _chat(admin, "ISO 8888'e göre temel kremayer basınç açısı nedir? kaynak ver")
    assert a["answer_mode"] == "verified_source" and a["show_sources"] is True and a["sources"]


@pytest.mark.parametrize("question", [
    "ISO 6336 dişli diş dibi gerilmesi için güvenlik katsayısı nedir?",  # named standard not in corpus
    "Rulman yağlama aralığı nedir?",  # no matching passage at all
])
def test_unanswerable_technical_question_returns_exact_refusal(admin, fake_llm, qa_corpus, question):
    fake_llm.responder = lambda m: "Uydurma cevap 42."  # must never be used
    _, a = _chat(admin, question)
    assert a["content"] == REFUSAL_PHRASE
    assert a["answer_mode"] == "unverified" and a["answer_mode_label"] == "Doğrulanamadı"
    assert a["sources"] == []
    assert fake_llm.calls == [], "no admissible passages -> the model must not even be asked"


def test_hallucinated_number_is_refused(admin, fake_llm, qa_corpus):
    fake_llm.responder = lambda m: ("Basınç açısı 25°'dir [S1]." if "Doğrulanmış" in m[0].content else "x")
    _, a = _chat(admin, "ISO 8888 standart temel kremayer basınç açısı nedir?")
    assert a["content"] == REFUSAL_PHRASE and a["answer_mode"] == "unverified"
    rows = audit_rows("answer.grounding_failed")
    assert rows and "25" in json.dumps(rows[-1]["details"])


def test_model_refusal_is_normalized_to_exact_phrase(admin, fake_llm, qa_corpus):
    fake_llm.responder = lambda m: "Üzgünüm. Bu kaynak setinde doğrulayamadım, çünkü..."
    _, a = _chat(admin, "ISO 8888 temel kremayer profil tanımı nedir?")
    assert a["content"] == REFUSAL_PHRASE


def test_general_chat_is_marked_and_streams(admin, fake_llm):
    fake_llm.responder = lambda m: "Merhaba! Size nasıl yardımcı olabilirim?"
    conv = admin.post("/conversations", {}).json()["id"]
    with admin.c.stream("POST", f"/api/v1/conversations/{conv}/messages/stream", json={"content": "Merhaba, nasılsın?"},
                        headers={"X-DAYANERA-CSRF": "1"}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    events = re.findall(r"^event: (\w+)$", body, re.M)
    assert events[0] == "user_message" and "token" in events and events[-1] == "done"
    final = json.loads(re.search(r"event: assistant_message\ndata: (.*)\n", body).group(1))
    assert final["answer_mode"] == "general" and final["answer_mode_label"] == "Genel sohbet"
    system_prompt = fake_llm.calls[-1][0].content
    assert "DOĞRULANMIŞ DEĞİLDİR" in system_prompt


def test_general_request_for_sources_explains_there_are_none(admin, fake_llm):
    conv, a = _chat(admin, "Merhaba!")
    _, s = _chat(admin, "kaynak ver", conv)
    assert s["show_sources"] is False and s["sources"] == []
    assert "doğrulanmış" in s["content"].lower()


def test_superseded_version_text_is_never_retrieved(admin, fake_llm, qa_corpus, settings):
    make_pdf(settings.iso_booklets_path / "qa-iso6666.pdf", [
        "INTERNATIONAL STANDARD\nISO 6666:2019\nGear rig testing (synthetic test copy)",
        "ISO 6666:2019\nThe test load is 55 kN for the rig.",
    ])
    _scan_and_process(settings)
    r = admin.post("/retrieval/search", {"query": "ISO 6666 test load rig"}).json()
    assert r["passages"], r
    assert all(p["version_number"] == 2 for p in r["passages"])
    assert not any("40 kN" in p["excerpt"] for p in r["passages"])
    fake_llm.responder = _grounded_responder("Test yükü {n} kN [S1].")
    _, a = _chat(admin, "What is the test load for the rig in ISO 6666?")
    assert a["answer_mode"] == "verified_source" and "55 kN" in a["content"]


def test_deleted_document_cannot_support_an_answer(admin, fake_llm, qa_corpus):
    fake_llm.responder = _grounded_responder("Boşluk mastarı sınıfı {n} [S1].")
    _, before = _chat(admin, "What is the backlash gauge class in ISO 7777?")
    assert before["answer_mode"] == "verified_source"
    doc = [d for d in admin.get("/documents?q=7777").json()["items"] if d["standard_code"] == "ISO 7777:2021"][0]
    assert admin.delete(f"/documents/{doc['id']}", {"reason": "test"}).status_code == 200
    fake_llm.calls.clear()
    _, after = _chat(admin, "What is the backlash gauge class in ISO 7777?")
    assert after["content"] == REFUSAL_PHRASE
    assert admin.post("/retrieval/search", {"query": "backlash gauge class ISO 7777"}).json()["passages"] == []
    # calculations likewise refuse: evidence resolver sees no active sources (covered in test_calc_api)


def _scanned_pdf(path, text):
    import pymupdf
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1400, 300), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    d.text((30, 100), text, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=300)
    page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(str(path))


@pytest.mark.slow
def test_draft_ocr_text_is_not_evidence_until_confirmed(admin, fake_llm, settings):
    _scanned_pdf(settings.iso_booklets_path / "qa-scanned.pdf", "The zeta flange width is 12 mm")
    _scan_and_process(settings)
    doc = [d for d in admin.get("/documents?q=qa-scanned").json()["items"]][0]
    vid = doc["current_version"]["id"]
    pages = admin.get(f"/documents/{doc['id']}/versions/{vid}/pages").json()
    assert pages[0]["extraction_method"] == "ocr" and pages[0]["confidence_status"] == "draft_extraction"
    assert admin.post("/retrieval/search", {"query": "zeta flange width"}).json()["passages"] == []
    fake_llm.responder = _grounded_responder("Flanş genişliği {n} mm [S1].")
    _, a = _chat(admin, "What is the zeta flange width?")
    assert a["content"] == REFUSAL_PHRASE
    # an authorized user confirms the OCR page in the review queue -> becomes user_confirmed evidence
    r = admin.post(f"/extractions/pages/{pages[0]['id']}/confirm", {"text": "The zeta flange width is 12 mm", "note": "kontrol edildi"})
    assert r.status_code == 200 and r.json()["confidence_status"] == "user_confirmed"
    hits = admin.post("/retrieval/search", {"query": "zeta flange width"}).json()["passages"]
    assert hits and hits[0]["confidence_status"] == "user_confirmed"
    assert audit_rows("extraction.page_confirm", target_id=pages[0]["id"])


def test_provider_unavailable_gives_actionable_turkish_error(admin, fake_llm, qa_corpus):
    fake_llm.fail_with = ProviderUnavailableError("down")
    _, a = _chat(admin, "Merhaba")
    assert a["status"] == "error" and a["error_code"] == "provider_unavailable"
    assert "Ollama" in a["content"]
    _, t = _chat(admin, "ISO 8888 temel kremayer basınç açısı nedir?")
    assert t["status"] == "error" and t["error_code"] == "provider_unavailable"
    assert audit_rows("provider.error")
    # the deterministic engine keeps working without the LLM (see test_calc_api)


def test_member_without_scope_cannot_use_corpus(member_factory, admin, fake_llm, qa_corpus):
    fake_llm.responder = _grounded_responder("Basınç açısı {n}° [S1].")
    no_scope = member_factory()
    _, a = _chat(no_scope, "ISO 8888 standart temel kremayer basınç açısı nedir?")
    assert a["content"] == REFUSAL_PHRASE
    area = [x for x in admin.get("/knowledge-areas").json() if x["slug"] == "iso-disli"][0]
    scoped = member_factory([("knowledge_area", area["id"])])
    _, b = _chat(scoped, "ISO 8888 standart temel kremayer basınç açısı nedir?")
    assert b["answer_mode"] == "verified_source"
