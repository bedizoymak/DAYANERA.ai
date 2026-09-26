"""Database-backed ingestion job queue with background worker threads.

Jobs survive restarts (interrupted 'running' jobs are re-queued on start).
User uploads/attachments are processed before watched-corpus jobs, and smaller
files first so that quick native-text standards become available early.
"""
from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, text, update

from app.core.config import Settings
from app.db.models import IngestionJob
from app.db.session import session_scope
from app.ingestion.pipeline import process_version

log = logging.getLogger(__name__)

_CLAIM_SQL = text(
    """
    SELECT j.id FROM ingestion_jobs j
    JOIN documents d ON d.id = j.document_id
    JOIN document_versions v ON v.id = j.version_id
    WHERE j.status = 'queued'
    ORDER BY (d.source_kind = 'watched'), v.size_bytes, j.created_at
    FOR UPDATE OF j SKIP LOCKED LIMIT 1
    """
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def claim_next() -> tuple[uuid.UUID, uuid.UUID] | None:
    with session_scope() as db:
        row = db.execute(_CLAIM_SQL).first()
        if row is None:
            return None
        job = db.get(IngestionJob, row[0])
        job.status = "running"
        job.attempts += 1
        job.started_at = _now()
        return job.id, job.version_id


def run_job(job_id: uuid.UUID, version_id: uuid.UUID, settings: Settings) -> str:
    status = "failed"
    error = None
    try:
        with session_scope() as db:
            status = process_version(db, version_id, settings=settings)
    except Exception as exc:  # pragma: no cover - unexpected crash
        log.exception("İş başarısız: %s", job_id)
        error = f"{type(exc).__name__}: {exc}"[:1000]
    with session_scope() as db:
        job = db.get(IngestionJob, job_id)
        job.status = "failed" if status == "failed" or error else "done"
        job.error = error
        job.finished_at = _now()
    return status


def run_pending(settings: Settings, max_jobs: int = 10_000) -> int:
    """Synchronously process queued jobs (CLI reindex, tests)."""
    n = 0
    while n < max_jobs:
        claimed = claim_next()
        if claimed is None:
            break
        run_job(*claimed, settings)
        n += 1
    return n


def queue_counts() -> dict[str, int]:
    with session_scope() as db:
        rows = db.execute(select(IngestionJob.status, func.count()).group_by(IngestionJob.status)).all()
    return {s: c for s, c in rows}


class JobWorker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self.current: dict[str, str] = {}
        self.processed = 0

    def start(self) -> None:
        with session_scope() as db:
            n = db.execute(
                update(IngestionJob).where(IngestionJob.status == "running").values(status="queued")
            ).rowcount
            if n:
                log.info("%d yarım kalmış alım işi yeniden kuyruğa alındı.", n)
        for i in range(max(1, self.settings.ingestion_workers)):
            t = threading.Thread(target=self._loop, name=f"ingest-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        self._wake.set()

    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def _loop(self) -> None:
        name = threading.current_thread().name
        while not self._stop.is_set():
            try:
                claimed = claim_next()
            except Exception:
                log.exception("İş kuyruğu okunamadı")
                claimed = None
            if claimed is None:
                self._wake.wait(timeout=5.0)
                self._wake.clear()
                continue
            job_id, version_id = claimed
            self.current[name] = str(version_id)
            try:
                run_job(job_id, version_id, self.settings)
                self.processed += 1
            finally:
                self.current.pop(name, None)
