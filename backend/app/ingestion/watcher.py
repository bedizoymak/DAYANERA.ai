"""Application-managed folder watcher (polling; robust on Windows long paths).

Detects new, changed and removed files below the configured roots
(``ISO_BOOKLETS_PATH`` plus ``EXTRA_WATCH_ROOTS``). Changed files become new
immutable versions; removed files are logically deleted (raw versions kept)
and excluded from retrieval.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.config import Settings
from app.core.paths import fs, strip_long_prefix
from app.db.models import Document, DocumentVersion, SystemState
from app.db.session import session_scope
from app.ingestion.pipeline import area_by_slug, mark_document_deleted, register_version_from_path
from app.ingestion.standard_code import clean_title
from app.ingestion.storage import sha256_file
from app.services import audit
from app.services.audit import Actor

log = logging.getLogger(__name__)

SKIP_PREFIXES = ("~$", ".")
SKIP_SUFFIXES = (".tmp", ".part", ".crdownload", ".partial", ".lnk")
SKIP_NAMES = {"thumbs.db", "desktop.ini"}
STABLE_SECONDS = 2.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Watcher:
    def __init__(self, settings: Settings, on_new_jobs=None):
        self.settings = settings
        self.on_new_jobs = on_new_jobs
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_scan: dict = {}
        self.last_error: str | None = None

    def roots(self) -> list[tuple[Path, str]]:
        roots = [(self.settings.iso_booklets_path, "iso-disli")]
        roots += [(p, "izlenen-diger") for p in self.settings.extra_watch_root_paths]
        return roots

    # ------------------------------------------------------------------
    def scan(self, actor: Actor | None = None) -> dict:
        actor = actor or Actor.system()
        with self._lock:
            started = time.perf_counter()
            summary = {"new": 0, "changed": 0, "unchanged": 0, "missing": 0, "reappeared": 0, "unstable": 0,
                       "errors": [], "roots": []}
            for root, slug in self.roots():
                self._scan_root(root, slug, actor, summary)
            summary["duration_ms"] = int((time.perf_counter() - started) * 1000)
            summary["finished_at"] = _now().isoformat()
            self.last_scan = summary
            try:
                with session_scope() as db:
                    st = db.get(SystemState, "watcher") or SystemState(key="watcher")
                    st.value = summary
                    st.updated_at = _now()
                    db.merge(st)
            except Exception:
                log.exception("İzleyici durumu kaydedilemedi")
            if (summary["new"] or summary["changed"] or summary["reappeared"]) and self.on_new_jobs:
                self.on_new_jobs()
            return summary

    def _scan_root(self, root: Path, slug: str, actor: Actor, summary: dict) -> None:
        root_str = str(root)
        info = {"path": root_str, "exists": os.path.isdir(fs(root)), "files": 0}
        summary["roots"].append(info)
        if not info["exists"]:
            summary["errors"].append(f"Klasör bulunamadı: {root_str}")
            return  # never mark everything deleted because a folder is temporarily unavailable
        seen: set[str] = set()
        now = time.time()
        for dirpath, _dirs, files in os.walk(fs(root)):
            for name in files:
                low = name.lower()
                if name.startswith(SKIP_PREFIXES) or low.endswith(SKIP_SUFFIXES) or low in SKIP_NAMES:
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(strip_long_prefix(full), root_str).replace("\\", "/")
                seen.add(rel)
                info["files"] += 1
                try:
                    st = os.stat(fs(full))
                    if now - st.st_mtime < STABLE_SECONDS:
                        summary["unstable"] += 1
                        continue
                    self._handle_file(root_str, rel, full, st, slug, actor, summary)
                except Exception as exc:
                    log.exception("İzlenen dosya işlenemedi: %s", rel)
                    summary["errors"].append(f"{rel}: {type(exc).__name__}")
        with session_scope() as db:
            docs = db.execute(select(Document).where(Document.source_kind == "watched",
                                                     Document.source_root == root_str,
                                                     Document.status != "deleted")).scalars().all()
            for doc in docs:
                if doc.source_relpath not in seen:
                    mark_document_deleted(db, doc, actor, reason="source_missing: dosya izlenen klasörden kaldırıldı",
                                          source_missing=True)
                    summary["missing"] += 1

    def _handle_file(self, root: str, rel: str, full: str, st: os.stat_result, slug: str, actor: Actor,
                     summary: dict) -> None:
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        with session_scope() as db:
            doc = db.execute(select(Document).where(Document.source_kind == "watched", Document.source_root == root,
                                                    Document.source_relpath == rel)).scalar_one_or_none()
            if doc is None:
                name = os.path.basename(rel)
                doc = Document(knowledge_area_id=area_by_slug(db, slug).id, title=clean_title(name, None),
                               source_kind="watched", source_root=root, source_relpath=rel, original_filename=name,
                               status="pending", created_by=None)
                db.add(doc)
                db.flush()
                register_version_from_path(db, self.settings, doc, full, actor=actor, source_mtime=mtime)
                summary["new"] += 1
                return
            latest = db.execute(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
                                .order_by(DocumentVersion.version_number.desc())).scalars().first()
            same_stat = (latest is not None and latest.size_bytes == st.st_size and latest.source_mtime is not None
                         and abs((latest.source_mtime - mtime).total_seconds()) < 1.0)
            if same_stat and not (doc.status == "deleted" and (doc.delete_reason or "").startswith("source_missing")):
                summary["unchanged"] += 1
                return
            sha, size = sha256_file(full)
            reappeared = doc.status == "deleted" and (doc.delete_reason or "").startswith("source_missing")
            if latest is not None and sha == latest.sha256 and not reappeared:
                latest.source_mtime = mtime  # touch only; content identical
                summary["unchanged"] += 1
                return
            if reappeared:
                doc.status = "pending"
                doc.deleted_at = None
                doc.delete_reason = None
                audit.record(db, actor, "document.source_reappeared", target_type="document", target_id=doc.id,
                             details={"relpath": rel})
                summary["reappeared"] += 1
            else:
                summary["changed"] += 1
            register_version_from_path(db, self.settings, doc, full, actor=actor, source_mtime=mtime, sha=sha, size=size)

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="folder-watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan()
                self.last_error = None
            except Exception as exc:  # pragma: no cover - environment dependent
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("İzleyici taraması başarısız")
            self._stop.wait(timeout=max(5, self.settings.watcher_interval_seconds))
