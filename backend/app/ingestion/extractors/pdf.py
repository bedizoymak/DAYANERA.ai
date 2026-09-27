"""PDF extraction: validation, native text per page with Symbol-font Greek
recovery and ISO structure blocks, character-gap reconstruction for PDFs with
per-glyph positioning, local OCR for scanned pages, and (optionally) Docling
table structure.

Parser modes (``PDF_PARSER``):
  pymupdf  text layer + deterministic ISO layout (default, no models)
  hybrid   pymupdf, with table grids replaced by Docling's table structure
  docling  Docling layout for all blocks; page text stays the PDF text layer
Docling runs out of process and is optional: when it is unavailable the
extractor falls back to ``pymupdf`` and records why.
"""
from __future__ import annotations

import logging
import re
import statistics

import numpy as np
import pymupdf

from app.ingestion.extractors.base import Block, ExtractContext, ExtractionOutput, PageOut, normalize_text
from app.ingestion.ocr import OcrEngine
from app.ingestion.parsing import iso_layout
from app.ingestion.parsing.symbols import symbol_lexicon

log = logging.getLogger(__name__)

MIN_NATIVE_CHARS = 40
LAYOUT_VERSION = "iso-layout/2"


def pymupdf_version() -> str:
    return f"pymupdf {pymupdf.VersionBind}; {LAYOUT_VERSION}"


def is_degenerate(text: str) -> bool:
    toks = text.split()
    if len(toks) < 30:
        return False
    singles = sum(1 for t in toks if len(t) == 1 and t.isalpha())
    return singles / len(toks) > 0.45


def rebuild_from_chars(page: "pymupdf.Page") -> str:
    """Rebuild words from glyph geometry when the text layer lost spaces."""
    raw = page.get_text("rawdict")
    chars = []
    for b in raw.get("blocks", []):
        for ln in b.get("lines", []):
            for sp in ln.get("spans", []):
                for c in sp.get("chars", []):
                    x0, _y0, x1, _y1 = c["bbox"]
                    chars.append((c["origin"][1], x0, x1, c["c"]))
    chars.sort(key=lambda t: (round(t[0], 0), t[1]))
    lines: list[list] = []
    for ch in chars:
        if lines and abs(lines[-1][0] - ch[0]) <= 2.0:
            lines[-1][1].append(ch)
        else:
            lines.append([ch[0], [ch]])
    out = []
    for _, cs in lines:
        cs = sorted((c for c in cs if not c[3].isspace()), key=lambda t: t[1])
        if not cs:
            continue
        widths = [c[2] - c[1] for c in cs if c[2] - c[1] > 0.5]
        mw = statistics.median(widths) if widths else 4.0
        buf, prev = "", None
        for ch in cs:
            if prev is not None and ch[1] - prev[2] > 0.33 * mw:
                buf += " "
            buf += ch[3]
            prev = ch
        out.append(buf)
    return "\n".join(out)


def _ocr_page(page: "pymupdf.Page", dpi: int) -> tuple[str, float]:
    engine = OcrEngine.get()
    if engine is None:
        raise RuntimeError("OCR motoru kullanılamıyor")
    pix = page.get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = img[:, :, :3]
    elif pix.n == 1:
        img = np.repeat(img, 3, axis=2)
    res = engine.recognize(np.ascontiguousarray(img))
    return res.text, res.mean_confidence


# --------------------------------------------------------------------------- validation / quality
def validate_pdf(data: bytes) -> dict:
    """Structural checks before any extraction (blocking errors raise in extract_pdf)."""
    tail = data[-2048:]
    report = {"header_ok": data[:1024].lstrip().startswith(b"%PDF-"), "eof_marker": b"%%EOF" in tail,
              "size_bytes": len(data), "errors": [], "warnings": []}
    if not report["header_ok"]:
        report["errors"].append("missing_pdf_header")
        return report
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        report["errors"].append(f"open_failed: {type(exc).__name__}")
        return report
    report.update(page_count=len(doc), encrypted=bool(doc.needs_pass), repaired=bool(getattr(doc, "is_repaired", False)),
                  pdf_version=(doc.metadata or {}).get("format"))
    if not report["eof_marker"]:
        report["warnings"].append("missing_eof_marker")
    if report["repaired"]:
        report["warnings"].append("xref_repaired")
    if len(doc) == 0:
        report["errors"].append("no_pages")
    return report


def page_quality(text: str, method: str, ocr_conf: float | None) -> dict:
    n = len(text)
    if n == 0:
        return {"chars": 0, "score": 0.0, "flags": ["no_text"]}
    replacement = text.count("�")
    printable = sum(1 for c in text if c.isprintable() or c in "\n\t") / n
    score = printable * (1.0 - min(1.0, 20.0 * replacement / n))
    flags = []
    if method == "ocr":
        score *= ocr_conf if ocr_conf is not None else 0.5
        flags.append("ocr")
    elif method == "native_text_rebuilt":
        score *= 0.95
        flags.append("rebuilt_text_layer")
    if replacement:
        flags.append("replacement_chars")
    if n < MIN_NATIVE_CHARS:
        flags.append("low_text")
    return {"chars": n, "replacement_chars": replacement, "score": round(score, 4), "flags": flags}


_SUSPECT_GLYPH = re.compile(r"[^\sA-Za-z0-9=+\-−–()\[\]{}/<>.,;:°±×·'\"|*]")


# --------------------------------------------------------------------------- extraction
def extract_pdf(data: bytes, ctx: ExtractContext) -> ExtractionOutput:
    out = ExtractionOutput(parser="pymupdf", parser_version=pymupdf_version())
    validation = validate_pdf(data)
    if any(e == "missing_pdf_header" or e.startswith("open_failed") for e in validation["errors"]):
        raise ValueError(f"PDF açılamadı/doğrulanamadı: {', '.join(validation['errors'])}")
    doc = pymupdf.open(stream=data, filetype="pdf")
    if doc.needs_pass:
        out.stored_only = True
        out.stored_only_reason = "Parola korumalı PDF: yalnızca arşivlendi, içerik çıkarılamadı."
        out.metadata["pdf_validation"] = validation
        return out
    meta = {k: v for k, v in (doc.metadata or {}).items() if v}
    out.metadata = {"pdf_metadata": meta, "page_count": len(doc), "pdf_validation": validation}
    settings = ctx.settings
    state = iso_layout.LayoutState()
    suspects: dict[int, list[tuple[str, str]]] = {}
    blank_pages: list[int] = []
    out.metadata["blank_pages"] = blank_pages
    ocr_pages = 0
    ocr_skipped = 0
    # pass 1: text lines of every page, then the document's own running headers/footers
    all_lines = [iso_layout.page_lines(page, state) for page in doc]
    state.repeated = iso_layout.repeated_furniture([(p.rect.height, ln) for p, ln in zip(doc, all_lines)])
    for idx, page in enumerate(doc):
        n = idx + 1
        lines = all_lines[idx]
        suspects[n] = _symbol_suspects(page)
        text = normalize_text(iso_layout.page_text(lines))
        method = "native_text"
        blocks: list[Block] = []
        if is_degenerate(text):
            rebuilt = normalize_text(rebuild_from_chars(page))
            if rebuilt and not is_degenerate(rebuilt):
                text, method = rebuilt, "native_text_rebuilt"
        if method == "native_text" and len(text) >= MIN_NATIVE_CHARS:
            # pass 2: table grids, inline symbols joined into their text rows, then the structure blocks
            tables = iso_layout.page_tables(page)
            lines = iso_layout.merge_inline_fragments(page, lines, state, [t.bbox for t in tables])
            text = normalize_text(iso_layout.page_text(lines))
            blocks = iso_layout.detect_blocks(page, lines, state, tables)
        conf = None
        if len(text) < MIN_NATIVE_CHARS:
            if settings.ocr_enabled and ocr_pages < settings.ocr_max_pages_per_document:
                cached = ctx.storage.ocr_cache_get(ctx.sha256, n)
                if cached is not None:
                    ocr_text, conf = cached.get("text", ""), cached.get("confidence")
                else:
                    try:
                        ocr_text, conf = _ocr_page(page, settings.ocr_dpi)
                        ctx.storage.ocr_cache_put(ctx.sha256, n, {"text": ocr_text, "confidence": conf,
                                                                  "dpi": settings.ocr_dpi, "engine": "rapidocr"})
                    except Exception as exc:
                        out.warnings.append(f"s. {n}: OCR başarısız ({exc})")
                        ocr_text, conf = "", None
                ocr_pages += 1
                ocr_text = normalize_text(ocr_text)
                if ocr_text:
                    text, method, blocks = ocr_text, "ocr", []
                elif not text:
                    blank_pages.append(n)  # neither a text layer nor anything OCR could read
            elif len(text) == 0:
                ocr_skipped += 1
        if text:
            out.pages.append(PageOut(page_number=n, text=text, method=method, locator=f"s. {n}", ocr_confidence=conf,
                                     blocks=blocks, quality=page_quality(text, method, conf),
                                     parser=out.parser, parser_version=out.parser_version))
    # glyphs of Symbol fonts WITHOUT a Unicode map (letters were mapped there) whose meaning is not
    # certain (e.g. "•" standing for ∞): counted per page for manual review, never rewritten
    unmapped_fonts = state.stats.get("unmapped_symbol_fonts", set())
    uncertain_total = 0
    for p in out.pages:
        k = sum(1 for font, _c in suspects.get(p.page_number, [])
                if font in unmapped_fonts or _MATH_PI_FONT.search(font))
        if k:
            p.quality["uncertain_symbol_glyphs"] = k
            p.quality["flags"].append("uncertain_symbol_glyphs")
            uncertain_total += k
    out.metadata["layout"] = {"symbol_glyphs_mapped": state.stats["symbol_glyphs_mapped"],
                              "uncertain_symbol_glyphs": uncertain_total, "rotated_lines": state.stats["rotated_lines"],
                              "unmapped_symbol_fonts": sorted(unmapped_fonts),
                              "repeated_furniture": sorted(state.repeated),
                              **{k: state.stats[k] for k in ("inline_merges", "repeated_furniture_lines", "formulas",
                                                             "formulas_needs_review", "tables_grid", "tables_lines",
                                                             "legends", "figures")}}
    if ocr_pages:
        out.warnings.append(f"{ocr_pages} sayfa yerel OCR ile okundu: içerik 'Taslak çıkarım' olarak işaretlendi.")
    if ocr_skipped:
        out.warnings.append(f"{ocr_skipped} sayfa OCR sınırı/ayarı nedeniyle okunmadı.")
    out.metadata["ocr_pages"] = ocr_pages
    if not out.pages:
        out.stored_only = True
        out.stored_only_reason = "PDF'de okunabilir metin bulunamadı (OCR kapalı veya başarısız)."
        return out
    mode = getattr(settings, "pdf_parser", "pymupdf")
    if mode in ("hybrid", "docling"):
        _apply_docling(out, ctx, mode)
    return out


_MATH_PI_FONT = re.compile(r"mathpi|greekwithmath|isoams|mt-?extra|cmsy|msam|msbm", re.IGNORECASE)


def _symbol_suspects(page: "pymupdf.Page") -> list[tuple[str, str]]:
    """Glyphs whose meaning depends on a font without a reliable Unicode map: Symbol-encoded
    fonts ("•" for ∞) and Math-Pi fonts (ISO 53 prints "≤" that the text layer stores as "<")."""
    found = []
    for b in page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT).get("blocks", []):
        for ln in b.get("lines", []):
            for sp in ln.get("spans", []):
                font = sp.get("font", "")
                if _MATH_PI_FONT.search(font):
                    found += [(font, c) for c in sp.get("text", "") if not c.isspace()]
                elif "symbol" in font.lower():
                    found += [(font, c) for c in _SUSPECT_GLYPH.findall(sp.get("text", "")) if ord(c) < 0x370
                              or ord(c) > 0x3FF]
    return found


def _apply_docling(out: ExtractionOutput, ctx: ExtractContext, mode: str) -> None:
    """Merge Docling's structure into the native pages (hybrid: tables only; docling: all blocks)."""
    from app.ingestion.parsing import docling_adapter as D

    settings = ctx.settings
    cache = ctx.storage.root / "indexes" / "docling-cache" / (
        f"{ctx.sha256}{'-formulas' if settings.docling_formula_enrichment else ''}.json")
    try:
        if cache.exists():
            payload = D.load(cache)
        else:
            payload = D.run_docling(ctx.abs_path, cache, settings.docling_python,
                                    formulas=settings.docling_formula_enrichment,
                                    timeout=settings.docling_timeout_seconds)
    except Exception as exc:
        out.warnings.append(f"Docling kullanılamadı, PyMuPDF yapısı kullanıldı: {exc}"[:300])
        out.metadata["docling"] = {"status": "unavailable", "error": str(exc)[:300]}
        return
    lexicon = symbol_lexicon("\n".join(p.text for p in out.pages))
    dpages = D.to_pages(payload, lexicon=lexicon)
    version = D.parser_version(payload)
    replaced = 0
    for p in out.pages:
        dblocks = dpages.get(p.page_number)
        if dblocks is None or p.method != "native_text":
            continue
        if mode == "docling":
            p.blocks = dblocks
        else:
            replaced += _replace_tables(p, [b for b in dblocks if b.kind == "table"])
        p.parser, p.parser_version = f"{mode}", f"{p.parser_version}; {version}"
    out.parser = mode
    out.parser_version = f"{out.parser_version}; {version}"
    out.metadata["docling"] = {"status": "ok", "mode": mode, "tables_replaced": replaced,
                               "seconds": payload.get("runner", {}).get("seconds"),
                               "options": payload.get("runner", {}).get("options")}


def _replace_tables(page: PageOut, dtables: list[Block]) -> int:
    """Swap native table grids for Docling's (matched by table label, else by order)."""
    native = [i for i, b in enumerate(page.blocks) if b.kind == "table"]
    if not native or not dtables:
        return 0
    by_label = {b.label: b for b in dtables if b.label}
    n = 0
    for pos, i in enumerate(native):
        nb = page.blocks[i]
        db = by_label.get(nb.label) if nb.label else None
        if db is None and len(native) == len(dtables):
            db = dtables[pos]
        if db is not None:
            caption = nb.text.split("\n", 1)[0] if nb.label and not db.label else None
            text = db.text if not caption else f"{caption}\n{db.text}"
            page.blocks[i] = Block("table", text, label=nb.label or db.label, source="docling:table")
            n += 1
    return n


def render_page_png(data: bytes, page_number: int, dpi: int = 110) -> bytes:
    doc = pymupdf.open(stream=data, filetype="pdf")
    if page_number < 1 or page_number > len(doc):
        raise IndexError("Sayfa yok")
    pix = doc[page_number - 1].get_pixmap(dpi=dpi)
    return pix.tobytes("png")
