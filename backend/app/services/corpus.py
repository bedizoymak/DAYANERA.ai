"""Verified-corpus approval: the only way a version becomes ``verified``.

An owner_admin approves one active, indexed version of a verified-corpus
document after its quality gates ran. ``needs_review`` versions need a written
review note; ``failed`` and ``candidate`` versions cannot be approved. The
approval is bound to the fingerprint of the chunks stored at that moment, so a
later re-extraction that changes anything drops the version out of the verified
corpus until it is approved again. Every decision is audited.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentChunk, DocumentVersion, KnowledgeArea, User
from app.ingestion.quality import fingerprint
from app.services import audit
from app.services.audit import Actor

APPROVABLE = ("extracted", "needs_review")


class CorpusError(ValueError):
    """A lifecycle rule forbids the requested transition."""


@dataclass
class Reviewer:
    user_id: uuid.UUID
    username: str
    session_actor: Actor | None = None  # API requests audit their session and client address

    @property
    def actor(self) -> Actor:
        return self.session_actor or Actor(user_id=self.user_id, username=self.username)


def reviewer_by_username(db: Session, username: str) -> Reviewer:
    u = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if u is None or not u.is_active or u.role != "owner_admin":
        raise CorpusError(f"'{username}' etkin bir owner_admin değil: onay yalnızca sahip/yönetici tarafından verilir.")
    return Reviewer(u.id, u.username)


def current_fingerprint(db: Session, ver: DocumentVersion, doc: Document) -> str:
    """Same parts as quality.evaluate: text hash, plus the structure hash of engineering chunks (0004)."""
    rows = db.execute(select(DocumentChunk.content_hash, DocumentChunk.meta_hash).where(DocumentChunk.version_id == ver.id)
                      .order_by(DocumentChunk.chunk_index)).all()
    return fingerprint(ver.parser, ver.parser_version, doc.standard_code,
                       [f"{h or ''}:{m}" if m else (h or "") for h, m in rows])


def _load(db: Session, version_id: uuid.UUID) -> tuple[DocumentVersion, Document, KnowledgeArea | None]:
    ver = db.get(DocumentVersion, version_id)
    if ver is None:
        raise CorpusError("Sürüm bulunamadı.")
    doc = db.get(Document, ver.document_id)
    area = db.get(KnowledgeArea, doc.knowledge_area_id) if doc.knowledge_area_id else None
    return ver, doc, area


def approve(db: Session, version_id: uuid.UUID, reviewer: Reviewer, note: str | None = None) -> DocumentVersion:
    ver, doc, area = _load(db, version_id)
    if not (area and area.is_verified_corpus):
        raise CorpusError("Yalnızca doğrulanmış korpus alanındaki belgeler onaylanabilir.")
    if doc.status != "active" or not ver.is_active or ver.state != "active" or ver.ingestion_status != "indexed":
        raise CorpusError("Yalnızca etkin ve indekslenmiş sürüm onaylanabilir.")
    if ver.corpus_status == "verified":
        return ver
    if ver.corpus_status not in APPROVABLE:
        raise CorpusError(f"'{ver.corpus_status}' durumundaki sürüm onaylanamaz (kalite kapıları geçilmedi).")
    note = (note or "").strip() or None
    if ver.corpus_status == "needs_review" and not note:
        raise CorpusError("İnceleme gerektiren sürüm için inceleme notu zorunludur (ne kontrol edildi?).")
    fp = current_fingerprint(db, ver, doc)
    prev = ver.corpus_status
    ver.corpus_status = "verified"
    ver.verified_by = reviewer.user_id
    ver.verified_at = datetime.now(timezone.utc)
    ver.verified_fingerprint = fp
    ver.review_note = note
    gates = {g["id"]: g["status"] for g in (ver.quality_report or {}).get("gates", [])}
    audit.record(db, reviewer.actor, "corpus.verified", target_type="document", target_id=doc.id,
                 details={"version": ver.version_number, "version_id": str(ver.id), "standard_code": doc.standard_code,
                          "previous_status": prev, "fingerprint": fp, "sha256": ver.sha256, "note": note,
                          "gates": gates})
    db.flush()
    return ver


def revoke(db: Session, version_id: uuid.UUID, reviewer: Reviewer, note: str) -> DocumentVersion:
    """Take a verified version out of the verified corpus (back to needs_review)."""
    ver, doc, _area = _load(db, version_id)
    if ver.corpus_status != "verified":
        raise CorpusError("Sürüm doğrulanmış değil.")
    if not (note or "").strip():
        raise CorpusError("Geri alma gerekçesi zorunludur.")
    ver.verified_by = ver.verified_at = ver.verified_fingerprint = None
    ver.corpus_status = "needs_review"
    ver.review_note = note.strip()
    audit.record(db, reviewer.actor, "corpus.verification_revoked", target_type="document", target_id=doc.id,
                 details={"version": ver.version_number, "reason": "manual", "note": note.strip()})
    db.flush()
    return ver


def reject(db: Session, version_id: uuid.UUID, reviewer: Reviewer, note: str) -> DocumentVersion:
    """Reviewer decision: the extraction is not fit for the verified corpus (failed)."""
    ver, doc, _area = _load(db, version_id)
    if not (note or "").strip():
        raise CorpusError("Red gerekçesi zorunludur.")
    if ver.corpus_status == "verified":
        ver.verified_by = ver.verified_at = ver.verified_fingerprint = None
    prev = ver.corpus_status
    ver.corpus_status = "failed"
    ver.review_note = note.strip()
    audit.record(db, reviewer.actor, "corpus.rejected", target_type="document", target_id=doc.id,
                 details={"version": ver.version_number, "previous_status": prev, "note": note.strip()})
    db.flush()
    return ver


_STATUS_SQL = """
SELECT d.id AS document_id, d.standard_code, d.title, d.status AS document_status, ka.slug AS area,
       ka.is_verified_corpus, v.id AS version_id, v.version_number, v.ingestion_status, v.corpus_status,
       v.parser, v.page_count, v.sha256, v.verified_at, v.review_note,
       v.quality_report->>'outcome' AS outcome,
       (SELECT count(*) FROM document_chunks c WHERE c.version_id = v.id) AS chunks
FROM documents d
JOIN document_versions v ON v.id = d.current_version_id
LEFT JOIN knowledge_areas ka ON ka.id = d.knowledge_area_id
WHERE d.status <> 'deleted'
ORDER BY ka.is_verified_corpus DESC, d.standard_code NULLS LAST, d.title
"""


def status_rows(db: Session) -> list[dict]:
    rows = []
    for r in db.execute(text(_STATUS_SQL)).mappings():
        ver = db.get(DocumentVersion, r["version_id"])
        review = [g for g in (ver.quality_report or {}).get("gates", []) if g.get("status") in ("review", "fail")]
        rows.append({**{k: (str(v) if isinstance(v, (uuid.UUID, datetime)) else v) for k, v in r.items()},
                     "attention": review})
    return rows


def find_versions(db: Session, code: str) -> list[DocumentVersion]:
    """Active versions of verified-corpus documents whose standard code starts with ``code``."""
    rows = db.execute(
        select(DocumentVersion).join(Document, Document.current_version_id == DocumentVersion.id)
        .join(KnowledgeArea, KnowledgeArea.id == Document.knowledge_area_id)
        .where(KnowledgeArea.is_verified_corpus.is_(True), Document.status == "active",
               Document.standard_code.is_not(None))).scalars().all()
    want = code.strip()
    out = []
    for v in rows:
        sc = db.get(Document, v.document_id).standard_code or ""
        if sc == want or sc.startswith(want + ":") or sc.startswith(want + "-") and ":" not in want:
            out.append(v)
    return out
