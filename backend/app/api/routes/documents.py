"""Document archive: uploads/attachments, versions, previews, download,
logical delete, restore, reindex and watcher controls."""
from __future__ import annotations

import os
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_dep, deny, require_owner, scopes_dep, settings_dep
from app.api.schemas import CorpusNoteIn, DeleteIn, DocumentListOut, DocumentOut
from app.core.config import Settings
from app.core.paths import fs, safe_join
from app.db.models import (
    ArchiveMember,
    Document,
    DocumentChunk,
    DocumentPage,
    DocumentRelationship,
    DocumentVersion,
    IngestionJob,
    KnowledgeArea,
)
from app.ingestion.extractors.pdf import render_page_png
from app.ingestion.pipeline import mark_document_deleted, queue_reindex, register_upload
from app.ingestion.storage import Storage
from app.services import audit, corpus
from app.services.access import ScopeSet, can_view_document
from app.services.auth import AuthenticatedUser

router = APIRouter(tags=["documents"])


def _uuid(v: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError as exc:
        raise HTTPException(404, "Bulunamadı.") from exc


def version_dict(v: DocumentVersion) -> dict:
    return {
        "id": str(v.id), "version_number": v.version_number, "sha256": v.sha256, "size_bytes": v.size_bytes,
        "mime_type": v.mime_type, "file_extension": v.file_extension, "state": v.state, "is_active": v.is_active,
        "ingestion_status": v.ingestion_status, "ingestion_error": v.ingestion_error, "page_count": v.page_count,
        "category": (v.metadata_ or {}).get("category"), "extraction_summary": v.extraction_summary,
        "metadata": {k: val for k, val in (v.metadata_ or {}).items() if k != "pdf_metadata"},
        "created_at": v.created_at.isoformat(), "ingested_at": v.ingested_at.isoformat() if v.ingested_at else None,
        "superseded_at": v.superseded_at.isoformat() if v.superseded_at else None,
        "source_mtime": v.source_mtime.isoformat() if v.source_mtime else None,
        "corpus_status": v.corpus_status, "parser": v.parser, "parser_version": v.parser_version,
        "quality_report": v.quality_report or {}, "verified_at": v.verified_at.isoformat() if v.verified_at else None,
        "review_note": v.review_note,
    }


def doc_dict(db: Session, d: Document, area: KnowledgeArea | None = None) -> dict:
    area = area or (db.get(KnowledgeArea, d.knowledge_area_id) if d.knowledge_area_id else None)
    ver = db.get(DocumentVersion, d.current_version_id) if d.current_version_id else db.execute(
        select(DocumentVersion).where(DocumentVersion.document_id == d.id)
        .order_by(DocumentVersion.version_number.desc())).scalars().first()
    return {
        "id": str(d.id), "title": d.title, "standard_code": d.standard_code, "source_kind": d.source_kind,
        "source_relpath": d.source_relpath, "original_filename": d.original_filename, "status": d.status,
        "knowledge_area": area.slug if area else None, "is_verified_corpus": bool(area and area.is_verified_corpus),
        "current_version": version_dict(ver) if ver else None, "created_at": d.created_at.isoformat(),
        "updated_at": d.updated_at.isoformat(), "deleted_at": d.deleted_at.isoformat() if d.deleted_at else None,
        "delete_reason": d.delete_reason,
    }


def _get_doc(db: Session, doc_id: str, user: AuthenticatedUser, scopes: ScopeSet, request: Request) -> Document:
    d = db.get(Document, _uuid(doc_id))
    if d is None:
        raise HTTPException(404, "Belge bulunamadı.")
    if not can_view_document(scopes, d):
        deny(user, request, "document_scope", target_id=str(d.id))
    return d


def _get_version(db: Session, d: Document, vid: str) -> DocumentVersion:
    v = db.get(DocumentVersion, _uuid(vid))
    if v is None or v.document_id != d.id:
        raise HTTPException(404, "Sürüm bulunamadı.")
    return v


# ----------------------------------------------------------------------------
@router.get("/documents", response_model=DocumentListOut, summary="Belge arşivi (aranabilir, filtrelenebilir)")
def list_documents(q: str | None = None, status: str | None = None, category: str | None = None,
                   area: str | None = None, active: bool | None = None, ingestion_status: str | None = None,
                   limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                   user: AuthenticatedUser = Depends(current_user), scopes: ScopeSet = Depends(scopes_dep),
                   db: Session = Depends(db_dep)) -> dict:
    query = select(Document, KnowledgeArea).outerjoin(KnowledgeArea, KnowledgeArea.id == Document.knowledge_area_id) \
        .outerjoin(DocumentVersion, DocumentVersion.id == Document.current_version_id)
    if not scopes.is_owner:
        conds = [Document.owner_id == user.id]
        if scopes.area_ids:
            conds.append(Document.knowledge_area_id.in_(scopes.area_ids))
        if scopes.document_ids:
            conds.append(Document.id.in_(scopes.document_ids))
        query = query.where(or_(*conds))
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Document.title.ilike(like), Document.original_filename.ilike(like),
                                Document.standard_code.ilike(like)))
    if status:
        query = query.where(Document.status == status)
    if area:
        query = query.where(KnowledgeArea.slug == area)
    if active is True:
        query = query.where(Document.status == "active")
    elif active is False:
        query = query.where(Document.status != "active")
    if category:
        query = query.where(DocumentVersion.metadata_["category"].astext == category)
    if ingestion_status:
        query = query.where(DocumentVersion.ingestion_status == ingestion_status)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar()
    rows = db.execute(query.order_by(Document.updated_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [doc_dict(db, d, a) for d, a in rows], "total": total}


def _upload(db: Session, settings: Settings, user: AuthenticatedUser, file: UploadFile, kind: str, request: Request):
    if not file.filename:
        raise HTTPException(400, "Dosya adı eksik.")
    try:
        doc, ver = register_upload(db, settings, stream=file.file, filename=file.filename, actor=user.actor,
                                   area_slug="ekler", source_kind=kind)
    except ValueError as exc:
        raise HTTPException(413, str(exc)) from exc
    db.commit()
    worker = getattr(request.app.state, "worker", None)
    if worker:
        worker.wake()
    return doc_dict(db, doc)


@router.post("/documents/upload", response_model=DocumentOut, status_code=201,
             summary="Arşive dosya yükle (tüm dosya türleri)")
def upload_document(request: Request, file: UploadFile = File(...), user: AuthenticatedUser = Depends(current_user),
                    db: Session = Depends(db_dep), settings: Settings = Depends(settings_dep)) -> dict:
    return _upload(db, settings, user, file, "upload", request)


@router.post("/attachments", response_model=DocumentOut, status_code=201, summary="Sohbet eki yükle (tüm dosya türleri)")
def upload_attachment(request: Request, file: UploadFile = File(...), conversation_id: str | None = Form(None),
                      user: AuthenticatedUser = Depends(current_user), db: Session = Depends(db_dep),
                      settings: Settings = Depends(settings_dep)) -> dict:
    return _upload(db, settings, user, file, "attachment", request)


@router.get("/documents/{doc_id}", response_model=DocumentOut, summary="Belge meta verisi (görüntüleme denetlenir)")
def get_document(doc_id: str, request: Request, user: AuthenticatedUser = Depends(current_user),
                 scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    d = _get_doc(db, doc_id, user, scopes, request)
    audit.record(db, user.actor, "document.view", target_type="document", target_id=d.id)
    db.commit()
    return doc_dict(db, d)


@router.get("/documents/{doc_id}/versions", summary="Sürüm geçmişi (soy ağacı)")
def list_versions(doc_id: str, request: Request, user: AuthenticatedUser = Depends(current_user),
                  scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> list[dict]:
    d = _get_doc(db, doc_id, user, scopes, request)
    vs = db.execute(select(DocumentVersion).where(DocumentVersion.document_id == d.id)
                    .order_by(DocumentVersion.version_number.desc())).scalars()
    return [version_dict(v) for v in vs]


@router.get("/documents/{doc_id}/versions/{vid}/download", summary="Ham sürümü indir (denetlenir)")
def download(doc_id: str, vid: str, request: Request, user: AuthenticatedUser = Depends(current_user),
             scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep),
             settings: Settings = Depends(settings_dep)) -> FileResponse:
    d = _get_doc(db, doc_id, user, scopes, request)
    v = _get_version(db, d, vid)
    path = Storage(settings).abs(v.storage_relpath)
    if not os.path.exists(fs(path)):
        raise HTTPException(410, "Ham dosya depoda bulunamadı.")
    audit.record(db, user.actor, "document.download", target_type="document", target_id=d.id,
                 details={"version": v.version_number, "sha256": v.sha256})
    db.commit()
    name = d.original_filename
    return FileResponse(fs(path), media_type=v.mime_type or "application/octet-stream",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.get("/documents/{doc_id}/versions/{vid}/pages", summary="Sayfa/bölüm listesi")
def list_pages(doc_id: str, vid: str, request: Request, user: AuthenticatedUser = Depends(current_user),
               scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> list[dict]:
    d = _get_doc(db, doc_id, user, scopes, request)
    v = _get_version(db, d, vid)
    rows = db.execute(select(DocumentPage).where(DocumentPage.version_id == v.id).order_by(DocumentPage.page_number)).scalars()
    return [{"id": str(p.id), "page_number": p.page_number, "locator": p.locator, "extraction_method": p.extraction_method,
             "confidence_status": p.confidence_status, "chars": len(p.text), "ocr_mean_confidence": p.ocr_mean_confidence}
            for p in rows]


@router.get("/documents/{doc_id}/versions/{vid}/pages/{n}", summary="Sayfa metni")
def page_text(doc_id: str, vid: str, n: int, request: Request, user: AuthenticatedUser = Depends(current_user),
              scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    d = _get_doc(db, doc_id, user, scopes, request)
    v = _get_version(db, d, vid)
    p = db.execute(select(DocumentPage).where(DocumentPage.version_id == v.id, DocumentPage.page_number == n)).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "Sayfa bulunamadı.")
    audit.record(db, user.actor, "document.view", target_type="document", target_id=d.id,
                 details={"version": v.version_number, "page": n})
    db.commit()
    return {"page_number": n, "locator": p.locator, "text": p.text, "extraction_method": p.extraction_method,
            "confidence_status": p.confidence_status}


@router.get("/documents/{doc_id}/versions/{vid}/preview", summary="Önizleme görseli (PDF sayfası / görüntü)")
def preview(doc_id: str, vid: str, request: Request, page: int = 1, user: AuthenticatedUser = Depends(current_user),
            scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep),
            settings: Settings = Depends(settings_dep)) -> Response:
    d = _get_doc(db, doc_id, user, scopes, request)
    v = _get_version(db, d, vid)
    storage = Storage(settings)
    category = (v.metadata_ or {}).get("category")
    if category == "pdf":
        cache = storage.root / "media" / str(v.id) / f"page-{page:04d}.png"
        if os.path.exists(fs(cache)):
            with open(fs(cache), "rb") as f:
                return Response(f.read(), media_type="image/png")
        try:
            png = render_page_png(storage.read_bytes(v.storage_relpath), page)
        except IndexError as exc:
            raise HTTPException(404, "Sayfa yok.") from exc
        os.makedirs(fs(cache.parent), exist_ok=True)
        with open(fs(cache), "wb") as f:
            f.write(png)
        return Response(png, media_type="image/png")
    media = (v.metadata_ or {}).get("media") or []
    if media:
        idx = max(0, min(len(media) - 1, page - 1))
        path = safe_join(storage.root, *media[idx].split("/"))
        with open(fs(path), "rb") as f:
            return Response(f.read(), media_type="image/jpeg")
    raise HTTPException(404, "Bu dosya türü için önizleme yok.")


@router.get("/documents/{doc_id}/versions/{vid}/archive-members", summary="Arşiv üyeleri envanteri")
def archive_members(doc_id: str, vid: str, request: Request, user: AuthenticatedUser = Depends(current_user),
                    scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> list[dict]:
    d = _get_doc(db, doc_id, user, scopes, request)
    v = _get_version(db, d, vid)
    rows = db.execute(select(ArchiveMember).where(ArchiveMember.version_id == v.id)).scalars()
    return [{"member_path": m.member_path, "is_dir": m.is_dir, "size_bytes": m.size_bytes,
             "compressed_bytes": m.compressed_bytes, "status": m.status, "reason": m.reason, "mime_type": m.mime_type}
            for m in rows]


@router.get("/documents/{doc_id}/relationships", summary="Belge ilişkileri / soy")
def relationships(doc_id: str, request: Request, user: AuthenticatedUser = Depends(current_user),
                  scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> list[dict]:
    d = _get_doc(db, doc_id, user, scopes, request)
    rows = db.execute(select(DocumentRelationship, Document).join(
        Document, Document.id == DocumentRelationship.to_document_id).where(
        DocumentRelationship.from_document_id == d.id)).all()
    back = db.execute(select(DocumentRelationship, Document).join(
        Document, Document.id == DocumentRelationship.from_document_id).where(
        DocumentRelationship.to_document_id == d.id)).all()
    return ([{"direction": "out", "relation_type": r.relation_type, "document_id": str(o.id), "title": o.title,
              "note": r.note} for r, o in rows]
            + [{"direction": "in", "relation_type": r.relation_type, "document_id": str(o.id), "title": o.title,
                "note": r.note} for r, o in back])


@router.delete("/documents/{doc_id}", summary="Mantıksal silme (ham sürümler korunur)")
def delete_document(doc_id: str, request: Request, body: DeleteIn | None = None,
                    user: AuthenticatedUser = Depends(current_user), scopes: ScopeSet = Depends(scopes_dep),
                    db: Session = Depends(db_dep)) -> dict:
    d = _get_doc(db, doc_id, user, scopes, request)
    if not (user.is_owner or d.owner_id == user.id):
        deny(user, request, "delete_requires_owner", target_id=str(d.id))
    mark_document_deleted(db, d, user.actor, reason=(body.reason if body else "Kullanıcı tarafından silindi"),
                          source_missing=False)
    db.commit()
    return doc_dict(db, d)


@router.post("/documents/{doc_id}/restore", summary="Mantıksal silmeyi geri al ve yeniden indeksle (sahip)")
def restore_document(doc_id: str, request: Request, user: AuthenticatedUser = Depends(require_owner),
                     db: Session = Depends(db_dep)) -> dict:
    d = db.get(Document, _uuid(doc_id))
    if d is None:
        raise HTTPException(404, "Belge bulunamadı.")
    if d.status != "deleted":
        raise HTTPException(409, "Belge silinmiş durumda değil.")
    d.status = "pending"
    d.deleted_at = None
    d.delete_reason = None
    d.current_version_id = None
    queue_reindex(db, d, user.actor)
    audit.record(db, user.actor, "document.restore", target_type="document", target_id=d.id)
    db.commit()
    worker = getattr(request.app.state, "worker", None)
    if worker:
        worker.wake()
    return doc_dict(db, d)


@router.post("/documents/{doc_id}/reindex", summary="Belgeyi yeniden indeksle")
def reindex_document(doc_id: str, request: Request, user: AuthenticatedUser = Depends(current_user),
                     scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    d = _get_doc(db, doc_id, user, scopes, request)
    if not (user.is_owner or d.owner_id == user.id):
        deny(user, request, "reindex_requires_owner", target_id=str(d.id))
    if d.status == "deleted":
        raise HTTPException(409, "Silinmiş belge yeniden indekslenemez; önce geri yükleyin.")
    job = queue_reindex(db, d, user.actor)
    db.commit()
    worker = getattr(request.app.state, "worker", None)
    if worker:
        worker.wake()
    return {"queued": job is not None, "document": doc_dict(db, d)}


# ----------------------------------------------------------------------------
# verified-corpus lifecycle (owner only; see app/services/corpus.py)
def _corpus_action(doc_id: str, user: AuthenticatedUser, db: Session, action, note: str | None) -> dict:
    d = db.get(Document, _uuid(doc_id))
    if d is None or d.current_version_id is None:
        raise HTTPException(404, "Belge veya etkin sürüm bulunamadı.")
    reviewer = corpus.Reviewer(user.id, user.username, session_actor=user.actor)
    try:
        action(db, d.current_version_id, reviewer, note)
    except corpus.CorpusError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return doc_dict(db, d)


@router.post("/documents/{doc_id}/corpus/approve", summary="Etkin sürümü doğrulanmış korpusa al (sahip)")
def corpus_approve(doc_id: str, body: CorpusNoteIn, user: AuthenticatedUser = Depends(require_owner),
                   db: Session = Depends(db_dep)) -> dict:
    return _corpus_action(doc_id, user, db, corpus.approve, body.note)


@router.post("/documents/{doc_id}/corpus/revoke", summary="Doğrulamayı geri al (sahip, gerekçe zorunlu)")
def corpus_revoke(doc_id: str, body: CorpusNoteIn, user: AuthenticatedUser = Depends(require_owner),
                  db: Session = Depends(db_dep)) -> dict:
    return _corpus_action(doc_id, user, db, corpus.revoke, body.note or "")


@router.post("/documents/{doc_id}/corpus/reject", summary="Çıkarımı reddet: korpusa alınamaz (sahip)")
def corpus_reject(doc_id: str, body: CorpusNoteIn, user: AuthenticatedUser = Depends(require_owner),
                  db: Session = Depends(db_dep)) -> dict:
    return _corpus_action(doc_id, user, db, corpus.reject, body.note or "")


@router.get("/corpus/status", summary="Korpus durumu ve kalite kapıları (sahip)")
def corpus_status(user: AuthenticatedUser = Depends(require_owner), db: Session = Depends(db_dep)) -> dict:
    return {"items": corpus.status_rows(db)}


@router.get("/documents/{doc_id}/versions/{vid}/chunks", summary="Parça soy ağacı (sayfa, madde, tür, özet)")
def version_chunks(doc_id: str, vid: str, request: Request, user: AuthenticatedUser = Depends(current_user),
                   scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep),
                   limit: int = Query(200, ge=1, le=2000), offset: int = Query(0, ge=0)) -> dict:
    d = _get_doc(db, doc_id, user, scopes, request)
    v = _get_version(db, d, vid)
    rows = db.execute(select(DocumentChunk).where(DocumentChunk.version_id == v.id)
                      .order_by(DocumentChunk.chunk_index).limit(limit).offset(offset)).scalars().all()
    return {"items": [{
        "id": str(c.id), "chunk_index": c.chunk_index, "page_id": str(c.page_id) if c.page_id else None,
        "page_start": c.page_start, "page_end": c.page_end, "clause": c.clause, "heading": c.heading,
        "content_type": c.content_type, "standard_code": c.standard_code, "extraction_method": c.extraction_method,
        "confidence_status": c.confidence_status, "extraction_confidence": c.extraction_confidence,
        "source_hash": c.source_hash, "content_hash": c.content_hash, "parser": c.parser,
        "parser_version": c.parser_version, "preview": c.text[:240],
        # engineering chunks (0004)
        "chunk_role": c.chunk_role, "parent_id": str(c.parent_id) if c.parent_id else None,
        "heading_path": c.heading_path or [], "equation_numbers": c.equation_numbers or [],
        "table_numbers": c.table_numbers or [], "figure_numbers": c.figure_numbers or [],
        "symbols": c.symbols or [], "units": c.units or [], "token_count": c.token_count,
        "chunker_version": c.chunker_version, "validation_status": c.validation_status,
        "formulas": [{k: f.get(k) for k in ("number", "plain", "latex", "status", "reasons")}
                     for f in (c.formula or [])],
        "meta_hash": c.meta_hash} for c in rows]}


# ----------------------------------------------------------------------------
@router.post("/ingestion/scan", summary="İzlenen klasörleri şimdi tara (sahip)")
def scan_now(request: Request, user: AuthenticatedUser = Depends(require_owner)) -> dict:
    watcher = getattr(request.app.state, "watcher", None)
    if watcher is None:
        raise HTTPException(503, "Klasör izleyici etkin değil.")
    summary = watcher.scan(user.actor)
    worker = getattr(request.app.state, "worker", None)
    if worker:
        worker.wake()
    return summary


@router.post("/ingestion/reindex-all", summary="Tam manuel yeniden indeksleme (sahip)")
def reindex_all(request: Request, user: AuthenticatedUser = Depends(require_owner), db: Session = Depends(db_dep)) -> dict:
    watcher = getattr(request.app.state, "watcher", None)
    summary = watcher.scan(user.actor) if watcher else None
    docs = db.execute(select(Document).where(Document.status == "active")).scalars().all()
    queued = 0
    for d in docs:
        if queue_reindex(db, d, user.actor):
            queued += 1
    audit.record(db, user.actor, "ingestion.reindex_all", target_type="system", details={"queued": queued})
    db.commit()
    worker = getattr(request.app.state, "worker", None)
    if worker:
        worker.wake()
    return {"scan": summary, "queued": queued}


@router.get("/ingestion/jobs", summary="Alım işleri (sahip)")
def jobs(status: str | None = None, limit: int = Query(100, le=1000), user: AuthenticatedUser = Depends(require_owner),
         db: Session = Depends(db_dep)) -> list[dict]:
    q = select(IngestionJob, Document).join(Document, Document.id == IngestionJob.document_id)
    if status:
        q = q.where(IngestionJob.status == status)
    rows = db.execute(q.order_by(IngestionJob.created_at.desc()).limit(limit)).all()
    return [{"id": str(j.id), "document_id": str(d.id), "title": d.title, "job_type": j.job_type, "status": j.status,
             "attempts": j.attempts, "error": j.error, "created_at": j.created_at.isoformat(),
             "finished_at": j.finished_at.isoformat() if j.finished_at else None} for j, d in rows]
