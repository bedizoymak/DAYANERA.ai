"""Engineering chunker: hierarchical document model -> retrieval chunks with parent/child lineage.

Segmentation follows the engineering structure, token size is only a constraint:

1. The document model (``structure.build_model``) gives sections (clauses) and units
   (paragraphs, formula + lead-in + legend, tables, figures, notes, examples).
2. A small section (<= ``leaf_section_max`` tokens) becomes ONE ``leaf`` chunk: heading,
   explanation, equations and their variable definitions stay a single knowledge unit
   ("4.3.10 Base cylinder ... The base diameter, db, is given by db = d cos αt (19)").
3. A larger section becomes ``parent`` chunk(s) (context windows over its units, at most
   ``parent_max`` tokens each) and ``child`` chunks (the retrieval units):
     * every numbered equation is its own FORMULA child: lead-in sentence + normalised
       formula + number + "where" legend + "(See Figure n.)";
     * a table is a TABLE child, or a table parent with TABLE_ROW_GROUP children that
       each repeat the caption, unit note and header rows;
     * consecutive paragraphs/notes/lists are packed up to ``soft_target`` tokens and
       never across a formula, table or figure; an oversized paragraph is split at
       sentence boundaries (never inside a sentence).
   Children point to their parent (``parent_key``), parents list their children.
4. Overlap is structural, not a repeated character window: every chunk carries a
   ``context`` line (document code > heading path) and, for formulas, the glossary
   descriptions of its symbols. ``text`` stays the source wording (plus the derived
   formula/table lines, recorded separately so provenance checks can tell them apart).
5. Chunks may span pages (page_start..page_end); a clause never spans chunks of another
   clause, and a page break alone never ends a chunk.

``chunk_page`` (sentence-aware page windows) remains for callers that only have page text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.ingestion.extractors.base import PageOut
from app.ingestion.structure import DocModel, Section, Unit, build_model
from app.ingestion.tokens import estimate_tokens

CHUNKER_VERSION = "engineering-chunker/1"

# content types (document_chunks.content_type)
DEFINITION = "definition"
NORMATIVE = "normative_requirement"
FORMULA = "formula"
FORMULA_CONTEXT = "formula_context"
VARIABLE_DEFINITION = "variable_definition"
TABLE = "table"
TABLE_ROW_GROUP = "table_row_group"
FIGURE_CAPTION = "figure_caption"
PROCEDURE = "procedure"
EXAMPLE = "example"
NOTE = "note"
REFERENCE = "reference"
GENERAL = "general_text"
FRONT_MATTER = "front_matter"
SECTION = "section"  # parent context windows
CONTENT_TYPES = (DEFINITION, NORMATIVE, FORMULA, FORMULA_CONTEXT, VARIABLE_DEFINITION, TABLE, TABLE_ROW_GROUP,
                 FIGURE_CAPTION, PROCEDURE, EXAMPLE, NOTE, REFERENCE, GENERAL, FRONT_MATTER, SECTION)
# a leaf that holds several kinds of units is labelled by the most specific one
_PRIORITY = [FORMULA, TABLE, DEFINITION, NORMATIVE, PROCEDURE, VARIABLE_DEFINITION, EXAMPLE, FIGURE_CAPTION,
             TABLE_ROW_GROUP, NOTE, FORMULA_CONTEXT, REFERENCE, FRONT_MATTER, GENERAL]


@dataclass(frozen=True)
class ChunkingConfig:
    """Token limits (Qwen tokens, see ``tokens.py``). Chosen experimentally, see
    docs/ENGINEERING_DOCUMENT_CHUNKING_GUIDE.md "Chunk size decisions"."""

    soft_target: int = 160  # packing target of narrative child chunks
    hard_max: int = 350  # no retrieval chunk exceeds this (except an indivisible unit, flagged)
    min_useful: int = 25  # a narrative piece below this is merged into its neighbour of the same section
    leaf_section_max: int = 280  # a whole section up to this size is one knowledge unit
    parent_max: int = 700  # context window of a parent chunk
    table_leaf_max: int = 280  # a table up to this size is one chunk
    table_group_target: int = 200  # rows per TABLE_ROW_GROUP child
    lead_in_max: int = 90  # tokens of the introducing sentence copied into a formula chunk


DEFAULT_CONFIG = ChunkingConfig()


def active_config() -> ChunkingConfig:
    """The configuration in force. ``DAYANERA_CHUNKING_CONFIG`` (JSON of field overrides) exists ONLY for the
    size experiments of ``app.evaluation.chunking_benchmark sweep``; production uses DEFAULT_CONFIG."""
    import json
    import os

    raw = os.environ.get("DAYANERA_CHUNKING_CONFIG")
    if not raw:
        return DEFAULT_CONFIG
    return ChunkingConfig(**{**DEFAULT_CONFIG.__dict__, **json.loads(raw)})


@dataclass
class Chunk:
    key: str
    text: str
    content_type: str
    role: str  # leaf | parent | child
    page_start: int
    page_end: int
    locator: str
    clause: str | None = None
    heading: str | None = None
    heading_path: list[str] = field(default_factory=list)
    parent_key: str | None = None
    child_keys: list[str] = field(default_factory=list)
    context: str = ""
    equation_numbers: list[str] = field(default_factory=list)
    table_numbers: list[str] = field(default_factory=list)
    figure_numbers: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    units: list[str] = field(default_factory=list)
    formulas: list[dict[str, Any]] = field(default_factory=list)
    table: dict[str, Any] | None = None
    tokens: int = 0
    sources: list[str] = field(default_factory=list)
    method: str = "native_text"
    validation_status: str = "ok"  # ok | needs_review
    review_reasons: list[str] = field(default_factory=list)
    derived_lines: list[str] = field(default_factory=list)  # lines not copied verbatim from the page (formula/table)
    char_start: int = 0
    char_end: int = 0
    section_key: str | None = None

    # backwards-compatible accessors used by older callers
    @property
    def page_number(self) -> int:
        return self.page_start

    @property
    def verbatim_text(self) -> str:
        derived = set(self.derived_lines)
        return "\n".join(ln for ln in self.text.split("\n") if ln not in derived)


# --------------------------------------------------------------------------- helpers
def _locator(p0: int, p1: int) -> str:
    return f"s. {p0}" if p0 == p1 else f"s. {p0}–{p1}"


def _pages(units: list[Unit]) -> tuple[int, int]:
    return min(u.page_start for u in units), max(u.page_end for u in units)


_SENT = re.compile(r"(?<=[.!?;:])\s+(?=[A-Z(\"“—–-]|\d+\s)")


def split_sentences(text: str) -> list[str]:
    """Sentence pieces with their line breaks kept (joined back they reproduce the text)."""
    parts, start = [], 0
    for m in _SENT.finditer(text):
        parts.append(text[start:m.start()])
        start = m.end()
    parts.append(text[start:])
    return [p for p in parts if p.strip()]


def _pack(pieces: list[str], limit: int) -> list[str]:
    out, cur = [], []
    for p in pieces:
        if cur and estimate_tokens(" ".join(cur + [p])) > limit:
            out.append(" ".join(cur))
            cur = []
        cur.append(p)
    if cur:
        out.append(" ".join(cur))
    return out


def split_oversize(text: str, cfg: ChunkingConfig) -> list[str]:
    """Semantic split of an oversized narrative unit: sentences first, then lines, then words (last resort)."""
    pieces: list[str] = []
    for sent in split_sentences(text):
        if estimate_tokens(sent) <= cfg.soft_target:
            pieces.append(sent)
            continue
        for line in sent.split("\n"):
            if estimate_tokens(line) <= cfg.soft_target:
                pieces.append(line)
            else:
                pieces += _pack(line.split(" "), cfg.soft_target)
    return [p.strip() for p in _pack_lines(pieces, cfg.soft_target) if p.strip()]


def _pack_lines(pieces: list[str], limit: int) -> list[str]:
    out, cur = [], []
    for p in pieces:
        if cur and estimate_tokens("\n".join(cur + [p])) > limit:
            out.append("\n".join(cur))
            cur = []
        cur.append(p)
    if cur:
        out.append("\n".join(cur))
    return out


_SHALL = re.compile(r"\b(?:shall|shall not|is required|are required|must)\b", re.IGNORECASE)
_PROCEDURE_TITLE = re.compile(r"procedure|method|measur|inspection|test|evaluation|calculation of|determination",
                              re.IGNORECASE)
_STEP = re.compile(r"^(?:[a-z]\)|\d{1,2}\)|Step\s+\d+)", re.MULTILINE)


def classify(unit: Unit, sec: Section, section_has_formula: bool) -> str:
    top = sec.top_title.lower()
    if sec.front_matter:
        return FRONT_MATTER
    if unit.kind == "formula":
        return FORMULA
    if unit.kind == "legend":
        return VARIABLE_DEFINITION
    if unit.kind == "table":
        return TABLE
    if unit.kind in ("figure", "figure_key"):
        return FIGURE_CAPTION
    if unit.kind == "example":
        return EXAMPLE
    if unit.kind == "note":
        return NOTE
    if unit.kind == "toc":
        return FRONT_MATTER
    if re.search(r"normative references|bibliography", top) or re.search(r"normative references|bibliography",
                                                                          (sec.heading or "").lower()):
        return REFERENCE
    if re.search(r"terms and definitions|symbols|abbreviat|definitions", top):
        return DEFINITION
    if _SHALL.search(unit.text):
        return NORMATIVE
    if _PROCEDURE_TITLE.search(sec.title) and (unit.kind == "list" or len(_STEP.findall(unit.text)) >= 2):
        return PROCEDURE
    if section_has_formula:
        return FORMULA_CONTEXT
    return GENERAL


def _dominant(types: list[str]) -> str:
    return min(types, key=lambda t: _PRIORITY.index(t) if t in _PRIORITY else len(_PRIORITY))


_DEFINING = [FORMULA, TABLE, DEFINITION, NORMATIVE, PROCEDURE, VARIABLE_DEFINITION]


def leaf_kind(pieces: list["Piece"]) -> str:
    """Type of a whole-section leaf: a defining kind when present (formula, table, definition, normative,
    procedure), otherwise the kind that holds most of the text (one NOTE does not make a clause a note)."""
    kinds = [p.kind for p in pieces]
    defining = [k for k in kinds if k in _DEFINING]
    if defining:
        return _dominant(defining)
    weight: dict[str, int] = {}
    for p in pieces:
        weight[p.kind] = weight.get(p.kind, 0) + p.tokens
    return max(weight, key=lambda k: (weight[k], -(_PRIORITY.index(k) if k in _PRIORITY else 99)))


# --------------------------------------------------------------------------- unit rendering
@dataclass
class Piece:
    """One rendered unit (or part of one) with its metadata, before it is placed into chunks."""

    text: str
    kind: str  # content type
    units: list[Unit]
    derived: list[str] = field(default_factory=list)
    formulas: list[dict] = field(default_factory=list)
    table: dict | None = None
    atomic: bool = False  # formula/table/figure: never merged with narrative neighbours
    lead: str | None = None  # formula: the copied lead-in sentence (dropped where the paragraph itself precedes)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)

    @property
    def core(self) -> str:
        """Text without the copied lead-in (used when the introducing paragraph is in the same chunk)."""
        if self.lead and self.text.startswith(self.lead):
            return self.text[len(self.lead):].lstrip("\n")
        return self.text


def _formula_piece(u: Unit, cfg: ChunkingConfig, glossary: dict) -> Piece:
    f = dict(u.data or {})
    number = f.get("number")
    line = u.text  # "db = d cos αt ... (19)" (normalised plain form, or the glyph line when needs_review)
    lead = u.lead_in or ""
    if lead and estimate_tokens(lead) > cfg.lead_in_max:
        lead = "… " + " ".join(split_sentences(lead)[-1:])
    parts = [x for x in (lead, line, u.legend, u.see_also) if x]
    variables = list(u.variables)
    known = {v["symbol"] for v in variables}
    for sym in f.get("symbols") or []:  # glossary descriptions of symbols the legend does not define
        g = glossary.get(sym)
        if g and sym not in known:
            variables.append({"symbol": sym, "description": g.get("description"), "unit": g.get("unit"),
                              "source": g.get("source")})
            known.add(sym)
    f["variables"] = variables
    f["lead_in"] = u.lead_in
    f["legend"] = u.legend
    f["page"] = u.page_start
    return Piece("\n".join(parts), FORMULA, [u], derived=[line], formulas=[f], atomic=True, lead=lead or None)


def _table_rows_text(data: dict, rows: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(r) + " |" for r in rows if any(c.strip() for c in r)]


def _table_pieces(u: Unit, cfg: ChunkingConfig) -> tuple[Piece, list[Piece]]:
    """A table as one piece, or a table overview piece plus TABLE_ROW_GROUP pieces (header repeated)."""
    data = dict(u.data or {})
    rows = data.get("rows") or []
    header_n = data.get("header_rows", 0)
    head_lines = [x for x in (data.get("caption"), data.get("unit_note")) if x]
    columns = [c for c in data.get("columns") or []]
    header_rows = _table_rows_text(data, rows[:header_n])
    if header_n >= 2 and columns and len(columns) == len(rows[0]):
        # several header rows (merged cells, mostly empty) -> ONE row of combined column names when that is
        # cheaper: same alignment and every column says what it is ("Nominal size mm / Above"). A long header
        # cell spanning many columns would be repeated per column, so the original rows are kept then.
        compact = ["| " + " | ".join(columns) + " |"]
        if estimate_tokens(compact[0]) < estimate_tokens("\n".join(header_rows)):
            header_rows = compact
    sep = ["|" + " --- |" * len(rows[0])] if header_rows and rows else []
    body = rows[header_n:]
    foot = list(data.get("footnotes") or [])
    table_meta = {k: data.get(k) for k in ("number", "caption", "continued", "header_rows", "columns", "units",
                                           "unit_note", "footnotes", "method", "continued_pages",
                                           "alignment_uncertain")}
    table_meta["row_count"] = len(body)
    table_meta["rows"] = rows
    if not rows:  # unstructured (caption + flattened text)
        return Piece(u.text, TABLE, [u], derived=[], table=table_meta, atomic=True), []
    derived = header_rows + sep + _table_rows_text(data, body)
    full = "\n".join(head_lines + derived + foot)
    whole = Piece(full, TABLE, [u], derived=derived, table=table_meta, atomic=True)
    if whole.tokens <= cfg.table_leaf_max:
        return whole, []
    groups: list[Piece] = []
    prefix = head_lines + header_rows + sep
    cur: list[list[str]] = []
    first = 0
    for i, r in enumerate(body):
        trial = "\n".join(prefix + _table_rows_text(data, cur + [r]))
        if cur and estimate_tokens(trial) > cfg.table_group_target:
            groups.append((first, cur))
            cur, first = [], i
        cur.append(r)
    if cur:
        groups.append((first, cur))
    pieces = []
    for first, grp in groups:
        rows_txt = _table_rows_text(data, grp)
        meta = dict(table_meta, rows=rows[:header_n] + grp, row_range=[first + 1, first + len(grp)])
        pieces.append(Piece("\n".join(prefix + rows_txt), TABLE_ROW_GROUP, [u], derived=header_rows + sep + rows_txt,
                            table=meta, atomic=True))
    overview = "\n".join(head_lines + header_rows + sep
                         + [f"({len(body)} rows in {len(pieces)} row groups" +
                            (f"; first column {body[0][0]} … {body[-1][0]}" if body and body[0] else "") + ")"]
                         + foot)
    over = Piece(overview, TABLE, [u], derived=header_rows + sep + [overview.split("\n")[-1 - len(foot)]],
                 table=dict(table_meta, rows=rows[:header_n]), atomic=True)
    return over, pieces


_INLINE_EQ = re.compile(r"(?<![\w|=])([A-Za-zα-ωΑ-Ω][A-Za-z0-9α-ω]{0,5})\s?=\s?([^=;\n]{1,40}?)(?=[.;,]\s|[.;,]?$|\n)")


def inline_equations(text: str, glossary: dict) -> list[dict]:
    """Equations written inside running text ("... has a pitch p = π m."): recorded when the left-hand side
    is a symbol of the document's own symbol list, so the quantity they define is known."""
    out = []
    for m in _INLINE_EQ.finditer(text):
        lhs, rhs = m.group(1), m.group(2).strip()
        g = glossary.get(lhs)
        if g is None or not rhs or re.fullmatch(r"[\d\s,.]+", rhs):
            continue  # unknown symbol, or a value assignment of an example
        out.append({"kind": "inline", "number": None, "plain": f"{lhs} = {rhs}", "latex": None, "status": "inline",
                    "lhs": lhs, "symbols": [lhs],
                    "variables": [{"symbol": lhs, "description": g.get("description"), "unit": g.get("unit"),
                                   "source": g.get("source")}]})
    return out[:6]


def _narrative_pieces(units: list[Unit], sec: Section, has_formula: bool, cfg: ChunkingConfig) -> list[Piece]:
    """Pack consecutive narrative units up to the soft target; split an oversized one at sentences."""
    out: list[Piece] = []
    cur: list[Unit] = []

    limit = cfg.hard_max - estimate_tokens(sec.heading or "") - 2  # room for the heading line of a leaf

    def emit(group: list[Unit]) -> None:
        text = "\n".join(u.text for u in group)
        kind = _dominant([classify(u, sec, has_formula) for u in group])
        inline = [e for u in group for e in (u.data or {}).get("inline_equations", [])]
        if estimate_tokens(text) <= limit:
            out.append(Piece(text, kind, group, formulas=inline))
            return
        for part in split_oversize(text, cfg):
            out.append(Piece(part, kind, group, formulas=[e for e in inline if e["plain"].split(" = ")[0] in part]))

    # outside a real clause (document root / cover pages) nothing says two pages belong together:
    # there, a page break ends a group (a sentence that runs on was already joined into one unit)
    by_page = sec.level == 0
    for u in units:
        if cur and (estimate_tokens("\n".join(x.text for x in cur + [u])) > cfg.soft_target
                    or by_page and u.page_start != cur[-1].page_end):
            emit(cur)
            cur = []
        cur.append(u)
    if cur:
        emit(cur)
    # a fragment below the minimum useful size joins its neighbour in the same section
    merged: list[Piece] = []
    for p in out:
        if merged and (p.tokens < cfg.min_useful or merged[-1].tokens < cfg.min_useful) \
                and not (by_page and p.units[0].page_start != merged[-1].units[-1].page_end) \
                and estimate_tokens(merged[-1].text + "\n" + p.text) <= cfg.hard_max:
            prev = merged[-1]
            merged[-1] = Piece(prev.text + "\n" + p.text, _dominant([prev.kind, p.kind]), prev.units + p.units,
                               formulas=prev.formulas + p.formulas)
            continue
        merged.append(p)
    return merged


def _section_pieces(sec: Section, cfg: ChunkingConfig, glossary: dict) -> list[Piece]:
    has_formula = any(u.kind == "formula" for u in sec.units)
    pieces: list[Piece] = []
    narrative: list[Unit] = []

    def flush() -> None:
        nonlocal narrative
        if narrative:
            pieces.extend(_narrative_pieces(narrative, sec, has_formula, cfg))
        narrative = []

    for u in sec.units:
        if u.kind == "formula":
            flush()
            pieces.append(_formula_piece(u, cfg, glossary))
            continue
        if u.kind in ("paragraph", "list", "note", "example"):
            u.data["inline_equations"] = inline_equations(u.text, glossary)
        if u.kind == "table":
            flush()
            over, groups = _table_pieces(u, cfg)
            pieces.append(over)
            pieces.extend(groups)
        elif u.kind in ("figure", "figure_key"):
            flush()
            pieces.append(Piece(u.text, FIGURE_CAPTION, [u], atomic=True))
        elif u.kind == "legend":
            flush()
            pieces.append(Piece(u.text, VARIABLE_DEFINITION, [u], atomic=True))
        else:
            narrative.append(u)
    flush()
    return pieces


# --------------------------------------------------------------------------- chunk assembly
@dataclass
class _Builder:
    doc_label: str
    cfg: ChunkingConfig
    glossary: dict
    chunks: list[Chunk] = field(default_factory=list)
    units_of: dict[str, Unit] = field(default_factory=dict)  # chunk key -> first unit (links table row groups)

    def new(self, sec: Section, pieces: list[Piece], role: str, content_type: str, text: str | None = None,
            parent: Chunk | None = None) -> Chunk:
        units = [u for p in pieces for u in p.units]
        p0, p1 = _pages(units)
        body = text if text is not None else "\n".join(p.text for p in pieces)
        path = sec.path
        formulas = [f for p in pieces for f in p.formulas]
        table = next((p.table for p in pieces if p.table), None)
        eq = [f.get("number") for f in formulas if f.get("number") and f.get("kind") != "inline"]
        tables = sorted({t for p in pieces for t in ([p.table.get("number")] if p.table and p.table.get("number") else [])})
        figs = sorted({u.label.split()[-1] for u in units if u.kind == "figure" and u.label})
        symbols: list[str] = []
        for f in formulas:
            for s_ in f.get("symbols") or []:
                if s_ not in symbols:
                    symbols.append(s_)
        for s_ in _table_symbols(table):
            if s_ not in symbols:
                symbols.append(s_)
        units_found: list[str] = []
        if table:
            units_found += [x for x in table.get("units") or [] if x not in units_found]
        for f in formulas:
            for v in f.get("variables") or []:
                if v.get("unit") and v["unit"] not in units_found:
                    units_found.append(v["unit"])
        reasons = [f"formula ({f.get('number')}): {', '.join(f.get('reasons') or [])}" for f in formulas
                   if f.get("status") == "needs_review" and f.get("kind") != "inline"]
        methods = {u.method for u in units}
        if methods & {"ocr"}:
            reasons.append("ocr_text")
        if table and table.get("alignment_uncertain"):
            reasons.append(f"table {table.get('number')}: column alignment uncertain (merged header cells)")
        ctx = self.context(path, formulas, table)
        ch = Chunk(key=f"c{len(self.chunks):04d}", text=body.strip(), content_type=content_type, role=role,
                   page_start=p0, page_end=p1, locator=_locator(p0, p1), clause=sec.clause or _nearest_clause(sec),
                   heading=sec.heading or (path[-1] if path else None), heading_path=path,
                   parent_key=parent.key if parent else None, context=ctx, equation_numbers=eq, table_numbers=tables,
                   figure_numbers=figs, symbols=symbols[:40], units=units_found, formulas=formulas, table=table,
                   sources=sorted({u.source for u in units}), method=sorted(methods)[0] if methods else "native_text",
                   validation_status="needs_review" if reasons else "ok", review_reasons=reasons,
                   derived_lines=[d for p in pieces for d in p.derived], section_key=sec.key)
        ch.tokens = estimate_tokens(ch.text)
        self.units_of[ch.key] = units[0]
        if parent is not None:
            parent.child_keys.append(ch.key)
        self.chunks.append(ch)
        return ch

    def context(self, path: list[str], formulas: list[dict], table: dict | None) -> str:
        """Structural overlap: document > heading path, plus what each formula symbol means."""
        lines = [" › ".join([self.doc_label] + path) if path else self.doc_label]
        seen = set()
        for f in formulas:
            for v in f.get("variables") or []:
                if v["symbol"] in seen or not v.get("description"):
                    continue
                seen.add(v["symbol"])
                lines.append(f"{v['symbol']}: {v['description']}" + (f" [{v['unit']}]" if v.get("unit") else ""))
        if table and table.get("columns"):
            lines.append("Columns: " + " | ".join(c for c in table["columns"] if c))
            # a value table keyed by symbols ("| hfP | 1,25 m |") says what those symbols are
            for sym in _table_symbols(table)[:12]:
                g = self.glossary.get(sym)
                if g and g.get("description") and sym not in seen and not _is_symbol_table_meta(table):
                    seen.add(sym)
                    lines.append(f"{sym}: {g['description']}" + (f" [{g['unit']}]" if g.get("unit") else ""))
        return "\n".join(lines[:20])


def _is_symbol_table_meta(table: dict) -> bool:
    cols = " ".join(c.lower() for c in table.get("columns") or [])
    return "symbol" in cols and any(w in cols for w in ("description", "meaning", "designation"))


def _table_symbols(table: dict | None) -> list[str]:
    if not table:
        return []
    cols = [c.lower() for c in table.get("columns") or []]
    idx = next((i for i, c in enumerate(cols) if "symbol" in c or c.startswith("item") or c == "parameter"), None)
    if idx is None:
        return []
    out = []
    for r in (table.get("rows") or [])[table.get("header_rows", 0):]:
        if idx < len(r) and 0 < len(r[idx].strip()) <= 12:
            out.append(r[idx].strip())
    return out


def _nearest_clause(sec: Section) -> str | None:
    s = sec.parent
    while s is not None:
        if s.clause:
            return s.clause
        s = s.parent
    return None


def _joined(pieces: list[Piece]) -> list[str]:
    """Piece texts in order; a formula's copied lead-in is left out when its paragraph is right before it."""
    out: list[str] = []
    for i, p in enumerate(pieces):
        prev = pieces[i - 1] if i else None
        if p.lead and prev is not None and not prev.atomic and p.lead[-40:] in re.sub(r"\s+", " ", prev.text):
            out.append(p.core)
        else:
            out.append(p.text)
    return out


def _emit_section(b: _Builder, sec: Section, pieces: list[Piece]) -> None:
    cfg = b.cfg
    if not pieces:
        return
    heading = sec.heading
    # an untitled numbered clause ("5.1 The characteristics ...") already starts with its number
    head_line = [heading] if heading and not pieces[0].text.lstrip().startswith(heading) else []
    total = estimate_tokens("\n".join(head_line + _joined(pieces)))
    n_formulas = sum(1 for p in pieces if p.kind == FORMULA)
    has_atomic = any(p.kind in (TABLE, TABLE_ROW_GROUP, FIGURE_CAPTION) for p in pieces)
    # (a) the whole section is one knowledge unit: explanation + equations + legends stay together.
    # Tables and figures are always their own retrieval unit (a value or caption lookup must hit the
    # table/figure itself, not the prose around it); the section then becomes their parent.
    one_page = len({u.page_start for p in pieces for u in p.units} | {u.page_end for p in pieces for u in p.units}) == 1
    if total <= cfg.leaf_section_max and n_formulas <= 3 and (not has_atomic or len(pieces) == 1) \
            and (sec.level > 0 or one_page):
        b.new(sec, pieces, "leaf", leaf_kind(pieces), text="\n".join(head_line + _joined(pieces)))
        return
    # (b) parent windows over consecutive pieces + children
    windows: list[list[Piece]] = []
    cur: list[Piece] = []
    for p in pieces:
        if p.kind == TABLE_ROW_GROUP:
            # row groups hang under their table's overview piece (already in the window)
            cur.append(p)
            continue
        size = estimate_tokens("\n".join(head_line + [x.text for x in cur if x.kind != TABLE_ROW_GROUP] + [p.text]))
        if cur and size > cfg.parent_max:
            windows.append(cur)
            cur = []
        cur.append(p)
    if cur:
        windows.append(cur)
    for w in windows:
        shown = [p for p in w if p.kind != TABLE_ROW_GROUP]
        groups = [p for p in w if p.kind == TABLE_ROW_GROUP]
        if len(w) == 1:  # a single piece needs no parent
            p = w[0]
            b.new(sec, [p], "leaf", p.kind, text="\n".join(head_line + [p.text]))
            continue
        if len(shown) == 1 and shown[0].kind == TABLE and groups:
            # a table alone: the table overview is the parent of its row groups
            table_parent = b.new(sec, [shown[0]], "parent", TABLE, text="\n".join(head_line + [shown[0].text]))
            for q in groups:
                b.new(sec, [q], "child", TABLE_ROW_GROUP, text=q.text, parent=table_parent)
            continue
        texts = _joined(shown)
        parent = b.new(sec, w, "parent", SECTION, text="\n".join(head_line + texts))
        parent.derived_lines = [d for p in shown for d in p.derived]
        table_parent: Chunk | None = None
        for p in w:
            if p.kind == TABLE and any(q.units[0] is p.units[0] for q in groups):
                table_parent = b.new(sec, [p], "child", TABLE, text=p.text, parent=parent)
                continue
            if p.kind == TABLE_ROW_GROUP and table_parent is not None and p.units[0] is table_parent_unit(table_parent, b):
                b.new(sec, [p], "child", TABLE_ROW_GROUP, text=p.text, parent=table_parent)
                continue
            b.new(sec, [p], "child", p.kind, text=p.text, parent=parent)
        parent.tokens = estimate_tokens(parent.text)


def table_parent_unit(chunk: Chunk, b: _Builder) -> Unit | None:
    return b.units_of.get(chunk.key)


def doc_label(standard_code: str | None, title: str | None = None) -> str:
    return standard_code or (title or "Document")


def chunk_model(model: DocModel, *, standard_code: str | None = None, title: str | None = None,
                config: ChunkingConfig = DEFAULT_CONFIG) -> list[Chunk]:
    b = _Builder(doc_label(standard_code, title), config, model.glossary)
    for sec in model.sections:
        _emit_section(b, sec, _section_pieces(sec, config, model.glossary))
    _check_limits(b.chunks, config)
    return b.chunks


def chunk_document(pages: list[PageOut], *, standard_code: str | None = None, title: str | None = None,
                   config: ChunkingConfig = DEFAULT_CONFIG) -> list[Chunk]:
    """Pages (with layout blocks where available) -> engineering chunks in document order."""
    return chunk_pages(pages, standard_code=standard_code, title=title, config=config)[0]


def chunk_pages(pages: list[PageOut], *, standard_code: str | None = None, title: str | None = None,
                config: ChunkingConfig = DEFAULT_CONFIG) -> tuple[list[Chunk], DocModel]:
    """Like ``chunk_document``, also returning the document model (sections, symbol glossary, stats)."""
    model = build_model(pages)
    chunks = chunk_model(model, standard_code=standard_code, title=title, config=config)
    by_page = {p.page_number: p for p in pages}
    for c in chunks:  # character offsets of the first verbatim line on the first page (citation anchor)
        page = by_page.get(c.page_start)
        first = next((ln for ln in c.verbatim_text.split("\n") if len(ln.strip()) >= 8), "")
        pos = page.text.find(first[:60]) if page is not None and first else -1
        c.char_start = max(0, pos)
        c.char_end = c.char_start + len(c.text)
    return chunks, model


def _check_limits(chunks: list[Chunk], cfg: ChunkingConfig) -> None:
    for c in chunks:
        if c.role != "parent" and c.tokens > cfg.hard_max:
            c.review_reasons.append(f"oversize_unit:{c.tokens}>{cfg.hard_max}")


# --------------------------------------------------------------------------- page-text fallback
@dataclass
class PageChunk:
    page_number: int
    locator: str
    text: str
    char_start: int
    char_end: int


def chunk_page(page_number: int, locator: str, text: str, limit: int = DEFAULT_CONFIG.soft_target) -> list[PageChunk]:
    """Sentence-aware windows of one page's text (used for ad-hoc page views, not for indexing)."""
    out: list[PageChunk] = []
    pos = 0
    for part in _pack(split_sentences(text), limit) if text.strip() else []:
        start = text.find(part[:40], pos)
        start = pos if start < 0 else start
        out.append(PageChunk(page_number, locator, part.strip(), start, start + len(part)))
        pos = start + 1
    return out
