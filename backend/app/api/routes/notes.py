"""Recommendation notes (agent-notes/). Never alters source code."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_dep, settings_dep
from app.api.schemas import NoteIn, NotePatchIn
from app.core.config import Settings
from app.services.auth import AuthenticatedUser
from app.services.notes import NoteError, NotesService

router = APIRouter(prefix="/notes", tags=["recommendation-notes"])


@router.get("", summary="Öneri notları")
def list_notes(user: AuthenticatedUser = Depends(current_user), db: Session = Depends(db_dep),
               settings: Settings = Depends(settings_dep)) -> list[dict]:
    return NotesService(settings).list(db)


@router.get("/{filename}", summary="Öneri notu (Markdown)")
def read_note(filename: str, user: AuthenticatedUser = Depends(current_user),
              settings: Settings = Depends(settings_dep)) -> dict:
    try:
        return NotesService(settings).read(filename)
    except NoteError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("", status_code=201, summary="Öneri notu oluştur (yalnızca agent-notes/)")
def create_note(body: NoteIn, user: AuthenticatedUser = Depends(current_user), db: Session = Depends(db_dep),
                settings: Settings = Depends(settings_dep)) -> dict:
    try:
        note = NotesService(settings).create(
            db, user.actor, title=body.title, author=body.author or f"{user.display_name} ({user.username})",
            status=body.status,
            fields={"recommendation": body.recommendation, "rationale": body.rationale,
                    "affected_areas": body.affected_areas, "expected_benefit": body.expected_benefit,
                    "risks": body.risks})
    except NoteError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return NotesService(settings).read(note.filename)


@router.patch("/{filename}", summary="Öneri notunu güncelle (durum/içerik)")
def update_note(filename: str, body: NotePatchIn, user: AuthenticatedUser = Depends(current_user),
                db: Session = Depends(db_dep), settings: Settings = Depends(settings_dep)) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if k not in ("title", "status") and v is not None}
    try:
        NotesService(settings).update(db, user.actor, filename, status=body.status, fields=fields, title=body.title)
    except NoteError as exc:
        raise HTTPException(404 if "bulunamadı" in str(exc) else 422, str(exc)) from exc
    db.commit()
    return NotesService(settings).read(filename)
