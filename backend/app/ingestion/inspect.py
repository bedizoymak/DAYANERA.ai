"""Chunk inspection for ONE document (python -m app.cli inspect-document ...).

Two sources:
  --file PATH   extract + chunk the PDF in memory with the current code (no database, nothing written)
  --code CODE   read the chunks stored for the active version of that standard (read-only)

Output is a compact table (page range, heading path, key, parent, type, tokens, equations,
tables, review flags, text preview) or JSON lines (``--export``). Previews are truncated
(default 80 characters, ``--preview 0`` hides text) so copyrighted documents are not dumped
into logs; ``--full`` prints complete chunk text and should only be used locally.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from app.core.config import Settings


def _row(c: Any, preview: int, full: bool) -> dict:
    text = c["text"]
    shown = text if full else (text[:preview].replace("\n", " ⏎ ") + ("…" if len(text) > preview else "")) if preview else ""
    out = {k: c.get(k) for k in ("key", "chunk_index", "role", "parent", "content_type", "page_start", "page_end",
                                 "clause", "heading_path", "tokens", "equation_numbers", "table_numbers",
                                 "figure_numbers", "symbols", "units", "validation_status", "review_reasons")}
    out["context"] = c.get("context") if full else (c.get("context") or "").split("\n")[0][:160]
    out["formulas"] = [{k: f.get(k) for k in ("number", "status", "plain", "latex", "reasons")}
                       for f in c.get("formulas") or []]
    if c.get("table"):
        t = c["table"]
        out["table"] = {k: t.get(k) for k in ("number", "caption", "columns", "header_rows", "units", "row_range",
                                              "row_count", "continued_pages", "method")}
    out["text"] = shown
    return out


def from_file(path: str, settings: Settings) -> tuple[list[dict], dict]:
    from app.ingestion.chunker import chunk_pages
    from app.ingestion.detect import detect
    from app.ingestion.extractors.base import ExtractContext
    from app.ingestion.pipeline import chunking_summary, extract_bytes
    from app.ingestion.standard_code import detect_standard_code
    from app.ingestion.storage import Storage, sha256_file

    p = Path(path)
    data = p.read_bytes()
    sha, _ = sha256_file(p)
    det = detect(p.name, data[:8192])
    ctx = ExtractContext(settings=settings, storage=Storage(settings), version_id=uuid.uuid4(), sha256=sha,
                         filename=p.name, abs_path=str(p))
    result = extract_bytes(data, det, ctx)
    code = detect_standard_code("\n".join(pg.text for pg in result.pages[:3]), p.name)
    chunks, model = chunk_pages(result.pages, standard_code=code, title=p.stem)
    rows = []
    for i, c in enumerate(chunks):
        d = asdict(c)
        d.update(chunk_index=i, parent=c.parent_key, tokens=c.tokens)
        rows.append(d)
    meta = {"file": p.name, "sha256": sha, "standard_code": code, "parser": result.parser,
            "parser_version": result.parser_version, "pages": len(result.pages),
            "layout": {k: v for k, v in (result.metadata.get("layout") or {}).items() if k != "repeated_furniture"},
            "repeated_furniture": (result.metadata.get("layout") or {}).get("repeated_furniture"),
            "summary": chunking_summary(chunks, model), "glossary_symbols": len(model.glossary)}
    return rows, meta


def from_db(code: str) -> tuple[list[dict], dict]:
    from sqlalchemy import text

    from app.db.session import session_scope

    with session_scope() as db:
        ver = db.execute(text("""
            SELECT v.id, d.title, d.standard_code, v.corpus_status, v.parser_version, v.page_count,
                   v.metadata -> 'chunking' AS chunking
            FROM documents d JOIN document_versions v ON v.id = d.current_version_id
            WHERE d.status = 'active' AND d.standard_code ILIKE :c
            ORDER BY d.updated_at DESC LIMIT 1"""), {"c": f"%{code}%"}).first()
        if ver is None:
            raise SystemExit(f"'{code}' için etkin belge bulunamadı")
        rows = db.execute(text("""
            SELECT c.id, c.chunk_index, c.chunk_role AS role, c.parent_id, c.content_type, c.page_start, c.page_end,
                   c.clause, c.heading_path, c.token_count AS tokens, c.equation_numbers, c.table_numbers,
                   c.figure_numbers, c.symbols, c.units, c.validation_status, c.meta, c.context, c.formula,
                   c.table_data, c.text
            FROM document_chunks c WHERE c.version_id = :v ORDER BY c.chunk_index"""), {"v": ver.id}).mappings().all()
        idx = {r["id"]: r["chunk_index"] for r in rows}
        out = []
        for r in rows:
            d = dict(r)
            d["key"] = (r["meta"] or {}).get("key") or f"#{r['chunk_index']}"
            d["parent"] = f"#{idx.get(r['parent_id'])}" if r["parent_id"] else None
            d["formulas"] = r["formula"] or []
            d["table"] = r["table_data"]
            d["review_reasons"] = (r["meta"] or {}).get("review_reasons")
            out.append(d)
        meta = {"document": ver.title, "standard_code": ver.standard_code, "version_id": str(ver.id),
                "corpus_status": ver.corpus_status, "parser_version": ver.parser_version, "pages": ver.page_count,
                "summary": ver.chunking}
    return out, meta


def render(rows: Iterable[dict], meta: dict, *, preview: int = 80, full: bool = False, kind: str | None = None,
           page: int | None = None, clause: str | None = None) -> str:
    lines = [json.dumps(meta, ensure_ascii=False, indent=1, default=str), ""]
    for c in rows:
        if kind and c.get("content_type") != kind:
            continue
        if page and not (c.get("page_start") <= page <= c.get("page_end")):
            continue
        if clause and not (c.get("clause") or "").startswith(clause):
            continue
        r = _row(c, preview, full)
        path = " › ".join(r["heading_path"] or [])
        pages = f"s.{r['page_start']}" + (f"-{r['page_end']}" if r["page_end"] != r["page_start"] else "")
        flags = " ⚠" + ";".join(r["review_reasons"] or ["review"]) if r["validation_status"] != "ok" else ""
        lines.append(f"{r['key']:>7} {r['role'] or 'leaf':6} parent={r['parent'] or '-':7} {r['content_type']:21} "
                     f"{pages:9} tok={r['tokens'] or 0:<4} {path[:90]}{flags}")
        if r["equation_numbers"] or r["table_numbers"] or r["figure_numbers"]:
            lines.append(f"{'':16}eq={r['equation_numbers']} tables={r['table_numbers']} figures={r['figure_numbers']}"
                         f" units={r['units']}")
        for f in r["formulas"]:
            lines.append(f"{'':16}({f['number']}) [{f['status']}] {f['plain'] if full or preview else ''}")
            if f.get("latex") and (full or preview):
                lines.append(f"{'':20}LaTeX: {f['latex']}")
        if r["text"]:
            lines.append(f"{'':16}{r['text']}")
    return "\n".join(lines)


def export(rows: Iterable[dict], meta: dict, out: str, *, preview: int = 80, full: bool = False) -> int:
    n = 0
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"meta": meta}, ensure_ascii=False, default=str) + "\n")
        for c in rows:
            fh.write(json.dumps(_row(c, preview, full), ensure_ascii=False, default=str) + "\n")
            n += 1
    return n
