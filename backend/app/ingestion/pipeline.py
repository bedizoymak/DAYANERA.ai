"""Document ingestion pipeline: versions, extraction, indexing, lineage."""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.metadata
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import BinaryIO

from sqlalchemy import delete, select, text, update
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
    MessageSource,
    KnowledgeArea,
)
from app.domain.enums import DRAFT_METHODS, ConfidenceStatus
from app.ingestion import quality
from app.ingestion.chunker import CHUNKER_VERSION, Chunk, active_config, chunk_pages
from app.ingestion.structure import DocModel
from app.ingestion.detect import Detected, detect
from app.ingestion.extractors.archive import extract_archive
from app.ingestion.extractors.base import ExtractContext, ExtractionOutput
from app.ingestion.extractors.media import extract_audio, extract_image, extract_video
from app.ingestion.extractors.office import extract_docx, extract_pptx, extract_text, extract_xlsx
from app.ingestion.extractors.pdf import extract_pdf
from app.ingestion.quality import PIPELINE_VERSION
from app.ingestion.standard_code import clean_title, detect_standard_code, validate_code
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
# tool that produced the text of each non-PDF category (PDFs name their parser in extract_pdf)
_CATEGORY_TOOL = {"docx": "python-docx", "xlsx": "openpyxl", "pptx": "python-pptx", "text": None,
                  "image": "rapidocr-onnxruntime", "audio": "faster-whisper", "video": "faster-whisper",
                  "zip": None, "tar": None}


def _tool_version(dist: str | None) -> str:
    if dist is None:
        return "stdlib"
    try:
        return f"{dist} {importlib.metadata.version(dist)}"
    except importlib.metadata.PackageNotFoundError:
        return dist


def extract_bytes(data: bytes, det: Detected, ctx: ExtractContext) -> ExtractionOutput:
    out = _extract_bytes(data, det, ctx)
    if out.parser is None:  # provenance: every chunk names the tool that produced its text
        tool = _CATEGORY_TOOL.get(det.category)
        out.parser = tool or det.category
        out.parser_version = _tool_version(tool)
    return out


def _extract_bytes(data: bytes, det: Detected, ctx: ExtractContext) -> ExtractionOutput:
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


def chunk_facts(chunks: list[Chunk], page_ok: dict[int, bool] | None = None) -> list[quality.ChunkFacts]:
    """Gate view of chunks (also used before anything is written: ``reingest --dry-run``)."""
    facts = []
    for ch in chunks:
        raw = " ".join(f.get("raw") or "" for f in ch.formulas)
        facts.append(quality.ChunkFacts(
            page_number=ch.page_start, text=ch.text, content_type=ch.content_type, clause=ch.clause,
            has_lineage=bool(ch.page_start and (page_ok is None or page_ok.get(ch.page_start))),
            content_hash=hashlib.sha256(ch.text.encode("utf-8")).hexdigest(), sources=ch.sources, heading=ch.heading,
            page_end=ch.page_end, role=ch.role, key=ch.key, parent_key=ch.parent_key, child_keys=list(ch.child_keys),
            tokens=ch.tokens, verbatim=ch.verbatim_text, raw_extra=raw, formulas=ch.formulas,
            heading_path=ch.heading_path, meta_hash=meta_hash(ch), review_reasons=ch.review_reasons))
    return facts


def meta_hash(ch: Chunk) -> str:
    """Hash of the structure an approval also covers: role, parent, type, path, formula forms, table cells."""
    payload = {"role": ch.role, "parent": ch.parent_key, "type": ch.content_type, "path": ch.heading_path,
               "context": ch.context, "eq": ch.equation_numbers, "tables": ch.table_numbers,
               "formulas": [{k: f.get(k) for k in ("number", "plain", "latex", "status")} for f in ch.formulas],
               "table": (ch.table or {}).get("rows"), "pages": [ch.page_start, ch.page_end]}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def build_chunks(doc: Document, result: ExtractionOutput) -> tuple[list[Chunk], DocModel]:
    return chunk_pages(result.pages, standard_code=doc.standard_code, title=doc.title, config=active_config())


def chunking_summary(chunks: list[Chunk], model: DocModel | None) -> dict:
    """Derived statistics stored on the version (no document text)."""
    kinds: dict[str, int] = {}
    for c in chunks:
        kinds[c.content_type] = kinds.get(c.content_type, 0) + 1
    tokens = sorted(c.tokens for c in chunks if c.role != "parent")
    formulas = [f for c in chunks if c.role != "parent" for f in c.formulas]
    return {"chunker_version": CHUNKER_VERSION, "config": dataclasses.asdict(active_config()), "chunks": len(chunks),
            "roles": {r: sum(c.role == r for c in chunks) for r in ("leaf", "parent", "child")},
            "content_types": kinds, "tokens_median": tokens[len(tokens) // 2] if tokens else 0,
            "tokens_max": tokens[-1] if tokens else 0,
            "multi_page_chunks": sum(c.page_start != c.page_end for c in chunks),
            "formulas": len(formulas), "formulas_latex": sum(1 for f in formulas if f.get("latex")),
            "formulas_needs_review": sum(1 for f in formulas if f.get("status") == "needs_review"),
            "needs_review_chunks": sum(c.validation_status != "ok" for c in chunks),
            "structure": dict(model.stats) if model else {}}


def _write_chunks(db: Session, doc: Document, ver: DocumentVersion, result: ExtractionOutput,
                  page_rows: dict[int, DocumentPage], chunks: list[Chunk] | None = None) -> list[quality.ChunkFacts]:
    """Persist engineering chunks (parents before their children) with full lineage, then read back
    the index coverage."""
    chunks = build_chunks(doc, result)[0] if chunks is None else chunks
    facts = chunk_facts(chunks, {n: bool(pr.id and pr.parser) for n, pr in page_rows.items()})
    ids = {ch.key: uuid.uuid4() for ch in chunks}
    by_page = {p.page_number: p for p in result.pages}
    language = detect_language("\n".join(p.text for p in result.pages[:8]))
    for idx, (ch, f) in enumerate(zip(chunks, facts)):
        page = page_rows[ch.page_start]
        pages = [page_rows[n] for n in range(ch.page_start, ch.page_end + 1) if n in page_rows]
        status = page.confidence_status
        if any(pr.confidence_status == ConfidenceStatus.DRAFT_EXTRACTION.value for pr in pages):
            status = ConfidenceStatus.DRAFT_EXTRACTION.value  # a chunk is only as verified as its weakest page
        if any(s.endswith("_model") for s in ch.sources) and status == ConfidenceStatus.VERIFIED_SOURCE.value:
            status = ConfidenceStatus.DRAFT_EXTRACTION.value  # model-generated text (e.g. formula LaTeX) is a draft
        p = by_page.get(ch.page_start)
        db.add(DocumentChunk(
            id=ids[ch.key], document_id=doc.id, version_id=ver.id, page_id=page.id, chunk_index=idx,
            page_number=ch.page_start, locator=ch.locator, char_start=ch.char_start, char_end=ch.char_end,
            text=ch.text, extraction_method=p.method if p else ch.method, confidence_status=status,
            page_start=ch.page_start, page_end=ch.page_end, clause=ch.clause, heading=ch.heading,
            content_type=ch.content_type, standard_code=doc.standard_code,
            extraction_confidence=min(((pr.quality or {}).get("score", 1.0) for pr in pages), default=None),
            source_hash=ver.sha256, content_hash=f.content_hash, parser=page.parser, parser_version=page.parser_version,
            parent_id=ids.get(ch.parent_key) if ch.parent_key else None, chunk_role=ch.role,
            heading_path=ch.heading_path, context=ch.context, equation_numbers=ch.equation_numbers,
            table_numbers=ch.table_numbers, figure_numbers=ch.figure_numbers, symbols=ch.symbols, units=ch.units,
            token_count=ch.tokens, chunker_version=CHUNKER_VERSION, formula=ch.formulas or None,
            table_data=ch.table, validation_status=ch.validation_status, meta_hash=f.meta_hash,
            meta={"key": ch.key, "child_keys": ch.child_keys, "review_reasons": ch.review_reasons,
                  "derived_lines": len(ch.derived_lines), "section": ch.section_key, "language": language,
                  "document_title": doc.title}))
        f.has_lineage = f.has_lineage and bool(page.id and ver.sha256 and page.parser)
    db.flush()
    if facts:
        empty = {r[0] for r in db.execute(text(
            "SELECT chunk_index FROM document_chunks WHERE version_id = :v AND length(tsv) = 0"), {"v": ver.id})}
        for i, f in enumerate(facts):
            # punctuation-only fragments legitimately have no lexemes
            f.tsv_empty = i in empty and bool(re.search(r"\w", f.text))
    return facts


_LANG_WORDS = {"en": {"the", "and", "of", "is", "shall", "for", "with", "to"},
               "de": {"der", "die", "und", "ist", "mit", "für", "den", "das"},
               "fr": {"le", "la", "les", "et", "est", "pour", "des", "du"},
               "tr": {"ve", "bir", "bu", "ile", "için", "olan", "de", "da"}}


def detect_language(sample: str) -> str:
    words = re.findall(r"[^\W\d_]+", sample.lower())[:4000]
    scores = {lang: sum(1 for w in words if w in vocab) for lang, vocab in _LANG_WORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 5 else "und"


def _revoke_verification(db: Session, doc: Document, ver: DocumentVersion, actor: Actor, reason: str) -> None:
    if ver.corpus_status != "verified":
        return
    ver.verified_by = ver.verified_at = ver.verified_fingerprint = None
    audit.record(db, actor, "corpus.verification_revoked", target_type="document", target_id=doc.id,
                 details={"version": ver.version_number, "reason": reason})


def _apply_corpus_status(db: Session, doc: Document, ver: DocumentVersion, report: dict, actor: Actor) -> None:
    """Gate outcome -> corpus status. An approval survives re-indexing only for an identical extraction."""
    outcome = report.get("outcome", "failed")
    fp = report.get("fingerprint")
    keep = (ver.corpus_status == "verified" and outcome != "failed" and ver.ingestion_status == "indexed"
            and fp is not None and fp == ver.verified_fingerprint)
    if not keep:
        _revoke_verification(db, doc, ver, actor, "extraction_changed" if outcome != "failed" else "extraction_failed")
    ver.corpus_status = "verified" if keep else outcome
    ver.quality_report = report
    audit.record(db, actor, "corpus.quality_gates", target_type="document", target_id=doc.id,
                 details={"version": ver.version_number, "outcome": outcome, "corpus_status": ver.corpus_status,
                          "fingerprint": fp, "gates": {g["id"]: g["status"] for g in report.get("gates", [])}})


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
        _revoke_verification(db, doc, ver, actor, "extraction_failed")
        ver.corpus_status = "failed"
        ver.quality_report = {"pipeline_version": PIPELINE_VERSION, "outcome": "failed",
                              "gates": [{"id": "extraction", "status": "fail", "detail": ver.ingestion_error}]}
        if not ver.is_active:
            ver.state = "failed"
        if doc.current_version_id is None:
            doc.status = "failed"
        audit.record(db, actor, "ingestion.failed", outcome="failure", target_type="document", target_id=doc.id,
                     details={"version": ver.version_number, "error": ver.ingestion_error})
        db.commit()
        return "failed"

    status = _persist_extraction(db, doc, ver, result, actor=actor, settings=settings, storage=storage,
                                 area_verified=area_verified)
    db.commit()
    write_manifest(db, storage, doc.id)
    return status


def _persist_extraction(db: Session, doc: Document, ver: DocumentVersion, result: ExtractionOutput, *,
                        actor: Actor, settings: Settings, storage: Storage, area_verified: bool,
                        chunks: list[Chunk] | None = None, model: DocModel | None = None,
                        reingest: bool = False) -> str:
    """Replace the pages and chunks of ``ver`` with ``result`` and apply the quality gates (one transaction;
    the caller commits or rolls back)."""
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

    # standard code & title (verified corpus documents), validated deterministically
    first_text = "\n".join(p.text for p in result.pages[:3])
    code = detect_standard_code(first_text, doc.original_filename)
    if code:
        doc.standard_code = code
    code_check = validate_code(doc.standard_code, doc.original_filename)
    if doc.source_kind == "watched":
        doc.title = clean_title(doc.original_filename, doc.standard_code)

    draft_value_pages = 0
    counts = {"pages": 0, "chunks": 0, "draft_pages": 0, "verified_pages": 0, "values": 0}
    methods: dict[str, int] = {}
    page_rows: dict[int, DocumentPage] = {}
    for p in result.pages:
        status = _chunk_status(area_verified, p.method)
        prior = prior_confirm.get(p.page_number)
        page = DocumentPage(document_id=doc.id, version_id=ver.id, page_number=p.page_number, locator=p.locator,
                            text=p.text, extraction_method=p.method, confidence_status=status,
                            ocr_mean_confidence=p.ocr_confidence, parser=p.parser or result.parser,
                            parser_version=p.parser_version or result.parser_version, quality=p.quality or {})
        if prior and prior[4] == p.text and status == ConfidenceStatus.DRAFT_EXTRACTION.value:
            page.confidence_status, page.confirmed_by, page.confirmed_at, page.review_note = prior[:4]
        db.add(page)
        db.flush()
        page_rows[p.page_number] = page
        methods[p.method] = methods.get(p.method, 0) + 1
        counts["pages"] += 1
        if page.confidence_status == "draft_extraction":
            counts["draft_pages"] += 1
        if page.confidence_status == "verified_source":
            counts["verified_pages"] += 1
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

    if chunks is None:
        chunks, model = build_chunks(doc, result)
    facts = _write_chunks(db, doc, ver, result, page_rows, chunks)
    counts["chunks"] = len(facts)

    ver.page_count = result.metadata.get("page_count", len(result.pages)) or len(result.pages)
    ver.metadata_ = {**(ver.metadata_ or {}), **{k: v for k, v in result.metadata.items() if k != "pdf_metadata"},
                     "pdf_metadata": result.metadata.get("pdf_metadata"), "media": result.media,
                     "standard_code_check": code_check,
                     "chunking": chunking_summary(chunks, model),
                     # symbol -> description/unit/clause (derived metadata, used to explain formula variables)
                     "symbol_glossary": dict(list((model.glossary if model else {}).items())[:2000])}
    ver.extraction_summary = {"counts": counts, "methods": methods, "warnings": result.warnings,
                              "stored_only_reason": result.stored_only_reason}
    ver.parser, ver.parser_version = result.parser, result.parser_version
    ver.ingestion_error = None
    ver.ingested_at = _now()
    if result.stored_only and not result.pages:
        ver.ingestion_status = "stored_only"
        report = {"pipeline_version": PIPELINE_VERSION, "outcome": "failed",
                  "gates": [{"id": "content", "status": "fail", "detail": result.stored_only_reason or "içerik yok"}]}
    else:
        report = quality.evaluate(
            is_pdf=(ver.metadata_ or {}).get("category") == "pdf", verified_area=area_verified,
            standard_code=doc.standard_code, code_check=code_check,
            validation=result.metadata.get("pdf_validation"), page_count=ver.page_count,
            blank_pages=result.metadata.get("blank_pages", []), pages=result.pages, chunks=facts,
            layout=result.metadata.get("layout"), parser=result.parser, parser_version=result.parser_version)
        # "indexed" only when chunks exist and the full-text index covers every one of them
        blocking = {g["id"] for g in report["gates"] if g["status"] == "fail"}
        if blocking & {"chunk_lineage", "fulltext_index"}:
            ver.ingestion_status = "failed"
            ver.ingestion_error = f"index_verification_failed: {', '.join(sorted(blocking))}"
        else:
            ver.ingestion_status = "indexed"
    _apply_corpus_status(db, doc, ver, report, actor)
    if ver.ingestion_status == "failed":
        if not ver.is_active:
            ver.state = "failed"
        if doc.current_version_id is None:
            doc.status = "failed"
        audit.record(db, actor, "ingestion.failed", outcome="failure", target_type="document", target_id=doc.id,
                     details={"version": ver.version_number, "error": ver.ingestion_error})
        return "failed"
    if doc.status == "deleted":
        # a logically deleted document never becomes an active source again by re-indexing
        ver.is_active = False
        ver.state = "deleted"
        db.flush()
        _set_version_derived_status(db, ver.id, ConfidenceStatus.DELETED.value)
    else:
        activate_version(db, doc, ver, actor)
    rel_count = _link_references(db, doc, "\n".join(p.text for p in result.pages))
    audit.record(db, actor, "ingestion.completed" if not reingest else "ingestion.reingested", target_type="document",
                 target_id=doc.id,
                 details={"version": ver.version_number, "status": ver.ingestion_status, **counts,
                          "relationships_created": rel_count, "corpus_status": ver.corpus_status,
                          "parser": ver.parser, "gates_outcome": report.get("outcome")})
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


# ---------------------------------------------------------------------------
# safe re-ingestion of one document (python -m app.cli reingest-document)
# ---------------------------------------------------------------------------
def _chunk_summary_db(db: Session, version_id: uuid.UUID) -> dict:
    rows = db.execute(text("""SELECT content_type, coalesce(chunk_role, 'leaf') AS role, count(*) AS n,
                                     coalesce(sum(cardinality(equation_numbers)), 0) AS eq
                              FROM document_chunks WHERE version_id = :v GROUP BY 1, 2"""), {"v": version_id}).all()
    kinds: dict[str, int] = {}
    roles: dict[str, int] = {}
    for r in rows:
        kinds[r.content_type] = kinds.get(r.content_type, 0) + r.n
        roles[r.role] = roles.get(r.role, 0) + r.n
    return {"chunks": sum(kinds.values()), "content_types": kinds, "roles": roles,
            "equations": int(sum(r.eq for r in rows))}


def _message_sources_before(db: Session, version_id: uuid.UUID) -> list[tuple]:
    return db.execute(select(MessageSource.id, MessageSource.page_number, MessageSource.excerpt)
                      .where(MessageSource.version_id == version_id)).all()


def _remap_message_sources(db: Session, version_id: uuid.UUID, before: list[tuple]) -> dict:
    """Point stored citations at the new chunk that holds the same excerpt on the same page. The citation's
    own provenance (version, page, excerpt) never changes; only the chunk link is refreshed."""
    if not before:
        return {"message_sources": 0, "remapped": 0}
    chunks = db.execute(select(DocumentChunk.id, DocumentChunk.page_start, DocumentChunk.page_end, DocumentChunk.text)
                        .where(DocumentChunk.version_id == version_id, DocumentChunk.chunk_role != "parent")).all()
    squash = [(c.id, c.page_start, c.page_end, re.sub(r"\s+", "", c.text)) for c in chunks]
    remapped = 0
    for sid, page, excerpt in before:
        probe = re.sub(r"\s+", "", excerpt or "")[:80]
        hit = next((cid for cid, p0, p1, body in squash if page is not None and p0 <= page <= p1 and probe
                    and probe in body), None)
        if hit is not None:
            db.execute(update(MessageSource).where(MessageSource.id == sid).values(chunk_id=hit))
            remapped += 1
    return {"message_sources": len(before), "remapped": remapped}


def reingest_version(db: Session, version_id: uuid.UUID, *, actor: Actor | None = None,
                     settings: Settings | None = None, dry_run: bool = True, allow_revoke: bool = False) -> dict:
    """Re-extract and re-chunk one version SAFELY.

    1. extract + chunk in memory and run every quality gate (nothing written);
    2. compare with the stored chunks; refuse when a blocking gate fails, or when the version is
       verified and the new extraction would revoke that approval without ``allow_revoke``;
    3. unless ``dry_run``: replace pages/chunks inside a savepoint, re-run the gates on what was written
       (full-text index included) and roll back to the old chunks if a blocking gate fails;
    4. refresh the chunk links of stored chat citations and audit the result.
    """
    settings = settings or get_settings()
    actor = actor or Actor.system()
    storage = Storage(settings)
    ver = db.get(DocumentVersion, version_id)
    if ver is None:
        raise ValueError("Sürüm bulunamadı")
    doc = db.get(Document, ver.document_id)
    area = db.get(KnowledgeArea, doc.knowledge_area_id) if doc.knowledge_area_id else None
    area_verified = bool(area and area.is_verified_corpus)
    data = storage.read_bytes(ver.storage_relpath)
    det = detect(doc.original_filename, data[:8192])
    ctx = ExtractContext(settings=settings, storage=storage, version_id=ver.id, sha256=ver.sha256,
                         filename=doc.original_filename, abs_path=str(storage.abs(ver.storage_relpath)))
    result = extract_bytes(data, det, ctx)
    chunks, model = build_chunks(doc, result)
    facts = chunk_facts(chunks)
    code_check = validate_code(doc.standard_code, doc.original_filename)
    staged = quality.evaluate(
        is_pdf=(ver.metadata_ or {}).get("category") == "pdf", verified_area=area_verified,
        standard_code=doc.standard_code, code_check=code_check, validation=result.metadata.get("pdf_validation"),
        page_count=result.metadata.get("page_count", len(result.pages)) or len(result.pages),
        blank_pages=result.metadata.get("blank_pages", []), pages=result.pages, chunks=facts,
        layout=result.metadata.get("layout"), parser=result.parser, parser_version=result.parser_version)
    report = {"document_id": str(doc.id), "version_id": str(ver.id), "standard_code": doc.standard_code,
              "dry_run": dry_run, "applied": False, "corpus_status_before": ver.corpus_status,
              "staged_outcome": staged["outcome"], "staged_fingerprint": staged["fingerprint"],
              "gates": {g["id"]: g["status"] for g in staged["gates"]},
              "old": _chunk_summary_db(db, ver.id), "new": chunking_summary(chunks, model)}
    blocking = [g["id"] for g in staged["gates"] if g["status"] == "fail"]
    if blocking:
        report["blocked"] = f"blocking gates failed: {', '.join(blocking)} (nothing was changed)"
        return report
    if ver.corpus_status == "verified" and staged["fingerprint"] != ver.verified_fingerprint and not allow_revoke:
        report["blocked"] = ("version is VERIFIED and the new extraction differs: applying it revokes the approval "
                             "(re-run with --allow-revoke, then review and corpus-approve again)")
        return report
    if dry_run:
        return report
    before = _message_sources_before(db, ver.id)
    savepoint = db.begin_nested()
    status = _persist_extraction(db, doc, ver, result, actor=actor, settings=settings, storage=storage,
                                 area_verified=area_verified, chunks=chunks, model=model, reingest=True)
    written = ver.quality_report or {}
    failed_after = [g["id"] for g in written.get("gates", []) if g["status"] == "fail"]
    if status == "failed" or failed_after:
        savepoint.rollback()
        report["blocked"] = f"post-write gates failed ({', '.join(failed_after) or status}); rolled back, old chunks kept"
        return report
    report["message_sources"] = _remap_message_sources(db, ver.id, before)
    savepoint.commit()
    db.commit()
    write_manifest(db, storage, doc.id)
    report.update(applied=True, corpus_status_after=ver.corpus_status, outcome=written.get("outcome"))
    return report
