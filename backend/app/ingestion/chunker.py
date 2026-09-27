"""Split extracted pages into retrieval chunks with stable locators and lineage.

``chunk_document`` is structure-aware: when a parser supplied blocks it cuts at
clause boundaries (never mixing two clauses in one chunk), carries the clause
number and heading across page breaks, keeps a formula together with its
lead-in sentence and legend, and emits every table as its own chunk (split by
rows with the caption repeated when long). Chunks never span pages, so the page
citation stays exact (``page_start == page_end``). Pages without blocks (office
files, plain text, transcripts) use the page-window chunker ``chunk_page``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ingestion.extractors.base import Block, PageOut

MAX_CHARS = 1200
OVERLAP = 150
_DEFINITION_SECTION = re.compile(r"terms and definitions|symbols|subscripts|abbreviat", re.IGNORECASE)
_FRONT_MATTER = {"foreword", "contents", "introduction"}


@dataclass
class Chunk:
    page_number: int
    locator: str
    text: str
    char_start: int
    char_end: int
    clause: str | None = None
    heading: str | None = None
    content_type: str = "text"
    sources: list[str] = field(default_factory=list)  # producing layers of the blocks ("pymupdf", "docling:table")

    @property
    def page_start(self) -> int:
        return self.page_number

    @property
    def page_end(self) -> int:
        return self.page_number


def chunk_page(page_number: int, locator: str, text: str) -> list[Chunk]:
    if len(text) <= MAX_CHARS:
        return [Chunk(page_number, locator, text, 0, len(text))]
    chunks: list[Chunk] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + MAX_CHARS)
        if end < n:
            cut = max(text.rfind("\n", start + MAX_CHARS // 2, end), text.rfind(". ", start + MAX_CHARS // 2, end))
            if cut > start:
                end = cut + 1
        chunks.append(Chunk(page_number, locator, text[start:end].strip(), start, end))
        if end >= n:
            break
        start = max(end - OVERLAP, start + 1)
    return [c for c in chunks if c.text]


@dataclass
class _Section:
    clause: str | None = None
    heading: str | None = None
    top: str | None = None  # level-1 heading title (decides definition / front matter)
    body_started: bool = False


def _content_type(kinds: set[str], sec: _Section, structured: bool = True) -> str:
    if "formula" in kinds:
        return "formula"
    # front matter is known only where the clause structure was recovered (not for OCR-only scans)
    if structured and (not sec.body_started or (sec.top or "").lower() in _FRONT_MATTER):
        return "front_matter"
    if _DEFINITION_SECTION.search(sec.top or ""):
        return "definition"
    if kinds == {"note"}:
        return "note"
    if kinds == {"list"}:
        return "list"
    return "text"


def _split_table(block: Block) -> list[str]:
    if len(block.text) <= MAX_CHARS:
        return [block.text]
    lines = block.text.split("\n")
    caption = lines[0] if not lines[0].startswith("|") else ""
    rows = lines[1:] if caption else lines
    parts, cur = [], []
    for row in rows:
        if cur and len("\n".join([caption, *cur, row])) > MAX_CHARS:
            parts.append("\n".join(x for x in [caption, *cur] if x))
            cur = []
        cur.append(row)
    if cur:
        parts.append("\n".join(x for x in [caption, *cur] if x))
    return parts


def _heading_title(block: Block) -> str:
    return block.text.split("\n")[0].strip()


def _flush(out: list[tuple[PageOut, Chunk]], page: PageOut, buf: list[Block], sec: _Section,
           page_end: bool = False) -> None:
    """Emit the buffered blocks of one page as chunk(s) of the current clause."""
    if not buf or all(b.kind == "heading" for b in buf):
        # heading-only: mid-page it prefixes the next chunk ("4.2.6 ..." + "4.2.6.1 ..."); at the
        # page end it is dropped from the text (clause/heading columns carry it to the next page)
        if page_end:
            buf.clear()
        return
    text = "\n".join(b.text for b in buf).strip()
    kinds = {b.kind for b in buf} - {"heading", "caption"} or {"text"}
    sources = sorted({b.source or "" for b in buf} - {""})
    for part in ([text] if len(text) <= MAX_CHARS else [c.text for c in chunk_page(0, "", text)]):
        start = max(0, page.text.find(part[:60]))
        out.append((page, Chunk(page.page_number, page.locator, part, start, start + len(part),
                                clause=sec.clause, heading=sec.heading, content_type=_content_type(kinds, sec),
                                sources=sources)))
    buf.clear()


def chunk_document(pages: list[PageOut]) -> list[tuple[PageOut, Chunk]]:
    """Structure-aware chunks for a whole document (reading order), with lineage."""
    out: list[tuple[PageOut, Chunk]] = []
    sec = _Section()
    structured = any(p.blocks for p in pages)
    for page in pages:
        if not page.blocks:
            for ch in chunk_page(page.page_number, page.locator, page.text):
                ch.clause, ch.heading = sec.clause, sec.heading
                ch.content_type = _content_type({"text"}, sec, structured)
                out.append((page, ch))
            continue
        buf: list[Block] = []

        def flush(page_end: bool = False, page: PageOut = page, buf: list[Block] = buf) -> None:
            _flush(out, page, buf, sec, page_end)

        for block in page.blocks:
            if block.kind == "heading":
                flush()
                title = _heading_title(block)
                sec.clause, sec.heading = block.clause, title
                if (block.level or 1) == 1:
                    sec.top = re.sub(r"^(?:\d+|Annex [A-Z])\s*(?:\((?:in)?formative\))?\s*", "", title)
                if block.clause:
                    sec.body_started = True
                buf.append(block)
                continue
            if block.clause and block.clause != sec.clause:  # numbered sub-clause with running text
                flush()
                sec.clause = block.clause
            if block.kind == "table":
                flush()
                buf.clear()  # a pending heading-only buffer is carried by the heading column
                for part in _split_table(block):
                    start = max(0, page.text.find(part.split("\n")[0][:60]))
                    out.append((page, Chunk(page.page_number, page.locator, part, start, start + len(part),
                                            clause=sec.clause, heading=sec.heading, content_type="table",
                                            sources=[block.source or ""])))
                continue
            if buf and len("\n".join(b.text for b in buf)) + len(block.text) > MAX_CHARS:
                flush()
            buf.append(block)
        flush(page_end=True)
    return out
