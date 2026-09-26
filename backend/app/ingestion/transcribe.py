"""Local speech-to-text adapter (faster-whisper, CPU, offline).

The model is loaded ONLY from ``DATA_ROOT/models/faster-whisper-<size>``
(fetched once by scripts/fetch-local-models.ps1). Runtime never downloads.
Transcripts are labelled ``draft_extraction``.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

from app.core.config import Settings
from app.core.paths import fs

log = logging.getLogger(__name__)


class TranscriptionUnavailable(RuntimeError):
    pass


@dataclass
class Segment:
    start: float
    end: float
    text: str
    avg_logprob: float | None = None


class Transcriber:
    _instance: "Transcriber | None" = None
    _lock = threading.Lock()

    def __init__(self, settings: Settings):
        model_dir = settings.whisper_model_dir
        if not os.path.exists(fs(model_dir / "model.bin")):
            raise TranscriptionUnavailable(
                f"Yerel konuşma tanıma modeli bulunamadı ({model_dir}). "
                "Kurulum için: powershell -File scripts\\fetch-local-models.ps1"
            )
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from faster_whisper import WhisperModel

        self.model = WhisperModel(
            str(model_dir), device="cpu", compute_type=settings.whisper_compute_type, local_files_only=True
        )
        self.run_lock = threading.Lock()
        self.language: str | None = None

    @classmethod
    def get(cls, settings: Settings) -> "Transcriber":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = Transcriber(settings)
        return cls._instance

    def transcribe(self, path: str, max_seconds: float) -> tuple[list[Segment], dict]:
        with self.run_lock:
            segments, info = self.model.transcribe(
                fs(path), beam_size=1, vad_filter=True, condition_on_previous_text=False
            )
            out: list[Segment] = []
            for s in segments:
                if s.start > max_seconds:
                    break
                out.append(Segment(start=s.start, end=s.end, text=s.text.strip(), avg_logprob=s.avg_logprob))
        meta = {"language": info.language, "language_probability": round(info.language_probability, 3),
                "duration": info.duration}
        return out, meta
