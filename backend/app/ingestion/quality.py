"""Extraction quality gates and the verified-corpus fingerprint.

Every gate is deterministic (no LLM) and recorded with its measured value, so
an approval can be audited later. Outcome of a version:

  failed        a blocking gate failed (invalid PDF, no chunks, index not built)
  needs_review  indexed, but something needs a human look (OCR pages, uncertain
                Symbol-font glyphs, standard-code mismatch, missing critical
                engineering tokens, model-generated content, weak structure)
  extracted     every gate passed; ready for owner approval (``verified``)

The fingerprint binds an approval to the exact extraction that was reviewed:
pipeline version, parser versions and the ordered chunk content hashes.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from app.ingestion.extractors.base import PageOut

PIPELINE_VERSION = "canonical-ingestion/2"  # /2: engineering chunker (parent/child, formula geometry)

# Critical engineering content that must survive extraction (checked on the text layer and
# the structure). Keyed by the base standard number; curated from the documents themselves.
EXPECTED: dict[str, dict[str, list[str]]] = {
    "ISO 53": {
        "tokens": ["αP", "αFP", "ρfP", "haP", "hfP", "hFfP", "cP", "sP", "eP", "UFP", "m"],
        "clauses": ["1", "2", "3", "3.1", "3.2", "4", "5", "5.1", "5.4", "5.9", "Annex A", "A.1", "A.2"],
        "tables": ["Table 1", "Table 2", "Table A.1"],
        "equations": ["(1)", "(2)", "(3)"],
    },
    "ISO 21771": {
        "tokens": ["m", "mn", "mt", "z", "d", "da", "df", "db", "β", "αt", "αn"],
        "clauses": ["1", "3.1", "4.2.4", "4.2.7", "4.3.10", "4.5.3", "4.5.4", "5.2.1", "Annex A"],
        "tables": [],
        "equations": ["(1)", "(2)", "(19)", "(33)", "(34)"],
    },
    "ISO 14104": {"tokens": ["FD", "FE"], "clauses": [], "tables": [], "equations": []},
}

MIN_TEXT_COVERAGE = 0.9
MIN_PAGE_SCORE = 0.9
MIN_TRACEABILITY = 0.95
MIN_CONTENT_COVERAGE = 0.97
MINOR_OCR_CHARS = 200


@dataclass
class Gate:
    id: str
    status: str  # pass | review | fail | skip
    value: Any = None
    threshold: Any = None
    detail: str = ""
    pages: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = {"id": self.id, "status": self.status, "value": self.value, "threshold": self.threshold,
             "detail": self.detail}
        if self.pages:
            d["pages"] = self.pages[:50]
        return d


@dataclass
class ChunkFacts:
    """What the gates need to know about one persisted chunk."""

    page_number: int
    text: str
    content_type: str
    clause: str | None
    has_lineage: bool
    content_hash: str
    sources: list[str]
    tsv_empty: bool = False
    heading: str | None = None
    # engineering chunks (canonical-ingestion/2)
    page_end: int | None = None
    role: str = "leaf"
    key: str | None = None
    parent_key: str | None = None
    child_keys: list[str] = field(default_factory=list)
    tokens: int = 0
    verbatim: str | None = None  # text without the derived formula/table lines
    raw_extra: str = ""  # raw formula glyphs (content coverage)
    formulas: list[dict] = field(default_factory=list)
    heading_path: list[str] = field(default_factory=list)
    meta_hash: str = ""
    review_reasons: list[str] = field(default_factory=list)

    @property
    def last_page(self) -> int:
        return self.page_end or self.page_number

    @property
    def fingerprint_part(self) -> str:
        return f"{self.content_hash}:{self.meta_hash}" if self.meta_hash else self.content_hash


def base_code(code: str | None) -> str | None:
    m = re.match(r"(ISO(?:/T[RS])?) (\d{2,5})", code or "")
    return f"{m.group(1)} {m.group(2)}" if m else None


def fingerprint(parser: str | None, parser_version: str | None, standard_code: str | None,
                content_hashes: list[str]) -> str:
    h = hashlib.sha256()
    for part in (PIPELINE_VERSION, parser or "", parser_version or "", standard_code or "", *content_hashes):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def clause_preserved(clause: str, chunk_clauses: set[str]) -> bool:
    """A parent clause ("5", "Annex A") is preserved when it or a descendant (5.1, A.1) is a chunk clause:
    a heading directly followed by its first sub-clause lives in that sub-clause's heading column."""
    prefix = clause.split()[-1] + "." if clause.startswith("Annex ") else clause + "."
    return clause in chunk_clauses or any(c.startswith(prefix) for c in chunk_clauses)


def _token_present(tok: str, text: str) -> bool:
    if tok.startswith("("):
        return tok in text
    return re.search(r"(?<![^\W_])" + re.escape(tok) + r"(?![^\W_])", text) is not None


def evaluate(*, is_pdf: bool, verified_area: bool, standard_code: str | None, code_check: dict,
             validation: dict | None, page_count: int, blank_pages: list[int], pages: list[PageOut],
             chunks: list[ChunkFacts], layout: dict | None, parser: str | None, parser_version: str | None) -> dict:
    gates: list[Gate] = []
    add = gates.append

    # G1 PDF validity (blocking errors never reach this point: extraction raises)
    if is_pdf and validation is not None:
        warn = validation.get("warnings", [])
        add(Gate("pdf_valid", "review" if warn else "pass", value=warn or "ok",
                 detail="PDF yapısı onarıldı veya EOF işareti eksik" if warn else ""))
    # G2 text coverage over non-blank pages
    denom = max(1, page_count - len(blank_pages))
    text_pages = {p.page_number for p in pages if p.text.strip()}
    coverage = round(len(text_pages) / denom, 4)
    missing = [n for n in range(1, page_count + 1) if n not in text_pages and n not in blank_pages]
    add(Gate("text_coverage", "pass" if coverage >= MIN_TEXT_COVERAGE else ("review" if coverage >= 0.5 else "fail"),
             value=coverage, threshold=MIN_TEXT_COVERAGE, pages=missing))
    # G3 OCR pages are draft extractions (never evidence until a person confirms them). Short OCR
    # pages (covers, back pages: < MINOR_OCR_CHARS) do not carry content and are only listed.
    ocr = [p for p in pages if p.method == "ocr"]
    substantive = [p.page_number for p in ocr if len(p.text) >= MINOR_OCR_CHARS]
    ocr_conf = [p.ocr_confidence for p in ocr if p.ocr_confidence is not None]
    detail = f"ortalama OCR güveni {sum(ocr_conf) / len(ocr_conf):.3f}" if ocr_conf else ""
    minor = [p.page_number for p in ocr if len(p.text) < MINOR_OCR_CHARS]
    if minor:
        detail += f"; içeriksiz kısa OCR sayfaları (taslak kalır): {minor[:20]}"
    add(Gate("ocr_pages", "review" if substantive else "pass", value=len(substantive), threshold=0,
             pages=substantive, detail=detail.strip("; ")))
    # G4 page quality score (printable ratio, replacement characters, OCR confidence)
    weak = [p.page_number for p in pages if (p.quality or {}).get("score", 1.0) < MIN_PAGE_SCORE]
    scores = [(p.quality or {}).get("score", 1.0) for p in pages]
    add(Gate("page_quality", "review" if weak else "pass", value=round(min(scores), 4) if scores else None,
             threshold=MIN_PAGE_SCORE, pages=weak))
    # G5 uncertain Symbol-font glyphs (e.g. "•" standing for ∞): never rewritten, reviewed by a human
    unc = [p.page_number for p in pages if (p.quality or {}).get("uncertain_symbol_glyphs")]
    add(Gate("uncertain_symbol_glyphs", "review" if unc else "pass",
             value=sum((p.quality or {}).get("uncertain_symbol_glyphs", 0) for p in pages), threshold=0, pages=unc,
             detail=f"Unicode eşlemesi olmayan Symbol fontları: {', '.join((layout or {}).get('unmapped_symbol_fonts', []))}"
             if unc else ""))
    # G6 standard code: detected, canonical, consistent with the file name
    if verified_area or standard_code:
        issues = code_check.get("issues", [])
        add(Gate("standard_code", "review" if issues or not standard_code else "pass", value=standard_code,
                 detail="; ".join(issues) if issues else ("standart kodu bulunamadı" if not standard_code else "")))
    # G7 structure: ISO clause structure recovered (otherwise chunks are page windows only)
    headings = [c for c in chunks if c.clause]
    if is_pdf and verified_area:
        clauses = {c.clause for c in chunks if c.clause}
        has_scope = clause_preserved("1", clauses) or clause_preserved("0", clauses)
        add(Gate("structure", "pass" if headings and has_scope else "review", value=len({c.clause for c in headings}),
                 detail="" if headings and has_scope else "ISO madde yapısı çıkarılamadı (sayfa pencereli parçalar)"))
    # G8 critical engineering tokens, clauses, tables and equations of known standards
    exp = EXPECTED.get(base_code(standard_code) or "")
    if exp:
        full = "\n".join(p.text for p in pages)
        missing_tokens = [t for t in exp["tokens"] if not _token_present(t, full)]
        clauses = {c.clause for c in chunks if c.clause}
        missing_clauses = [c for c in exp["clauses"] if not clause_preserved(c, clauses)]
        table_text = "\n".join(c.text for c in chunks if c.content_type == "table")
        missing_tables = [t for t in exp["tables"] if not re.search(re.escape(t) + r"\s*[—–-]", table_text)]
        formula_text = "\n".join(c.text for c in chunks if c.content_type == "formula")
        missing_eq = [e for e in exp["equations"] if e not in formula_text]
        problems = {k: v for k, v in (("tokens", missing_tokens), ("clauses", missing_clauses),
                                      ("tables", missing_tables), ("equations", missing_eq)) if v}
        add(Gate("critical_content", "review" if problems else "pass", value=problems or "ok"))
    # G9 chunk lineage (blocking)
    no_lineage = [c.page_number for c in chunks if not c.has_lineage]
    add(Gate("chunk_lineage", "fail" if not chunks or no_lineage else "pass", value=len(chunks),
             pages=sorted(set(no_lineage)), detail="parça yok" if not chunks else ""))
    # G10 full-text index built for every chunk (blocking)
    empty = [c.page_number for c in chunks if c.tsv_empty]
    add(Gate("fulltext_index", "fail" if not chunks or empty else "pass", value=len(chunks) - len(empty),
             pages=sorted(set(empty))))
    # G11 model-generated content (formula LaTeX, layout-model text) needs review before it is evidence
    model = sorted({c.page_number for c in chunks if any(s.endswith("_model") for s in c.sources)})
    add(Gate("model_generated_content", "review" if model else "pass", value=len(model), pages=model))
    # G12 source-page traceability: the verbatim lines of a chunk are found on its cited page range
    # (derived formula/table lines are excluded; they are checked by their own gates)
    text_chunks = [c for c in chunks if c.content_type not in ("table", "formula", "table_row_group")
                   and c.role != "parent"]
    page_text = {p.page_number: _squash(p.text) for p in pages}

    def span(c: ChunkFacts) -> str:
        return "".join(page_text.get(n, "") for n in range(c.page_number, c.last_page + 1))

    traced = [c for c in text_chunks if is_traced(c.verbatim if c.verbatim is not None else c.text, span(c))]
    ratio = round(len(traced) / len(text_chunks), 4) if text_chunks else 1.0
    untraced = sorted({c.page_number for c in text_chunks if c not in traced})
    add(Gate("traceability", "pass" if ratio >= MIN_TRACEABILITY else "review", value=ratio,
             threshold=MIN_TRACEABILITY, pages=untraced))
    # G13 content coverage: every substantive line of a page reaches that page's chunks (nothing the
    # structure layer drops silently; page headers/footers are excluded)
    cov, weak_pages = content_coverage(pages, chunks, set((layout or {}).get("repeated_furniture") or []))
    add(Gate("content_coverage", "pass" if cov >= MIN_CONTENT_COVERAGE else "review", value=cov,
             threshold=MIN_CONTENT_COVERAGE, pages=weak_pages))
    # G14 chunk structure (blocking): parent/child links resolve both ways, page ranges are ordered,
    # and no retrieval chunk exceeds the hard limit unless it is one indivisible unit (then review)
    if any(c.key for c in chunks):
        add(chunk_structure_gate(chunks))
    # G16 table structure: grids whose column alignment is uncertain (merged header cells) are listed
    uncertain_tables = sorted({c.page_number for c in chunks
                               if any(r.startswith("table ") and "alignment" in r for r in c.review_reasons)})
    if any(c.content_type in ("table", "table_row_group") for c in chunks):
        add(Gate("table_structure", "review" if uncertain_tables else "pass", value=len(uncertain_tables),
                 pages=uncertain_tables, detail="tablo sütun hizası belirsiz (birleşik başlık hücreleri)"
                 if uncertain_tables else ""))
    # G15 formula extraction: equations whose 2-D layout could not be reconstructed with certainty keep
    # their PDF glyph line, get no LaTeX and are listed for a human check of the rendered page
    formulas = [f for c in chunks if c.role != "parent" for f in c.formulas if f.get("kind") != "inline"]
    if formulas:
        unsure = [f for f in formulas if f.get("status") == "needs_review"]
        add(Gate("formula_extraction", "review" if unsure else "pass",
                 value={"formulas": len(formulas), "needs_review": len(unsure),
                        "latex": sum(1 for f in formulas if f.get("latex"))},
                 detail=", ".join(f"({f.get('number')})" for f in unsure[:30]),
                 pages=sorted({f.get("page") for f in unsure if f.get("page")})))

    statuses = {g.status for g in gates}
    outcome = "failed" if "fail" in statuses else ("needs_review" if "review" in statuses else "extracted")
    return {
        "pipeline_version": PIPELINE_VERSION,
        "parser": parser,
        "parser_version": parser_version,
        "outcome": outcome,
        "gates": [g.as_dict() for g in gates],
        "metrics": {"page_count": page_count, "text_pages": len(text_pages), "blank_pages": blank_pages,
                    "chunks": len(chunks), "clauses": len({c.clause for c in chunks if c.clause}),
                    "content_types": _count(c.content_type for c in chunks),
                    "symbol_glyphs_mapped": (layout or {}).get("symbol_glyphs_mapped", 0)},
        "fingerprint": fingerprint(parser, parser_version, standard_code, [c.fingerprint_part for c in chunks]),
    }


HARD_MAX_TOKENS = 350  # must equal chunker.ChunkingConfig.hard_max (checked by a test)


def chunk_structure_gate(chunks: list[ChunkFacts]) -> Gate:
    keys = {c.key: c for c in chunks}
    broken: list[int] = []
    for c in chunks:
        if c.parent_key is not None:
            # a table overview is a child of its section window AND the parent of its row groups
            parent = keys.get(c.parent_key)
            if parent is None or c.key not in parent.child_keys:
                broken.append(c.page_number)
        for k in c.child_keys:
            child = keys.get(k)
            if child is None or child.parent_key != c.key:
                broken.append(c.page_number)
        if c.role == "parent" and not c.child_keys:
            broken.append(c.page_number)
        if c.last_page < c.page_number:
            broken.append(c.page_number)
    oversize = [c for c in chunks if c.role != "parent" and c.tokens > HARD_MAX_TOKENS]
    if broken:
        return Gate("chunk_structure", "fail", value={"broken_links": len(broken)}, pages=sorted(set(broken)),
                    detail="parent/child bağlantısı veya sayfa aralığı bozuk")
    return Gate("chunk_structure", "review" if oversize else "pass",
                value={"chunks": len(chunks), "parents": sum(c.role == "parent" for c in chunks),
                       "children": sum(c.role == "child" for c in chunks), "oversize": len(oversize),
                       "max_tokens": max((c.tokens for c in chunks if c.role != "parent"), default=0)},
                threshold=HARD_MAX_TOKENS, pages=sorted({c.page_number for c in oversize}))


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s.replace("­", ""))


def content_coverage(pages: list[PageOut], chunks: list[ChunkFacts],
                     repeated: set[str] | None = None) -> tuple[float, list[int]]:
    """Share of substantive page lines found in the chunks covering that page, and the weak pages.
    Page furniture (fixed patterns and the document's learned running headers/footers) is excluded."""
    from app.ingestion.parsing.iso_layout import FURNITURE, furniture_key

    repeated = repeated or set()
    blobs: dict[int, list[str]] = {}
    for c in chunks:
        blob = _squash((c.heading or "") + c.text + c.raw_extra).replace("|", "")
        for n in range(c.page_number, c.last_page + 1):
            blobs.setdefault(n, []).append(blob)
    found = total = 0
    weak: list[int] = []
    for p in pages:
        blob = "".join(blobs.get(p.page_number, []))
        lines = [s for ln in p.text.splitlines() if not any(f.match(ln.strip()) for f in FURNITURE)
                 and furniture_key(ln) not in repeated and len(s := _squash(ln)) >= 4]
        hit = sum(1 for s in lines if s in blob)
        found, total = found + hit, total + len(lines)
        if lines and hit / len(lines) < MIN_CONTENT_COVERAGE:
            weak.append(p.page_number)
    return (round(found / total, 4) if total else 1.0), weak


def is_traced(chunk_text: str, squashed_page: str) -> bool:
    """At least 90 % of the chunk's substantive lines occur verbatim (ignoring spaces) on its page."""
    lines = [s for s in (_squash(ln) for ln in chunk_text.splitlines()) if len(s) >= 8]
    if not lines:
        return True
    return sum(1 for s in lines if s in squashed_page) / len(lines) >= 0.9


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out
