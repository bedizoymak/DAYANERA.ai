"""ISO document structure from the PDF text layer (PyMuPDF), without models.

ISO standards follow ISO/IEC Directives Part 2: numbered clauses ("4.2.4"),
annexes ("Annex A", "A.1"), captions ("Table 2 —", "Figure 1 —") and numbered
equations ("(1)" at the right margin). These rules are deterministic, so the
structure is recovered from line text, font weight and geometry:

* clause numbers must advance plausibly (figure keys "1 Datum line" and table of
  contents entries are therefore never taken for clauses);
* a clause number and its title on the same baseline are merged;
* table rows are rebuilt from line geometry inside the detected table box;
* equation regions are anchored on the right-margin equation number.
Formula text rebuilt from positioned glyphs is linearised, never interpreted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

from app.ingestion.extractors.base import Block, normalize_hyphens
from app.ingestion.parsing.symbols import map_symbol_span

_UNICODE_SPACES = re.compile(r"[\u00a0\u2000-\u200b\u202f\u205f\u3000\ufeff]")
CLAUSE_NUM = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,5}|[A-Z](?:\.\d{1,2}){1,5})$")
CLAUSE_LINE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,5}|[A-Z](?:\.\d{1,2}){1,5})\s+(\S.*)$")
ANNEX = re.compile(r"^Annex\s+([A-Z])\b\s*(.*)$")
ANNEX_TYPE = re.compile(r"^\((?:informative|normative)\)$")
TOC_LEADER = re.compile(r"\.{5,}\s*\S{0,6}\s*$")
TABLE_CAP = re.compile(r"^Table\s+((?:[A-Z]\.)?\d{1,3})\s*[—–-]\s*\S")
FIG_CAP = re.compile(r"^Figure\s+((?:[A-Z]\.)?\d{1,3})\s*[—–-]\s*\S")
EQ_NUM = re.compile(r"^\(((?:[A-Z]\.)?\d{1,3}[a-z]?)\)$")
NOTE = re.compile(r"^NOTE(?:\s+\d+)?\b")
LIST_ITEM = re.compile(r"^(?:[—–•\-]\s|[a-z]\)\s)")
UNNUMBERED = {"foreword", "introduction", "contents", "bibliography", "index"}
FURNITURE = [
    re.compile(r"^(?:ISO|ISO/TR|ISO/TS)\s?\d{2,5}(?:-\d+)?:\d{4}\(E\)$"),
    re.compile(r"^©\s*ISO(?:\s+\d{4})?(?:\s*[–-]\s*All rights reserved)?$", re.IGNORECASE),
    re.compile(r"^(?:INTERNATIONAL STANDARD|TECHNICAL REPORT)(?:\s+©\s*ISO)?$"),
    re.compile(r"^[ivxlc]{1,6}$|^\d{1,3}$", re.IGNORECASE),
    re.compile(r"^--`[`,\-]*---$"),
    re.compile(r"^www\.bzfxw\.com$"),
    re.compile(r"^(Copyright International Organization for Standardization|Provided by IHS under license with ISO"
               r"|No reproduction or networking permitted without license from IHS|Not for Resale"
               r"|Licensee=.*|.*Not for Resale.*)$"),
]


@dataclass
class Line:
    text: str
    bbox: tuple[float, float, float, float]
    bold: bool = False
    horizontal: bool = True
    used: bool = False

    @property
    def yc(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


@dataclass
class LayoutState:
    """Carried across pages: the last accepted clause number."""

    clause_key: tuple = ()
    in_key: bool = False
    stats: dict = field(default_factory=lambda: {"symbol_glyphs_mapped": 0, "rotated_lines": 0,
                                                 "unmapped_symbol_fonts": set()})


def _clean(s: str) -> str:
    # soft hyphens (U+00AD) stay: a line-final one joins the next line only after the lines of a
    # page or paragraph are joined (normalize_hyphens), exactly as the page text layer does
    s = s.replace("‐", "-").replace("‑", "-").replace("‒", "-")
    return _UNICODE_SPACES.sub(" ", s).strip()


def page_lines(page: "pymupdf.Page", state: LayoutState) -> list[Line]:
    """Text lines with Symbol-encoded Greek recovered (reading order of PyMuPDF)."""
    out: list[Line] = []
    d = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    for b in d.get("blocks", []):
        for ln in b.get("lines", []):
            parts, bold_chars, chars = [], 0, 0
            for sp in ln.get("spans", []):
                text, n = map_symbol_span(sp.get("font", ""), sp.get("text", ""))
                if n:
                    state.stats["symbol_glyphs_mapped"] += n
                    state.stats["unmapped_symbol_fonts"].add(sp.get("font", ""))
                parts.append(text)
                visible = len(text.strip())
                chars += visible
                if sp.get("flags", 0) & 16 or "bold" in sp.get("font", "").lower():
                    bold_chars += visible
            text = _clean("".join(parts))
            if not text:
                continue
            dx, dy = ln.get("dir", (1, 0))
            horizontal = abs(dy) < 0.1 and dx > 0
            if not horizontal:
                state.stats["rotated_lines"] += 1
            out.append(Line(text, tuple(ln["bbox"]), bold=chars > 0 and bold_chars / chars > 0.6,
                            horizontal=horizontal))
    return out


def page_text(lines: list[Line]) -> str:
    return "\n".join(ln.text for ln in lines)


# --------------------------------------------------------------------------- clauses
def clause_key(num: str) -> tuple:
    parts = num.split(".")
    if parts[0].isalpha():
        return (100 + ord(parts[0]) - ord("A"),) + tuple(int(p) for p in parts[1:])
    return tuple(int(p) for p in parts)


def plausible_next(cur: tuple, cand: tuple, strong: bool = False) -> bool:
    """ISO numbering advances by one step (a skipped number is tolerated). ``strong`` evidence (a bold
    numbered heading with a title) may skip further, so one missed heading does not cascade."""
    if not cand or cand <= cur:
        return False
    step = 5 if strong else 2
    if cand[0] >= 100:  # annexes follow the body or the previous annex
        prev = cur[0] if cur and cur[0] >= 100 else 99
        if cand[0] not in (prev, prev + 1, prev + 2):
            return False
        cur = cur if cur and cur[0] == cand[0] else (cand[0],)
        if cand == cur:
            return True
    elif not cur:
        return (cand[0] in (0, 1) or strong and cand[0] <= step) and all(p <= 2 for p in cand[1:])
    for i, (a, b) in enumerate(zip(cur, cand)):
        if a != b:
            return 0 < b - a <= step and all(p <= 2 for p in cand[i + 1:])
    return len(cand) == len(cur) + 1 and 1 <= cand[-1] <= (step if strong else 2)  # first sub-clause


def _is_furniture(ln: Line, height: float) -> bool:
    t = ln.text.strip()
    if any(p.match(t) for p in FURNITURE[4:]):
        return True
    in_margin = ln.bbox[3] < 0.085 * height or ln.bbox[1] > 0.915 * height
    return in_margin and len(t) < 70 and any(p.match(t) for p in FURNITURE)


def _is_prose(t: str) -> bool:
    return len(t) >= 25 and len(re.findall(r"[a-z]{3,}", t)) >= 4


_STRUCTURAL_WORDS = {"Key", "where", "NOTE"}


def _is_structural(ln: Line) -> bool:
    """Headings, captions and legend markers are never glyphs of a neighbouring equation."""
    t = ln.text
    return (t in _STRUCTURAL_WORDS or bool(TABLE_CAP.match(t) or FIG_CAP.match(t) or ANNEX.match(t) or NOTE.match(t))
            or bool(CLAUSE_LINE.match(t) and ln.bold) or bool(CLAUSE_NUM.match(t) and ln.bold))


def _merge_baseline_numbers(lines: list[Line]) -> None:
    """'4.2.4' and its title set as separate text lines on one baseline -> one line."""
    for ln in lines:
        if ln.used or not CLAUSE_NUM.match(ln.text) and not ANNEX.match(ln.text):
            continue
        for other in lines:
            if other is ln or other.used or not other.horizontal:
                continue
            if abs(other.yc - ln.yc) <= 2.5 and 0 < other.bbox[0] - ln.bbox[2] < 120:
                ln.text = f"{ln.text} {other.text}"
                ln.bold = ln.bold or other.bold
                ln.bbox = (ln.bbox[0], min(ln.bbox[1], other.bbox[1]), other.bbox[2], max(ln.bbox[3], other.bbox[3]))
                other.used = True
                break


# --------------------------------------------------------------------------- tables
def _table_boxes(page: "pymupdf.Page") -> list[tuple[float, float, float, float]]:
    try:
        return [tuple(t.bbox) for t in page.find_tables().tables]
    except Exception:  # pragma: no cover - table finder is best-effort
        return []


def _inside(ln: Line, box) -> bool:
    x = (ln.bbox[0] + ln.bbox[2]) / 2
    return box[0] - 2 <= x <= box[2] + 2 and box[1] - 2 <= ln.yc <= box[3] + 2


def rows_from_lines(lines: list[Line]) -> list[list[str]]:
    """Group table lines into rows (vertical overlap), cells ordered left to right."""
    rows: list[list[Line]] = []
    for ln in sorted(lines, key=lambda l_: (l_.yc, l_.bbox[0])):
        if rows:
            top = min(x.bbox[1] for x in rows[-1])
            bottom = max(x.bbox[3] for x in rows[-1])
            if ln.yc < bottom - 1 and ln.yc > top:
                rows[-1].append(ln)
                continue
        rows.append([ln])
    return [[c.text for c in sorted(r, key=lambda l_: l_.bbox[0])] for r in rows]


def render_table(caption: str | None, rows: list[list[str]]) -> str:
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows if any(c.strip() for c in r))
    return f"{caption}\n{body}" if caption else body


def linearize(fragments: list[Line]) -> str:
    """Positioned formula glyph runs in left-to-right order. A run that directly touches the previous
    one and is set smaller (a subscript: "m" + "n") is joined without a space ("mn"). Only the PDF's
    own characters are used; fractions are NOT reconstructed (the order stays approximate)."""
    out, prev = "", None
    for ln in sorted(fragments, key=lambda l_: (round(l_.bbox[0] / 4), l_.yc)):
        if prev is not None:
            h_prev, h = prev.bbox[3] - prev.bbox[1], ln.bbox[3] - ln.bbox[1]
            touching = -1.0 <= ln.bbox[0] - prev.bbox[2] <= 1.5
            out += "" if touching and h < 0.85 * h_prev else " "
        out += ln.text
        prev = ln
    return out


# --------------------------------------------------------------------------- blocks
def detect_blocks(page: "pymupdf.Page", lines: list[Line], state: LayoutState) -> list[Block]:
    height, width = page.rect.height, page.rect.width
    # visual reading order (ISO pages are single-column): some processed PDFs store "3 Terms ..." before
    # "1 Scope" in the content stream. The stored page text keeps the stream order.
    lines = sorted(lines, key=lambda l_: (not l_.horizontal, round(l_.yc / 3), l_.bbox[0]))
    # rotated text is content (landscape tables set on portrait pages) unless it is a known watermark
    rotated = [ln for ln in lines if not ln.horizontal and not _is_furniture(ln, height)]
    for ln in lines:
        if not ln.horizontal or _is_furniture(ln, height):
            ln.used = True
    _merge_baseline_numbers(lines)
    live = [ln for ln in lines if not ln.used]
    toc_page = sum(1 for ln in live if TOC_LEADER.search(ln.text)) >= 3  # contents pages never hold headings

    # tables: text lines inside a detected table box (rotated watermark glyphs are already dropped).
    # Fraction bars look like ruling lines to the table finder: a box level with a right-margin
    # equation number, or an uncaptioned box whose cells hold "=", is a formula, not a table.
    eq_lines = [ln for ln in live if EQ_NUM.match(ln.text) and ln.bbox[0] > 0.6 * width]
    table_at: dict[int, Block] = {}
    for box in _table_boxes(page):
        members = [ln for ln in live if not ln.used and _inside(ln, box)]
        if len(members) < 2 or any(box[1] - 6 <= eq.yc <= box[3] + 6 for eq in eq_lines):
            continue
        captioned = any(TABLE_CAP.match(ln.text) and ln.bbox[3] <= box[1] + 4 for ln in live)
        if not captioned and any(ln.text.strip() in ("=", "+", "−", "-") for ln in members):
            continue
        cap_line = None
        above = [ln for ln in live if not ln.used and TABLE_CAP.match(ln.text) and ln.bbox[3] <= box[1] + 4]
        if above:
            cap_line = max(above, key=lambda l_: l_.bbox[3])
            cap_line.used = True
        for ln in members:
            ln.used = True
        caption = cap_line.text if cap_line else None
        label = f"Table {TABLE_CAP.match(caption).group(1)}" if caption else None
        first = min(members + ([cap_line] if cap_line else []), key=lambda l_: lines.index(l_))
        table_at[lines.index(first)] = Block("table", render_table(caption, rows_from_lines(members)),
                                             label=label, source="pymupdf:table")

    # equations: fragments left of a right-margin equation number, within its vertical band
    formula_at: dict[int, Block] = {}
    for eq in [ln for ln in live if not ln.used and EQ_NUM.match(ln.text) and ln.bbox[0] > 0.6 * width]:
        band = [ln for ln in live if not ln.used and ln is not eq and ln.bbox[2] <= eq.bbox[0] + 2
                and abs(ln.yc - eq.yc) <= 28 and not _is_prose(ln.text) and not _is_structural(ln)]
        eq.used = True
        for ln in band:
            ln.used = True
        first = min(band + [eq], key=lambda l_: lines.index(l_))
        formula_at[lines.index(first)] = Block(
            "formula", f"{linearize(band)} ({EQ_NUM.match(eq.text).group(1)})".strip(), label=eq.text,
            source="pymupdf:formula")

    blocks: list[Block] = []
    para: list[str] = []
    para_clause: str | None = None
    para_kind = "text"

    def flush() -> None:
        nonlocal para, para_clause, para_kind
        if para:
            blocks.append(Block(para_kind, normalize_hyphens("\n".join(para)), clause=para_clause, source="pymupdf"))
        para, para_clause, para_kind = [], None, "text"

    for idx, ln in enumerate(lines):
        if idx in table_at:
            flush()
            blocks.append(table_at[idx])
        if idx in formula_at:
            flush()
            blocks.append(formula_at[idx])
        if ln.used:
            continue
        t = ln.text
        if TOC_LEADER.search(t):  # table of contents entry: text, never a clause
            para.append(t)
            continue
        if t == "Key":
            state.in_key = True
        if FIG_CAP.match(t):
            state.in_key = False
            flush()
            blocks.append(Block("caption", t, label=f"Figure {FIG_CAP.match(t).group(1)}", source="pymupdf"))
            continue
        if TABLE_CAP.match(t):
            flush()
            blocks.append(Block("caption", t, label=f"Table {TABLE_CAP.match(t).group(1)}", source="pymupdf"))
            continue
        heading = None if toc_page and t.lower().rstrip(".") != "contents" else _heading(t, ln, lines, idx, state)
        if heading is not None:
            flush()
            blocks.append(heading)
            continue
        m = CLAUSE_LINE.match(t)
        if m and not state.in_key and "." in m.group(1) and plausible_next(state.clause_key, clause_key(m.group(1))):
            flush()  # numbered sub-clause with running text: "5.1 The characteristics ..."
            state.clause_key = clause_key(m.group(1))
            para, para_clause = [t], m.group(1)
            continue
        if NOTE.match(t) or LIST_ITEM.match(t):
            flush()
            para_kind = "note" if NOTE.match(t) else "list"
        para.append(t)
    flush()
    if rotated:
        blocks.append(Block("text", normalize_hyphens("\n".join(ln.text for ln in rotated)), source="pymupdf:rotated"))
    return blocks


def _take(title_lines: list[Line]) -> list[str]:
    for ln in title_lines:
        ln.used = True
    return [ln.text for ln in title_lines]


def _heading(t: str, ln: Line, lines: list[Line], idx: int, state: LayoutState) -> Block | None:
    m_annex = ANNEX.match(t)
    if m_annex and (ln.bold or len(t) < 60) and state.clause_key:  # annexes follow the body
        key = clause_key(m_annex.group(1))
        if plausible_next(state.clause_key, key) or state.clause_key == key:
            state.clause_key, state.in_key = key, False
            parts = [f"Annex {m_annex.group(1)}", m_annex.group(2).strip()]
            nxt = _next_title_lines(lines, idx, limit=3)
            if nxt and ANNEX_TYPE.match(nxt[0].text):  # "(informative)" precedes the bold annex title
                nxt = nxt[:1] + [x for x in _next_title_lines(lines, lines.index(nxt[0]), limit=2) if x.bold]
            text = " ".join(x for x in parts + _take(nxt) if x)
            return Block("heading", text, clause=f"Annex {m_annex.group(1)}", level=1, source="pymupdf")
    if ln.bold and t == "Scope" and not state.clause_key:
        # clause 1 always opens an ISO document; some text layers omit its number
        state.clause_key = (1,)
        return Block("heading", "1 Scope", clause="1", level=1, source="pymupdf")
    if ln.bold and t.lower().rstrip(".") in UNNUMBERED:
        return Block("heading", t, clause=t if t.lower() == "bibliography" else None, level=1, source="pymupdf")
    if state.in_key:
        return None
    m_num = CLAUSE_NUM.match(t)
    m_line = CLAUSE_LINE.match(t)
    num = m_num.group(1) if m_num else (m_line.group(1) if m_line else None)
    # a bold numbered title line (titles never end with a full stop, running text does)
    strong = bool(ln.bold and m_line and not m_line.group(2).rstrip().endswith("."))
    if num is None or not plausible_next(state.clause_key, clause_key(num), strong=strong):
        return None
    top_level = "." not in num
    if m_line:
        title = m_line.group(2).strip()
        # a heading is a short bold line; running text after a sub-clause number is handled by the caller
        if TOC_LEADER.search(title) or not (ln.bold or (top_level and len(title) < 70 and not title.endswith("."))):
            return None
        if top_level and not ln.bold and _is_prose(title) and title.endswith("."):
            return None
    else:
        nxt = _next_title_lines(lines, idx, limit=1)
        if top_level and not ln.bold and not nxt:
            return None
        if nxt and TOC_LEADER.search(nxt[0].text):
            return None
        title = " ".join(_take(nxt))
    state.clause_key = clause_key(num)
    return Block("heading", f"{num} {title}".strip(), clause=num, level=num.count(".") + 1, source="pymupdf")


def _next_title_lines(lines: list[Line], idx: int, limit: int = 2) -> list[Line]:
    """Bold (or short title-like) lines directly following a heading number (not consumed)."""
    out: list[Line] = []
    for ln in lines[idx + 1:]:
        if ln.used:
            continue
        t = ln.text
        if len(out) >= limit or CLAUSE_NUM.match(t) or CLAUSE_LINE.match(t) or _is_prose(t) and not ln.bold:
            break
        if ln.bold or (len(t) < 80 and not t.endswith(".") and not EQ_NUM.match(t)):
            if not ln.bold and t[:1].islower() and not out:
                # a definition term ("standard basic rack tooth profile") is a title only when bold
                break
            out.append(ln)
            if not ln.bold:
                break
        else:
            break
    return out
