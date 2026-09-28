"""Canonical ingestion: parser layer, structure-aware chunks with lineage, quality
gates, the verified-corpus lifecycle, hybrid retrieval and formula/calculation routing.

Unit tests need no database. Integration tests ingest synthetic ISO-structured PDFs
(``iso_like_pdf``: invented text, fictional codes) into the temporary test database.
"""
from __future__ import annotations

import hashlib
import time

import pytest
from app.domain.enums import REFUSAL_PHRASE
from app.ingestion.chunker import chunk_document
from app.ingestion.extractors.base import Block, PageOut
from app.ingestion.parsing.iso_layout import clause_key, plausible_next
from app.ingestion.parsing.symbols import map_symbol_span, rejoin_subscripts, symbol_lexicon
from app.ingestion.quality import ChunkFacts, clause_preserved, evaluate, fingerprint
from app.ingestion.standard_code import validate_code
from app.services.intent import classify
from app.services.retrieval import extract_clauses, extract_equations, plan_query, structural_matches, symbol_row

from conftest import audit_rows, run_jobs
from iso_like_pdf import CODE_GEOM, CODE_RACK, write_geometry_standard, write_rack_standard


# =========================================================================== unit: symbols
def test_symbol_font_letters_become_greek_and_other_fonts_are_untouched():
    assert map_symbol_span("MT-Symbol-Italic", "a") == ("α", 1)
    assert map_symbol_span("Symbol", "r") == ("ρ", 1)
    assert map_symbol_span("MT-Symbol", "p") == ("π", 1)
    assert map_symbol_span("Times-Italic", "aP") == ("aP", 0)
    assert map_symbol_span("SymbolMT", "α") == ("α", 0)  # font with a Unicode map: already Greek
    assert map_symbol_span("MT-Symbol", "•") == ("•", 0)  # uncertain glyph (∞?) is never rewritten


def test_split_subscripts_rejoin_only_to_symbols_of_the_same_document():
    lex = symbol_lexicon("haP hfP hFfP cP da df mn and a basic rack")
    assert rejoin_subscripts("| h aP | 1 m |", lex) == "| haP | 1 m |"
    assert rejoin_subscripts("h FfP and d a", lex) == "hFfP and da"
    assert rejoin_subscripts("a n example", lex) == "a n example"  # "an" is a word, never a joined symbol
    assert rejoin_subscripts("x yz", lex) == "x yz"  # "xyz" is not in the document


# =========================================================================== unit: layout
def test_iso_clause_numbering_rules():
    assert plausible_next((), (1,)) and not plausible_next((), (4,))
    assert plausible_next((5, 3), (5, 4)) and not plausible_next((5, 3), (1,))  # figure key "1 Datum line"
    assert plausible_next((4, 2), (4, 2, 1)) and not plausible_next((4, 2), (4, 2, 4))
    assert plausible_next((4, 2), (4, 2, 4), strong=True)  # bold titled heading after a missed one
    assert plausible_next((7, 9), clause_key("A")) and plausible_next(clause_key("A"), clause_key("A.1"))


def test_native_layout_recovers_structure_symbols_tables_and_equations(tmp_path):
    import pymupdf

    from app.ingestion.parsing.iso_layout import LayoutState, detect_blocks, page_lines

    doc = pymupdf.open(str(write_rack_standard(tmp_path / "rack.pdf")))
    state, blocks = LayoutState(), []
    for page in doc:
        blocks += detect_blocks(page, page_lines(page, state), state)
    heads = {b.clause for b in blocks if b.kind == "heading"}
    assert {"1", "2", "3", "3.1", "4", "5", "Annex A", "A.1"} <= heads
    assert "Key" not in " ".join(b.text for b in blocks if b.kind == "heading")
    tables = {b.label: b.text for b in blocks if b.kind == "table"}
    assert "| αP | 20° |" in tables["Table 2"] and "| ρfP | 0,38 m |" in tables["Table 2"]  # Symbol font -> Greek
    assert "| αFP | Angle of undercut | degrees |" in tables["Table 1"]
    eq = [b for b in blocks if b.kind == "formula"]
    assert eq and eq[0].label == "(1)" and "πm/2" in eq[0].text and "Key" not in eq[0].text
    assert state.stats["symbol_glyphs_mapped"] >= 8


def test_structure_chunks_keep_clauses_apart_and_carry_lineage():
    pages = [
        PageOut(1, "x", "native_text", "s. 1", blocks=[
            Block("heading", "4 Individual gears", clause="4", level=1),
            Block("heading", "4.2.4 Reference diameter", clause="4.2.4", level=3),
            Block("text", "The reference diameter, d, is determined by"),
            Block("formula", "d = z mn / cos β (1)", label="(1)"),
            Block("heading", "4.2.7 Module", clause="4.2.7", level=3),
            Block("text", "For a helical gear the transverse module is found as")]),
        PageOut(2, "y", "native_text", "s. 2", blocks=[
            Block("text", "continued text of the module clause"),
            Block("table", "Table 2 - Values\n| mn | 2 |", label="Table 2")]),
    ]
    chunks = chunk_document(pages)
    # 4.2.4 is one knowledge unit (heading + lead-in + formula); in 4.2.7 the sentence that runs over the
    # page break stays ONE chunk citing pages 1-2, and the table is its own unit under the section parent
    leaf = chunks[0]
    assert (leaf.clause, leaf.content_type, leaf.role, leaf.page_start) == ("4.2.4", "formula", "leaf", 1)
    assert leaf.heading == "4.2.4 Reference diameter" and "(1)" in leaf.text and "determined by" in leaf.text
    assert leaf.heading_path == ["4 Individual gears", "4.2.4 Reference diameter"]
    text = next(c for c in chunks if "continued text" in c.text and c.role != "parent")
    assert text.clause == "4.2.7" and (text.page_start, text.page_end) == (1, 2) and "found as" in text.text
    table = next(c for c in chunks if c.content_type == "table" and c.role != "parent")
    assert table.clause == "4.2.7" and table.page_start == 2 and "| mn | 2 |" in table.text
    assert all(len({c.clause for c in chunks if c.key in p.child_keys} | {p.clause}) == 1
               for p in chunks if p.role == "parent")  # a parent never mixes two clauses


# =========================================================================== unit: gates
def _facts(n=3, **kw):
    base = dict(page_number=1, text="1 Scope\nsome text of the scope clause", content_type="text", clause="1",
                has_lineage=True, content_hash="h", sources=["pymupdf"])
    return [ChunkFacts(**{**base, **kw}) for _ in range(n)]


def _page(n=1, text="1 Scope\nsome text of the scope clause", method="native_text", **kw):
    kw.setdefault("quality", {"score": 1.0, "flags": []})
    return PageOut(n, text, method, f"s. {n}", **kw)


def _evaluate(**over):
    args = dict(is_pdf=True, verified_area=True, standard_code="ISO 90001:2020", code_check={"issues": []},
                validation={"warnings": []}, page_count=1, blank_pages=[], pages=[_page()], chunks=_facts(),
                layout={}, parser="pymupdf", parser_version="x")
    args.update(over)
    return evaluate(**args)


def test_quality_gates_outcomes():
    assert _evaluate()["outcome"] == "extracted"
    assert _evaluate(chunks=[])["outcome"] == "failed"  # nothing indexed -> never "indexed"
    assert _evaluate(chunks=_facts(tsv_empty=True))["outcome"] == "failed"
    ocr = [_page(text="x" * 400, method="ocr", ocr_confidence=0.95)]
    assert _evaluate(pages=ocr)["outcome"] == "needs_review"
    tiny_ocr = [_page(), _page(2, text="ICS 21.200", method="ocr", ocr_confidence=0.9)]
    back = _facts(1, page_number=2, text="ICS 21.200", clause=None)
    assert _evaluate(pages=tiny_ocr, page_count=2, chunks=_facts() + back)["outcome"] == "extracted"  # back cover
    lost = _evaluate(pages=tiny_ocr, page_count=2)  # the back-cover text reached no chunk
    assert any(g["id"] == "content_coverage" and g["status"] == "review" for g in lost["gates"])
    assert _evaluate(code_check={"issues": ["baskı yılı uyuşmuyor"]})["outcome"] == "needs_review"
    unsure = [_page(quality={"score": 1.0, "flags": [], "uncertain_symbol_glyphs": 2})]
    assert _evaluate(pages=unsure)["outcome"] == "needs_review"
    model = _facts(sources=["docling:formula_model"])
    assert _evaluate(chunks=model)["outcome"] == "needs_review"
    known = _evaluate(standard_code="ISO 53:1998")  # critical αP/αFP/tables/equations are missing here
    assert known["outcome"] == "needs_review"
    assert any(g["id"] == "critical_content" and g["status"] == "review" for g in known["gates"])


def test_fingerprint_is_deterministic_and_content_bound():
    a = fingerprint("pymupdf", "1", "ISO 1:2000", ["x", "y"])
    assert a == fingerprint("pymupdf", "1", "ISO 1:2000", ["x", "y"])
    assert a != fingerprint("pymupdf", "1", "ISO 1:2000", ["x", "z"]) != fingerprint("docling", "1", "ISO 1:2000", ["x", "y"])


def test_clause_preservation_counts_parent_clauses():
    assert clause_preserved("5", {"5.1"}) and clause_preserved("Annex A", {"A.1"}) and not clause_preserved("6", {"5.1"})


def test_standard_code_validation_is_deterministic():
    ok = validate_code("ISO 53:1998", "Cylindrical gears -- ISO_TC 60 Gears -- ISO 53, 2, 1998 -- ISO -- x.pdf")
    assert ok["canonical"] and not ok["issues"]
    year = validate_code("ISO/TR 10064-1:1992", "Code of inspection -- ISO_TR 10064, 1, 1991 -- ISO.pdf")
    assert any("baskı yılı" in i for i in year["issues"])
    other = validate_code("ISO 54:1996", "x -- ISO 53, 2, 1998 -- y.pdf")
    assert any("farklı" in i for i in other["issues"])
    assert validate_code("ISO 53", "x.pdf")["issues"]  # no edition year


# =========================================================================== unit: retrieval plan
def test_query_plan_detects_clauses_equations_and_symbols():
    assert extract_clauses("ISO 53 madde 5.4'e göre") == ["5.4"]
    assert extract_clauses("ISO 21771 4.2.4 nedir") == ["4.2.4"]
    assert extract_clauses("0,25 m ve 1.25 m değerleri") == []  # values are not clauses
    assert extract_clauses("ISO 286-2:2010 tablosu") == []
    assert extract_clauses("Ek A.1 nedir") == ["A.1"]
    assert extract_equations("ISO 53 Eşitlik (2) nedir") == ["2"]
    plan = plan_query("ISO 53'e göre Eşitlik (2) formülü nedir?")
    assert structural_matches(plan, "formula") and not structural_matches(plan, "text")
    plan = plan_query("ISO 53 ρfP kaç m?")
    assert symbol_row(plan, "| ρfP | 0,38 m |") and symbol_row(plan, "| rfP | 0,38 m |")
    assert not symbol_row(plan, "| haP | 1 m |")


# =========================================================================== unit: routing (rule #11)
@pytest.mark.parametrize("text", [
    "ISO 21771'e göre alın modülü mt formülü nedir?",
    "ISO 21771'e göre referans çapı d nasıl hesaplanır?",
    "da nasıl hesaplanır?",
    "df formülü nedir",
    "ISO 21771'e göre alın modülünü hesapla",  # a compute verb without numbers asks for the relation
    "ISO 21771 Eşitlik (33) nedir?",
])
def test_formula_questions_route_to_formula_lookup_never_to_the_engine(text):
    it = classify(text)
    assert it.kind == "technical" and it.subtype == "standards_formula_lookup"


@pytest.mark.parametrize("text,calc_type", [
    ("mn = 2 mm ve β = 25° için alın modülü mt hesapla", "transverse_module"),
    ("z=20, mn=2 mm için referans çapı d hesapla", "cylindrical_gear_geometry"),
    ("ISO 286'ya göre 50 mm H7 toleransı nedir?", "iso286_hole_H"),
])
def test_numeric_inputs_route_to_the_calculation_engine(text, calc_type):
    it = classify(text)
    assert it.kind == "calculation" and it.subtype == "numeric_calculation" and it.calc.calc_type == calc_type
    assert it.calc.inputs


def test_value_questions_stay_value_lookups():
    assert classify("ISO 53 αP kaç derece?").subtype == "standards_value_lookup"


def test_evidence_matching_folds_greek_symmetrically_but_keeps_micrometres():
    from app.calc.evidence import norm_text

    assert norm_text("αP 20° ρfP 0,38 m") == norm_text("aP 20° rfP 0,38 m")
    assert norm_text("10 µm") != norm_text("10 mm")


# =========================================================================== integration
def _scan(settings):
    from app.ingestion.watcher import Watcher

    time.sleep(2.1)
    return Watcher(settings).scan()


def _doc(admin, code):
    items = admin.get("/documents?limit=500").json()["items"]
    return next(d for d in items if d["standard_code"] == code)


def _ask(user, text):
    conv = user.post("/conversations", {}).json()["id"]
    r = user.post(f"/conversations/{conv}/messages", {"content": text})
    assert r.status_code == 200, r.text
    return r.json()["assistant_message"]


@pytest.fixture(scope="module")
def canonical_docs(settings, admin):
    folder = settings.iso_booklets_path / "canonical"
    write_rack_standard(folder / "rack-standard.pdf")
    write_geometry_standard(folder / "geometry-standard.pdf")
    _scan(settings)
    run_jobs(settings, verify=False)  # the lifecycle tests decide about approval themselves
    return {"rack": _doc(admin, CODE_RACK), "geom": _doc(admin, CODE_GEOM)}


def test_ingested_but_unapproved_documents_are_not_evidence(admin, canonical_docs):
    rack = canonical_docs["rack"]
    v = rack["current_version"]
    assert v["ingestion_status"] == "indexed" and v["corpus_status"] in ("extracted", "needs_review")
    assert v["parser"] == "pymupdf" and "iso-layout" in v["parser_version"]
    assert v["quality_report"]["fingerprint"] and v["quality_report"]["gates"]
    r = admin.post("/retrieval/search", {"query": f"{CODE_RACK.split(':')[0]} basınç açısı αP"}).json()
    assert r["passages"] == []
    inv = admin.get("/corpus/status").json()["items"]
    assert any(i["standard_code"] == CODE_RACK and i["corpus_status"] != "verified" for i in inv)


def test_every_chunk_carries_full_lineage(admin, canonical_docs):
    doc = canonical_docs["geom"]
    ver = doc["current_version"]
    chunks = admin.get(f"/documents/{doc['id']}/versions/{ver['id']}/chunks").json()["items"]
    assert chunks
    from app.ingestion.chunker import CONTENT_TYPES

    for c in chunks:
        assert c["page_id"] and c["page_start"] <= c["page_end"] and c["chunk_index"] is not None
        assert c["standard_code"] == CODE_GEOM and c["source_hash"] == ver["sha256"]
        assert c["parser"] == "pymupdf" and c["parser_version"] and c["extraction_method"] == "native_text"
        assert c["content_type"] in CONTENT_TYPES and c["heading_path"] is not None
        assert c["extraction_confidence"] is not None
    by_clause = {c["clause"]: c for c in chunks}
    assert by_clause["4.2.4"]["content_type"] == "formula"
    assert by_clause["4.2.4"]["heading"] == "4.2.4 Reference cylinder, reference circle, reference diameter"
    from app.db.models import DocumentChunk
    from app.db.session import session_scope

    with session_scope() as db:
        row = db.get(DocumentChunk, __import__("uuid").UUID(chunks[0]["id"]))
        assert row.content_hash == hashlib.sha256(row.text.encode("utf-8")).hexdigest()


def test_critical_engineering_tokens_survive_extraction_and_indexing(admin, canonical_docs):
    from sqlalchemy import text

    from app.db.session import session_scope

    with session_scope() as db:
        full = {k: db.execute(text("SELECT string_agg(c.text, E'\\n') FROM document_chunks c "
                                   "WHERE c.document_id = :d"), {"d": canonical_docs[k]["id"]}).scalar()
                for k in ("rack", "geom")}
        lexemes = db.execute(text("SELECT string_agg(c.tsv::text, ' ') FROM document_chunks c "
                                  "WHERE c.document_id = :d"), {"d": canonical_docs["rack"]["id"]}).scalar()
    import re

    def has(tok, s):
        return re.search(r"(?<![^\W_])" + re.escape(tok) + r"(?![^\W_])", s) is not None

    for tok in ("αP", "αFP", "ρfP", "haP", "hfP", "cP", "Table 1", "Table 2", "(1)", "5.4", "A.1"):
        assert has(tok, full["rack"]), tok
    for tok in ("m", "mn", "mt", "z", "d", "da", "df", "β", "FD", "FE", "(1)", "(2)", "(33)", "(34)", "4.2.4"):
        assert has(tok, full["geom"]), tok
    assert not has("aP", full["rack"]) and not has("rfP", full["rack"])  # no Symbol-font Latin left
    for lexeme in ("'αp'", "'αfp'", "'ρfp'", "'hap'", "'5.4'"):
        assert lexeme in lexemes, lexeme  # exact lexemes for the exact-match channel


def test_approval_lifecycle_is_owner_only_audited_and_fingerprint_bound(admin, member_factory, settings,
                                                                        canonical_docs):
    rack = canonical_docs["rack"]
    member = member_factory()
    assert member.post(f"/documents/{rack['id']}/corpus/approve", {"note": "x"}).status_code == 403
    if rack["current_version"]["corpus_status"] == "needs_review":  # a review needs a written note
        assert admin.post(f"/documents/{rack['id']}/corpus/approve", {}).status_code == 409
    r = admin.post(f"/documents/{rack['id']}/corpus/approve", {"note": "Tablolar ve semboller kontrol edildi."})
    assert r.status_code == 200 and r.json()["current_version"]["corpus_status"] == "verified"
    assert audit_rows("corpus.verified", target_id=rack["id"])
    hits = admin.post("/retrieval/search", {"query": f"{CODE_RACK.split(':')[0]} basınç açısı αP"}).json()["passages"]
    assert hits and hits[0]["standard_code"] == CODE_RACK
    # an identical re-extraction keeps the approval (same fingerprint)
    assert admin.post(f"/documents/{rack['id']}/reindex").status_code == 200
    run_jobs(settings, verify=False)
    assert _doc(admin, CODE_RACK)["current_version"]["corpus_status"] == "verified"
    # revoke -> out of the verified corpus, reason required
    assert admin.post(f"/documents/{rack['id']}/corpus/revoke", {}).status_code == 409
    r = admin.post(f"/documents/{rack['id']}/corpus/revoke", {"note": "yeniden inceleme"})
    assert r.status_code == 200 and r.json()["current_version"]["corpus_status"] == "needs_review"
    assert admin.post("/retrieval/search", {"query": f"{CODE_RACK.split(':')[0]} basınç açısı αP"}).json()["passages"] == []
    assert audit_rows("corpus.verification_revoked", target_id=rack["id"])
    assert admin.post(f"/documents/{rack['id']}/corpus/approve", {"note": "yeniden onay"}).status_code == 200


def test_changed_file_drops_out_of_the_verified_corpus_until_reapproved(admin, settings, canonical_docs):
    geom = canonical_docs["geom"]
    assert admin.post(f"/documents/{geom['id']}/corpus/approve", {"note": "ilk onay"}).status_code == 200
    path = settings.iso_booklets_path / "canonical" / "geometry-standard.pdf"
    import pymupdf

    doc = pymupdf.open(str(path))
    doc[0].insert_text((48, 800), "Revision note added to the synthetic geometry standard.", fontname="helv", fontsize=8)
    tmp = path.with_suffix(".tmp")  # the watcher ignores *.tmp while the new bytes are written
    doc.save(str(tmp))
    doc.close()
    tmp.replace(path)
    assert _scan(settings)["changed"] == 1
    run_jobs(settings, verify=False)
    v = _doc(admin, CODE_GEOM)["current_version"]
    assert v["version_number"] == 2 and v["corpus_status"] != "verified"
    r = admin.post("/retrieval/search", {"query": f"{CODE_GEOM.split(':')[0]} referans çapı d"}).json()
    assert r["passages"] == []


def test_formula_lookup_retrieves_the_numbered_formula_chunk(admin, settings, canonical_docs):
    geom = _doc(admin, CODE_GEOM)
    if geom["current_version"]["corpus_status"] != "verified":
        assert admin.post(f"/documents/{geom['id']}/corpus/approve", {"note": "onay"}).status_code == 200
    code = CODE_GEOM.split(":")[0]
    r = admin.post("/retrieval/search", {"query": f"{code}'e göre referans çapı d nasıl hesaplanır?"}).json()
    assert r["passages"] and "determined by" in r["passages"][0]["excerpt"] and "(1)" in r["passages"][0]["excerpt"]
    r = admin.post("/retrieval/search", {"query": f"{code} Eşitlik (33) formülü nedir?"}).json()
    assert r["passages"] and "(33)" in r["passages"][0]["excerpt"]


def test_iso_code_routing_distinguishes_missing_and_unverified_standards(admin, fake_llm, settings, canonical_docs):
    rack = _doc(admin, CODE_RACK)
    assert admin.post(f"/documents/{rack['id']}/corpus/revoke", {"note": "test"}).status_code == 200
    code = CODE_RACK.split(":")[0]
    a = _ask(admin, f"{code}'e göre basınç açısı αP kaç derecedir?")
    assert a["content"] == REFUSAL_PHRASE
    refusal = a["metadata"]["refusal"]
    assert refusal["reason"] == "document_not_verified" and refusal["codes_unverified"] == [code]
    assert fake_llm.calls == []  # refused before any generation
    a = _ask(admin, "ISO 99999'a göre basınç açısı kaç derecedir?")
    assert a["content"] == REFUSAL_PHRASE and a["metadata"]["refusal"]["reason"] == "no_passages"
    assert a["metadata"]["refusal"]["codes_missing"] == ["ISO 99999"]
    assert admin.post(f"/documents/{rack['id']}/corpus/approve", {"note": "geri"}).status_code == 200


def test_empty_and_ocr_only_documents_are_refused(admin, settings):
    import pymupdf

    folder = settings.iso_booklets_path / "canonical"
    folder.mkdir(parents=True, exist_ok=True)
    empty = pymupdf.open()
    empty.new_page()
    empty.save(str(folder / "iso-91111-empty.pdf"))
    # a scanned page: text only as an image -> OCR draft
    src = pymupdf.open()
    pg = src.new_page(width=400, height=200)
    pg.insert_text((20, 60), "ISO 91112:2020 synthetic scanned standard", fontsize=14)
    pg.insert_text((20, 100), "The module m of the scanned gear is 7 mm.", fontsize=14)
    pix = pg.get_pixmap(dpi=150)
    scan = pymupdf.open()
    scan.new_page(width=400, height=200).insert_image(pymupdf.Rect(0, 0, 400, 200), pixmap=pix)
    scan.save(str(folder / "iso-91112-scanned.pdf"))
    _scan(settings)
    run_jobs(settings, verify=False)
    items = {d["original_filename"]: d for d in admin.get("/documents?limit=500").json()["items"]}
    empty_doc = items["iso-91111-empty.pdf"]
    assert empty_doc["current_version"]["corpus_status"] == "failed"
    assert empty_doc["current_version"]["ingestion_status"] in ("stored_only", "failed")
    assert admin.post(f"/documents/{empty_doc['id']}/corpus/approve", {"note": "x"}).status_code == 409
    from app.ingestion.ocr import OcrEngine

    if OcrEngine.get() is None:
        pytest.skip("local OCR engine unavailable")
    scanned = items["iso-91112-scanned.pdf"]
    assert scanned["current_version"]["extraction_summary"]["methods"].get("ocr")
    assert scanned["current_version"]["corpus_status"] == "needs_review"
    # even an approved scan never answers from unconfirmed OCR text (draft_extraction chunks)
    assert admin.post(f"/documents/{scanned['id']}/corpus/approve", {"note": "OCR kontrol edilecek"}).status_code == 200
    a = _ask(admin, "ISO 91112'ye göre dişlinin modülü kaç mm?")
    assert a["content"] == REFUSAL_PHRASE
