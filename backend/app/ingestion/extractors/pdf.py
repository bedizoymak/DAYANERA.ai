"""PDF extraction: native text per page, character-gap reconstruction for
PDFs with per-glyph positioning, and local OCR for scanned pages."""
from __future__ import annotations

import logging
import statistics

import numpy as np
import pymupdf

from app.ingestion.extractors.base import ExtractContext, ExtractionOutput, PageOut, normalize_text
from app.ingestion.ocr import OcrEngine

log = logging.getLogger(__name__)

MIN_NATIVE_CHARS = 40


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


def extract_pdf(data: bytes, ctx: ExtractContext) -> ExtractionOutput:
    out = ExtractionOutput()
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"PDF açılamadı: {exc}") from exc
    if doc.needs_pass:
        out.stored_only = True
        out.stored_only_reason = "Parola korumalı PDF: yalnızca arşivlendi, içerik çıkarılamadı."
        return out
    meta = {k: v for k, v in (doc.metadata or {}).items() if v}
    out.metadata = {"pdf_metadata": meta, "page_count": len(doc)}
    settings = ctx.settings
    ocr_pages = 0
    ocr_skipped = 0
    for idx, page in enumerate(doc):
        n = idx + 1
        text = normalize_text(page.get_text("text"))
        method = "native_text"
        if is_degenerate(text):
            rebuilt = normalize_text(rebuild_from_chars(page))
            if rebuilt and not is_degenerate(rebuilt):
                text, method = rebuilt, "native_text_rebuilt"
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
                    text, method = ocr_text, "ocr"
            elif len(text) == 0:
                ocr_skipped += 1
        if text:
            out.pages.append(PageOut(page_number=n, text=text, method=method, locator=f"s. {n}", ocr_confidence=conf))
    if ocr_pages:
        out.warnings.append(f"{ocr_pages} sayfa yerel OCR ile okundu: içerik 'Taslak çıkarım' olarak işaretlendi.")
    if ocr_skipped:
        out.warnings.append(f"{ocr_skipped} sayfa OCR sınırı/ayarı nedeniyle okunmadı.")
    out.metadata["ocr_pages"] = ocr_pages
    if not out.pages:
        out.stored_only = True
        out.stored_only_reason = "PDF'de okunabilir metin bulunamadı (OCR kapalı veya başarısız)."
    return out


def render_page_png(data: bytes, page_number: int, dpi: int = 110) -> bytes:
    doc = pymupdf.open(stream=data, filetype="pdf")
    if page_number < 1 or page_number > len(doc):
        raise IndexError("Sayfa yok")
    pix = doc[page_number - 1].get_pixmap(dpi=dpi)
    return pix.tobytes("png")
