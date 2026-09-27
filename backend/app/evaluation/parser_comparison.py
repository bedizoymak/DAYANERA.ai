"""Controlled parser comparison on the pilot documents (ISO 53, ISO 21771).

    python -m app.evaluation.parser_comparison [--variants baseline,pymupdf,hybrid,docling] [--docling-dir DIR]

Every variant is ingested into its own throwaway database (``dayanera_eval_<variant>``)
through the REAL pipeline: persistence, chunk lineage, quality gates, the tsvector
index, owner approval and ``retrieval.search``. Only the PDF parse step differs:

  baseline  the extractor before this change (PyMuPDF page text, page-window chunks)
  pymupdf   PyMuPDF + Symbol-font recovery + deterministic ISO layout + structure chunks
  hybrid    pymupdf, with Docling's table structure
  docling   Docling layout for every block (page text stays the PDF text layer)

Docling output is read from pre-computed runner JSON files (``--docling-dir``:
``iso53*.json`` / ``iso21771*.json``) produced by ``docling_runner.py``. Results
(metrics only, no document text) go to DATA_ROOT/eval/parser_comparison.{json,md}.
The real ``dayanera`` database and the corpus folder are never touched.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import statistics
import sys
import time
import uuid
from pathlib import Path

from app.core.config import REPO_ROOT, reset_settings_cache
from app.core.paths import fs
from app.evaluation.pilot_ground_truth import PILOT, QUERIES

VARIANTS = ("baseline", "pymupdf", "hybrid", "docling")


# --------------------------------------------------------------------------- environment
def _read_env() -> dict[str, str]:
    out = {}
    p = REPO_ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def _prepare(variant: str, run_root: Path) -> str:
    import psycopg

    base = os.environ.get("DAYANERA_EVAL_BASE_DATABASE_URL") or _read_env().get("DATABASE_URL")
    head, _ = base.rsplit("/", 1)
    name = f"dayanera_eval_{variant}"
    with psycopg.connect(f"{head}/postgres".replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{name}"')
    if run_root.exists():
        shutil.rmtree(fs(run_root))
    (run_root / "iso").mkdir(parents=True)
    os.environ.update({
        "DATABASE_URL": f"{head}/{name}", "PROJECT_ROOT": str(REPO_ROOT), "ISO_BOOKLETS_PATH": str(run_root / "iso"),
        "DATA_ROOT": str(run_root / "data"), "AGENT_NOTES_PATH": str(run_root / "notes"), "EXTRA_WATCH_ROOTS": "",
        "WATCHER_ENABLED": "false", "DISABLE_BACKGROUND_WORKERS": "true", "SUPABASE_ENABLED": "false",
        "INITIAL_ADMIN_USERNAME": "eval-admin", "INITIAL_ADMIN_PASSWORD": uuid.uuid4().hex,
        "PDF_PARSER": "pymupdf" if variant == "baseline" else variant, "DOCLING_FORMULA_ENRICHMENT": "true",
    })
    reset_settings_cache()
    return os.environ["DATABASE_URL"]


# --------------------------------------------------------------------------- variant parse steps
def _baseline_extract(data: bytes, ctx):
    """The PDF extractor as it was before the canonical pipeline (git HEAD 3f9df6d)."""
    import pymupdf

    from app.ingestion.extractors import pdf as P
    from app.ingestion.extractors.base import ExtractionOutput, PageOut, normalize_text

    out = ExtractionOutput(parser="baseline-pymupdf", parser_version=f"pymupdf {pymupdf.VersionBind}; page-window")
    doc = pymupdf.open(stream=data, filetype="pdf")
    out.metadata = {"page_count": len(doc), "blank_pages": []}
    for idx, page in enumerate(doc):
        n = idx + 1
        text, method, conf = normalize_text(page.get_text("text")), "native_text", None
        if P.is_degenerate(text):
            rebuilt = normalize_text(P.rebuild_from_chars(page))
            if rebuilt and not P.is_degenerate(rebuilt):
                text, method = rebuilt, "native_text_rebuilt"
        if len(text) < P.MIN_NATIVE_CHARS:
            cached = ctx.storage.ocr_cache_get(ctx.sha256, n)
            ocr_text, conf = (cached.get("text", ""), cached.get("confidence")) if cached else P._ocr_page(page, 200)
            if not cached:
                ctx.storage.ocr_cache_put(ctx.sha256, n, {"text": ocr_text, "confidence": conf})
            if normalize_text(ocr_text):
                text, method = normalize_text(ocr_text), "ocr"
        if text:
            out.pages.append(PageOut(n, text, method, f"s. {n}", conf, quality=P.page_quality(text, method, conf),
                                     parser=out.parser, parser_version=out.parser_version))
    return out


_ORIGINAL_EXTRACT = None


def _install_parse_step(variant: str, captured: dict):
    from app.ingestion import pipeline

    global _ORIGINAL_EXTRACT
    if _ORIGINAL_EXTRACT is None:  # always wrap the real extractor, never a previous variant's wrapper
        _ORIGINAL_EXTRACT = pipeline.extract_bytes
    original = _ORIGINAL_EXTRACT

    def extract(data, det, ctx):
        if det.category != "pdf":
            return original(data, det, ctx)
        out = _baseline_extract(data, ctx) if variant == "baseline" else original(data, det, ctx)
        captured[ctx.filename] = out
        return out

    pipeline.extract_bytes = extract


def _seed_docling_cache(settings, docling_dir: Path, sha_by_key: dict[str, str]) -> None:
    cache = settings.data_root / "indexes" / "docling-cache"
    cache.mkdir(parents=True, exist_ok=True)
    for key, sha in sha_by_key.items():
        src = sorted(docling_dir.glob(f"{key}*.json"))
        if not src:
            raise SystemExit(f"Docling JSON for {key} not found in {docling_dir}")
        shutil.copyfile(src[0], cache / f"{sha}-formulas.json")


# --------------------------------------------------------------------------- metrics
def _norm_words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


_LATEX = [(re.compile(r"\\(?:mathrm|text|operatorname)\s*\{([^{}]*)\}"), r"\1"),
          (re.compile(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}"), r"(\1)/(\2)")]
_LATEX_SYM = {r"\alpha": "α", r"\beta": "β", r"\rho": "ρ", r"\pi": "π", r"\gamma": "γ", r"\cos": "cos",
              r"\sin": "sin", r"\tan": "tan", r"\cdot": "", r"\times": "", r"\left": "", r"\right": "",
              r"\circ": "°", r"\,": "", r"\;": "", r"\!": "", r"\Box": "?", r"\quad": "", r"\mathrm": ""}
_FTOK = re.compile(r"cos|sin|tan|max|[A-Za-zα-ωΑ-Ω]|\d+(?:,\d+)?|[=+\-/()\[\]°]")


def formula_tokens(s: str) -> list[str]:
    s = re.sub(r"\(\s*(?:[A-Z]\.)?\d{1,3}[a-z]?\s*\)\s*$", "", s.strip())  # equation number
    for _ in range(4):
        for pat, rep in _LATEX:
            s = pat.sub(rep, s)
    for k, v in sorted(_LATEX_SYM.items(), key=lambda kv: -len(kv[0])):
        s = s.replace(k, v)
    s = s.replace("−", "-").replace("–", "-").replace("·", "").replace("×", "").replace("^", "").replace("_", "")
    s = s.replace("{", "").replace("}", "").replace("\\", "")
    return _FTOK.findall(s)


def _bag_f1(a: list[str], b: list[str]) -> float:
    from collections import Counter

    ca, cb = Counter(a), Counter(b)
    inter = sum((ca & cb).values())
    if not inter:
        return 0.0
    p, r = inter / sum(ca.values()), inter / sum(cb.values())
    return 2 * p * r / (p + r)


def _token(tok: str, text: str) -> int:
    return len(re.findall(r"(?<![^\W_])" + re.escape(tok) + r"(?![^\W_])", text))


def _rows(table_text: str) -> list[list[str]]:
    return [[" ".join(c.split()) for c in ln.strip().strip("|").split("|")]
            for ln in table_text.splitlines() if ln.strip().startswith("|")]


def parse_metrics(key: str, out, chunks: list[dict]) -> dict:
    gt = PILOT[key]
    pages = out.pages
    blocks = [(p.page_number, b) for p in pages for b in p.blocks]
    page_count = out.metadata.get("page_count", len(pages))
    blank = out.metadata.get("blank_pages", [])
    text_pages = {p.page_number for p in pages if p.text.strip()}
    chunk_text = "\n".join(c["text"] for c in chunks)
    # headings: clause number + title words, on the right page (±1)
    heads = [(pg, b) for pg, b in blocks if b.kind == "heading"]
    found_heads = 0
    for clause, title, page in gt["headings"]:
        want = set(_norm_words(title))
        if any(b.clause == clause and abs(pg - page) <= 1 and len(want & set(_norm_words(b.text))) >= 0.8 * len(want)
               for pg, b in heads):
            found_heads += 1
    chunk_clauses = {c["clause"] for c in chunks if c["clause"]}
    # tables: complete, ordered rows inside a table block of the right page
    table_rows_ok = table_rows = captions_ok = 0
    for label, (page, rows) in gt["tables"].items():
        tbls = [b for pg, b in blocks if b.kind == "table" and pg == page]
        if label.startswith("Table"):
            captions_ok += any(b.label == label for b in tbls)
        grid = [r for b in tbls for r in _rows(b.text)]
        for row_label, cells in rows:
            table_rows += 1
            for r in grid:
                if r and r[0] == row_label:
                    rest, ok = r[1:], True
                    for want in cells:
                        idx = next((i for i, c in enumerate(rest) if want in c), None)
                        if idx is None:
                            ok = False
                            break
                        rest = rest[idx + 1:]
                    if ok:
                        table_rows_ok += 1
                        break
    # formulas: detected (numbered formula block on the right page), token F1, order similarity, exact
    f_detect, f1s, sims, exact = 0, [], [], 0
    for num, (page, canon) in gt["formulas"].items():
        want = formula_tokens(canon)
        cands = [b for pg, b in blocks if b.kind == "formula" and abs(pg - page) <= 1
                 and (b.label in (f"({num})", num) or re.search(rf"\({re.escape(num)}\)\s*$", b.text))]
        if not cands:
            f1s.append(0.0)
            sims.append(0.0)
            continue
        f_detect += 1
        best = max(cands, key=lambda b: _bag_f1(formula_tokens(b.text), want))
        got = formula_tokens(best.text)
        f1s.append(_bag_f1(got, want))
        sims.append(difflib.SequenceMatcher(None, got, want).ratio())
        exact += got == want
    # engineering symbols in the retrievable chunk text
    sym_ok = sum(1 for s in gt["symbols"] if _token(s, chunk_text))
    corrupt = sum(_token(s, chunk_text) for s in gt["corrupt"])
    # chunks
    lens = [len(c["text"]) for c in chunks] or [0]
    # dotted sub-clause numbers only: figure keys ("1 developed view ...") look like top-level clauses
    gt_clause_re = re.compile(r"^(" + "|".join(re.escape(c) for c in gt["clauses"] if "." in c)
                              + r")\s{1,4}[A-Za-z]", re.M)
    crossing = sum(1 for c in chunks if len(set(gt_clause_re.findall(c["text"]))) > 1)
    from app.ingestion.quality import _squash, clause_preserved, is_traced

    page_text = {p.page_number: _squash(p.text) for p in pages}
    text_chunks = [c for c in chunks if c["content_type"] not in ("table", "formula")]
    traced = sum(1 for c in text_chunks if is_traced(c["text"], page_text.get(c["page"], "")))
    ocr = [p for p in pages if p.method == "ocr"]
    return {
        "page_coverage": round(len(text_pages) / max(1, page_count - len(blank)), 4),
        "clause_preservation": round(sum(clause_preserved(c, chunk_clauses) for c in gt["clauses"])
                                     / len(gt["clauses"]), 4),
        "heading_preservation": round(found_heads / len(gt["headings"]), 4),
        "table_caption_detection": round(captions_ok / max(1, sum(k.startswith("Table") for k in gt["tables"])), 4),
        "table_row_accuracy": round(table_rows_ok / max(1, table_rows), 4),
        "formula_detection": round(f_detect / len(gt["formulas"]), 4),
        "formula_token_f1": round(statistics.mean(f1s), 4),
        "formula_order_similarity": round(statistics.mean(sims), 4),
        "formula_exact": round(exact / len(gt["formulas"]), 4),
        "symbol_accuracy": round(sym_ok / len(gt["symbols"]), 4),
        "corrupted_symbol_occurrences": corrupt,
        "ocr_pages": len(ocr),
        "ocr_mean_confidence": round(statistics.mean(p.ocr_confidence for p in ocr), 4) if ocr else None,
        "chunks": len(chunks),
        "chunk_chars_mean": round(statistics.mean(lens)),
        "chunk_chars_max": max(lens),
        "tiny_chunks": sum(1 for x in lens if x < 80),
        "chunks_with_clause": round(sum(1 for c in chunks if c["clause"]) / max(1, len(chunks)), 4),
        "chunks_crossing_clauses": crossing,
        "traceability": round(traced / max(1, len(text_chunks)), 4),
    }


def retrieval_metrics(db, settings, scopes=None, queries=None) -> dict:
    """Recall@k / Precision@k / MRR / answer grounding over ``queries`` (default: the pilot set).
    ``scopes`` restricts the evaluated corpus with DAYANERA's own access scopes (default: owner)."""
    from app.services.access import ScopeSet
    from app.services.retrieval import plan_query, search

    scopes = scopes or ScopeSet(is_owner=True, user_id=uuid.UUID(int=0))
    per_query, k_prod = [], settings.retrieval_top_k
    for q in queries or QUERIES:
        plan = plan_query(q["q"])
        res = {"id": q["id"]}
        for k in (k_prod, 10):
            ps = search(db, scopes, plan, top_k=k, min_coverage=settings.retrieval_min_coverage, subtype=q["subtype"])
            units = q["relevant"]

            def rel(p, unit):
                std, page, rx = unit
                return (p.standard_code or "").startswith(std) and p.page_number == page and re.search(rx, p.text)

            hits = [any(rel(p, u) for u in units) for p in ps]
            covered = [u for u in units if any(rel(p, u) for p in ps)]
            res[f"recall@{k}"] = round(len(covered) / len(units), 4) if units else None
            res[f"precision@{k}"] = round(sum(hits) / k, 4) if units else None
            res[f"returned@{k}"] = len(ps)
            if k == k_prod:
                first = next((i for i, h in enumerate(hits, start=1) if h), None)
                res["mrr"] = round(1 / first, 4) if first else 0.0
                best = max((p.score for p in ps), default=0.0)
                res["passes_relevance_gate"] = best >= settings.retrieval_min_score
                # context the model would receive (chat: focused excerpts within the character budget)
                budget, total, ctx = settings.retrieval_max_context_chars, 0, []
                for p in ps:
                    if total >= budget:
                        break
                    p.focus(plan, size=min(900, max(400, budget - total)))
                    ctx.append(p.text)
                    total += len(p.text)
                context = "\n".join(ctx)
                res["answer_grounded"] = (bool(q["answer"]) and res["passes_relevance_gate"]
                                          and all(re.search(rx, context) for rx in q["answer"]))
                res["abstained"] = not res["passes_relevance_gate"]
        per_query.append(res)
    pos = [r for r in per_query if r["recall@10"] is not None]
    neg = [r for r in per_query if r["recall@10"] is None]
    agg = {f"mean_{m}": round(statistics.mean(r[m] for r in pos), 4)
           for m in (f"recall@{k_prod}", "recall@10", f"precision@{k_prod}", "precision@10", "mrr")}
    agg["answer_grounding_rate"] = round(sum(r["answer_grounded"] for r in pos) / len(pos), 4)
    agg["negatives_abstained"] = f"{sum(r['abstained'] for r in neg)}/{len(neg)}"
    return {"aggregate": agg, "queries": per_query}


# --------------------------------------------------------------------------- run
def run_variant(variant: str, docling_dir: Path, eval_root: Path) -> dict:
    from sqlalchemy import select

    from app.cli import cmd_migrate
    from app.core.config import get_settings
    from app.db.models import Document, DocumentChunk, DocumentVersion
    from app.db.session import init_engine, session_scope
    from app.ingestion.jobs import run_pending
    from app.ingestion.storage import sha256_file
    from app.ingestion.watcher import Watcher
    from app.services import corpus
    from app.services.auth import seed_initial_admin, seed_reference_data

    run_root = eval_root / "runs" / variant
    url = _prepare(variant, run_root)
    settings = get_settings()
    settings.ensure_runtime_dirs()
    cmd_migrate()
    init_engine(url)
    with session_scope() as db:
        seed_reference_data(db)
        seed_initial_admin(db, settings)
    shas = {}
    for key, gt in PILOT.items():
        src = next(p for p in (REPO_ROOT / "iso booklets").iterdir() if gt["file_key"] in p.name)
        dst = run_root / "iso" / src.name
        shutil.copyfile(fs(src), fs(dst))
        past = time.time() - 60
        os.utime(fs(dst), (past, past))
        shas[key] = sha256_file(dst)[0]
    if variant in ("hybrid", "docling"):
        _seed_docling_cache(settings, docling_dir, {"iso53": shas["ISO 53"], "iso21771": shas["ISO 21771"]})
    # reuse the OCR cache of the real data root (derived data, keyed by file hash)
    for sha in shas.values():
        real = REPO_ROOT / "data" / "indexes" / "ocr-cache" / sha
        if real.exists():
            shutil.copytree(fs(real), fs(settings.data_root / "indexes" / "ocr-cache" / sha), dirs_exist_ok=True)
    captured: dict = {}
    _install_parse_step(variant, captured)
    t0 = time.perf_counter()
    Watcher(settings).scan()
    run_pending(settings)
    seconds = round(time.perf_counter() - t0, 1)
    report = {"variant": variant, "ingest_seconds": seconds, "documents": {}}
    with session_scope() as db:
        reviewer = corpus.reviewer_by_username(db, "eval-admin")
        for key in PILOT:
            doc = db.execute(select(Document).where(Document.standard_code.like(f"{key}:%"))).scalars().first()
            ver = db.get(DocumentVersion, doc.current_version_id)
            gates = {g["id"]: g["status"] for g in ver.quality_report.get("gates", [])}
            status_before = ver.corpus_status
            if ver.corpus_status in corpus.APPROVABLE:
                corpus.approve(db, ver.id, reviewer, "parser evaluation run (throwaway database)")
            chunks = [{"text": c.text, "clause": c.clause, "content_type": c.content_type, "page": c.page_number}
                      for c in db.execute(select(DocumentChunk).where(DocumentChunk.version_id == ver.id)
                                          .order_by(DocumentChunk.chunk_index)).scalars()]
            out = captured[doc.original_filename]
            report["documents"][key] = {
                "standard_code": doc.standard_code, "ingestion_status": ver.ingestion_status,
                "gate_outcome": status_before, "gates_needing_attention": {k: v for k, v in gates.items() if v != "pass"},
                "parser": ver.parser, "parser_version": ver.parser_version,
                "metrics": parse_metrics(key, out, chunks)}
        db.commit()
        report["retrieval"] = retrieval_metrics(db, settings)
    return report


def to_markdown(results: list[dict]) -> str:
    keys = ["page_coverage", "clause_preservation", "heading_preservation", "table_caption_detection",
            "table_row_accuracy", "formula_detection", "formula_token_f1", "formula_order_similarity", "formula_exact",
            "symbol_accuracy", "corrupted_symbol_occurrences", "ocr_pages", "chunks", "chunk_chars_mean",
            "tiny_chunks", "chunks_with_clause", "chunks_crossing_clauses", "traceability"]
    lines = []
    for key in PILOT:
        lines += [f"### {key}", "", "| metric | " + " | ".join(r["variant"] for r in results) + " |",
                  "|---|" + "---|" * len(results)]
        for k in keys:
            lines.append(f"| {k} | " + " | ".join(str(r["documents"][key]["metrics"][k]) for r in results) + " |")
        lines.append("| gate outcome | " + " | ".join(r["documents"][key]["gate_outcome"] for r in results) + " |")
        lines.append("")
    agg = list(results[0]["retrieval"]["aggregate"])
    lines += ["### Retrieval (both documents, 21 positive + 2 negative queries)", "",
              "| metric | " + " | ".join(r["variant"] for r in results) + " |", "|---|" + "---|" * len(results)]
    for k in agg:
        lines.append(f"| {k} | " + " | ".join(str(r["retrieval"]["aggregate"][k]) for r in results) + " |")
    lines.append("| ingest seconds | " + " | ".join(str(r["ingest_seconds"]) for r in results) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--docling-dir", default=str(REPO_ROOT / "data" / "eval" / "docling"))
    args = ap.parse_args(argv)
    eval_root = REPO_ROOT / "data" / "eval"
    results = []
    for v in [x.strip() for x in args.variants.split(",") if x.strip()]:
        print(f"== {v}", flush=True)
        results.append(run_variant(v, Path(args.docling_dir), eval_root))
    (eval_root / "parser_comparison.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    md = to_markdown(results)
    (eval_root / "parser_comparison.md").write_text(md, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
