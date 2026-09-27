"""Docling adapter: DoclingDocument JSON -> DAYANERA pages and blocks.

Docling runs out of process (``docling_runner.py`` in the ``DOCLING_PYTHON``
environment); this module only reads its JSON output, so the backend never
imports PyTorch or the layout models. Docling's layout labels are treated as
proposals and checked with the same deterministic ISO numbering rules as the
PyMuPDF layer (``iso_layout``): a "section_header" that is not a plausible next
clause becomes text. Formula LaTeX produced by the formula model is
model-generated content and is marked as such; it never counts as source text.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.ingestion.extractors.base import Block, normalize_text
from app.ingestion.parsing import iso_layout as L
from app.ingestion.parsing.symbols import rejoin_subscripts

log = logging.getLogger(__name__)

RUNNER = Path(__file__).with_name("docling_runner.py")
SKIP_LABELS = {"page_header", "page_footer", "picture", "chart", "document_index"}
_EQ_TAIL = re.compile(r"\(((?:[A-Z]\.)?\d{1,3}[a-z]?)\)\s*$")


class DoclingUnavailable(RuntimeError):
    """DOCLING_PYTHON is not configured or the runner failed."""


def run_docling(pdf_path: str, out_path: Path, python: str, *, formulas: bool, ocr: bool = False,
                timeout: int = 3600) -> dict[str, Any]:
    """Run the Docling runner in its own interpreter and return the parsed JSON."""
    if not python or not os.path.exists(python):
        raise DoclingUnavailable("DOCLING_PYTHON yapılandırılmamış veya bulunamadı")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".partial")
    cmd = [python, str(RUNNER), pdf_path, str(tmp)] + (["--formulas"] if formulas else []) + (["--ocr"] if ocr else [])
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_DISABLE_TELEMETRY": "1"}
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
                          timeout=timeout, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                          if sys.platform == "win32" else 0)
    if proc.returncode != 0 or not tmp.exists():
        raise DoclingUnavailable(f"Docling çalıştırılamadı (kod {proc.returncode}): {proc.stderr[-400:]}")
    os.replace(tmp, out_path)
    return load(out_path)


def load(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parser_version(payload: dict[str, Any]) -> str:
    v = payload.get("runner", {}).get("versions", {})
    opts = payload.get("runner", {}).get("options", {})
    parts = [f"docling {v.get('docling')}", f"docling-parse {v.get('docling-parse')}",
             f"docling-ibm-models {v.get('docling-ibm-models')}"]
    if opts.get("formula_enrichment"):
        parts.append("formula-enrichment")
    return "; ".join(parts)


# --------------------------------------------------------------------------- traversal
def _resolve(doc: dict, ref: str) -> dict:
    _, kind, idx = ref.split("/")
    return doc[kind][int(idx)]


def iter_items(doc: dict):
    """Body items in reading order (groups are flattened)."""
    def walk(node):
        for child in node.get("children", []):
            item = _resolve(doc, child["$ref"])
            if child["$ref"].startswith("#/groups/"):
                yield from walk(item)
            else:
                yield item
                # captions/footnotes of tables are emitted with the table itself
    yield from walk(doc["body"])


def _page(item: dict) -> int | None:
    prov = item.get("prov") or []
    return prov[0].get("page_no") if prov else None


def table_rows(table: dict, lexicon: set[str] | None = None) -> list[list[str]]:
    data = table.get("data", {})
    grid = [["" for _ in range(data.get("num_cols", 0))] for _ in range(data.get("num_rows", 0))]
    for c in data.get("table_cells", []):
        r, k = c["start_row_offset_idx"], c["start_col_offset_idx"]
        if r < len(grid) and k < len(grid[r]):
            text = " ".join(c.get("text", "").split())
            grid[r][k] = rejoin_subscripts(text, lexicon) if lexicon else text
    return grid


def _is_furniture_text(t: str) -> bool:
    return any(p.match(t) for p in L.FURNITURE)


# --------------------------------------------------------------------------- mapping
def to_pages(payload: dict[str, Any], *, lexicon: set[str] | None = None) -> dict[int, list[Block]]:
    """Map a DoclingDocument to {page_number: [Block, ...]} in reading order."""
    doc = payload["document"]
    enriched = bool(payload.get("runner", {}).get("options", {}).get("formula_enrichment"))
    state = L.LayoutState()
    pages: dict[int, list[Block]] = {int(k): [] for k in doc.get("pages", {})}
    caption_refs = {c["$ref"] for t in doc.get("tables", []) for c in t.get("captions", [])}
    pending_number: tuple[int, Block] | None = None  # "3.2" header waiting for its title header

    for item in iter_items(doc):
        label = item.get("label", "text")
        page = _page(item)
        if page is None or label in SKIP_LABELS or item.get("self_ref") in caption_refs:
            continue
        out = pages.setdefault(page, [])
        if label == "table":
            caps = [_resolve(doc, c["$ref"]).get("text", "") for c in item.get("captions", [])]
            caption = normalize_text(" ".join(caps)) or None
            m = L.TABLE_CAP.match(caption or "")
            rows = table_rows(item, lexicon)
            out.append(Block("table", L.render_table(caption, rows), label=f"Table {m.group(1)}" if m else None,
                             source="docling:table"))
            continue
        text = normalize_text(item.get("text", ""))
        if not text or _is_furniture_text(text):
            continue
        if lexicon:
            text = rejoin_subscripts(text, lexicon)
        if label == "formula":
            orig = item.get("orig", "")
            m = _EQ_TAIL.search(orig)
            model_made = enriched and text != normalize_text(orig)
            out.append(Block("formula", text + (f" ({m.group(1)})" if m and m.group(0) not in text else ""),
                             label=f"({m.group(1)})" if m else None,
                             source="docling:formula_model" if model_made else "docling:formula"))
            continue
        if label in ("section_header", "title"):
            block = _heading_block(text, state, pending_number, page, out)
            if block is None:
                pending_number = None  # merged into the pending number-only header: it now has its title
                continue
            if block.kind == "heading" and L.CLAUSE_NUM.match(text):
                pending_number = (page, block)
            else:
                pending_number = None
            out.append(block)
            continue
        pending_number = None
        if label == "caption":
            m = L.TABLE_CAP.match(text) or L.FIG_CAP.match(text)
            out.append(Block("caption", text, label=m.group(0).split()[0] + " " + m.group(1) if m else None,
                             source="docling"))
        elif label == "list_item":
            out.append(Block("list", text, source="docling"))
        elif label == "footnote":
            out.append(Block("note", text, source="docling"))
        else:
            m = L.CLAUSE_LINE.match(text)
            clause = None
            if m and "." in m.group(1) and L.plausible_next(state.clause_key, L.clause_key(m.group(1))):
                state.clause_key = L.clause_key(m.group(1))
                clause = m.group(1)
            kind = "note" if L.NOTE.match(text) else "text"
            out.append(Block(kind, text, clause=clause, source="docling"))
    return pages


def _heading_block(text: str, state: L.LayoutState, pending, page: int, out: list[Block]) -> Block | None:
    # a number-only header followed by its title header ("3.2" + "mating standard rack ...")
    if pending and pending[0] == page and not L.CLAUSE_NUM.match(text) and not L.CLAUSE_LINE.match(text):
        pending[1].text = f"{pending[1].text} {text}"
        return None
    m_annex = L.ANNEX.match(text)
    if m_annex:
        key = L.clause_key(m_annex.group(1))
        if L.plausible_next(state.clause_key, key) or state.clause_key == key:
            state.clause_key = key
            return Block("heading", text, clause=f"Annex {m_annex.group(1)}", level=1, source="docling")
    if text == "Scope" and not state.clause_key:
        state.clause_key = (1,)
        return Block("heading", "1 Scope", clause="1", level=1, source="docling")
    if text.lower().rstrip(".") in L.UNNUMBERED:
        return Block("heading", text, level=1, source="docling")
    m = L.CLAUSE_NUM.match(text) or L.CLAUSE_LINE.match(text)
    # the layout model labelled it a header AND it carries a clause number: accept any forward
    # step (one missed header must not cascade), unlike bare text lines (iso_layout.plausible_next)
    if m and L.clause_key(m.group(1)) > state.clause_key:
        state.clause_key = L.clause_key(m.group(1))
        return Block("heading", text, clause=m.group(1), level=m.group(1).count(".") + 1, source="docling")
    # Docling proposed a header that is not a plausible clause ("Figure 1.", "where"): keep it as text
    return Block("text", text, source="docling:demoted_header")
