"""Safe archive handling (zip, tar).

Every member is inventoried. Extraction happens only into a controlled
temporary folder below DATA_ROOT/tmp and is blocked for: absolute paths,
drive letters, '..' traversal (zip-slip), symlinks/hardlinks/devices,
too many members, oversize members, excessive total size and suspicious
compression ratios (zip bombs). Nested archives are catalogued only.
The temporary folder is always removed afterwards.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import posixpath
import shutil
import tarfile
import zipfile

from app.core.paths import fs, safe_join
from app.ingestion.detect import detect
from app.ingestion.extractors.base import ExtractContext, ExtractionOutput, PageOut

log = logging.getLogger(__name__)

TEXT_BEARING = {"pdf", "docx", "xlsx", "pptx", "text"}


def _unsafe_reason(name: str) -> str | None:
    n = name.replace("\\", "/")
    if n.startswith("/") or (len(n) > 1 and n[1] == ":"):
        return "mutlak yol engellendi"
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return "üst dizine çıkış (zip-slip) engellendi"
    if posixpath.normpath(n).startswith(".."):
        return "üst dizine çıkış (zip-slip) engellendi"
    return None


def extract_archive(data: bytes, ctx: ExtractContext, kind: str) -> ExtractionOutput:
    from app.ingestion.pipeline import extract_bytes  # local import to avoid a cycle

    s = ctx.settings
    out = ExtractionOutput()
    members: list[dict] = []
    tmp = ctx.storage.temp_dir()
    total_uncompressed = 0
    try:
        if kind == "zip":
            zf = zipfile.ZipFile(io.BytesIO(data))
            infos = zf.infolist()
            entries = [(i.filename, i.is_dir(), i.file_size, i.compress_size, i) for i in infos]
        else:
            tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
            infos = tf.getmembers()
            entries = [(i.name, i.isdir(), i.size, None, i) for i in infos]
        if len(entries) > s.archive_max_members:
            out.stored_only = True
            out.stored_only_reason = (f"Arşivde {len(entries)} üye var; sınır {s.archive_max_members} "
                                      "(ARCHIVE_MAX_MEMBERS). Güvenlik nedeniyle açılmadı, yalnızca arşivlendi.")
            out.metadata = {"member_count": len(entries)}
            return out
        page_no = 0
        for name, is_dir, size, csize, info in entries:
            rec = {"member_path": name[:1000], "is_dir": is_dir, "size_bytes": size, "compressed_bytes": csize,
                   "status": "catalogued", "reason": None, "mime_type": None, "sha256": None}
            members.append(rec)
            reason = _unsafe_reason(name)
            if reason is None and kind == "tar" and not (info.isfile() or info.isdir()):
                reason = "sembolik bağlantı / özel dosya engellendi"
            if reason is None and kind == "zip" and (info.external_attr >> 16) & 0o170000 == 0o120000:
                reason = "sembolik bağlantı engellendi"
            if reason:
                rec["status"], rec["reason"] = "blocked", reason
                continue
            if is_dir:
                continue
            if size > s.archive_max_member_bytes:
                rec["status"], rec["reason"] = "skipped", "üye boyut sınırını aşıyor"
                continue
            if csize and csize > 0 and size / csize > s.archive_max_compression_ratio:
                rec["status"], rec["reason"] = "blocked", "şüpheli sıkıştırma oranı (zip bomb koruması)"
                continue
            if total_uncompressed + size > s.archive_max_total_uncompressed_bytes:
                rec["status"], rec["reason"] = "skipped", "toplam açılmış boyut sınırı aşıldı"
                continue
            det = detect(name, b"")
            rec["mime_type"] = det.mime_type
            if det.category not in TEXT_BEARING and det.category != "image":
                continue  # catalogued only (media, nested archives, binaries)
            if ctx.depth >= 1:
                continue
            dst = safe_join(tmp, *[p for p in name.replace("\\", "/").split("/") if p])
            os.makedirs(fs(dst.parent), exist_ok=True)
            # bounded copy (never trust header sizes blindly)
            src = zf.open(info) if kind == "zip" else tf.extractfile(info)
            if src is None:
                continue
            written = 0
            h = hashlib.sha256()
            with src, open(fs(dst), "wb") as f:
                while True:
                    b = src.read(1024 * 1024)
                    if not b:
                        break
                    written += len(b)
                    if written > s.archive_max_member_bytes:
                        raise ValueError("Arşiv üyesi bildirilen boyutu aşıyor; açma durduruldu.")
                    h.update(b)
                    f.write(b)
            total_uncompressed += written
            rec["status"], rec["sha256"] = "extracted", h.hexdigest()
            with open(fs(dst), "rb") as f:
                member_bytes = f.read()
            sub_ctx = ExtractContext(settings=s, storage=ctx.storage, version_id=ctx.version_id,
                                     sha256=h.hexdigest(), filename=name, abs_path=str(dst), depth=ctx.depth + 1)
            try:
                sub = extract_bytes(member_bytes, det, sub_ctx)
            except Exception as exc:
                rec["reason"] = f"içerik çıkarılamadı: {type(exc).__name__}"
                continue
            for p in sub.pages:
                page_no += 1
                method = p.method if p.method in ("ocr", "transcription") else "archive_member_text"
                out.pages.append(PageOut(page_no, p.text, method, f"arşiv üyesi: {name} · {p.locator}", p.ocr_confidence))
        out.archive_members = members
        out.metadata = {"member_count": len(members), "extracted": sum(1 for m in members if m["status"] == "extracted"),
                        "blocked": sum(1 for m in members if m["status"] == "blocked"),
                        "total_uncompressed_extracted": total_uncompressed}
        blocked = out.metadata["blocked"]
        if blocked:
            out.warnings.append(f"{blocked} arşiv üyesi güvenlik nedeniyle engellendi.")
        if not out.pages:
            out.stored_only = True
            out.stored_only_reason = "Arşiv envanteri çıkarıldı; metin içeren üye bulunamadı."
        return out
    except (zipfile.BadZipFile, tarfile.TarError) as exc:
        out.stored_only, out.stored_only_reason = True, f"Arşiv okunamadı ({type(exc).__name__}); yalnızca saklandı."
        out.archive_members = members
        return out
    finally:
        shutil.rmtree(fs(tmp), ignore_errors=True)
