"""Hierarchical document model: pages of blocks -> sections of engineering units.

The layout layer (``parsing/iso_layout.py``) delivers blocks per page. Engineering
meaning lives above the page, so this module rebuilds the document tree first:

    DOCUMENT
      > SECTION (clause / subclause / annex, with its heading path)
          > UNIT (paragraph | formula (+ lead-in, + legend) | table | figure | note | example | list | ...)

Rules, all deterministic:

* a heading opens a section at its clause depth ("4.3.10" -> level 3); its parent is
  the nearest open section of lower level, which gives the heading path
  ("4 Individual cylindrical gears > 4.3 Involute helicoids > 4.3.10 Base cylinder ...");
* page breaks are NOT unit boundaries: a paragraph that ends without closing
  punctuation continues into the first paragraph of the next page; a table
  "(continued)" (or an uncaptioned grid of the same width at the top of the next
  page) is appended to the table it continues; every unit keeps the list of its pages;
* a "where" legend belongs to the formula directly before it; the paragraph just
  before a formula provides its lead-in sentence;
* symbol tables ("Symbol | Description | Unit") and legends build a per-document
  symbol glossary used to describe the variables of every formula;
* pages without layout blocks (OCR, rebuilt text layers, office text) get a
  text-only structure pass (numbered headings, notes, captions, equation numbers),
  so scanned documents are also chunked by clause instead of by page window.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.ingestion.extractors.base import Block, PageOut
from app.ingestion.parsing.iso_layout import (
    CLAUSE_LINE,
    EQ_NUM,
    EXAMPLE,
    FIG_CAP,
    NOTE,
    TABLE_CAP,
    TABLE_CONT,
    clause_key,
    parse_variable,
    plausible_next,
)

FRONT_MATTER_TITLES = {"foreword", "contents", "introduction"}
_END_OF_SENTENCE = re.compile(r"[.:;!?)\]]\s*$")


@dataclass
class Unit:
    kind: str  # paragraph | formula | legend | table | figure | figure_key | note | example | list | caption | toc
    text: str
    pages: list[int]
    source: str = "pymupdf"
    label: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    method: str = "native_text"  # extraction method of the page(s)
    lead_in: str | None = None  # formula: sentence(s) that introduce it
    legend: str | None = None  # formula: the verbatim "where ..." block
    variables: list[dict] = field(default_factory=list)  # formula/legend: [{symbol, description, unit}]
    see_also: str | None = None  # formula: "(See Figure 10.)"

    @property
    def page_start(self) -> int:
        return min(self.pages)

    @property
    def page_end(self) -> int:
        return max(self.pages)


@dataclass
class Section:
    key: str
    clause: str | None
    heading: str | None  # full heading line: "4.3.10 Base cylinder, base circle, base diameter"
    level: int
    parent: "Section | None" = None
    children: list["Section"] = field(default_factory=list)
    units: list[Unit] = field(default_factory=list)
    page: int = 1
    front_matter: bool = False

    @property
    def path(self) -> list[str]:
        out, s = [], self
        while s is not None:
            if s.heading:
                out.append(s.heading)
            s = s.parent
        return list(reversed(out))

    @property
    def top_title(self) -> str:
        """Title of the level-1 ancestor without its number ("Terms and definitions")."""
        s = self
        while s.parent is not None and s.parent.level >= 1:
            s = s.parent
        return re.sub(r"^(?:\d+|Annex [A-Z])\s*(?:\((?:in)?formative\))?\s*", "", s.heading or "").strip()

    @property
    def title(self) -> str:
        return re.sub(r"^(?:\d{1,2}(?:\.\d{1,2})*|[A-Z](?:\.\d{1,2})+|Annex [A-Z])\s+", "", self.heading or "").strip()


@dataclass
class DocModel:
    root: Section
    sections: list[Section]  # document order (root first)
    glossary: dict[str, dict]
    stats: dict[str, int]


# --------------------------------------------------------------------------- text-only structure (OCR pages)
def text_blocks(text: str, state: dict) -> list[Block]:
    """Blocks for a page without layout information: numbered headings (plausible ISO numbering),
    notes/examples, captions and equation-number lines; everything else is paragraph text."""
    blocks: list[Block] = []
    para: list[str] = []
    kind = "text"

    def flush() -> None:
        nonlocal para, kind
        if para:
            blocks.append(Block(kind, "\n".join(para), source="text"))
        para, kind = [], "text"

    for raw in text.splitlines():
        t = raw.strip()
        if not t:
            flush()
            continue
        m = CLAUSE_LINE.match(t)
        if m and len(t) < 90 and not t.endswith(".") and not re.search(r"\.{4,}", t):
            key = clause_key(m.group(1)) if not m.group(1)[0].isalpha() or "." in m.group(1) else None
            if key and plausible_next(state.get("clause", ()), key, strong=len(t) < 70):
                flush()
                state["clause"] = key
                blocks.append(Block("heading", t, clause=m.group(1), level=m.group(1).count(".") + 1, source="text"))
                continue
        if FIG_CAP.match(t):
            flush()
            blocks.append(Block("figure", t, label=f"Figure {FIG_CAP.match(t).group(1)}", source="text",
                                data={"number": FIG_CAP.match(t).group(1), "caption": t, "key": []}))
            continue
        if TABLE_CAP.match(t) or TABLE_CONT.match(t):
            flush()
            mm = TABLE_CAP.match(t) or TABLE_CONT.match(t)
            blocks.append(Block("caption", t, label=f"Table {mm.group(1)}", source="text"))
            continue
        eq = re.search(r"\(((?:[A-Z]\.)?\d{1,3}[a-z]?)\)\s*$", t)
        if eq and "=" in t and len(t) < 160:
            flush()
            blocks.append(Block("formula", t, label=f"({eq.group(1)})", source="text",
                                data={"number": eq.group(1), "raw": t, "plain": t, "latex": None,
                                      "status": "needs_review", "confidence": 0.3, "reasons": ["text_layer_only"],
                                      "structures": [], "symbols": [], "lhs": None, "source": "text"}))
            continue
        if NOTE.match(t) or EXAMPLE.match(t):
            flush()
            kind = "note" if NOTE.match(t) else "example"
        para.append(t)
    flush()
    return blocks


# --------------------------------------------------------------------------- model
def _is_symbol_table(data: dict) -> bool:
    cols = " ".join(c.lower() for c in data.get("columns") or [])
    return "symbol" in cols and any(w in cols for w in ("description", "meaning", "designation", "term", "name"))


def _glossary_from_table(data: dict, clause: str | None) -> dict[str, dict]:
    cols = [c.lower() for c in data.get("columns") or []]
    out: dict[str, dict] = {}
    try:
        si = next(i for i, c in enumerate(cols) if "symbol" in c)
        di = next(i for i, c in enumerate(cols) if any(w in c for w in ("description", "meaning", "designation",
                                                                          "term", "name")))
    except StopIteration:
        return out
    ui = next((i for i, c in enumerate(cols) if c.startswith("unit")), None)
    ci = next((i for i, c in enumerate(cols) if "used in" in c or "clause" in c or c.startswith("see")), None)
    for row in (data.get("rows") or [])[data.get("header_rows", 0):]:
        if len(row) <= max(si, di):
            continue
        sym, desc = row[si].strip(), row[di].strip()
        if not sym or not desc or len(sym) > 16:
            continue
        out.setdefault(sym, {"description": desc, "unit": row[ui].strip() if ui is not None and ui < len(row) else None,
                             "clause": row[ci].strip() if ci is not None and ci < len(row) else None,
                             "source": f"symbol table ({clause or 'document'})"})
    return out


def build_model(pages: list[PageOut]) -> DocModel:
    root = Section("s0000", None, None, 0, front_matter=True)
    sections = [root]
    stack = [root]
    body_started = False
    glossary: dict[str, dict] = {}
    stats = {"cross_page_merges": 0, "continued_tables": 0, "legends_linked": 0, "text_structure_pages": 0}
    text_state: dict = {}
    last_table: dict[str, Unit] = {}

    def open_section(clause: str | None, heading: str, level: int, page: int) -> Section:
        nonlocal body_started
        while len(stack) > 1 and stack[-1].level >= level:
            stack.pop()
        title = re.sub(r"^(?:\d{1,2}(?:\.\d{1,2})*|[A-Z](?:\.\d{1,2})+|Annex [A-Z])\s+", "", heading).strip().lower()
        if clause and clause not in ("Bibliography",):
            body_started = True
        front = (not body_started) or (level == 1 and title.rstrip(".") in FRONT_MATTER_TITLES) or \
            (stack[-1].front_matter and stack[-1] is not root)
        sec = Section(f"s{len(sections):04d}", clause, heading, level, parent=stack[-1], page=page, front_matter=front)
        stack[-1].children.append(sec)
        stack.append(sec)
        sections.append(sec)
        return sec

    for p in pages:
        blocks = p.blocks
        if not blocks and p.text.strip():
            blocks = text_blocks(p.text, text_state)
            stats["text_structure_pages"] += 1
        for b in blocks:
            cur = stack[-1]
            if b.kind == "heading":
                open_section(b.clause, b.text.split("\n")[0].strip(), max(1, b.level or 1), p.page_number)
                if "\n" in b.text:  # multi-line heading block: title continuation lines are part of the heading
                    stack[-1].heading = " ".join(x.strip() for x in b.text.split("\n"))
                continue
            if b.clause and b.kind == "text" and "." in b.clause:  # "5.1 The characteristics ..." (no title)
                cur = open_section(b.clause, b.clause, b.clause.count(".") + 1, p.page_number)
            _add(cur, b, p, stats, last_table, glossary)
    _link_lead_ins(sections)
    # symbol tables are read after continuation pages were appended (ISO 21771 3.1 spans six pages);
    # table entries take precedence over legend entries
    for sec in sections:
        for u in sec.units:
            if u.kind == "table" and _is_symbol_table(u.data):
                for sym, info in _glossary_from_table(u.data, sec.clause).items():
                    if sym not in glossary or glossary[sym].get("source", "").startswith("legend"):
                        glossary[sym] = info
    stats["glossary_symbols"] = len(glossary)
    return DocModel(root, sections, glossary, stats)


def _unit_kind(b: Block) -> str:
    return {"text": "paragraph", "list": "list", "note": "note", "example": "example", "formula": "formula",
            "legend": "legend", "table": "table", "figure": "figure", "figure_key": "figure_key",
            "caption": "caption"}.get(b.kind, "paragraph")


def _add(sec: Section, b: Block, p: PageOut, stats: dict, last_table: dict[str, Unit], glossary: dict) -> None:
    kind = _unit_kind(b)
    prev = sec.units[-1] if sec.units else None
    source = b.source or "pymupdf"
    if kind == "paragraph" and re.search(r"\.{5,}\s*\S{0,6}\s*$", b.text, re.M):
        kind = "toc"  # contents entries: the dot leaders carry no information (and cost ~0.6 token per dot)
        b = Block(b.kind, re.sub(r"\s*\.{4,}\s*", " … ", b.text), b.clause, b.level, b.label, b.confidence,
                  b.source, b.data, b.bbox)
    # --- cross-page continuation of running text
    if kind in ("paragraph", "list") and prev is not None and prev.kind == kind and prev.page_end < p.page_number \
            and not _END_OF_SENTENCE.search(prev.text) and (b.text[:1].islower() or b.text[:1] in "(,;–—-"):
        prev.text = f"{prev.text}\n{b.text}"
        prev.pages.append(p.page_number)
        stats["cross_page_merges"] += 1
        return
    # --- a legend belongs to the run of formulas right before it ("(19)", "(20)", then "where ...")
    if kind == "legend" and prev is not None and prev.kind == "formula" and not prev.legend:
        variables = list((b.data or {}).get("variables", []))
        run = []
        for u in reversed(sec.units):
            if u.kind != "formula" or u.legend:
                break
            run.append(u)
        for u in run:
            u.legend = b.text
            u.variables = list(variables)
            if p.page_number not in u.pages:
                u.pages.append(p.page_number)
        stats["legends_linked"] += 1
        for v in variables:
            glossary.setdefault(v["symbol"], {**v, "clause": sec.clause, "source": f"legend ({sec.clause})"})
        return
    # --- "(See Figure 10.)" directly after a formula is part of it
    if kind == "paragraph" and prev is not None and prev.kind == "formula" and not prev.see_also \
            and re.fullmatch(r"\(See [^)]{1,60}\)\.?", b.text.strip()):
        prev.see_also = b.text.strip()
        return
    # --- tables continued on the next page
    if kind == "table":
        data = dict(b.data or {})
        number = data.get("number")
        target = last_table.get(number) if number and data.get("continued") else None
        if target is None and not number and prev is not None and prev.kind == "table" \
                and prev.page_end == p.page_number - 1 and len((prev.data.get("rows") or [[]])[-1]) == \
                len((data.get("rows") or [[]])[0]):
            target = prev  # uncaptioned grid at the top of the next page, same width
        if target is not None:
            _append_rows(target, data, p.page_number)
            stats["continued_tables"] += 1
            return
        unit = Unit("table", b.text, [p.page_number], source, b.label, data, p.method)
        sec.units.append(unit)
        if number:
            last_table[number] = unit
        return
    if kind == "figure_key" and prev is not None and prev.kind == "figure" and not prev.data.get("key"):
        prev.data["key"] = b.text.split("\n")[1:]
        prev.text += "\n" + b.text
        return
    if kind == "legend":
        for v in (b.data or {}).get("variables", []):
            glossary.setdefault(v["symbol"], {**v, "clause": sec.clause, "source": f"legend ({sec.clause})"})
    unit = Unit(kind, b.text, [p.page_number], source, b.label, dict(b.data or {}), p.method)
    if kind == "legend":
        unit.variables = list(unit.data.get("variables", []))
    sec.units.append(unit)


def _append_rows(target: Unit, data: dict, page: int) -> None:
    """Rows of a continuation grid; its repeated header rows are dropped."""
    header = data.get("header_rows", 0)
    rows = (data.get("rows") or [])[header:]
    target.data.setdefault("rows", []).extend(rows)
    target.data.setdefault("continued_pages", []).append(page)
    if page not in target.pages:
        target.pages.append(page)
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows if any(c.strip() for c in r))
    target.text = f"{target.text}\n{body}" if body else target.text


_LEAD_IN_END = re.compile(r"(?:\b(?:by|is|as|are|hence|thus|then|follows|formula|equation|expressed|determined|"
                          r"given|calculated|found|obtained|written|defined)\s*[:,]?|:)\s*$", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    flat = re.sub(r"\s*\n\s*", " ", text).strip()
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", flat) if s]


def _link_lead_ins(sections: list[Section]) -> None:
    """The sentence(s) that introduce a formula: the end of the paragraph directly before it."""
    for sec in sections:
        for i, u in enumerate(sec.units):
            if u.kind != "formula" or i == 0:
                continue
            prev = sec.units[i - 1]
            if prev.kind not in ("paragraph", "list"):
                continue
            sents = _sentences(prev.text)
            if not sents:
                continue
            lead = sents[-1]
            # "It is expressed by" / "This gives" refer to the sentence before: take both
            if len(sents) > 1 and (len(lead) < 40 or re.match(r"(?:It|This|These|They|Hence|Thus)\b", lead)):
                lead = " ".join(sents[-2:])
            if _LEAD_IN_END.search(lead) or len(prev.text) < 400:
                u.lead_in = lead if len(lead) < 600 else lead[-600:]
