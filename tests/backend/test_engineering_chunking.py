"""Engineering document chunking: invariants on a synthetic standard, formula/LaTeX safety,
lineage in the database, retrieval of the right engineering unit, parent-context expansion,
safe re-ingestion and the 0004 migration round trip.

The synthetic standard (``engineering_pdf``) has invented text and a fictional code; it reproduces
the layout situations of real ISO PDFs with real PDF geometry (subscripts, fraction bars, running
headers/footers, continued tables, clauses across page breaks).
"""
from __future__ import annotations

import re
import time
import uuid

import pytest
from app.ingestion import quality
from app.ingestion.chunker import DEFAULT_CONFIG, chunk_pages, split_sentences
from app.ingestion.parsing import formula as F
from app.ingestion.tokens import estimate_tokens

from conftest import run_jobs
from engineering_pdf import CODE, write_engineering_standard


# =========================================================================== extraction fixture (no DB writes)
@pytest.fixture(scope="module")
def extracted(settings, tmp_path_factory):
    from app.ingestion.extractors.base import ExtractContext
    from app.ingestion.extractors.pdf import extract_pdf
    from app.ingestion.storage import Storage

    path = write_engineering_standard(tmp_path_factory.mktemp("eng") / "engineering.pdf")
    ctx = ExtractContext(settings=settings, storage=Storage(settings), version_id=uuid.uuid4(), sha256="0" * 64,
                         filename=path.name, abs_path=str(path))
    result = extract_pdf(path.read_bytes(), ctx)
    chunks, model = chunk_pages(result.pages, standard_code=CODE)
    return {"result": result, "chunks": chunks, "model": model}


def _chunk_with(chunks, needle: str):
    return next(c for c in chunks if needle in c.text and c.role != "parent")


def _formula(chunks, number: str) -> dict:
    return next(f for c in chunks for f in c.formulas if f.get("number") == number)


# =========================================================================== invariants
def test_repeated_headers_footers_and_page_numbers_are_suppressed(extracted):
    layout = extracted["result"].metadata["layout"]
    assert any("bs iso" in k for k in layout["repeated_furniture"]), layout["repeated_furniture"]
    for c in extracted["chunks"]:
        assert "BS ISO 97771" not in c.text and "All rights reserved" not in c.text
        assert not re.search(r"(?m)^\d{1,2}$", c.text), c.text  # page numbers


def test_equation_keeps_number_heading_clause_page_and_lead_in(extracted):
    c = _chunk_with(extracted["chunks"], "(19)")
    assert c.clause == "4.3.1" and c.page_start == 1
    assert c.heading_path == ["4 Individual gears", "4.3 Involute helicoids",
                              "4.3.1 Base cylinder, base circle, base diameter"]
    assert "is given by" in c.text  # the sentence that introduces the equation
    assert c.content_type == "formula" and "19" in c.equation_numbers and "20" in c.equation_numbers


def test_variable_definitions_are_linked_to_the_equation(extracted):
    f = _formula(extracted["chunks"], "19")
    meanings = {v["symbol"]: v["description"] for v in f["variables"]}
    assert meanings["db"] == "base diameter" and meanings["d"] == "reference diameter"
    assert meanings["αt"] == "transverse pressure angle"
    assert f["legend"].startswith("where")  # the legend after (19) and (20) defines both
    assert _formula(extracted["chunks"], "20")["legend"] == f["legend"]
    c = _chunk_with(extracted["chunks"], "(19)")
    assert "db: base diameter" in c.context  # structural overlap shown to the model and indexed


def test_latex_is_derived_from_the_page_geometry_only(extracted):
    f19, f20 = _formula(extracted["chunks"], "19"), _formula(extracted["chunks"], "20")
    assert f19["status"] == "exact" and f19["latex"] == r"d_{b} = d \cos \alpha_{t}"
    assert f19["plain"] == "db = d cos αt" and f19["raw"]  # provenance kept next to the normalised forms
    assert f20["status"] == "reconstructed" and "fraction" in f20["structures"]
    assert f20["latex"] == r"d_{b} = \frac{z m_{n} \cos \alpha_{t}}{\cos \beta}"
    for f in (f19, f20):
        assert F.glyphs_conserved(f["raw"].replace(" ", ""), f["plain"])  # no invented symbol
        assert not F.latex_problems(f["latex"])


def test_unexplained_layout_or_uncertain_glyphs_never_get_latex():
    g = [F.Glyph("x", (0, 0, 5, 10), 10, 9), F.Glyph("=", (8, 0, 13, 10), 10, 9),
         F.Glyph("y", (16, -8, 21, 2), 10, 1)]  # full-size glyph 8 pt above the baseline, no bar explains it
    res = F.reconstruct(g, F.Prims(), "7")
    assert res.status == "needs_review" and res.latex is None and res.reasons
    w = [F.Glyph("u", (0, 0, 5, 10), 10, 9, "Times-Italic"), F.Glyph("W", (8, 0, 13, 10), 10, 9, "ISOamsr"),
         F.Glyph("1", (16, 0, 21, 10), 10, 9)]  # ISOamsr "W" is ⩾: never guessed
    res = F.reconstruct(w, F.Prims(), "52")
    assert res.status == "needs_review" and res.latex is None
    assert any(r.startswith("uncertain_font_glyphs") for r in res.reasons)
    assert F.reconstruct([], F.Prims(), "1").status == "needs_review"
    assert F.latex_problems(r"F_{\alphaT}")  # fused commands are rejected


def test_fraction_and_subscripts_are_reconstructed_from_primitives():
    # num "a" over den "b" with a drawn bar, subscript "n" on the numerator
    g = [F.Glyph("c", (0, 5, 5, 15), 10, 13), F.Glyph("=", (7, 5, 12, 15), 10, 13),
         F.Glyph("a", (16, -2, 21, 8), 10, 6), F.Glyph("n", (21, 1, 25, 8), 7, 8),
         F.Glyph("b", (16, 12, 21, 22), 10, 20)]
    res = F.reconstruct(g, F.Prims(hbars=[(15, 27, 10)]), "3")
    assert res.latex == r"c = \frac{a_{n}}{b}" and res.plain == "c = an/b" and res.lhs == "c"


def test_heading_path_survives_for_every_body_chunk(extracted):
    body = [c for c in extracted["chunks"] if c.content_type != "front_matter"]
    assert body and all(c.heading_path for c in body)
    assert all(c.heading_path[-1].startswith(c.clause) for c in body if c.clause)


def test_clause_crossing_a_page_break_stays_one_unit(extracted):
    c = _chunk_with(extracted["chunks"], "evaluated along the path of contact")
    assert "the tooth flank is" in c.text  # the first half of the sentence is on page 1
    assert (c.page_start, c.page_end) == (1, 2) and c.locator == "s. 1–2"


def test_continued_table_is_merged_and_every_part_keeps_caption_header_and_units(extracted):
    tables = [c for c in extracted["chunks"] if "3" in c.table_numbers and c.role != "parent"]
    assert tables
    rows = [ln for c in tables for ln in c.text.splitlines() if re.match(r"^\| \d+ \| \d+ \| mm \|$", ln)]
    assert len(set(rows)) == 24  # 12 rows on page 2 + 12 rows of "Table 3 (continued)" on page 3
    for c in tables:
        assert "| Module | Tolerance | Unit |" in c.text and "Table 3" in c.text
        assert c.page_start == 2 and c.page_end == 3
    t = tables[0].table
    assert t["columns"] == ["Module", "Tolerance", "Unit"] and t["header_rows"] == 1
    assert "µm" in t["units"] and t["continued_pages"] == [3]


def test_chunks_respect_the_hard_limit_and_split_at_sentences(extracted):
    for c in extracted["chunks"]:
        if c.role != "parent":
            assert c.tokens <= DEFAULT_CONFIG.hard_max, (c.key, c.tokens)
    long = [c for c in extracted["chunks"] if c.clause == "6" and c.role != "parent"]
    assert long and all(c.text.rstrip().endswith(".") for c in long)  # never cut inside a sentence
    assert quality.HARD_MAX_TOKENS == DEFAULT_CONFIG.hard_max  # the gate uses the chunker's limit


def test_parent_child_links_are_valid(extracted):
    from app.ingestion.pipeline import chunk_facts

    facts = chunk_facts(extracted["chunks"])
    assert quality.chunk_structure_gate(facts).status in ("pass", "review")
    keys = {c.key: c for c in extracted["chunks"]}
    for c in extracted["chunks"]:
        if c.parent_key:
            assert c.key in keys[c.parent_key].child_keys
    broken = [*facts[:1]]
    broken[0].parent_key = "c9999"
    assert quality.chunk_structure_gate(broken).status == "fail"


def test_content_types_and_symbol_glossary(extracted):
    kinds = {c.content_type for c in extracted["chunks"]}
    assert {"formula", "table", "figure_caption"} <= kinds
    assert extracted["model"].glossary["mn"]["description"] == "normal module"


def test_quality_gates_trace_every_chunk_to_its_pages(extracted):
    from app.ingestion.pipeline import chunk_facts

    r = extracted["result"]
    report = quality.evaluate(is_pdf=True, verified_area=True, standard_code=CODE, code_check={"issues": []},
                              validation={"warnings": []}, page_count=len(r.pages), blank_pages=[], pages=r.pages,
                              chunks=chunk_facts(extracted["chunks"]), layout=r.metadata["layout"],
                              parser=r.parser, parser_version=r.parser_version)
    gates = {g["id"]: g for g in report["gates"]}
    assert gates["traceability"]["value"] >= 0.95 and gates["chunk_structure"]["status"] == "pass"
    assert gates["content_coverage"]["value"] >= 0.95
    assert gates["formula_extraction"]["value"]["latex"] == 2


def test_sentence_splitting_and_token_estimate_are_sane():
    parts = split_sentences("The first one. The second one; see 4.2. (1) = x")
    assert "".join(parts).replace(" ", "") == "Thefirstone.Thesecondone;see4.2.(1)=x"
    assert estimate_tokens("db = d cos αt (19)") > estimate_tokens("base diameter")
    assert estimate_tokens("") == 0


# =========================================================================== database: lineage, retrieval, LaTeX
@pytest.fixture(scope="module")
def eng_doc(settings, admin):
    from app.ingestion.watcher import Watcher

    write_engineering_standard(settings.iso_booklets_path / "engineering" / "engineering-standard.pdf")
    time.sleep(2.1)
    Watcher(settings).scan()
    run_jobs(settings, verify=True)
    items = admin.get("/documents?limit=500").json()["items"]
    return next(d for d in items if d["standard_code"] == CODE)


def test_chunks_are_stored_with_hierarchy_formulas_and_tables(admin, eng_doc):
    ver = eng_doc["current_version"]
    rows = admin.get(f"/documents/{eng_doc['id']}/versions/{ver['id']}/chunks").json()["items"]
    by_id = {r["id"]: r for r in rows}
    assert ver["corpus_status"] == "verified" and ver["quality_report"]["pipeline_version"].endswith("/2")
    for r in rows:
        assert r["chunker_version"].startswith("engineering-chunker/") and r["meta_hash"]
        assert r["page_start"] <= r["page_end"]
        if r["parent_id"]:
            assert by_id[r["parent_id"]]["chunk_role"] in ("parent", "child")  # table overview parents row groups
    eq = next(r for r in rows if "19" in r["equation_numbers"])
    f19 = next(f for f in eq["formulas"] if f["number"] == "19")
    assert f19["latex"] == r"d_{b} = d \cos \alpha_{t}"  # LaTeX survives storage
    from app.db.models import DocumentVersion
    from app.db.session import session_scope

    with session_scope() as db:
        meta = db.get(DocumentVersion, uuid.UUID(ver["id"])).metadata_
    assert meta["symbol_glossary"]["db"]["description"] == "base diameter"
    assert meta["chunking"]["formulas_latex"] == 2 and meta["chunking"]["chunker_version"]


def test_formula_question_retrieves_the_equation_with_latex_and_definitions(settings, eng_doc):
    from app.db.session import session_scope
    from app.services.access import ScopeSet
    from app.services.retrieval import build_context, plan_query, search

    q = "What is the base circle diameter formula in ISO 97771?"
    with session_scope() as db:
        plan = plan_query(q)
        ps = search(db, ScopeSet(is_owner=True, user_id=uuid.UUID(int=0)), plan, top_k=4,
                    subtype="standards_formula_lookup", question=q)
        assert ps and "19" in ps[0].equation_numbers and ps[0].content_type == "formula"
        assert ps[0].page_number == 1 and ps[0].locator == "s. 1"  # citation points to the source page
        assert ps[0].boosts.get("defines_quantity")
        used = build_context(db, ps, plan, settings.retrieval_max_context_chars)
    top = used[0].prompt_text
    assert r"LaTeX (19): d_{b} = d \cos \alpha_{t}" in top  # LaTeX survives retrieval
    assert "db: base diameter" in top and "(19)" in top


def test_table_value_question_retrieves_the_table_unit(eng_doc):
    from app.db.session import session_scope
    from app.services.access import ScopeSet
    from app.services.retrieval import plan_query, search

    q = "ISO 97771 synthetic flank tolerance value table for module 14"
    with session_scope() as db:
        ps = search(db, ScopeSet(is_owner=True, user_id=uuid.UUID(int=0)), plan_query(q), top_k=4,
                    subtype="standards_value_lookup", question=q)
    assert ps and ps[0].content_type in ("table", "table_row_group") and "| 14 | 24 | mm |" in ps[0].text


def test_reingest_is_dry_run_first_protects_approval_and_remaps_citations(settings, admin, eng_doc, fake_llm,
                                                                         monkeypatch):
    from sqlalchemy import select, text

    from app.db.models import DocumentChunk, MessageSource
    from app.db.session import session_scope
    from app.ingestion.pipeline import reingest_version

    vid = uuid.UUID(eng_doc["current_version"]["id"])
    fake_llm.responder = lambda messages: "Temel çap db = d cos αt ile verilir, Eşitlik (19) [S1]."
    conv = admin.post("/conversations", {}).json()["id"]
    r = admin.post(f"/conversations/{conv}/messages",
                   {"content": "ISO 97771 base diameter formula nedir? kaynak ver"}).json()["assistant_message"]
    assert r["answer_mode"] == "verified_source", r
    with session_scope() as db:
        before = set(db.execute(select(DocumentChunk.id).where(DocumentChunk.version_id == vid)).scalars())
        dry = reingest_version(db, vid, dry_run=True)
        assert dry["applied"] is False and "blocked" not in dry and dry["old"]["chunks"] == len(before)
        assert set(db.execute(select(DocumentChunk.id).where(DocumentChunk.version_id == vid)).scalars()) == before
    # a different chunking configuration changes the extraction fingerprint of a VERIFIED version
    monkeypatch.setenv("DAYANERA_CHUNKING_CONFIG", '{"leaf_section_max": 0}')
    with session_scope() as db:
        blocked = reingest_version(db, vid, dry_run=False)
        assert blocked["applied"] is False and "VERIFIED" in blocked["blocked"]
        applied = reingest_version(db, vid, dry_run=False, allow_revoke=True)
        assert applied["applied"] is True and applied["corpus_status_after"] != "verified"
        after = set(db.execute(select(DocumentChunk.id).where(DocumentChunk.version_id == vid)).scalars())
        assert after and not after & before
        cited = db.execute(select(MessageSource.chunk_id).where(MessageSource.version_id == vid)).scalars().all()
        assert cited and all(c in after for c in cited if c is not None)
        assert applied["message_sources"]["remapped"] >= 1
        n_audit = db.execute(text("SELECT count(*) FROM audit_events WHERE event_type = 'corpus.verification_revoked'"
                                  " AND target_id = :d"), {"d": eng_doc["id"]}).scalar()
        assert n_audit >= 1


def test_migration_0004_round_trip(settings):
    import psycopg
    from alembic import command

    from app.cli import _alembic_cfg

    url = settings.database_url.rsplit("/", 1)[0] + "/dayanera_test_mig0004"
    admin_url = url.rsplit("/", 1)[0].replace("postgresql+psycopg://", "postgresql://") + "/postgres"
    with psycopg.connect(admin_url, autocommit=True) as c:
        c.execute('DROP DATABASE IF EXISTS "dayanera_test_mig0004" WITH (FORCE)')
        c.execute('CREATE DATABASE "dayanera_test_mig0004"')
    try:
        cfg = _alembic_cfg()
        cfg.cmd_opts = type("o", (), {"x": [f"database_url={url}"]})()
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0003_self_maintenance")
        command.upgrade(cfg, "head")
        with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as c:
            cols = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns "
                                            "WHERE table_name = 'document_chunks'")}
        assert {"parent_id", "chunk_role", "heading_path", "formula", "table_data", "meta_hash"} <= cols
    finally:
        with psycopg.connect(admin_url, autocommit=True) as c:
            c.execute('DROP DATABASE IF EXISTS "dayanera_test_mig0004" WITH (FORCE)')
