"""Step 2 regression tests (DAYANERA_AI_STEP2_ORDERS.md, Orders A–E).

Pure backend tests: a deterministic fake provider replaces Ollama, and the
temporary test database holds only synthetic documents.
"""
from __future__ import annotations

import time

import pytest
from app.domain.enums import REFUSAL_PHRASE
from app.services.intent import classify, is_inventory_question
from conftest import audit_rows, make_pdf

REFUSAL_BYTES = "Bu kaynak setinde doğrulayamadım".encode()


def _scan_and_process(settings):
    from conftest import run_jobs
    from app.ingestion.watcher import Watcher

    time.sleep(2.1)
    Watcher(settings).scan()
    run_jobs(settings)


@pytest.fixture(scope="module")
def step2_corpus(settings):
    iso = settings.iso_booklets_path / "step2"
    make_pdf(iso / "Gear inspection gauges -- ISO_TC 60 -- ISO 7700, 1, 2020.pdf", [
        "INTERNATIONAL STANDARD\nISO 7700:2020\nGear inspection gauges (synthetic test copy)",
        ("ISO 7700:2020\nThe pressure angle of the standard basic rack tooth profile is 20°.\n"
         "The backlash gauge class is 4 for general inspection of spur gears."),
        "ISO 7700:2020\nThe transmitted torque of the gear pair is considered in the inspection set-up.",
    ])
    _scan_and_process(settings)
    yield


def _chat(user, text):
    conv = user.post("/conversations", {}).json()["id"]
    t0 = time.perf_counter()
    r = user.post(f"/conversations/{conv}/messages", {"content": text})
    assert r.status_code == 200, r.text
    return r.json()["assistant_message"], time.perf_counter() - t0


# ------------------------------------------------------------------ Order A
@pytest.mark.parametrize("text", [
    "elinde hangi ISO standartları var, listeler misin?",
    "peki elinde hangi ISO standartları var, listeler misin?",
    "hangi standartlar yüklü",
    "which standards do you have",
    "standartları listele",
    "kaynak setinde ne var",
    "neleri biliyorsun?",
    "list the documents",
])
def test_inventory_questions_are_recognised(text):
    assert is_inventory_question(text)
    assert classify(text).kind == "corpus_inventory"


@pytest.mark.parametrize("text", [
    "ISO 286'ya göre 50 mm H7 toleransı nedir?",
    "ISO 286 standartında hangi toleranslar var?",   # names a concrete standard -> not inventory
    "hangi standart 50 mm için geçerli?",            # digits outside the inventory phrase
    "kaynak ver",
    "bana kısaca kendini tanıt",
])
def test_concrete_questions_are_not_inventory(text):
    assert classify(text).kind != "corpus_inventory"


def test_inventory_lists_active_codes_without_llm(admin, fake_llm, step2_corpus):
    fake_llm.responder = lambda m: pytest.fail("the model must not be called for an inventory question")
    a, seconds = _chat(admin, "elinde hangi ISO standartları var, listeler misin?")
    assert fake_llm.calls == []
    assert a["answer_mode"] == "general" and a["metadata"]["kind"] == "corpus_inventory"
    assert "- ISO 7700:2020 — Gear inspection gauges" in a["content"]
    assert "ISO 7700:2020 — ISO 7700:2020" not in a["content"]  # title prefix not repeated
    assert a["metadata"]["verified_document_count"] >= 1
    assert seconds < 2.0 and a["metadata"]["timings_ms"]["inventory"] < 1000
    assert audit_rows("corpus.inventory")


def test_inventory_respects_member_scope(member_factory, step2_corpus):
    m = member_factory()
    a, _ = _chat(m, "hangi standartlar yüklü")
    assert a["metadata"]["kind"] == "corpus_inventory"
    assert a["metadata"]["verified_document_count"] == 0
    assert "ISO 7700" not in a["content"]


# ------------------------------------------------------------------ Order B
def _assert_exact_refusal(a):
    assert a["content"] == REFUSAL_PHRASE
    assert a["content"].encode("utf-8") == REFUSAL_BYTES  # byte-identical to the master-spec phrase
    assert a["answer_mode"] == "unverified"


def test_refusal_for_missing_standard_names_the_code(admin, fake_llm, step2_corpus):
    fake_llm.responder = lambda m: pytest.fail("no LLM call for a standard that is not loaded")
    a, _ = _chat(admin, "ISO 2768 m sınıfı 30-120 mm tolerans ne? kaynak ver")
    _assert_exact_refusal(a)
    assert fake_llm.calls == []
    r = a["metadata"]["refusal"]
    assert r["reason"] == "no_passages"
    assert r["codes_requested"] == ["ISO 2768"] and r["codes_missing"] == ["ISO 2768"]
    rows = audit_rows("answer.refused")
    assert rows[-1]["details"]["reason"] == "no_passages" and rows[-1]["details"]["codes_missing"] == ["ISO 2768"]


def test_loaded_standard_is_not_reported_missing(admin, fake_llm, step2_corpus):
    fake_llm.responder = lambda m: "Bu konuda pasajlarda bilgi yok."  # model refuses in its own words
    a, _ = _chat(admin, "ISO 7700 standart temel kremayer basınç açısı ve boşluk mastarı nedir?")
    _assert_exact_refusal(a)
    assert a["metadata"]["refusal"]["codes_requested"] == ["ISO 7700"]
    assert a["metadata"]["refusal"]["codes_missing"] == []


def test_grounding_refusals_carry_normalized_reason(admin, fake_llm, step2_corpus):
    fake_llm.responder = lambda m: ("Basınç açısı 25°'dir [S1]." if "Doğrulanmış" in m[0].content else "x")
    a, _ = _chat(admin, "ISO 7700 temel kremayer basınç açısı nedir?")
    _assert_exact_refusal(a)
    assert a["metadata"]["refusal"]["reason"] == "unsupported_numbers"
    assert audit_rows("answer.grounding_failed")[-1]["details"]["reason"] == "unsupported_numbers"
    fake_llm.responder = lambda m: ("Bkz. [S9]." if "Doğrulanmış" in m[0].content else "x")
    b, _ = _chat(admin, "ISO 7700 temel kremayer basınç açısı nedir?")
    _assert_exact_refusal(b)
    assert b["metadata"]["refusal"]["reason"] == "invalid_citation"


def test_unsupported_calculation_refusal_has_reason(admin, fake_llm, step2_corpus):
    fake_llm.responder = lambda m: '{"calc_type": "unsupported"}'
    a, _ = _chat(admin, "Dişli mukavemet hesabını yap, tork 250 Nm")
    _assert_exact_refusal(a)
    assert a["metadata"]["refusal"]["reason"] == "unsupported_calculation"


def test_refusal_reason_normalization():
    from app.services.chat import normalize_refusal_reason

    assert normalize_refusal_reason("invalid_citation:[7]") == "invalid_citation"
    assert normalize_refusal_reason("unsupported_formula") == "unsupported_calculation"
    assert normalize_refusal_reason("low_relevance") == "low_relevance"
    assert normalize_refusal_reason("something-else") == "model_refused"


def test_requested_code_matching():
    from app.services.inventory import code_present, missing_codes

    loaded = ["ISO 286-1:2010", "ISO 286-2:2010", "ISO 53:1998", "ISO/TR 10064-1:1992"]
    assert code_present("286", loaded) and code_present("286-2", loaded) and not code_present("286-3", loaded)
    assert code_present("53", loaded) and not code_present("5", loaded) and code_present("10064", loaded)
    assert missing_codes("ISO 2768 ve ISO 286 karşılaştır", loaded) == (["ISO 2768", "ISO 286"], ["ISO 2768"])
    assert missing_codes("ISO/TR 10828 sonsuz vida", loaded) == (["ISO/TR 10828"], ["ISO/TR 10828"])


# ------------------------------------------------------------------ Order C
def test_missing_code_short_circuits_before_retrieval(admin, fake_llm, step2_corpus):
    a, seconds = _chat(admin, "ISO 2768 m sınıfı 30-120 mm tolerans ne? kaynak ver")
    _assert_exact_refusal(a)
    assert fake_llm.calls == []
    assert a["metadata"]["timings_ms"]["retrieval"] == 0
    assert seconds < 2.0


@pytest.mark.parametrize("question", [
    "M10 civata 8.8 kalite, çekme dayanımı ne kadar? sıkma torku kaç Nm",   # T1
    "M10 civata 8.8 çekme dayanımı",
])
def test_bolt_questions_refuse_without_generation(admin, fake_llm, step2_corpus, question):
    # the synthetic corpus mentions "torque" but nothing about bolts, grade 8.8 or tensile strength
    a, seconds = _chat(admin, question)
    _assert_exact_refusal(a)
    assert fake_llm.calls == [], "no LLM call for a question the corpus cannot support"
    r = a["metadata"]["refusal"]
    assert r["reason"] in ("low_relevance", "no_passages") and r["codes_missing"] == []
    assert "generation" not in a["metadata"]["timings_ms"]
    assert seconds < 2.0


def test_low_relevance_gate_stops_partial_matches(admin, fake_llm, step2_corpus):
    # passages match "backlash gauge", but most of the question (calibration, certificate,
    # laboratory accreditation) is unsupported -> refused before generation
    a, seconds = _chat(admin, "Boşluk mastarı kalibrasyon periyodu, sertifika süresi ve laboratuvar akreditasyonu nedir?")
    _assert_exact_refusal(a)
    assert fake_llm.calls == []
    assert a["metadata"]["refusal"]["reason"] == "low_relevance"
    assert a["metadata"]["best_score"] < a["metadata"]["min_score"]
    assert audit_rows("answer.refused")[-1]["details"]["reason"] == "low_relevance"
    assert seconds < 2.0


def test_verified_answer_uses_short_budget_and_records_timings(admin, fake_llm, step2_corpus):
    fake_llm.responder = lambda m: ("Temel kremayer profilinde basınç açısı 20° [S1]."
                                    if "Doğrulanmış" in m[0].content else "x")
    a, _ = _chat(admin, "ISO 7700 temel kremayer basınç açısı nedir?")
    assert a["answer_mode"] == "verified_source"
    assert fake_llm.options[-1].num_predict == 250 and fake_llm.options[-1].temperature == 0.1
    t = a["metadata"]["timings_ms"]
    assert {"retrieval", "generation", "validation", "total"} <= set(t)
    fake_llm.options.clear()
    _chat(admin, "ISO 7700 temel kremayer basınç açısını ayrıntılı açıkla")
    assert fake_llm.options[-1].num_predict == 900  # detail mode unchanged


def test_relevance_score_is_absolute(settings):
    from app.services.retrieval import plan_query, term_coverage

    plan = plan_query("M10 civata 8.8 kalite, çekme dayanımı ne kadar? sıkma torku kaç Nm")
    assert sorted(plan.concepts) == [["bolt"], ["tensile strength"], ["tightening torque"]]
    assert {"m10", "8.8", "kalite"} <= set(plan.literals)
    assert term_coverage(plan, "The transmitted torque of the gear pair.", 0) < settings.retrieval_min_score
    ok = plan_query("ISO 53 standart temel kremayer profilinde basınç açısı nedir?")
    assert ok.literals == [] and len(ok.concepts) == 3
    assert term_coverage(ok, "basic rack tooth profile ... pressure angle", 3) == 1.0
    h7 = plan_query("ISO 286'ya göre 50 mm H7 toleransı nedir?")
    assert h7.literals == ["50", "mm", "h7"]
    assert term_coverage(h7, "tolerance class H7 for nominal sizes above 30 up to 50 mm", 1) == 1.0


# ------------------------------------------------------------------ Order E
@pytest.mark.parametrize("text,allowed", [
    ("elinde hangi ISO standartları var, listeler misin?", {"corpus_inventory"}),
    ("hangi standartlar yüklü", {"corpus_inventory"}),
    ("which standards do you have", {"corpus_inventory"}),
    ("ISO 286'ya göre 50 mm H7 toleransı nedir?", {"technical", "calculation"}),
    ("ISO 2768 m sınıfı 30-120 mm tolerans ne? kaynak ver", {"technical", "calculation"}),
    ("M10 civata 8.8 çekme dayanımı", {"technical", "calculation"}),
    ("ISO iyi bir şey mi", {"general"}),
    ("bana kısaca kendini tanıt", {"general"}),
    ("DIN mi ISO mu daha iyi?", {"general"}),
    ("standart nedir", {"general"}),
])
def test_order_e_intent_table(text, allowed):
    assert classify(text).kind in allowed


@pytest.mark.parametrize("question", ["ISO iyi bir şey mi", "bana kısaca kendini tanıt"])
def test_order_e_general_questions_use_general_chat(admin, fake_llm, step2_corpus, question):
    fake_llm.responder = lambda m: "Merhaba! Kısaca yardımcı olayım."
    a, _ = _chat(admin, question)
    assert a["answer_mode"] == "general" and a["content"] == "Merhaba! Kısaca yardımcı olayım."
    assert "DOĞRULANMIŞ DEĞİLDİR" in fake_llm.calls[-1][0].content  # general-mode prompt, not verified mode


def test_paraphrased_model_refusal_detection():
    from app.services.chat import looks_like_refusal

    for t in ("Bu konuda pasajlarda bilgi yok.", "Pasajlarda bu değer yer almıyor.",
              "Kaynaklarda bu konu bulunmamaktadır.", "This value is not mentioned in the passages."):
        assert looks_like_refusal(t), t
    for t in ("Temel kremayer (basic rack) profilinde basınç açısı (pressure angle) 20°dir.",
              "Tolerans sınıfı 1 ile 11 arasındadır; kaynak pasajında tanımlanmıştır."):
        assert not looks_like_refusal(t), t


def test_refusal_constant_is_unchanged():
    assert REFUSAL_PHRASE.encode("utf-8") == REFUSAL_BYTES
