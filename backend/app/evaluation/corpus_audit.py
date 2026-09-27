"""Dry-run quality audit of the corpus folder (no database writes).

    python -m app.evaluation.corpus_audit [--folder "iso booklets"] [--parser pymupdf|hybrid|docling]

Runs the production extractor, the structure-aware chunker and the quality gates on
every PDF and prints one line per document: detected code, pages, OCR pages,
clauses, chunks, the gate outcome and the gates that need attention. The full-text
index gate is not evaluated here (it needs the database); everything else is the
same code path as ingestion. A metrics-only JSON goes to DATA_ROOT/eval/corpus_audit.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

from app.core.config import REPO_ROOT, get_settings
from app.core.paths import fs


def audit_file(path: Path, settings) -> dict:
    from app.ingestion import quality
    from app.ingestion.chunker import chunk_document
    from app.ingestion.detect import detect
    from app.ingestion.extractors.base import ExtractContext
    from app.ingestion.pipeline import extract_bytes
    from app.ingestion.standard_code import detect_standard_code, validate_code
    from app.ingestion.storage import Storage

    with open(fs(path), "rb") as f:
        data = f.read()
    sha = hashlib.sha256(data).hexdigest()
    storage = Storage(settings)
    det = detect(path.name, data[:8192])
    ctx = ExtractContext(settings=settings, storage=storage, version_id=uuid.uuid4(), sha256=sha,
                         filename=path.name, abs_path=str(path))
    out = extract_bytes(data, det, ctx)
    code = detect_standard_code("\n".join(p.text for p in out.pages[:3]), path.name)
    facts = [quality.ChunkFacts(page_number=c.page_number, text=c.text, content_type=c.content_type, clause=c.clause,
                                has_lineage=True, content_hash=hashlib.sha256(c.text.encode()).hexdigest(),
                                sources=c.sources, heading=c.heading)
             for _p, c in chunk_document(out.pages)]
    report = quality.evaluate(is_pdf=det.category == "pdf", verified_area=True, standard_code=code,
                              code_check=validate_code(code, path.name), validation=out.metadata.get("pdf_validation"),
                              page_count=out.metadata.get("page_count", len(out.pages)),
                              blank_pages=out.metadata.get("blank_pages", []), pages=out.pages, chunks=facts,
                              layout=out.metadata.get("layout"), parser=out.parser, parser_version=out.parser_version)
    methods: dict[str, int] = {}
    for p in out.pages:
        methods[p.method] = methods.get(p.method, 0) + 1
    return {"file": path.name, "sha256": sha, "size_bytes": len(data), "standard_code": code, "outcome": report["outcome"],
            "pages": out.metadata.get("page_count"), "methods": methods, "chunks": len(facts),
            "clauses": len({f.clause for f in facts if f.clause}),
            "attention": {g["id"]: {k: g[k] for k in ("status", "value", "detail") if g.get(k) not in (None, "")}
                          | ({"pages": g["pages"][:12]} if g.get("pages") else {})
                          for g in report["gates"] if g["status"] != "pass"},
            "code_check": validate_code(code, path.name)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=str(REPO_ROOT / "iso booklets"))
    ap.add_argument("--parser", default=None, help="override PDF_PARSER for this run")
    args = ap.parse_args(argv)
    if args.parser:
        os.environ["PDF_PARSER"] = args.parser
    settings = get_settings()
    # OCR results are cached below DATA_ROOT (derived data); nothing else is written
    rows = []
    folder = Path(args.folder)
    for name in sorted(os.listdir(fs(folder))):
        if name.lower().endswith(".pdf"):
            rows.append(audit_file(folder / name, settings))
    sys.stdout.reconfigure(encoding="utf-8")
    for r in rows:
        att = ", ".join(f"{k}{'(' + ','.join(map(str, v.get('pages', []))) + ')' if v.get('pages') else ''}"
                        for k, v in r["attention"].items()) or "-"
        print(f"{(r['standard_code'] or '?'):22s} {r['outcome']:12s} pages={r['pages']:<4} "
              f"ocr={r['methods'].get('ocr', 0):<4} clauses={r['clauses']:<4} chunks={r['chunks']:<4} {att}")
    out = REPO_ROOT / "data" / "eval" / "corpus_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
