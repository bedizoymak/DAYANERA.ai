"""Image / audio / video processing.

- Images: metadata, preview thumbnail, local OCR (draft extraction).
- Audio: duration probe and local faster-whisper transcription (draft).
- Video: archived and catalogued; basic metadata, a few representative
  frames and optional audio transcription within configurable limits.
  Heavy video understanding is deliberately deferred to a future workflow.
The text-only Qwen model never receives raw media.
"""
from __future__ import annotations

import io
import logging
import os

import numpy as np
from PIL import Image, ImageOps

from app.core.paths import fs
from app.ingestion.extractors.base import ExtractContext, ExtractionOutput, PageOut, normalize_text
from app.ingestion.ocr import OcrEngine
from app.ingestion.transcribe import Segment, Transcriber, TranscriptionUnavailable

log = logging.getLogger(__name__)


def _fmt_ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def extract_image(data: bytes, ctx: ExtractContext) -> ExtractionOutput:
    out = ExtractionOutput()
    Image.MAX_IMAGE_PIXELS = ctx.settings.image_max_pixels
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Image.DecompressionBombError:
        out.stored_only = True
        out.stored_only_reason = "Görüntü piksel sınırını aşıyor (IMAGE_MAX_PIXELS); yalnızca arşivlendi."
        return out
    except Exception as exc:
        raise ValueError(f"Görüntü açılamadı: {exc}") from exc
    frames = getattr(img, "n_frames", 1)
    out.metadata = {"width": img.width, "height": img.height, "mode": img.mode, "format": img.format, "frames": frames}
    img = ImageOps.exif_transpose(img).convert("RGB")
    thumb = img.copy()
    thumb.thumbnail((1024, 1024))
    media_dir = ctx.storage.media_dir(ctx.version_id)
    thumb_path = media_dir / "preview.jpg"
    thumb.save(fs(thumb_path), "JPEG", quality=85)
    out.media.append(f"media/{ctx.version_id}/preview.jpg")
    if frames > 1:
        out.warnings.append(f"Çok kareli görüntü: yalnızca ilk kare işlendi ({frames} kare).")
    if not ctx.settings.ocr_enabled:
        out.stored_only, out.stored_only_reason = True, "OCR kapalı (OCR_ENABLED=false): görüntü yalnızca arşivlendi."
        return out
    engine = OcrEngine.get()
    if engine is None:
        out.stored_only, out.stored_only_reason = True, "Yerel OCR motoru yüklenemedi; görüntü yalnızca arşivlendi."
        return out
    cached = ctx.storage.ocr_cache_get(ctx.sha256, 1)
    if cached is None:
        work = img
        if max(img.size) > 3000:
            work = img.copy()
            work.thumbnail((3000, 3000))
        res = engine.recognize(np.ascontiguousarray(np.array(work)))
        cached = {"text": res.text, "confidence": res.mean_confidence, "engine": "rapidocr"}
        ctx.storage.ocr_cache_put(ctx.sha256, 1, cached)
    text = normalize_text(cached.get("text", ""))
    if text:
        out.pages.append(PageOut(1, text, "ocr", "görüntü", cached.get("confidence")))
        out.warnings.append("Görüntü metni yerel OCR ile okundu: 'Taslak çıkarım' (onay gerektirir).")
    else:
        out.stored_only, out.stored_only_reason = True, "Görüntüde okunabilir metin bulunamadı; görsel yalnızca arşivlendi."
    return out


def _probe_av(path: str) -> dict:
    import av

    meta: dict = {"streams": []}
    with av.open(path) as c:
        meta["format"] = c.format.name if c.format else None
        meta["duration_seconds"] = round(c.duration / 1_000_000, 2) if c.duration else None
        for s in c.streams:
            info = {"type": s.type, "codec": s.codec_context.name if s.codec_context else None}
            if s.type == "video":
                info.update({"width": s.codec_context.width, "height": s.codec_context.height,
                             "fps": float(s.average_rate) if s.average_rate else None})
            elif s.type == "audio":
                info.update({"sample_rate": s.codec_context.sample_rate,
                             "channels": getattr(s.codec_context, "channels", None)})
            meta["streams"].append(info)
    return meta


def _transcript_pages(segments: list[Segment], window: float = 60.0) -> list[PageOut]:
    pages: list[PageOut] = []
    buf: list[Segment] = []
    for seg in segments:
        if buf and seg.start - buf[0].start >= window:
            pages.append(_seg_page(len(pages) + 1, buf))
            buf = []
        buf.append(seg)
    if buf:
        pages.append(_seg_page(len(pages) + 1, buf))
    return pages


def _seg_page(n: int, segs: list[Segment]) -> PageOut:
    text = "\n".join(f"[{_fmt_ts(s.start)}] {s.text}" for s in segs if s.text)
    return PageOut(n, text, "transcription", f"{_fmt_ts(segs[0].start)}–{_fmt_ts(segs[-1].end)}")


def _transcribe(ctx: ExtractContext, out: ExtractionOutput, max_seconds: float) -> None:
    if not ctx.settings.transcription_enabled:
        out.warnings.append("Ses dökümü kapalı (TRANSCRIPTION_ENABLED=false).")
        return
    try:
        tr = Transcriber.get(ctx.settings)
    except TranscriptionUnavailable as exc:
        out.warnings.append(str(exc))
        return
    cached = ctx.storage.ocr_cache_get(ctx.sha256, 0)
    if cached is None:
        segs, info = tr.transcribe(ctx.abs_path, max_seconds)
        cached = {"segments": [s.__dict__ for s in segs], "info": info, "engine": "faster-whisper"}
        ctx.storage.ocr_cache_put(ctx.sha256, 0, cached)
    segs = [Segment(**s) for s in cached["segments"]]
    out.metadata["transcription"] = cached.get("info", {})
    out.pages.extend(_transcript_pages(segs))
    if out.pages:
        out.warnings.append("Ses yerel olarak yazıya döküldü: 'Taslak çıkarım' (onay gerektirir).")


def extract_audio(data: bytes, ctx: ExtractContext) -> ExtractionOutput:
    out = ExtractionOutput()
    try:
        out.metadata = _probe_av(ctx.abs_path)
    except Exception as exc:
        out.stored_only, out.stored_only_reason = True, f"Ses dosyası çözümlenemedi ({type(exc).__name__}); yalnızca arşivlendi."
        return out
    dur = out.metadata.get("duration_seconds") or 0
    if dur and dur > ctx.settings.audio_max_seconds:
        out.warnings.append(f"Ses {int(dur)} sn; yalnızca ilk {ctx.settings.audio_max_seconds} sn yazıya dökülür.")
    _transcribe(ctx, out, ctx.settings.audio_max_seconds)
    if not out.pages:
        out.stored_only = True
        out.stored_only_reason = out.stored_only_reason or "Ses dökümü üretilemedi; dosya arşivlendi ve kataloglandı."
    return out


def extract_video(data: bytes, ctx: ExtractContext) -> ExtractionOutput:
    import av

    out = ExtractionOutput()
    try:
        out.metadata = _probe_av(ctx.abs_path)
    except Exception as exc:
        out.stored_only, out.stored_only_reason = True, f"Video çözümlenemedi ({type(exc).__name__}); yalnızca arşivlendi."
        return out
    dur = out.metadata.get("duration_seconds") or 0
    # representative frames
    n = max(0, ctx.settings.video_frame_count)
    if n and any(s["type"] == "video" for s in out.metadata["streams"]):
        media_dir = ctx.storage.media_dir(ctx.version_id)
        try:
            with av.open(ctx.abs_path) as c:
                vs = c.streams.video[0]
                targets = [dur * (i + 1) / (n + 1) for i in range(n)] if dur else [0.0]
                for i, t in enumerate(targets):
                    if vs.time_base:
                        c.seek(int(t / float(vs.time_base)), stream=vs, any_frame=False, backward=True)
                    for frame in c.decode(vs):
                        img = frame.to_image()
                        img.thumbnail((640, 640))
                        name = f"frame-{i + 1:02d}.jpg"
                        img.save(fs(media_dir / name), "JPEG", quality=80)
                        out.media.append(f"media/{ctx.version_id}/{name}")
                        break
        except Exception as exc:
            out.warnings.append(f"Temsili kareler çıkarılamadı ({type(exc).__name__}).")
    has_audio = any(s["type"] == "audio" for s in out.metadata["streams"])
    if has_audio:
        if dur and dur > ctx.settings.video_max_transcribe_seconds:
            out.warnings.append(
                f"Video {int(dur)} sn; ses dökümü sınırı {ctx.settings.video_max_transcribe_seconds} sn "
                "(VIDEO_MAX_TRANSCRIBE_SECONDS). Ağır video analizi gelecekteki API iş akışına bırakıldı."
            )
        else:
            _transcribe(ctx, out, ctx.settings.video_max_transcribe_seconds)
    out.warnings.append("Video arşivlendi ve kataloglandı; görüntü içeriği anlamsal olarak analiz edilmez (beta sınırı).")
    if not out.pages:
        out.stored_only = True
        out.stored_only_reason = "Video yalnızca arşivlendi ve kataloglandı (meta veri + temsili kareler)."
    return out


def file_exists(path: str) -> bool:
    return os.path.exists(fs(path))
