"""Document ingestion pipeline: versions, extraction, indexing, lineage."""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import BinaryIO

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db.models import (
    ArchiveMember,
    Document,
    DocumentChunk,
    DocumentPage,
    DocumentRelationship,
    DocumentVersion,
    ExtractedValue,
    IngestionJob,
    KnowledgeArea,
)
from app.domain.enums import DRAFT_METHODS, ConfidenceStatus
from app.ingestion.chunker import chunk_page
from app.ingestion.detect import Detected, detect
from app.ingestion.extractors.archive import extract_archive
from app.ingestion.extractors.base import ExtractContext, ExtractionOutput
from app.ingestion.extractors.media import extract_audio, extract_image, extract_video
from app.ingestion.extractors.office import extract_docx, extract_pptx, extract_text, extract_xlsx
from app.ingestion.extractors.pdf import extract_pdf
from app.ingestion.standard_code import clean_title, detect_standard_code
from app.ingestion.storage import Storage, read_head, sha256_file
from app.ingestion.values import extract_candidates
from app.services import audit
from app.services.audit import Actor

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# extraction dispatch
# ---------------------------------------------------------------------------
def extract_bytes(data: bytes, det: Detected, ctx: ExtractContext) -> ExtractionOutput:
    c = det.category
    if c == "pdf":
        return extract_pdf(data, ctx)
    if c == "docx":
        return extract_docx(data, ctx)
    if c == "xlsx":
        return extract_xlsx(data, ctx)
    if c == "pptx":
        return extract_pptx(data, ctx)
    if c == "text":
        return extract_text(data, ctx)
    if c == "image":
        return extract_image(data, ctx)
    if c == "audio":
        return extract_audio(data, ctx)
    if c == "video":
        return extract_video(data, ctx)
    if c in ("zip", "tar"):
        return extract_archive(data, ctx, c)
    out = ExtractionOutput()
    out.stored_only = True
    if c == "office_legacy":
        out.stored_only_reason = (
            "Eski Office/ODF biçimi yerel olarak çözümlenmiyor: dosya arşivlendi ve kataloglandı. "
            "İçeriğin aranabilmesi için DOCX/XLSX/PPTX veya PDF olarak kaydedip yeniden yükleyin."
        )
    elif c == "archive_other":
        out.stored_only_reason = "7z/RAR arşivleri beta sürümde açılmaz: dosya arşivlendi. ZIP olarak yeniden paketleyin."
    else:
        out.stored_only_reason = "Desteklenmeyen dosya türü: dosya güvenle arşivlendi, içerik analizi yapılmadı."
    return out


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------
def area_by_slug(db: Session, slug: str) -> KnowledgeArea:
    area = db.execute(select(KnowledgeArea).where(KnowledgeArea.slug == slug)).scalar_one_or_none()
    if area is None:
        raise RuntimeError(f"Bilgi alanı bulunamadı: {slug} (seed çalıştırılmamış olabilir)")
    return area


def next_version_number(db: Session, document_id: uuid.UUID) -> int:
    nums = db.execute(select(DocumentVersion.version_number).where(DocumentVersion.document_id == document_id)).scalars()
    return max(nums, default=0) + 1


def _queue(db: Session, doc: Document, ver: DocumentVersion, job_type: str, requested_by: uuid.UUID | None) -> IngestionJob:
    job = IngestionJob(document_id=doc.id, version_id=ver.id, job_type=job_type, status="queued", requested_by=requested_by)
    db.add(job)
    return job


def register_version_from_path(
    db: Session,
    settings: Settings,
    doc: Document,
    src_path: str,
    *,
    actor: Actor,
    source_mtime: datetime | None,
    sha: str | None = None,
    size: int | None = None,
) -> DocumentVersion:
    storage = Storage(settings)
    if sha is None or size is None:
        sha, size = sha256_file(src_path)
    det = detect(doc.original_filename, read_head(src_path))
    vnum = next_version_number(db, doc.id)
    rel = storage.version_relpath(doc.id, vnum, sha, det.extension)
    storage.store_from_path(src_path, rel)
    ver = DocumentVersion(
        document_id=doc.id, version_number=vnum, sha256=sha, size_bytes=size, mime_type=det.mime_type,
        file_extension=det.extension, storage_relpath=rel, source_path_snapshot=str(src_path)[:2000],
        source_mtime=source_mtime, state="pending", is_active=False, ingestion_status="queued",
        metadata_={"category": det.category}, created_by=actor.user_id,
    )
    db.add(ver)
    db.flush()
    _queue(db, doc, ver, "ingest", actor.user_id)
    audit.record(db, actor, "document.version_created", target_type="document", target_id=doc.id,
                 details={"version": vnum, "sha256": sha, "size_bytes": size, "mime_type": det.mime_type,
                          "source_kind": doc.source_kind})
    return ver


def register_upload(
    db: Session,
    settings: Settings,
    *,
    stream: BinaryIO,
    filename: str,
    actor: Actor,
    area_slug: str,
    source_kind: str,
) -> tuple[Document, DocumentVersion]:
    storage = Storage(settings)
    safe_name = os.path.basename(filename.replace("\\", "/"))[:255] or "dosya"
    doc = Document(
        knowledge_area_id=area_by_slug(db, area_slug).id, title=safe_name, source_kind=source_kind,
        original_filename=safe_name, status="pending", owner_id=actor.user_id, created_by=actor.user_id,
    )
    db.add(doc)
    db.flush()
    vnum = 1
    ext = ("." + safe_name.rsplit(".", 1)[-1].lower()) if "." in safe_name else ""
    _abs, sha, size, rel = storage.store_from_stream(
        stream, lambda s: storage.version_relpath(doc.id, vnum, s, ext), settings.upload_max_bytes
    )
    det = detect(safe_name, read_head(storage.abs(rel)))
    ver = DocumentVersion(
        document_id=doc.id, version_number=vnum, sha256=sha, size_bytes=size, mime_type=det.mime_type,
        file_extension=det.extension, storage_relpath=rel, source_path_snapshot=None, state="pending",
        is_active=False, ingestion_status="queued", metadata_={"category": det.category}, created_by=actor.user_id,
    )
    db.add(ver)
    db.flush()
    _queue(db, doc, ver, "ingest", actor.user_id)
    audit.record(db, actor, "document.upload", target_type="document", target_id=doc.id,
                 details={"filename": safe_name, "sha256": sha, "size_bytes": size, "mime_type": det.mime_type,
                          "source_kind": source_kind, "area": area_slug})
    return doc, ver


# ---------------------------------------------------------------------------
# lifecycle transitions
# ---------------------------------------------------------------------------
def _set_version_derived_status(db: Session, version_id: uuid.UUID, status: str) -> None:
    db.execute(update(DocumentPage).where(DocumentPage.version_id == version_id).values(confidence_status=status))
    db.execute(update(DocumentChunk).where(DocumentChunk.version_id == version_id).values(confidence_status=status))
    db.execute(
        update(ExtractedValue)
        .where(ExtractedValue.version_id == version_id, ExtractedValue.status == "draft_extraction")
        .values(status="superseded" if status == "superseded" else "deleted")
    )


def activate_version(db: Session, doc: Document, ver: DocumentVersion, actor: Actor) -> None:
    prev = db.execute(
        select(DocumentVersion).where(DocumentVersion.document_id == doc.id, DocumentVersion.is_active.is_(True))
    ).scalar_one_or_none()
    if prev is not None and prev.id != ver.id:
        prev.is_active = False
        prev.state = "superseded"
        prev.superseded_at = _now()
        db.flush()
        _set_version_derived_status(db, prev.id, ConfidenceStatus.SUPERSEDED.value)
        audit.record(db, actor, "document.version_superseded", target_type="document", target_id=doc.id,
                     details={"version": prev.version_number, "superseded_by": ver.version_number})
    ver.is_active = True
    ver.state = "active"
    doc.current_version_id = ver.id
    doc.status = "active"
    doc.deleted_at = None
    doc.updated_at = _now()
    db.flush()


def mark_document_deleted(db: Session, doc: Document, actor: Actor, *, reason: str, source_missing: bool) -> None:
    """Logical deletion: raw versions are preserved; derived data leaves retrieval."""
    if doc.status == "deleted":
        return
    for ver in db.execute(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)).scalars():
        if ver.is_active or ver.state in ("active", "pending"):
            ver.is_active = False
            ver.state = "deleted"
            db.flush()
            _set_version_derived_status(db, ver.id, ConfidenceStatus.DELETED.value)
    doc.status = "deleted"
    doc.deleted_at = _now()
    doc.deleted_by = actor.user_id
    doc.delete_reason = reason[:500]
    doc.updated_at = _now()
    audit.record(db, actor, "document.source_missing" if source_missing else "document.delete",
                 target_type="document", target_id=doc.id,
                 details={"reason": reason[:500], "title": doc.title, "raw_versions_preserved": True})


# ---------------------------------------------------------------------------
# processing (called by the job worker)
# ---------------------------------------------------------------------------
def _chunk_status(area_verified: bool, method: str) -> str:
    if method in DRAFT_METHODS:
        return ConfidenceStatus.DRAFT_EXTRACTION.value
    if area_verified and method in ("native_text", "native_text_rebuilt"):
        return ConfidenceStatus.VERIFIED_SOURCE.value
    return ConfidenceStatus.UNVERIFIED.value


_REF_CODE = re.compile(r"\bISO(?:/TR|/TS)?\s?(\d{2,5}(?:-\d{1,2})?)(?::\d{4})?")


def _link_references(db: Session, doc: Document, full_text: str) -> int:
    mentioned = {m.group(1) for m in _REF_CODE.finditer(full_text[:400000])}
    if not mentioned:
        return 0
    created = 0
    others = db.execute(
        select(Document).where(Document.id != doc.id, Document.standard_code.is_not(None), Document.status != "deleted")
    ).scalars()
    for other in others:
        m = re.search(r"ISO(?:/T[RS])? (\d{2,5}(?:-\d{1,2})?)", other.standard_code or "")
        if not m or m.group(1) not in mentioned:
            continue
        own = re.search(r"ISO(?:/T[RS])? (\d{2,5}(?:-\d{1,2})?)", doc.standard_code or "")
        if own and own.group(1) == m.group(1):
            continue
        exists = db.execute(select(DocumentRelationship.id).where(
            DocumentRelationship.from_document_id == doc.id, DocumentRelationship.to_document_id == other.id,
            DocumentRelationship.relation_type == "references")).first()
        if not exists:
            db.add(DocumentRelationship(from_document_id=doc.id, to_document_id=other.id, relation_type="references",
                                        note=f"Metinde {other.standard_code} referansı bulundu"))
            created += 1
    return created


def process_version(db: Session, version_id: uuid.UUID, *, actor: Actor | None = None, settings: Settings | None = None) -> str:
    """Extract, index and activate one version. Returns the ingestion status."""
    settings = settings or get_settings()
    actor = actor or Actor.system()
    storage = Storage(settings)
    ver = db.get(DocumentVersion, version_id)
    if ver is None:
        raise ValueError("Sürüm bulunamadı")
    doc = db.get(Document, ver.document_id)
    area = db.get(KnowledgeArea, doc.knowledge_area_id) if doc.knowledge_area_id else None
    area_verified = bool(area and area.is_verified_corpus)
    ver.ingestion_status = "processing"
    db.commit()

    abs_path = str(storage.abs(ver.storage_relpath))
    try:
        data = storage.read_bytes(ver.storage_relpath)
        det = detect(doc.original_filename, data[:8192])
        ctx = ExtractContext(settings=settings, storage=storage, version_id=ver.id, sha256=ver.sha256,
                             filename=doc.original_filename, abs_path=abs_path)
        result = extract_bytes(data, det, ctx)
    except Exception as exc:
        log.exception("Alım başarısız: %s", doc.original_filename)
        db.rollback()
        ver = db.get(DocumentVersion, version_id)
        doc = db.get(Document, ver.document_id)
        ver.ingestion_status = "failed"
        ver.ingestion_error = f"{type(exc).__name__}: {exc}"[:1000]
        if not ver.is_active:
            ver.state = "failed"
        if doc.current_version_id is None:
            doc.status = "failed"
        audit.record(db, actor, "ingestion.failed", outcome="failure", target_type="document", target_id=doc.id,
                     details={"version": ver.version_number, "error": ver.ingestion_error})
        db.commit()
        return "failed"

    # --- preserve user confirmations across re-indexing of the same version
    prior_confirm = {
        p.page_number: (p.confidence_status, p.confirmed_by, p.confirmed_at, p.review_note, p.text)
        for p in db.execute(select(DocumentPage).where(DocumentPage.version_id == ver.id)).scalars()
        if p.confidence_status in ("user_confirmed", "rejected")
    }
    had_values = db.execute(select(ExtractedValue.id).where(ExtractedValue.version_id == ver.id).limit(1)).first()
    db.execute(delete(DocumentChunk).where(DocumentChunk.version_id == ver.id))
    db.execute(delete(DocumentPage).where(DocumentPage.version_id == ver.id))
    db.execute(delete(ArchiveMember).where(ArchiveMember.version_id == ver.id))
    db.flush()

    # standard code & title (verified corpus documents)
    first_text = "\n".join(p.text for p in result.pages[:3])
    code = detect_standard_code(first_text, doc.original_filename)
    if code:
        doc.standard_code = code
    if doc.source_kind == "watched":
        doc.title = clean_title(doc.original_filename, doc.standard_code)

    draft_value_pages = 0
    chunk_index = 0
    counts = {"pages": 0, "chunks": 0, "draft_pages": 0, "verified_pages": 0, "values": 0}
    methods: dict[str, int] = {}
    for p in result.pages:
        status = _chunk_status(area_verified, p.method)
        prior = prior_confirm.get(p.page_number)
        page = DocumentPage(document_id=doc.id, version_id=ver.id, page_number=p.page_number, locator=p.locator,
                            text=p.text, extraction_method=p.method, confidence_status=status,
                            ocr_mean_confidence=p.ocr_confidence)
        if prior and prior[4] == p.text and status == ConfidenceStatus.DRAFT_EXTRACTION.value:
            page.confidence_status, page.confirmed_by, page.confirmed_at, page.review_note = prior[:4]
        db.add(page)
        db.flush()
        methods[p.method] = methods.get(p.method, 0) + 1
        counts["pages"] += 1
        if page.confidence_status == "draft_extraction":
            counts["draft_pages"] += 1
        if page.confidence_status == "verified_source":
            counts["verified_pages"] += 1
        for ch in chunk_page(p.page_number, p.locator, p.text):
            db.add(DocumentChunk(document_id=doc.id, version_id=ver.id, page_id=page.id, chunk_index=chunk_index,
                                 page_number=ch.page_number, locator=ch.locator, char_start=ch.char_start,
                                 char_end=ch.char_end, text=ch.text, extraction_method=p.method,
                                 confidence_status=page.confidence_status))
            chunk_index += 1
            counts["chunks"] += 1
        # candidate engineering values from OCR / transcripts -> review queue
        if p.method in DRAFT_METHODS and not had_values and draft_value_pages < settings.draft_value_max_pages:
            draft_value_pages += 1
            for c in extract_candidates(p.text):
                db.add(ExtractedValue(
                    document_id=doc.id, version_id=ver.id, page_id=page.id, page_number=p.page_number,
                    locator=p.locator, quantity_kind=c.quantity_kind, label=c.label, context_text=c.context,
                    raw_text=c.raw_text, value_numeric=c.value, unit=c.unit, extraction_method=p.method,
                    status="draft_extraction",
                    original={"raw_text": c.raw_text, "value": c.value, "unit": c.unit, "label": c.label,
                              "page": p.page_number, "version_id": str(ver.id), "method": p.method},
                ))
                counts["values"] += 1
    for m in result.archive_members:
        db.add(ArchiveMember(version_id=ver.id, **m))

    ver.page_count = result.metadata.get("page_count", len(result.pages)) or len(result.pages)
    ver.metadata_ = {**(ver.metadata_ or {}), **{k: v for k, v in result.metadata.items() if k != "pdf_metadata"},
                     "pdf_metadata": result.metadata.get("pdf_metadata"), "media": result.media}
    ver.extraction_summary = {"counts": counts, "methods": methods, "warnings": result.warnings,
                              "stored_only_reason": result.stored_only_reason}
    ver.ingestion_status = "stored_only" if result.stored_only and not result.pages else "indexed"
    ver.ingestion_error = None
    ver.ingested_at = _now()
    if doc.status == "deleted":
        # a logically deleted document never becomes an active source again by re-indexing
        ver.is_active = False
        ver.state = "deleted"
        db.flush()
        _set_version_derived_status(db, ver.id, ConfidenceStatus.DELETED.value)
    else:
        activate_version(db, doc, ver, actor)
    rel_count = _link_references(db, doc, "\n".join(p.text for p in result.pages))
    audit.record(db, actor, "ingestion.completed", target_type="document", target_id=doc.id,
                 details={"version": ver.version_number, "status": ver.ingestion_status, **counts,
                          "relationships_created": rel_count})
    db.commit()
    write_manifest(db, storage, doc.id)
    return ver.ingestion_status


def write_manifest(db: Session, storage: Storage, document_id: uuid.UUID) -> None:
    doc = db.get(Document, document_id)
    versions = db.execute(
        select(DocumentVersion).where(DocumentVersion.document_id == document_id).order_by(DocumentVersion.version_number)
    ).scalars()
    storage.write_manifest(document_id, {
        "document_id": str(doc.id), "title": doc.title, "standard_code": doc.standard_code,
        "status": doc.status, "source_kind": doc.source_kind, "source_relpath": doc.source_relpath,
        "versions": [{"version": v.version_number, "id": str(v.id), "sha256": v.sha256, "size_bytes": v.size_bytes,
                      "state": v.state, "ingestion_status": v.ingestion_status, "raw": v.storage_relpath,
                      "created_at": v.created_at, "ingested_at": v.ingested_at} for v in versions],
    })


def queue_reindex(db: Session, doc: Document, actor: Actor) -> IngestionJob | None:
    if doc.current_version_id is None:
        ver = db.execute(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
                         .order_by(DocumentVersion.version_number.desc())).scalars().first()
    else:
        ver = db.get(DocumentVersion, doc.current_version_id)
    if ver is None:
        return None
    ver.ingestion_status = "queued"
    job = _queue(db, doc, ver, "reindex", actor.user_id)
    audit.record(db, actor, "document.reindex", target_type="document", target_id=doc.id,
                 details={"version": ver.version_number})
    return job
