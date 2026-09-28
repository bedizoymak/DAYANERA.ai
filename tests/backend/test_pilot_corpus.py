"""Pilot and staged rollout of the REAL corpus through the canonical pipeline (``corpus`` marker).

Stage 1 (pilot): ISO 53:1998 and ISO 21771:2007 — quality gates, approval, and the
retrieval regression set of ``app/evaluation/pilot_ground_truth.py``.
Stage 2 (only after the pilot passed): ISO 54, ISO 286-1/-2 and ISO/TR 10828. The
scanned documents (ISO 54, ISO/TR 10828) need a human review of their OCR pages
before they can be evidence; the test plays that reviewer through the review API.

The licensed PDFs are copied into the temporary test project only (the same
``real`` folder as test_real_corpus, so no duplicate documents). Retrieval is
measured through DAYANERA's access scopes: a member who sees exactly the documents
under test, so synthetic look-alikes of other test modules never interfere.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import time
import uuid

import pytest
from app.core.paths import fs
from app.domain.enums import REFUSAL_PHRASE

from conftest import REPO, run_jobs

pytestmark = pytest.mark.corpus
REAL = Path(os.environ.get("DAYANERA_CORPUS_DIR") or REPO / "iso booklets")  # override: renamed copies
PILOT = {"ISO 53, 2, 1998": "ISO 53:1998", "ISO 21771, 1, 2007": "ISO 21771:2007"}
STAGE2 = {"ISO 54, 2, 1996": "ISO 54:1996", "ISO 286, 2, 2010": "ISO 286-2:2010",
          "Geometrical product specifications (GPS)_ ISO code system": "ISO 286-1:2010",
          "ISO_TR 10828, 2, 2015": "ISO/TR 10828:2015"}


def _ingest(settings, admin, wanted: dict[str, str]) -> dict[str, dict]:
    if not REAL.exists():
        pytest.skip("Gerçek ISO korpusu yok")
    from app.ingestion.storage import sha256_file
    from app.ingestion.watcher import Watcher

    dest = settings.iso_booklets_path / "real"
    dest.mkdir(exist_ok=True)
    found = 0
    for p in REAL.iterdir():
        if any(k in p.name for k in wanted):
            found += 1
            if not os.path.exists(fs(dest / p.name)):
                shutil.copyfile(fs(p), fs(dest / p.name))
            # OCR results are derived data keyed by the file hash: reuse the local cache (minutes of OCR)
            cache = REPO / "data" / "indexes" / "ocr-cache" / sha256_file(p)[0]
            if cache.exists():
                shutil.copytree(fs(cache), fs(settings.data_root / "indexes" / "ocr-cache" / cache.name),
                                dirs_exist_ok=True)
    if found < len(wanted):
        pytest.skip("Gerekli ISO PDF'lerinden bazıları eksik")
    time.sleep(2.1)
    Watcher(settings).scan()
    run_jobs(settings, verify=False)
    docs = {d["standard_code"]: d for d in admin.get("/documents?limit=500").json()["items"]
            if d["source_relpath"] and d["source_relpath"].startswith("real/")}
    missing = set(wanted.values()) - set(docs)
    assert not missing, f"standart kodu algılanamadı: {missing}"
    return {code: docs[code] for code in wanted.values()}


def _scoped(member_factory, docs: dict[str, dict]):
    return member_factory([("document", d["id"]) for d in docs.values()])


def _scopes(docs):
    from app.services.access import ScopeSet

    return ScopeSet(is_owner=False, user_id=uuid.UUID(int=1), document_ids={uuid.UUID(d["id"]) for d in docs.values()})


def _ask(user, text):
    conv = user.post("/conversations", {}).json()["id"]
    r = user.post(f"/conversations/{conv}/messages", {"content": text})
    assert r.status_code == 200, r.text
    return r.json()["assistant_message"]


def _gates(doc):
    return {g["id"]: g for g in doc["current_version"]["quality_report"]["gates"]}


# =========================================================================== stage 1: pilot
@pytest.fixture(scope="module")
def pilot(settings, admin):
    return _ingest(settings, admin, PILOT)


def test_pilot_quality_gates(pilot):
    iso21771, iso53 = pilot["ISO 21771:2007"], pilot["ISO 53:1998"]
    for d in (iso21771, iso53):
        v = d["current_version"]
        assert v["ingestion_status"] == "indexed" and v["parser"] == "pymupdf"
        assert _gates(d)["chunk_lineage"]["status"] == _gates(d)["fulltext_index"]["status"] == "pass"
        assert _gates(d)["critical_content"]["status"] == "pass"  # αP/αFP/mn/mt/da/df, clauses, tables, equations
        assert _gates(d)["structure"]["status"] == _gates(d)["traceability"]["status"] == "pass"
    # engineering chunker (canonical-ingestion/2): equations whose 2-D layout cannot be reconstructed with
    # certainty, and math-font glyphs without Unicode meaning (ISO 21771 ISOamsr "W" = ⩾ on s. 32), are
    # listed for a human check; nothing else may need attention
    assert iso21771["current_version"]["corpus_status"] == "needs_review"
    attention = {k for k, g in _gates(iso21771).items() if g["status"] != "pass"}
    assert attention <= {"formula_extraction", "uncertain_symbol_glyphs"}
    assert 32 in _gates(iso21771)["uncertain_symbol_glyphs"]["pages"]
    assert _gates(iso21771)["formula_extraction"]["value"]["latex"] >= 150
    assert _gates(iso21771)["chunk_structure"]["status"] in ("pass", "review")
    # ISO 53 stores ∞ as "•" (Symbol font) and ≤ as "<" (Math-Pi font): only a person can confirm these;
    # its Eq. (3) prints 90° with the degree sign as the letter "o" (never rewritten)
    assert iso53["current_version"]["corpus_status"] == "needs_review"
    attention = {k for k, g in _gates(iso53).items() if g["status"] != "pass"}
    assert attention == {"uncertain_symbol_glyphs", "formula_extraction"}
    assert {3, 6} <= set(_gates(iso53)["uncertain_symbol_glyphs"]["pages"])


def test_pilot_is_refused_until_approved(member_factory, pilot, fake_llm):
    member = _scoped(member_factory, pilot)
    a = _ask(member, "ISO 53'e göre temel kremayer basınç açısı αP kaç derecedir?")
    assert a["content"] == REFUSAL_PHRASE
    assert a["metadata"]["refusal"]["reason"] == "document_not_verified"
    assert a["metadata"]["refusal"]["codes_unverified"] == ["ISO 53"]
    assert fake_llm.calls == []


@pytest.fixture(scope="module")
def pilot_approved(admin, pilot):
    notes = {"ISO 53:1998": "s.3 '•' = ∞ (z = ∞, d = ∞) ve s.6 '<' = ≤ (cP ≤ 0,295 m) sayfa görüntüsüyle kontrol edildi.",
             "ISO 21771:2007": "Pilot: başlıklar, 3.1 sembol tablosu ve eşitlik numaraları kontrol edildi."}
    for code, d in pilot.items():
        r = admin.post(f"/documents/{d['id']}/corpus/approve", {"note": notes[code]})
        assert r.status_code == 200, r.text
        assert r.json()["current_version"]["corpus_status"] == "verified"
    return pilot


def test_pilot_retrieval_regression(settings, pilot_approved):
    from app.db.session import session_scope
    from app.evaluation.parser_comparison import retrieval_metrics

    with session_scope() as db:
        res = retrieval_metrics(db, settings, scopes=_scopes(pilot_approved))
    agg, by_id = res["aggregate"], {q["id"]: q for q in res["queries"]}
    assert agg["mean_recall@4"] >= 0.9, agg
    assert agg["mean_mrr"] >= 0.6, agg
    assert agg["answer_grounding_rate"] >= 0.9, agg
    assert agg["negatives_abstained"] == "2/2", agg
    # the critical regressions individually: ISO 53 αP, ISO 21771 geometry formulas
    for qid in ("53-alphaP", "53-typeD", "21771-d", "21771-mt", "21771-da", "21771-df", "21771-db"):
        assert by_id[qid]["answer_grounded"], by_id[qid]


def test_pilot_formula_question_never_reaches_the_engine(member_factory, fake_llm, pilot_approved):
    member = _scoped(member_factory, pilot_approved)
    fake_llm.responder = lambda m: ("Referans çapı (reference diameter) d, 4.2.4 maddesinde Eşitlik (1) ile "
                                    "tanımlanır [S1]." if "(1)" in m[-1].content else REFUSAL_PHRASE)
    a = _ask(member, "ISO 21771'e göre referans çapı d nasıl hesaplanır?")
    assert a["answer_mode"] == "verified_source" and not a["metadata"].get("calculation_id")
    prompt = fake_llm.calls[-1][0].content
    assert "formül KURMA" in prompt  # formula rule: quote, never rebuild or compute
    from app.db.models import Message
    from app.db.session import session_scope

    with session_scope() as db:  # routing diagnostics are stored, not exposed to members
        meta = db.get(Message, uuid.UUID(a["id"])).metadata_
    assert meta["plan"]["intent_subtype"] == "standards_formula_lookup"
    assert meta["retrieved"][0]["clause"] == "4.2.4" and meta["retrieved"][0]["content_type"] == "formula"


def test_pilot_numeric_request_goes_to_the_engine(admin, pilot_approved):
    r = admin.post("/calculations", {"calc_type": "cylindrical_gear_geometry",
                                     "inputs": {"z": {"value": 20}, "m_n": {"value": 2, "unit": "mm"}}}).json()
    assert r["status"] == "ok"
    pages = {(e["standard_code"], e["page_number"]) for e in r["result"]["evidence"]}
    assert ("ISO 21771:2007", 18) in pages and ("ISO 53:1998", 5) in pages  # verified pilot pages only


# =========================================================================== stage 2
STAGE2_QUERIES = [
    {"id": "54-modules", "q": "ISO 54'e göre modül serisi değerleri nelerdir?", "subtype": "standards_value_lookup",
     "relevant": [("ISO 54", 4, r"(?i)modules")], "answer": [r"1,25", r"(?i)series"]},
    {"id": "286-2-H", "q": "ISO 286-2'ye göre H temel sapmalı delikler için sınır sapmaları tablosu hangisidir?",
     "subtype": "standards_value_lookup",
     "relevant": [("ISO 286-2", 18, r"Table 6 — Limit deviations for holes \(fundamental deviation H\)")],
     "answer": [r"Table 6"]},
    {"id": "286-1-IT", "q": "ISO 286-1'e göre standart tolerans dereceleri tablosu hangi değerleri verir?",
     "subtype": "standards_value_lookup",
     "relevant": [("ISO 286-1", 26, r"Table 1 — Values of standard tolerance grades")], "answer": [r"(?i)tolerance grades"]},
    {"id": "10828-types", "q": "ISO/TR 10828'e göre kapsanan sonsuz vida (worm) profil tipleri nelerdir?",
     "subtype": "standards_value_lookup",
     "relevant": [("ISO/TR 10828", 7, r"(?i)five|worm types"), ("ISO/TR 10828", 84, r"(?i)profiles")],
     "answer": [r"(?i)worm"]},
]


@pytest.fixture(scope="module")
def stage2(settings, admin, pilot_approved):
    return _ingest(settings, admin, STAGE2)


def test_stage2_gates_send_scanned_documents_to_review(stage2):
    for code in ("ISO 54:1996", "ISO/TR 10828:2015"):
        v = stage2[code]["current_version"]
        assert v["ingestion_status"] == "indexed" and v["corpus_status"] == "needs_review"
        assert _gates(stage2[code])["ocr_pages"]["status"] == "review"
    for code in ("ISO 286-1:2010", "ISO 286-2:2010"):
        v = stage2[code]["current_version"]
        assert v["ingestion_status"] == "indexed" and v["corpus_status"] in ("extracted", "needs_review")
        assert _gates(stage2[code])["ocr_pages"]["status"] == "pass"


def test_stage2_scanned_standard_is_refused_before_review(member_factory, stage2):
    member = _scoped(member_factory, {"54": stage2["ISO 54:1996"]})
    a = _ask(member, "ISO 54'e göre modül serisi değerleri nelerdir?")
    assert a["content"] == REFUSAL_PHRASE and a["metadata"]["refusal"]["reason"] == "document_not_verified"


def test_stage2_after_human_review_and_approval(settings, admin, stage2):
    # the reviewer confirms every OCR page of the scanned documents (review queue), then approves
    for code in ("ISO 54:1996", "ISO/TR 10828:2015"):
        pages = admin.get(f"/extractions/pages?document_id={stage2[code]['id']}&limit=500").json()["items"]
        assert pages
        for p in pages:
            assert admin.post(f"/extractions/pages/{p['id']}/confirm",
                              {"note": "OCR metni sayfa görüntüsüyle karşılaştırıldı"}).status_code == 200
    for code, d in stage2.items():
        r = admin.post(f"/documents/{d['id']}/corpus/approve", {"note": "Aşama 2: kalite kapıları ve OCR incelendi."})
        assert r.status_code == 200, r.text
    from app.db.session import session_scope
    from app.evaluation.parser_comparison import retrieval_metrics

    with session_scope() as db:
        res = retrieval_metrics(db, settings, scopes=_scopes(stage2), queries=STAGE2_QUERIES)
    by_id = {q["id"]: q for q in res["queries"]}
    for qid in ("54-modules", "286-2-H", "286-1-IT", "10828-types"):
        assert by_id[qid]["recall@10"] and by_id[qid]["recall@10"] > 0, by_id[qid]
    assert res["aggregate"]["mean_recall@4"] >= 0.75, res["aggregate"]
