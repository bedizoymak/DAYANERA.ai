"""Review queue for 'Taslak çıkarım' (draft extraction) values and pages.

Only an authorized user (owner_admin, or the owner of an uploaded document)
can confirm, edit or reject. Every decision stores actor, time, original
value and source/version provenance in the audit log.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_dep, deny, scopes_dep
from app.api.schemas import ConfirmPageIn, ConfirmValueIn, ReviewNoteIn
from app.calc.units import InvalidUnitError, to_canonical
from app.db.models import Document, DocumentChunk, DocumentPage, DocumentVersion, ExtractedValue
from app.ingestion.chunker import chunk_page
from app.services import audit, memory
from app.services.access import ScopeSet, can_view_document
from app.services.auth import AuthenticatedUser

router = APIRouter(prefix="/extractions", tags=["draft-extractions"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(v: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError as exc:
        raise HTTPException(404, "Bulunamadı.") from exc


def _authorize(db: Session, user: AuthenticatedUser, scopes: ScopeSet, doc: Document, request: Request) -> None:
    if not can_view_document(scopes, doc):
        deny(user, request, "document_scope", target_id=str(doc.id))
    if not (user.is_owner or doc.owner_id == user.id):
        deny(user, request, "confirm_requires_authorized_user", target_id=str(doc.id))


def _value_dict(ev: ExtractedValue, doc: Document, ver: DocumentVersion) -> dict:
    return {
        "id": str(ev.id), "document_id": str(doc.id), "document_title": doc.title, "version_id": str(ver.id),
        "version_number": ver.version_number, "version_active": ver.is_active, "page_number": ev.page_number,
        "locator": ev.locator, "label": ev.label, "raw_text": ev.raw_text, "context": ev.context_text,
        "value": ev.value_numeric, "unit": ev.unit, "quantity_kind": ev.quantity_kind,
        "extraction_method": ev.extraction_method, "status": ev.status, "original": ev.original,
        "confirmed_value": ev.confirmed_value_numeric, "confirmed_unit": ev.confirmed_unit,
        "confirmed_by": str(ev.confirmed_by) if ev.confirmed_by else None,
        "confirmed_at": ev.confirmed_at.isoformat() if ev.confirmed_at else None, "review_note": ev.review_note,
        "label_tr": "Taslak çıkarım" if ev.status == "draft_extraction" else None,
    }


@router.get("/values", summary="Taslak çıkarım değerleri (inceleme kuyruğu)")
def list_values(status: str = Query("draft_extraction"), document_id: str | None = None,
                limit: int = Query(200, le=1000), offset: int = 0, user: AuthenticatedUser = Depends(current_user),
                scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    q = select(ExtractedValue, Document, DocumentVersion).join(Document, Document.id == ExtractedValue.document_id) \
        .join(DocumentVersion, DocumentVersion.id == ExtractedValue.version_id)
    if status != "all":
        q = q.where(ExtractedValue.status == status)
    if document_id:
        q = q.where(ExtractedValue.document_id == _uuid(document_id))
    rows = db.execute(q.order_by(Document.title, ExtractedValue.page_number, ExtractedValue.created_at)).all()
    items = [_value_dict(ev, d, v) for ev, d, v in rows if can_view_document(scopes, d)]
    return {"items": items[offset: offset + limit], "total": len(items)}


@router.post("/values/{value_id}/confirm", summary="Taslak değeri onayla (düzenleyerek onay mümkün)")
def confirm_value(value_id: str, body: ConfirmValueIn, request: Request, user: AuthenticatedUser = Depends(current_user),
                  scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    ev = db.get(ExtractedValue, _uuid(value_id))
    if ev is None:
        raise HTTPException(404, "Değer bulunamadı.")
    doc = db.get(Document, ev.document_id)
    ver = db.get(DocumentVersion, ev.version_id)
    _authorize(db, user, scopes, doc, request)
    if ev.status != "draft_extraction":
        raise HTTPException(409, f"Bu değer zaten '{ev.status}' durumunda.")
    value = body.value if body.value is not None else ev.value_numeric
    unit = body.unit if body.unit is not None else (ev.unit or "")
    if value is None:
        raise HTTPException(422, "Onay için sayısal bir değer gerekli.")
    if ev.quantity_kind in ("length", "angle") :
        try:
            to_canonical(ev.quantity_kind, value, unit)
        except InvalidUnitError as exc:
            raise HTTPException(422, str(exc)) from exc
    edited = value != ev.value_numeric or unit != (ev.unit or "")
    ev.status = "user_confirmed"
    ev.confirmed_value_numeric = value
    ev.confirmed_unit = unit
    ev.confirmed_by = user.id
    ev.confirmed_at = _now()
    ev.review_note = body.note
    mi = memory.create_item(
        db, user, kind="technical_value", title=f"{ev.label} = {value} {unit}".strip(),
        content=f"{ev.label} = {value} {unit} (kaynak: {doc.title}, {ev.locator}, sürüm {ver.version_number}; "
                f"kullanıcı onaylı taslak çıkarım)",
        status="user_confirmed", structured={"value": value, "unit": unit, "label": ev.label,
                                             "quantity_kind": ev.quantity_kind},
        source_document_id=doc.id, source_version_id=ver.id, source_extracted_value_id=ev.id)
    audit.record(db, user.actor, "extraction.edit_confirm" if edited else "extraction.confirm",
                 target_type="extracted_value", target_id=ev.id,
                 details={"original": ev.original, "confirmed": {"value": value, "unit": unit}, "edited": edited,
                          "document_id": str(doc.id), "version_id": str(ver.id), "version": ver.version_number,
                          "page": ev.page_number, "locator": ev.locator, "memory_item_id": str(mi.id)})
    db.commit()
    return _value_dict(ev, doc, ver)


@router.post("/values/{value_id}/reject", summary="Taslak değeri reddet")
def reject_value(value_id: str, body: ReviewNoteIn, request: Request, user: AuthenticatedUser = Depends(current_user),
                 scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    ev = db.get(ExtractedValue, _uuid(value_id))
    if ev is None:
        raise HTTPException(404, "Değer bulunamadı.")
    doc = db.get(Document, ev.document_id)
    ver = db.get(DocumentVersion, ev.version_id)
    _authorize(db, user, scopes, doc, request)
    if ev.status != "draft_extraction":
        raise HTTPException(409, f"Bu değer zaten '{ev.status}' durumunda.")
    ev.status = "rejected"
    ev.confirmed_by = user.id
    ev.confirmed_at = _now()
    ev.review_note = body.note
    audit.record(db, user.actor, "extraction.reject", target_type="extracted_value", target_id=ev.id,
                 details={"original": ev.original, "document_id": str(doc.id), "version": ver.version_number,
                          "page": ev.page_number})
    db.commit()
    return _value_dict(ev, doc, ver)


# ---------------------------------------------------------------------------
def _page_dict(p: DocumentPage, doc: Document, ver: DocumentVersion, full: bool = False) -> dict:
    return {"id": str(p.id), "document_id": str(doc.id), "document_title": doc.title, "version_id": str(ver.id),
            "version_number": ver.version_number, "version_active": ver.is_active, "page_number": p.page_number,
            "locator": p.locator, "extraction_method": p.extraction_method, "confidence_status": p.confidence_status,
            "ocr_mean_confidence": p.ocr_mean_confidence, "text": p.text if full else p.text[:600],
            "chars": len(p.text), "review_note": p.review_note,
            "confirmed_at": p.confirmed_at.isoformat() if p.confirmed_at else None}


@router.get("/pages", summary="Taslak OCR/döküm sayfaları")
def list_pages(status: str = Query("draft_extraction"), document_id: str | None = None,
               limit: int = Query(100, le=500), offset: int = 0, user: AuthenticatedUser = Depends(current_user),
               scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    q = select(DocumentPage, Document, DocumentVersion).join(Document, Document.id == DocumentPage.document_id) \
        .join(DocumentVersion, DocumentVersion.id == DocumentPage.version_id) \
        .where(DocumentVersion.is_active.is_(True))
    if status != "all":
        q = q.where(DocumentPage.confidence_status == status)
    else:
        q = q.where(DocumentPage.extraction_method.in_(("ocr", "transcription")))
    if document_id:
        q = q.where(DocumentPage.document_id == _uuid(document_id))
    rows = [(p, d, v) for p, d, v in db.execute(q.order_by(Document.title, DocumentPage.page_number)).all()
            if can_view_document(scopes, d)]
    return {"items": [_page_dict(p, d, v) for p, d, v in rows[offset: offset + limit]], "total": len(rows)}


@router.get("/pages/{page_id}", summary="Taslak sayfa (tam metin)")
def get_page(page_id: str, request: Request, user: AuthenticatedUser = Depends(current_user),
             scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    p = db.get(DocumentPage, _uuid(page_id))
    if p is None:
        raise HTTPException(404, "Sayfa bulunamadı.")
    doc = db.get(Document, p.document_id)
    if not can_view_document(scopes, doc):
        deny(user, request, "document_scope", target_id=str(doc.id))
    return _page_dict(p, doc, db.get(DocumentVersion, p.version_id), full=True)


def _rebuild_page_chunks(db: Session, p: DocumentPage, status: str) -> None:
    old = db.execute(select(DocumentChunk).where(DocumentChunk.page_id == p.id)
                     .order_by(DocumentChunk.chunk_index)).scalars().all()
    method = old[0].extraction_method if old else p.extraction_method
    first = old[0] if old else None
    ver = db.get(DocumentVersion, p.version_id)
    doc = db.get(Document, p.document_id)
    db.execute(delete(DocumentChunk).where(DocumentChunk.page_id == p.id))
    db.flush()
    base = db.execute(select(func.coalesce(func.max(DocumentChunk.chunk_index), -1))
                      .where(DocumentChunk.version_id == p.version_id)).scalar() + 1
    for i, ch in enumerate(chunk_page(p.page_number, p.locator, p.text)):
        # lineage: the clause context of the page's previous chunks is kept; the text is now person-confirmed
        db.add(DocumentChunk(document_id=p.document_id, version_id=p.version_id, page_id=p.id, chunk_index=base + i,
                             page_number=p.page_number, locator=p.locator, char_start=ch.char_start,
                             char_end=ch.char_end, text=ch.text, extraction_method=method, confidence_status=status,
                             page_start=p.page_number, page_end=p.page_number,
                             clause=first.clause if first else None, heading=first.heading if first else None,
                             content_type=first.content_type if first else "text", standard_code=doc.standard_code,
                             extraction_confidence=None, source_hash=ver.sha256,
                             content_hash=hashlib.sha256(ch.text.encode("utf-8")).hexdigest(),
                             parser=p.parser, parser_version=p.parser_version))


@router.post("/pages/{page_id}/confirm", summary="OCR/döküm sayfasını onayla (düzeltilmiş metinle)")
def confirm_page(page_id: str, body: ConfirmPageIn, request: Request, user: AuthenticatedUser = Depends(current_user),
                 scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    p = db.get(DocumentPage, _uuid(page_id))
    if p is None:
        raise HTTPException(404, "Sayfa bulunamadı.")
    doc = db.get(Document, p.document_id)
    ver = db.get(DocumentVersion, p.version_id)
    _authorize(db, user, scopes, doc, request)
    if p.confidence_status != "draft_extraction":
        raise HTTPException(409, f"Sayfa '{p.confidence_status}' durumunda; yalnızca taslak sayfalar onaylanır.")
    if not ver.is_active:
        raise HTTPException(409, "Yalnızca etkin sürümün sayfaları onaylanabilir.")
    original_hash = hashlib.sha256(p.text.encode()).hexdigest()
    edited = body.text is not None and body.text.strip() != p.text
    if edited:
        p.text = body.text.strip()
    p.confidence_status = "user_confirmed"
    p.confirmed_by = user.id
    p.confirmed_at = _now()
    p.review_note = body.note
    db.flush()
    _rebuild_page_chunks(db, p, "user_confirmed")
    audit.record(db, user.actor, "extraction.page_confirm", target_type="document_page", target_id=p.id,
                 details={"document_id": str(doc.id), "version_id": str(ver.id), "version": ver.version_number,
                          "page": p.page_number, "method": p.extraction_method, "edited": edited,
                          "original_text_sha256": original_hash})
    db.commit()
    return _page_dict(p, doc, ver)


@router.post("/pages/{page_id}/reject", summary="OCR/döküm sayfasını reddet")
def reject_page(page_id: str, body: ReviewNoteIn, request: Request, user: AuthenticatedUser = Depends(current_user),
                scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    p = db.get(DocumentPage, _uuid(page_id))
    if p is None:
        raise HTTPException(404, "Sayfa bulunamadı.")
    doc = db.get(Document, p.document_id)
    ver = db.get(DocumentVersion, p.version_id)
    _authorize(db, user, scopes, doc, request)
    if p.confidence_status != "draft_extraction":
        raise HTTPException(409, f"Sayfa '{p.confidence_status}' durumunda.")
    p.confidence_status = "rejected"
    p.confirmed_by = user.id
    p.confirmed_at = _now()
    p.review_note = body.note
    db.flush()
    _rebuild_page_chunks(db, p, "rejected")
    audit.record(db, user.actor, "extraction.page_reject", target_type="document_page", target_id=p.id,
                 details={"document_id": str(doc.id), "version": ver.version_number, "page": p.page_number})
    db.commit()
    return _page_dict(p, doc, ver)
