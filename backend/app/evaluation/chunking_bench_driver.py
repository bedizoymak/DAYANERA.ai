"""Benchmark driver: ingest a document set with ONE code tree and dump chunks + retrieval results.

Run by ``chunking_benchmark.py`` as a subprocess with ``PYTHONPATH`` pointing at the backend of
the tree under test (the current tree, or a ``git worktree`` of the baseline commit), so the
same driver measures the old and the new chunker through their own real pipeline:
watcher -> extraction -> chunking -> PostgreSQL full-text index -> owner approval ->
``retrieval.search`` -> the context the chat would hand to the model.

It only uses APIs that exist in both trees and writes into a throwaway database
(``dayanera_eval_chunk_<variant>``) and a throwaway data root; the real database, the
corpus folder and the OCR cache are never modified (the OCR cache is copied in).
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path


def _jsonable(v):
    if isinstance(v, uuid.UUID):
        return str(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True)
    ap.add_argument("--run-root", required=True)
    ap.add_argument("--docs", required=True, help="comma separated file names inside --source")
    ap.add_argument("--source", required=True)
    ap.add_argument("--ocr-cache", required=True)
    ap.add_argument("--queries", required=True, help="JSON file with the query set")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import psycopg

    run_root = Path(args.run_root)
    base = os.environ["DATABASE_URL"]
    head, _ = base.rsplit("/", 1)
    db_name = f"dayanera_eval_chunk_{args.variant}"
    with psycopg.connect(f"{head}/postgres".replace("postgresql+psycopg://", "postgresql://"), autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{db_name}"')
    if run_root.exists():
        shutil.rmtree(run_root)
    iso = run_root / "iso"
    iso.mkdir(parents=True)
    data = run_root / "data"
    from hashlib import sha256

    for name in args.docs.split(","):
        src = Path(args.source) / name
        shutil.copy2(src, iso / name)  # keeps the modification time: the watcher skips files still being written
        sha = sha256(src.read_bytes()).hexdigest()
        cache = Path(args.ocr_cache) / sha
        if cache.exists():  # OCR results of scanned pages (read-only copy)
            shutil.copytree(cache, data / "indexes" / "ocr-cache" / sha)
    os.environ.update({
        "DATABASE_URL": f"{head}/{db_name}", "ISO_BOOKLETS_PATH": str(iso), "DATA_ROOT": str(data),
        "AGENT_NOTES_PATH": str(run_root / "notes"), "EXTRA_WATCH_ROOTS": "", "WATCHER_ENABLED": "false",
        "DISABLE_BACKGROUND_WORKERS": "true", "SUPABASE_ENABLED": "false", "INITIAL_ADMIN_USERNAME": "eval-admin",
        "INITIAL_ADMIN_PASSWORD": uuid.uuid4().hex, "PDF_PARSER": "pymupdf",
    })
    from app.core.config import get_settings, reset_settings_cache

    reset_settings_cache()
    settings = get_settings()
    settings.ensure_runtime_dirs()
    from app.cli import cmd_migrate
    from app.db.session import init_engine, session_scope
    from app.ingestion.jobs import run_pending
    from app.ingestion.watcher import Watcher
    from app.services import corpus
    from app.services.access import ScopeSet
    from app.services.auth import seed_initial_admin, seed_reference_data

    cmd_migrate()
    init_engine(settings.database_url)
    with session_scope() as db:
        seed_reference_data(db)
        seed_initial_admin(db, settings)
    t0 = time.time()
    time.sleep(2.5)  # the watcher only registers files whose size/mtime are stable
    summary = Watcher(settings).scan()
    print(f"{args.variant}: scan {({k: v for k, v in summary.items() if k != 'roots'})}", flush=True)
    run_pending(settings)
    ingest_seconds = round(time.time() - t0, 1)

    from sqlalchemy import text

    out: dict = {"variant": args.variant, "ingest_seconds": ingest_seconds, "documents": [], "chunks": [], "pages": []}
    with session_scope() as db:
        reviewer = corpus.reviewer_by_username(db, "eval-admin")
        vers = db.execute(text("""SELECT v.id, d.standard_code, v.corpus_status, v.ingestion_status,
                                         v.quality_report -> 'outcome' AS outcome, v.metadata -> 'chunking' AS chunking
                                  FROM documents d JOIN document_versions v ON v.id = d.current_version_id""")).all()
        for v in vers:
            out["documents"].append({"standard_code": v.standard_code, "corpus_status": v.corpus_status,
                                     "ingestion_status": v.ingestion_status, "outcome": v.outcome,
                                     "chunking": v.chunking})
            if v.ingestion_status == "indexed" and v.corpus_status in ("extracted", "needs_review"):
                corpus.approve(db, v.id, reviewer, "chunking benchmark (throwaway database)")
        cols = [r[0] for r in db.execute(text("""SELECT column_name FROM information_schema.columns
                                                 WHERE table_name = 'document_chunks' AND column_name <> 'tsv'"""))]
        rows = db.execute(text(f"""SELECT {', '.join('c.' + c for c in cols)}, d.standard_code AS doc_code
                                   FROM document_chunks c JOIN documents d ON d.id = c.document_id
                                   ORDER BY d.standard_code, c.chunk_index""")).mappings().all()
        out["chunks"] = [{k: _jsonable(v) for k, v in r.items()} for r in rows]
        pages = db.execute(text("""SELECT d.standard_code AS doc_code, p.page_number, p.text, p.extraction_method
                                   FROM document_pages p JOIN documents d ON d.id = p.document_id""")).mappings().all()
        out["pages"] = [dict(r) for r in pages]

    from app.services import retrieval as R

    queries = json.loads(Path(args.queries).read_text(encoding="utf-8"))
    scopes = ScopeSet(is_owner=True, user_id=uuid.UUID(int=0))
    has_question = "question" in inspect.signature(R.search).parameters
    results = []
    with session_scope() as db:
        for q in queries:
            plan = R.plan_query(q["q"])
            kw = {"question": q["q"]} if has_question else {}
            res = {"id": q["id"]}
            for k in (settings.retrieval_top_k, 10):
                ps = R.search(db, scopes, plan, top_k=k, min_coverage=settings.retrieval_min_coverage,
                              subtype=q.get("subtype"), **kw)
                res[f"top{k}"] = [{"chunk_id": str(p.chunk_id), "standard_code": p.standard_code,
                                   "page": p.page_number, "page_end": getattr(p, "page_end", None) or p.page_number,
                                   "content_type": p.content_type, "clause": p.clause, "score": p.score,
                                   "heading": p.heading, "heading_path": getattr(p, "heading_path", None) or [],
                                   "text": p.text, "boosts": p.boosts} for p in ps]
                if k == settings.retrieval_top_k:
                    best = max((p.score for p in ps), default=0.0)
                    res["passes_gate"] = best >= settings.retrieval_min_score
                    budget = settings.retrieval_max_context_chars
                    if hasattr(R, "build_context"):  # engineering chunks: parent-context expansion
                        used = R.build_context(db, ps, plan, budget) if res["passes_gate"] else []
                        ctx = [getattr(p, "prompt_text", "") or p.text for p in used]
                    else:  # the chat's focus loop before this change
                        total, ctx = 0, []
                        for p in ps:
                            if total >= budget:
                                break
                            p.focus(plan, size=min(900, max(400, budget - total)))
                            ctx.append(p.text)
                            total += len(p.text)
                    res["context"] = ctx if res["passes_gate"] else []
            results.append(res)
    out["queries"] = results
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, default=_jsonable), encoding="utf-8")
    print(f"{args.variant}: {len(out['chunks'])} chunks, {len(results)} queries, ingest {ingest_seconds}s -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
