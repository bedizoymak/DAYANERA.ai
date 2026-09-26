"""Local OCR adapter (RapidOCR + ONNX Runtime, CPU).

The OCR models ship inside the ``rapidocr_onnxruntime`` wheel, so no network
access happens at runtime. All OCR output is labelled ``draft_extraction``
("Taslak çıkarım") by the pipeline.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class OcrResult:
    text: str
    mean_confidence: float
    line_count: int


class OcrEngine:
    _instance: "OcrEngine | None" = None
    _init_lock = threading.Lock()

    def __init__(self):
        from rapidocr_onnxruntime import RapidOCR  # heavy import, lazily loaded

        self._engine = RapidOCR()
        self._lock = threading.Lock()  # onnxruntime sessions are used serially

    @classmethod
    def get(cls) -> "OcrEngine | None":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    try:
                        cls._instance = OcrEngine()
                    except Exception as exc:  # pragma: no cover - missing optional dependency
                        log.warning("OCR motoru yüklenemedi: %s", exc)
                        return None
        return cls._instance

    def recognize(self, image) -> OcrResult:
        """``image``: numpy array (H, W, 3) uint8 RGB/BGR."""
        with self._lock:
            result, _elapse = self._engine(image)
        if not result:
            return OcrResult(text="", mean_confidence=0.0, line_count=0)
        # group boxes into visual lines (top-to-bottom, left-to-right)
        items = []
        for box, text, score in result:
            ys = [p[1] for p in box]
            xs = [p[0] for p in box]
            items.append((sum(ys) / 4.0, min(xs), max(ys) - min(ys), text, float(score)))
        items.sort(key=lambda t: (t[0], t[1]))
        lines: list[list[tuple]] = []
        for it in items:
            if lines and abs(lines[-1][0][0] - it[0]) <= max(6.0, 0.5 * it[2]):
                lines[-1].append(it)
            else:
                lines.append([it])
        text_lines = [" ".join(t[3] for t in sorted(ln, key=lambda t: t[1])) for ln in lines]
        conf = sum(t[4] for t in items) / len(items)
        return OcrResult(text="\n".join(text_lines), mean_confidence=round(conf, 4), line_count=len(text_lines))
