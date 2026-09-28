"""System status (no secrets) and future-Supabase readiness."""
from __future__ import annotations

import shutil
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.paths import dir_size_bytes
from app.db.models import Document, DocumentVersion, ExtractedValue, IngestionJob, KnowledgeArea
from app.db.session import current_migration_revision, database_ping
from app.domain.enums import ACTIVE_EVIDENCE_STATUSES, CorpusState
from app.inference.base import LLMProvider
from app.inference.registry import provider_status
from app.services.self_maintenance import stats as knowledge_stats


def supabase_status(settings: Settings) -> dict[str, Any]:
    """Read local sync status only; never return credentials or contact cloud."""
    from app.services.database_sync import LOCAL_ONLY, TABLES, read_status
    state = read_status(settings)
    return {
        "enabled_flag": settings.supabase_enabled,
        "url_configured": bool(settings.supabase_url.strip()),
        "publishable_key_configured": bool(settings.supabase_publishable_key.get_secret_value()),
        "secret_key_configured": bool(settings.supabase_secret_key.get_secret_value()),
        "database_url_configured": bool(settings.supabase_database_url.get_secret_value()),
        "sync_implemented": True,
        "network_calls": "background_sync" if settings.supabase_enabled else "none",
        "data_sent": bool(state.get("last_success")),
        "direction": "bidirectional",
        "interval_seconds": settings.supabase_sync_interval_seconds,
        "sync": state,
        "tables": TABLES,
        "local_only_tables": LOCAL_ONLY,
        "files_synced": False,
        "note": ("Çift yönlü veritabanı senkronizasyonu. Çakışmada aktarım durur; iki sürüm korunur. "
                 "Dosyalar, oturumlar, iş kuyruğu ve denetim günlüğü yerel kalır."),
    }


def _corpus_state(indexed_documents: int, indexed_chunks: int, approved_versions: int,
                  eligible_chunks: int) -> CorpusState:
    """Classify availability without conflating indexing with approval."""
    if not indexed_documents and not indexed_chunks:
        return CorpusState.EMPTY
    if approved_versions and eligible_chunks:
        return CorpusState.READY
    return CorpusState.INDEXED_UNAPPROVED


def corpus_status(db: Session) -> dict[str, Any]:
    area_rows = db.execute(
        select(KnowledgeArea.slug, Document.status, func.count())
        .join(Document, Document.knowledge_area_id == KnowledgeArea.id)
        .group_by(KnowledgeArea.slug, Document.status)
    ).all()
    areas: dict[str, dict[str, int]] = {}
    for slug, status, n in area_rows:
        areas.setdefault(slug, {})[status] = n
    ing = dict(db.execute(select(DocumentVersion.ingestion_status, func.count())
                          .where(DocumentVersion.is_active.is_(True)).group_by(DocumentVersion.ingestion_status)).all())
    pages = dict(db.execute(text("SELECT confidence_status, count(*) FROM document_pages GROUP BY 1")).all())
    indexed_documents = db.execute(text("""
        SELECT count(*) FROM documents d
        JOIN document_versions v ON v.id = d.current_version_id
        JOIN knowledge_areas ka ON ka.id = d.knowledge_area_id
        WHERE ka.is_verified_corpus AND d.status = 'active'
          AND v.is_active AND v.state = 'active' AND v.ingestion_status = 'indexed'
    """)).scalar() or 0
    indexed_chunks = db.execute(text("""
        SELECT count(*) FROM document_chunks c
        JOIN document_versions v ON v.id = c.version_id
        JOIN documents d ON d.id = c.document_id AND d.current_version_id = v.id
        JOIN knowledge_areas ka ON ka.id = d.knowledge_area_id
        WHERE ka.is_verified_corpus AND d.status = 'active'
          AND v.is_active AND v.state = 'active' AND v.ingestion_status = 'indexed'
    """)).scalar() or 0
    approved_versions = db.execute(text("""
        SELECT count(*) FROM documents d
        JOIN document_versions v ON v.id = d.current_version_id
        JOIN knowledge_areas ka ON ka.id = d.knowledge_area_id
        WHERE ka.is_verified_corpus AND d.status = 'active'
          AND v.is_active AND v.state = 'active' AND v.ingestion_status = 'indexed'
          AND v.corpus_status = 'verified'
    """)).scalar() or 0
    chunks_active = db.execute(text("""
        SELECT count(*) FROM document_chunks c JOIN document_versions v ON v.id = c.version_id
        JOIN documents d ON d.id = c.document_id AND d.current_version_id = v.id
        JOIN knowledge_areas ka ON ka.id = d.knowledge_area_id
        WHERE ka.is_verified_corpus AND d.status = 'active' AND v.is_active AND v.ingestion_status = 'indexed'
          AND v.state = 'active' AND v.corpus_status = 'verified'
          AND c.confidence_status = ANY(:evidence_statuses)"""),
        {"evidence_statuses": list(ACTIVE_EVIDENCE_STATUSES)}).scalar() or 0
    corpus = dict(db.execute(select(DocumentVersion.corpus_status, func.count())
                             .where(DocumentVersion.is_active.is_(True)).group_by(DocumentVersion.corpus_status)).all())
    jobs = dict(db.execute(select(IngestionJob.status, func.count()).group_by(IngestionJob.status)).all())
    drafts = db.execute(select(func.count()).select_from(ExtractedValue)
                        .where(ExtractedValue.status == "draft_extraction")).scalar()
    failed = db.execute(select(Document.title, DocumentVersion.ingestion_error)
                        .join(DocumentVersion, DocumentVersion.document_id == Document.id)
                        .where(DocumentVersion.ingestion_status == "failed").limit(10)).all()
    state = _corpus_state(indexed_documents, indexed_chunks, approved_versions, chunks_active)
    return {
        "areas": areas, "active_version_ingestion": ing, "active_version_corpus": corpus, "pages_by_status": pages,
        "state": state.value,
        "indexed_active_documents": indexed_documents,
        "indexed_active_chunks": indexed_chunks,
        "owner_approved_active_versions": approved_versions,
        "verified_active_chunks": chunks_active,
        "jobs": jobs, "draft_values_pending": drafts,
        "failed": [{"title": t, "error": e} for t, e in failed],
        # Deprecated compatibility field. The structured state is authoritative;
        # an indexed-but-unapproved corpus is not empty.
        "empty_verified_corpus": state is CorpusState.EMPTY,
    }


_STORAGE_CACHE: dict[str, Any] = {"at": 0.0, "value": None}


def storage_status(settings: Settings) -> dict[str, Any]:
    """Disk usage of the runtime folders (cached for 60 s: walking data/ is not free)."""
    import time

    if _STORAGE_CACHE["value"] is not None and time.monotonic() - _STORAGE_CACHE["at"] < 60:
        return _STORAGE_CACHE["value"]
    value = _storage_status_uncached(settings)
    _STORAGE_CACHE.update(at=time.monotonic(), value=value)
    return value


def _storage_status_uncached(settings: Settings) -> dict[str, Any]:
    root = settings.data_root
    subdirs = ["documents", "document-versions", "media", "indexes", "exports", "logs", "backups", "models"]
    sizes = {s: dir_size_bytes(root / s) for s in subdirs}
    try:
        du = shutil.disk_usage(str(settings.project_root))
        disk = {"total_bytes": du.total, "free_bytes": du.free}
    except OSError:
        disk = {}
    return {"data_root": str(root), "sizes_bytes": sizes, "total_bytes": sum(sizes.values()), "disk": disk}


def system_status(db: Session, settings: Settings, provider: LLMProvider, watcher, worker, *, is_owner: bool,
                  path_warnings: list[str]) -> dict[str, Any]:
    db_ok, db_err = database_ping()
    health = provider.health()
    status: dict[str, Any] = {
        "app": {"name": settings.app_name, "env": settings.app_env,
                "bind": f"{settings.app_host}:{settings.app_port}", "localhost_only": True},
        "database": {"ok": db_ok, "error": db_err, "migration_revision": current_migration_revision(),
                     "host": settings.postgres_host, "port": settings.postgres_port, "kind": "local Docker PostgreSQL"},
        "ollama": {"reachable": health.reachable, "model": health.model, "model_available": health.model_available,
                   "version": health.version, "detail": health.detail, "base_url": settings.ollama_base_url},
        "watcher": {"enabled": settings.watcher_enabled, "running": bool(watcher and watcher.running),
                    "interval_seconds": settings.watcher_interval_seconds,
                    "roots": [str(p) for p, _ in watcher.roots()] if watcher else [],
                    "last_scan": watcher.last_scan if watcher else None,
                    "last_error": watcher.last_error if watcher else None},
        "worker": {"running": bool(worker and worker.running), "current": dict(worker.current) if worker else {},
                   "processed_since_start": worker.processed if worker else 0},
        "capabilities": {"ocr": settings.ocr_enabled,
                         "transcription": settings.transcription_enabled
                         and (settings.whisper_model_dir / "model.bin").exists(),
                         "whisper_model_dir_present": (settings.whisper_model_dir / "model.bin").exists()},
        "warnings": path_warnings,
    }
    if db_ok:
        status["corpus"] = corpus_status(db)
        status["self_maintenance"] = knowledge_stats(db)
    if is_owner:
        status["storage"] = storage_status(settings)
        status["supabase"] = supabase_status(settings)
        status["providers"] = provider_status(settings)
    return status
