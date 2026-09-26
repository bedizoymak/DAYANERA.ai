"""Raw artifact storage below DATA_ROOT.

Layout (all gitignored):
  data/document-versions/<document_id>/v0001_<sha12><ext>   immutable raw bytes
  data/documents/<document_id>/manifest.json                human-readable lineage
  data/media/<version_id>/...                               previews, frames
  data/indexes/ocr-cache/<sha256>/page-0001.json            OCR/transcript cache
Raw versions are never overwritten or deleted by the application.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from app.core.config import Settings
from app.core.paths import fs, safe_join

CHUNK = 1024 * 1024


def sha256_file(path: str | os.PathLike[str]) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(fs(path), "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
            size += len(b)
    return h.hexdigest(), size


def read_head(path: str | os.PathLike[str], n: int = 8192) -> bytes:
    with open(fs(path), "rb") as f:
        return f.read(n)


class Storage:
    def __init__(self, settings: Settings):
        self.root = settings.data_root

    def version_relpath(self, document_id: uuid.UUID, version_number: int, sha: str, ext: str) -> str:
        ext = ext if ext and len(ext) <= 12 and ext.replace(".", "").isalnum() else ".bin"
        return f"document-versions/{document_id}/v{version_number:04d}_{sha[:12]}{ext}"

    def abs(self, relpath: str) -> Path:
        return safe_join(self.root, *relpath.split("/"))

    def store_from_path(self, src: str | os.PathLike[str], relpath: str) -> Path:
        dst = self.abs(relpath)
        if os.path.exists(fs(dst)):
            return dst  # immutable: identical version already stored
        os.makedirs(fs(dst.parent), exist_ok=True)
        tmp = str(dst) + ".partial"
        shutil.copyfile(fs(src), fs(tmp))
        os.replace(fs(tmp), fs(dst))
        return dst

    def store_from_stream(self, stream: BinaryIO, relpath_factory, max_bytes: int) -> tuple[Path, str, int, str]:
        """Stream an upload to a temp file, hash it, then move it into place.

        ``relpath_factory(sha)`` returns the final relative path once the hash
        is known. Raises ValueError when ``max_bytes`` is exceeded.
        """
        tmp_dir = self.root / "tmp"
        os.makedirs(fs(tmp_dir), exist_ok=True)
        tmp = tmp_dir / f"upload-{uuid.uuid4().hex}.partial"
        h = hashlib.sha256()
        size = 0
        try:
            with open(fs(tmp), "wb") as out:
                while True:
                    b = stream.read(CHUNK)
                    if not b:
                        break
                    size += len(b)
                    if size > max_bytes:
                        raise ValueError("Dosya izin verilen en büyük boyutu aşıyor (UPLOAD_MAX_BYTES).")
                    h.update(b)
                    out.write(b)
            sha = h.hexdigest()
            rel = relpath_factory(sha)
            dst = self.abs(rel)
            os.makedirs(fs(dst.parent), exist_ok=True)
            if os.path.exists(fs(dst)):
                os.remove(fs(tmp))
            else:
                os.replace(fs(tmp), fs(dst))
            return dst, sha, size, rel
        finally:
            if os.path.exists(fs(tmp)):
                os.remove(fs(tmp))

    def read_bytes(self, relpath: str) -> bytes:
        with open(fs(self.abs(relpath)), "rb") as f:
            return f.read()

    def media_dir(self, version_id: uuid.UUID) -> Path:
        p = self.root / "media" / str(version_id)
        os.makedirs(fs(p), exist_ok=True)
        return p

    def ocr_cache_path(self, sha: str, page: int) -> Path:
        return self.root / "indexes" / "ocr-cache" / sha / f"page-{page:04d}.json"

    def ocr_cache_get(self, sha: str, page: int) -> dict[str, Any] | None:
        p = self.ocr_cache_path(sha, page)
        if os.path.exists(fs(p)):
            try:
                with open(fs(p), encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                return None
        return None

    def ocr_cache_put(self, sha: str, page: int, data: dict[str, Any]) -> None:
        p = self.ocr_cache_path(sha, page)
        os.makedirs(fs(p.parent), exist_ok=True)
        with open(fs(p), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def write_manifest(self, document_id: uuid.UUID, manifest: dict[str, Any]) -> None:
        p = self.root / "documents" / str(document_id) / "manifest.json"
        os.makedirs(fs(p.parent), exist_ok=True)
        with open(fs(p), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2, default=str)

    def temp_dir(self) -> Path:
        p = self.root / "tmp" / f"extract-{uuid.uuid4().hex}"
        os.makedirs(fs(p), exist_ok=True)
        return p
