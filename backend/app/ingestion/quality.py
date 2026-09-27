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

PIPELINE_VERSION = "canonical-ingestion/1"

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
    # G12 source-page traceability: chunk text is found on its cited page
    text_chunks = [c for c in chunks if c.content_type not in ("table", "formula")]
    page_text = {p.page_number: _squash(p.text) for p in pages}
    traced = [c for c in text_chunks if is_traced(c.text, page_text.get(c.page_number, ""))]
    ratio = round(len(traced) / len(text_chunks), 4) if text_chunks else 1.0
    untraced = sorted({c.page_number for c in text_chunks if c not in traced})
    add(Gate("traceability", "pass" if ratio >= MIN_TRACEABILITY else "review", value=ratio,
             threshold=MIN_TRACEABILITY, pages=untraced))
    # G13 content coverage: every substantive line of a page reaches that page's chunks (nothing the
    # structure layer drops silently; page headers/footers are excluded)
    cov, weak_pages = content_coverage(pages, chunks)
    add(Gate("content_coverage", "pass" if cov >= MIN_CONTENT_COVERAGE else "review", value=cov,
             threshold=MIN_CONTENT_COVERAGE, pages=weak_pages))

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
        "fingerprint": fingerprint(parser, parser_version, standard_code, [c.content_hash for c in chunks]),
    }


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s.replace("­", ""))


def content_coverage(pages: list[PageOut], chunks: list[ChunkFacts]) -> tuple[float, list[int]]:
    """Share of substantive page lines found in the chunks of the same page, and the weak pages."""
    from app.ingestion.parsing.iso_layout import FURNITURE

    blobs: dict[int, list[str]] = {}
    for c in chunks:
        blobs.setdefault(c.page_number, []).append(_squash((c.heading or "") + c.text).replace("|", ""))
    found = total = 0
    weak: list[int] = []
    for p in pages:
        blob = "".join(blobs.get(p.page_number, []))
        lines = [s for ln in p.text.splitlines() if not any(f.match(ln.strip()) for f in FURNITURE)
                 and len(s := _squash(ln)) >= 4]
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
