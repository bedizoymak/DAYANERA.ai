"""Strict ISO acceptance regressions (2026-09-27).

Five questions of the strict ISO acceptance test failed:
  T1   literal symbol lookup (ISO 53 αP)          -> was routed to the calculation path
  T8   transverse module m_t = m_n / cos β         -> engine asked for the number of teeth z
  T9   "how is d calculated" (formula question)    -> ran numeric input validation
  T20  ISO 14104 "Class FD / Class FE"             -> short codes were not searched
  T4468 ISO 4468 maximum module range              -> answer taken from the first table row

Unit tests below need no database. The ``corpus`` tests ingest the real licensed
PDFs into the temporary test database (skipped when they are absent) and check,
with a fake model that only answers when the required evidence is in its prompt,
that each question takes the right path and that the evidence reaches the model.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import time

import pytest
from app.calc.engine import CalculationEngine
from app.calc.evidence import REQ, InMemoryEvidenceResolver, PageRecord
from app.calc.rules import RULES
from app.calc.types import CalcRequest, InputValue, Provenance
from app.core.paths import fs
from app.domain.enums import REFUSAL_PHRASE
from app.inference.prompts import RANGE_RULE, verified_messages
from app.services.intent import classify, has_parameter_digits
from app.services.retrieval import (
    extract_labels,
    extract_symbols,
    focus_window,
    label_lines,
    plan_query,
    symbol_variants,
    term_coverage,
)

T1 = "ISO 53:1998’e göre standard basic rack tooth profile için basınç açısı αP kaç derecedir?"
T8 = "mn = 2 mm ve β = 25° için ISO 21771 bağıntısını kullanarak transverse module mt değerini hesapla."
T9 = "ISO 21771:2007’e göre bir helisel dişlinin reference diameter d değeri z, mn ve β ile nasıl hesaplanır?"
T20 = "ISO 14104:2017’e göre Class FD ve Class FE neyi temsil eder?"
T_RANGE = "ISO 4468:2020’e göre 5, 6 ve 7 thread hoblar için testlerin geçerli olduğu maksimum module aralığı nedir?"


# =========================================================== routing (no DB)
def test_t1_literal_symbol_value_lookup_goes_to_retrieval():
    it = classify(T1)
    assert it.kind == "technical" and it.subtype == "standards_value_lookup"


def test_t8_transverse_module_is_a_calculation_without_z():
    it = classify(T8)
    assert it.kind == "calculation" and it.subtype == "numeric_calculation"
    assert it.calc.calc_type == "transverse_module"
    assert it.calc.inputs == {"m_n": (2.0, "mm"), "beta": (25.0, "°")}


def test_t9_formula_question_is_a_source_lookup_not_a_calculation():
    it = classify(T9)
    assert it.kind == "technical" and it.subtype == "standards_formula_lookup"
    assert it.calc.inputs == {}  # nothing reaches the engine's range validation


def test_t20_and_range_questions_are_source_lookups():
    assert classify(T20).kind == "technical"
    it = classify(T_RANGE)
    assert it.kind == "technical" and it.subtype == "standards_range_lookup"


@pytest.mark.parametrize("text", [
    "ISO 21771’e göre alın modülü mt nasıl hesaplanır?",
    "ISO 21771:2007 Eşitlik (1)’e göre reference diameter formülü nedir?",
    "ISO 53:1998’e göre pitch p ile module m arasındaki bağıntı nedir?",
])
def test_formula_only_questions_never_become_calculations(text):
    it = classify(text)
    assert it.kind == "technical" and it.subtype == "standards_formula_lookup"


@pytest.mark.parametrize("text,calc_type", [
    ("z=24, mn=2 mm, β=20° için referans çapı d hesapla", "cylindrical_gear_geometry"),
    ("mn=3 mm, β=15° için alın modülü m_t hesapla", "transverse_module"),
    ("z=20, m=2 mm dişli geometrisini hesapla", "cylindrical_gear_geometry"),
    ("z1=20, z2=40, m=2 mm dişli çifti hesapla", "gear_pair"),
    ("40 mm anma ölçüsü için IT6 kaç µm?", "iso286_it_tolerance"),
])
def test_numeric_requests_still_reach_the_engine(text, calc_type):
    it = classify(text)
    assert it.kind == "calculation" and it.calc.calc_type == calc_type


def test_standard_references_are_not_numeric_inputs():
    assert not has_parameter_digits("ISO 53:1998’e göre Table 2’deki αP kaç derece?")
    assert not has_parameter_digits("ISO/TR 10064-1:1992 ve ISO 4468 Test 9B, Eşitlik (1)")
    assert has_parameter_digits("Dişli mukavemet hesabını yap, tork 250 Nm")
    assert has_parameter_digits("eksen mesafesi 100 mm")
    assert has_parameter_digits("en 20 mm genişlik") and not has_parameter_digits("EN 10204 belgesi")
    # unsupported numeric requests are still calculation requests (and are refused there)
    assert classify("Dişli mukavemet hesabını yap, tork 250 Nm").kind == "calculation"


# =============================================== symbols / retrieval (no DB)
def test_symbol_variants_link_greek_and_pdf_text_spellings():
    assert symbol_variants("αP") == ["αP", "aP"]
    assert symbol_variants("aP") == ["aP", "αP"]
    assert symbol_variants("ρfP") == ["ρfP", "rfP"]
    assert symbol_variants("rfP") == ["rfP", "ρfP"]
    assert symbol_variants("β") == ["β", "beta"]
    assert symbol_variants("FD") == ["FD"]
    assert extract_symbols("beta = 20 ve αP, haP, 9B, H7; ISO 53 Class FD") == ["β", "αP", "haP", "9B", "H7", "FD"]
    assert extract_labels("Class FD ve Class FE, Test 9B, Type A, Tablo 2") == [
        "class FD", "class FE", "test 9B", "type A", "table 2"]


def test_t1_alpha_p_matches_the_pdf_spelling_ap():
    plan = plan_query(T1)
    assert plan.symbols == ["αP"] and {"αp", "ap"} <= set(plan.tsquery_simple.split(" | "))
    table2 = "Table 2 — Standard basic rack proportions\nItem\nStandard basic rack value\naP\n20°\nhaP\n1 m"
    assert any(p.search(table2) for p in plan.literal_patterns("αp"))
    assert not any(p.search("apply the approximately") for p in plan.literal_patterns("αp"))
    # "derece" only positions the excerpt on the ° value; it never raises the relevance score
    assert any(p.search(table2) for p in plan.literal_patterns("derecedir", focus=True))
    assert not any(p.search(table2) for p in plan.literal_patterns("derecedir"))


def test_greek_names_match_greek_letters_as_literals():
    plan = plan_query("ISO 21771’e göre beta açısı nedir?")
    assert plan.symbols == ["β"] and plan.literals == ["beta"]
    assert any(p.search("the helix angle, β, at the reference cylinder") for p in plan.literal_patterns("beta"))
    # an English-only question keeps its concepts: "zeta" is not widened to any "ζ" symbol
    assert plan_query("What is the zeta flange width?").concepts == [["zeta"], ["flange"], ["width"]]


def test_t20_short_class_codes_are_searched_lexically():
    plan = plan_query(T20)
    assert plan.concepts == [["class"]]  # "neyi", "temsil", "eder" are Turkish, not English concepts
    assert plan.symbols == ["FD", "FE"] and plan.labels == ["class FD", "class FE"]
    assert {"fd", "fe"} <= set(plan.tsquery_simple.split(" | "))
    caption = "Figure 5 — Class FE rehardening/severe overheating with adjacent Class FD heavy tempering"
    assert term_coverage(plan, caption, 1) == 1.0
    # 2-letter codes match whole tokens only ("fe" is not a prefix of "feature" / "few")
    assert term_coverage(plan, "Class A feature list for a few items", 1) == pytest.approx(1 / 3)


def test_turkish_suffixed_english_words_are_scored():
    plan = plan_query(T_RANGE)
    assert plan.literals == ["5", "6", "7", "thread", "hoblar", "testlerin", "geçerli", "maksimum", "aralığı"]
    line = "Tests 8 and 10 are only valid for the following: d) 5-, 6- and 7-threads, module range 0,5 ≤ m ≤ 3,5 hob."
    # module + 5, 6, 7 + thread + hoblar (hob) + testlerin (test); generic words are not scored
    assert term_coverage(plan, line, 1) == pytest.approx(7 / 10)


def test_generic_turkish_words_do_not_raise_the_relevance_score(settings):
    plan = plan_query("Rulman yağlama aralığı nedir?")  # bearing lubrication interval: not in the corpus
    passage = "Ranges for the shades of grey are suggested. Lubrication is not covered by this document."
    score = term_coverage(plan, passage, 1)
    assert score == pytest.approx(1 / 3) and score < settings.retrieval_min_score


def _filler(n: int) -> str:
    return "\n".join(f"5.{i} Test {i} uses Formula ({i}) with the datum value of the module table." for i in range(1, n))


def test_range_excerpt_lands_on_the_validity_statement_not_the_first_module_mention():
    statement = ("Tests 8, 10 and 11 are only valid for the following:\na) 1-thread, all module ranges;\n"
                 "b) 2-threads, module range 0,5 ≤ m ≤ 16;\nd) 5-, 6- and 7-threads, module range 0,5 ≤ m ≤ 3,5.")
    text = _filler(12) + "\n" + statement + "\n" + _filler(8)
    s, e = focus_window(text, plan_query(T_RANGE), size=400)
    assert "5-, 6- and 7-threads, module range 0,5 ≤ m ≤ 3,5" in text[s:e]


def test_table_excerpt_keeps_every_row_of_the_matched_block():
    def block(label: str) -> str:
        rows = ["0,5 ≤ m ≤ 1", "3", "4", "1 < m ≤ 2", "3", "5", "2 < m ≤ 3,5", "4", "6", "3,5 < m ≤ 6", "—", "—"]
        return label + "\n" + "\n".join(rows)
    text = ("Test\nElement\nThread spacing\nPitch range\nmodule, m\n"
            + "\n".join(block(f"{n}-threads") for n in (2, 3, 4)) + "\n" + block("5-, 6-, 7-threads")
            + "\nTable 6 (continued)")
    s, e = focus_window(text, plan_query(T_RANGE), size=len(text) - 60)
    tail = text[s:e][text[s:e].rfind("5-, 6-, 7-threads"):]
    assert "2 < m ≤ 3,5" in tail and "3,5 < m ≤ 6\n—\n—" in tail  # not only the first row of the block
    assert "Table 6" not in tail  # stops at the end of the table block


def test_label_lines_quote_what_the_source_ties_to_a_code():
    plan = plan_query(T20)
    passages = [("NOTE Rehardened areas (FE) appear as white etching areas.\n"
                 "Figure 5 — Class FE rehardening/severe overheating with adjacent Class FD heavy tempering"),
                "Figure 7 — From Class FB to Class FD heavy tempering\nFigure 8 — Class FA no tempering"]
    lines = label_lines(plan, passages)
    assert lines == [(1, "Figure 5 — Class FE rehardening/severe overheating with adjacent Class FD heavy tempering"),
                     (2, "Figure 7 — From Class FB to Class FD heavy tempering")]
    user = verified_messages(T20, [{"standard_code": "ISO 14104:2017", "title": "t", "locator": "s. 18", "text": p}
                                   for p in passages], False, label_lines=lines)[1]["content"]
    assert "[S1] Figure 5 — Class FE rehardening/severe overheating" in user
    assert label_lines(plan_query(T1), passages) == []  # no labelled code in the question


def test_range_rule_is_added_to_the_verified_prompt():
    msgs = verified_messages("q", [{"standard_code": "ISO 1:2000", "title": "t", "locator": "s. 1", "text": "x"}],
                             False, extra_rules=[RANGE_RULE])
    system = msgs[0]["content"]
    assert "8. " + RANGE_RULE in system and system.index(RANGE_RULE) < system.index("BİÇİM ÖRNEĞİ")
    assert "aP = αP" in system  # PDF symbol spelling hint (all verified answers)
    assert RANGE_RULE not in verified_messages("q", [], False)[0]["content"]


# ======================================================= calc engine (no DB)
def _pages() -> list[PageRecord]:
    pages = []
    for n, (rid, req) in enumerate((r for r in REQ.items() if r[0].startswith(("iso21771", "iso53"))), start=2):
        code = "ISO 21771:2007" if rid.startswith("iso21771") else "ISO 53:1998"
        pages.append(PageRecord(document_id=code, version_id=f"{code}-v1", version_number=1, document_title=code,
                                standard_code=code, page_number=n, locator=f"s. {n}", text="\n".join(req.must_contain)))
    return pages


def _run(calc_type, **inputs):
    req = CalcRequest(calc_type, {k: InputValue(k, v, u, Provenance("user_input", "m", "user_input"))
                                  for k, (v, u) in inputs.items()})
    return CalculationEngine(InMemoryEvidenceResolver(_pages())).run(req)


def test_transverse_module_needs_only_mn_and_beta():
    assert {s.key: s.required for s in RULES["transverse_module"].inputs} == {"m_n": True, "beta": True}
    r = _run("transverse_module", m_n=(2, "mm"), beta=(25, "°"))
    assert r.status == "ok", r.diagnostics
    assert r.output_map()["m_t"] == pytest.approx(2 / math.cos(math.radians(25)))
    assert round(r.output_map()["m_t"], 3) == 2.207
    assert r.outputs[0].expression == "m_t = m_n / cos β"
    assert [e["requirement_id"] for e in r.evidence] == ["iso21771.eq2"]
    assert any("cos 25°" in t for t in r.trace)


def test_transverse_module_angles_are_explicit_degrees():
    deg = _run("transverse_module", m_n=(2, "mm"), beta=(25, "°")).output_map()["m_t"]
    rad = _run("transverse_module", m_n=(2, "mm"), beta=(math.radians(25), "rad")).output_map()["m_t"]
    assert deg == pytest.approx(rad)
    missing = _run("transverse_module", m_n=(2, "mm"))
    assert missing.status == "invalid_input" and [d.code for d in missing.diagnostics] == ["missing_input"]
    assert _run("transverse_module", m_n=(2, "mm"), beta=(95, "°")).status == "invalid_input"
    assert _run("transverse_module", m_n=(0, "mm"), beta=(25, "°")).status == "invalid_input"


def test_helical_reference_diameter_is_z_mn_over_cos_beta():
    r = _run("cylindrical_gear_geometry", z=(24, ""), m_n=(2, "mm"), beta=(25, "°"))
    assert r.status == "ok", r.diagnostics
    assert r.output_map()["d"] == pytest.approx(24 * 2 / math.cos(math.radians(25)))
    assert next(o for o in r.outputs if o.key == "d").expression == "d = z·m_n / cos β"


# =================================================== real corpus (chat path)
REAL_PDFS = {"ISO 53, 2, 1998": "ISO 53:1998", "ISO 21771, 1, 2007": "ISO 21771:2007",
             "Surface temper etch": "ISO 14104:2017", "ISO 4468, 3, 2020": "ISO 4468:2020"}


@pytest.fixture(scope="module")
def strict_corpus(settings, admin):
    from conftest import REPO

    src = Path(os.environ.get("DAYANERA_CORPUS_DIR") or REPO / "iso booklets")
    if not src.exists():
        pytest.skip("Gerçek ISO korpusu yok")
    dest = settings.iso_booklets_path / "real"  # same folder/names as test_real_corpus: no duplicates
    dest.mkdir(exist_ok=True)
    found = 0
    for p in src.iterdir():
        if any(key in p.name for key in REAL_PDFS):
            found += 1
            if not os.path.exists(fs(dest / p.name)):  # long ISO file names need the \\?\ form
                shutil.copyfile(fs(p), fs(dest / p.name))
    if found < len(REAL_PDFS):
        pytest.skip("Gerekli ISO PDF'lerinden bazıları eksik")
    from conftest import run_jobs
    from app.ingestion.watcher import Watcher

    time.sleep(2.1)
    Watcher(settings).scan()
    run_jobs(settings)
    codes = {d["standard_code"] for d in admin.get("/documents?limit=500").json()["items"]}
    assert set(REAL_PDFS.values()) <= codes
    yield


def _chat(user, text):
    conv = user.post("/conversations", {}).json()["id"]
    r = user.post(f"/conversations/{conv}/messages", {"content": text})
    assert r.status_code == 200, r.text
    return r.json()["assistant_message"]


def _evidence_responder(evidence: str, answer: str, draft: dict | None = None):
    """Fake Qwen: answers only when the evidence regex is in the source passages it was given."""
    def respond(messages):
        system, user = messages[0].content, messages[-1].content
        if "Doğrulanmış kaynak cevabı" in system:
            return answer if re.search(evidence, user, re.DOTALL) else REFUSAL_PHRASE
        if "TASLAK" in system and "outputs" in system:
            return json.dumps({"outputs": draft or {}})
        if "SADECE JSON" in system:
            return '{"calc_type": "unsupported"}'
        return "Genel yanıt."
    return respond


def _stored_meta(message: dict) -> dict:
    """Full stored metadata (the API exposes only a whitelisted subset)."""
    import uuid

    from app.db.models import Message
    from app.db.session import session_scope

    with session_scope() as db:
        return dict(db.get(Message, uuid.UUID(message["id"])).metadata_ or {})


def _no_calc_mapping(fake_llm) -> bool:
    return not any("HESAP TÜRLERİ" in m[0].content for m in fake_llm.calls)


pytestmark_corpus = pytest.mark.corpus


@pytestmark_corpus
def test_t1_alpha_p_is_answered_from_iso53_table2(admin, fake_llm, strict_corpus):
    fake_llm.responder = _evidence_responder(r"(?:a|α)P\W{0,5}20°|αP = 20°", "Basınç açısı αP = 20° [S1].")
    a = _chat(admin, T1)
    assert a["answer_mode"] == "verified_source", a
    assert "20°" in a["content"] and _stored_meta(a)["plan"]["intent_subtype"] == "standards_value_lookup"
    assert _no_calc_mapping(fake_llm)
    user_prompt = fake_llm.calls[-1][-1].content
    assert re.search(r"Table 2 — Standard basic rack proportions.*(?:a|α)P\W{0,5}20°", user_prompt, re.DOTALL)


@pytestmark_corpus
def test_t8_transverse_module_chat_does_not_ask_for_z(admin, fake_llm, strict_corpus):
    fake_llm.responder = _evidence_responder("x", "x", draft={"m_t": 2.2068})
    a = _chat(admin, T8)
    assert a["answer_mode"] == "calculation", a
    assert a["metadata"]["calc_type"] == "transverse_module" and a["metadata"]["calc_status"] == "ok"
    assert r"\frac" in a["content"] and r"m_t" in a["content"] and r"\cos" in a["content"]
    assert "Diş sayısı" not in a["content"] and "eksik" not in a["content"]
    detail = admin.get(f"/messages/{a['id']}/calculation").json()
    assert detail["result"]["outputs"][0]["value"] == pytest.approx(2.2068, abs=1e-4)
    assert {e["standard_code"] for e in detail["result"]["evidence"]} == {"ISO 21771:2007"}


@pytestmark_corpus
def test_t8_engine_overrides_conflicting_llm_arithmetic(admin, fake_llm, strict_corpus):
    fake_llm.responder = _evidence_responder("x", "x", draft={"m_t": 2.142})  # what the local model drafted
    a = _chat(admin, T8)
    assert a["metadata"]["mismatch"] is True and "UYUŞMADI" in a["content"]
    assert r"\frac" in a["content"] and "2.2068" in a["content"] and "2,142" not in a["content"]


@pytestmark_corpus
def test_t9_formula_question_is_answered_from_source_without_validation(admin, fake_llm, strict_corpus):
    fake_llm.responder = _evidence_responder(r"The reference diameter, d, is determined by",
                                             "Referans çapı d = z · m_n / cos β bağıntısıyla bulunur [S1].")
    a = _chat(admin, T9)
    assert a["answer_mode"] == "verified_source", a
    assert "cos β" in a["content"] and "Hesap yapılmadı" not in a["content"]
    assert "calculation_id" not in a["metadata"] and _no_calc_mapping(fake_llm)
    assert _stored_meta(a)["plan"]["intent_subtype"] == "standards_formula_lookup"


@pytestmark_corpus
def test_t20_class_fd_fe_are_retrieved_from_figure_captions(admin, fake_llm, strict_corpus):
    fake_llm.responder = _evidence_responder(
        r"Class FD heavy tempering.*|Class FE rehardening",
        "Class FD ağır temperlemeyi (heavy tempering), Class FE yeniden sertleşmeyi / şiddetli aşırı ısınmayı "
        "(rehardening / severe overheating) temsil eder [S1].")
    a = _chat(admin, T20)
    assert a["answer_mode"] == "verified_source", a
    user_prompt = fake_llm.calls[-1][-1].content
    assert "Class FD heavy tempering" in user_prompt and re.search(r"Class FE\s+rehardening", user_prompt)
    assert re.search(r"Rehardened areas \(FE\) appear\s+as white etching areas", user_prompt)
    assert "SORUDAKİ KODLARIN GEÇTİĞİ SATIRLAR" in user_prompt  # captions naming each code, quoted verbatim
    assert _stored_meta(a)["plan"]["symbols"] == ["FD", "FE"]


@pytestmark_corpus
def test_iso4468_max_range_sees_the_full_applicable_range(admin, fake_llm, strict_corpus):
    fake_llm.responder = _evidence_responder(r"5-, 6- and 7-threads, module range 0,5 ≤ m ≤ 3,5",
                                             "Geçerli aralık 0,5 ≤ m ≤ 3,5; maksimum modül m = 3,5 [S1].")
    a = _chat(admin, T_RANGE)
    assert a["answer_mode"] == "verified_source", a
    assert "3,5" in a["content"]
    assert RANGE_RULE in fake_llm.calls[-1][0].content
    assert _stored_meta(a)["plan"]["intent_subtype"] == "standards_range_lookup"


# previously passing strict cases: the evidence must still reach the model
@pytestmark_corpus
@pytest.mark.parametrize("question,evidence", [
    ("ISO 53:1998’e göre standart temel kremayer için haP, cP, hfP ve ρfP değerleri nelerdir?",
     r"haP\W*1 m\W*cP\W*0,25 m\W*hfP\W*1,25 m\W*(?:r|ρ)fP\W*0,38 m"),
    ("ISO 53:1998’e göre pitch p ile module m arasındaki bağıntı nedir?", r"is the pitch;\s*m\s*is the module"),
    ("ISO 53:1998’e göre yüksek tork ileten dişliler için hangi basic rack tooth profile tipi önerilir?",
     r"type A is\s+recommended for gears transmitting high torques"),
    ("ISO 53:1998’e göre Type D basic rack profili için hfP ve ρfP değerleri nedir?",
     r"hfP = 1,4 m, with the associated fillet radii, (?:r|ρ)fP = 0,39 m"),
    ("ISO 21771:2007’e göre backlash nedir?", r"is the shortest distance between the non-working fla|backlash is the clearance between the non-working fla"),
    ("ISO 21771:2007’e göre normal, circumferential ve radial backlash türleri nelerdir?",
     r"normal backlash.*circumferential backlash.*radial backlash"),
    ("ISO 21771:2007’e göre external helical gear pair için iki dişlinin helis yönü nasıldır?",
     r"one gear has a left-handed and the\s+other gear \(mating gear\) a right-handed"),
    ("ISO 4468:2020’e göre hob accuracy grade’leri nelerdir?", r"Grade 4A;.*Grade D\."),
    ("ISO 4468:2020’e göre hob kalite sınıfları (grades) nelerdir?", r"Grade 4A;.*Grade D\."),
    ("ISO 4468:2020’e göre Test 9B hangi kalite sınıfları için geçerlidir?",
     r"Test 9B is only valid for quality Grades 4A, 3A and 2A"),
    ("ISO 4468:2020 Annex A’ya göre tek ağızlı solid hoblar hangi module aralığını kapsar?",
     r"single-start solid.*0,5 to\s+40 module"),
    ("ISO 14104:2017’e göre rehardened alanlar dağlama sonrasında nasıl görünür?",
     r"white or light-coloured untempered martensite|appear\s+as white etching areas"),
])
def test_previously_passing_strict_cases_still_get_their_evidence(admin, fake_llm, strict_corpus, question, evidence):
    fake_llm.responder = _evidence_responder(evidence, "Kaynağa göre yanıt [S1].")
    a = _chat(admin, question)
    assert a["answer_mode"] == "verified_source", (a["content"], a["metadata"].get("reason"),
                                                   fake_llm.calls[-1][-1].content[:4000] if fake_llm.calls else None)
    assert _no_calc_mapping(fake_llm)
