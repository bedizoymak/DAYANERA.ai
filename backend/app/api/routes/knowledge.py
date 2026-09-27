"""Engineering knowledge: formula registry with provenance, external conflicts,
reference repositories and the self-maintenance correction memory."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_dep, require_owner, scopes_dep
from app.api.schemas import CorrectionProposalIn
from app.db.models import CalcMismatchEvent, EngineeringCorrection
from app.knowledge.validation import get_report, provenance, rule_summary
from app.services import self_maintenance
from app.services.access import ScopeSet
from app.services.auth import AuthenticatedUser
from app.services.calculations import DbEvidenceResolver

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(404, "Bulunamadı.") from exc


@router.get("/summary", summary="Bilgi kaydı ve öz-bakım sayıları")
def summary(user: AuthenticatedUser = Depends(current_user), db: Session = Depends(db_dep)) -> dict:
    return self_maintenance.stats(db)


@router.get("/formulas", summary="Formül kaydı (durum hesaplanır, beyan edilmez)")
def formulas(status: str | None = Query(None, pattern="^(VERIFIED|CANDIDATE|UNVERIFIED|CONFLICT|REJECTED)$"),
             family: str | None = None, user: AuthenticatedUser = Depends(current_user)) -> list[dict]:
    report = get_report()
    out = [rule_summary(v) for v in report.results.values()]
    return [r for r in out if (status is None or r["status"] == status) and (family is None or r["family"] == family)]


@router.get("/formulas/{rule_id}", summary="Bu formül nereden geldi? (ISO pasajı, motor, dış uygulamalar, testler)")
def formula(rule_id: str, user: AuthenticatedUser = Depends(current_user), scopes: ScopeSet = Depends(scopes_dep),
            db: Session = Depends(db_dep)) -> dict:
    data = provenance(rule_id, DbEvidenceResolver(db, scopes))
    if data is None:
        raise HTTPException(404, "Formül bulunamadı.")
    return data


@router.get("/conflicts", summary="Yetkili tanımla çelişen dış uygulamalar")
def conflicts(user: AuthenticatedUser = Depends(current_user)) -> list[dict]:
    return get_report().conflicts()


@router.get("/references", summary="İncelenen referans depoları (lisans, commit, sınıflandırma)")
def references(user: AuthenticatedUser = Depends(current_user)) -> list[dict]:
    return list(get_report().registry.repositories)


@router.get("/corrections", summary="Düzeltme hafızası (üye: yalnızca VERIFIED)")
def corrections(status: str | None = Query(None, pattern="^(CANDIDATE|TESTING|VERIFIED|REJECTED)$"),
                user: AuthenticatedUser = Depends(current_user), db: Session = Depends(db_dep)) -> list[dict]:
    q = select(EngineeringCorrection).order_by(EngineeringCorrection.updated_at.desc())
    if not user.is_owner:
        q = q.where(EngineeringCorrection.status == "VERIFIED")
    if status:
        q = q.where(EngineeringCorrection.status == status)
    return [self_maintenance.correction_public(c) for c in db.execute(q).scalars()]


@router.get("/corrections/{correction_id}", summary="Düzeltme ayrıntısı ve regresyon sonucu")
def correction(correction_id: str, user: AuthenticatedUser = Depends(current_user), db: Session = Depends(db_dep)) -> dict:
    c = db.get(EngineeringCorrection, _uuid(correction_id))
    if c is None or (not user.is_owner and c.status != "VERIFIED"):
        raise HTTPException(404, "Düzeltme bulunamadı.")
    return self_maintenance.correction_public(c)


@router.post("/corrections/{correction_id}/verify", summary="Deterministik regresyonu yeniden çalıştır (yalnızca sahip)")
def verify(correction_id: str, user: AuthenticatedUser = Depends(require_owner), db: Session = Depends(db_dep)) -> dict:
    c = db.get(EngineeringCorrection, _uuid(correction_id))
    if c is None:
        raise HTTPException(404, "Düzeltme bulunamadı.")
    self_maintenance.verify_correction(db, c, user.actor)
    db.commit()
    return self_maintenance.correction_public(c)


@router.post("/corrections", status_code=201, summary="Düzeltme önerisi (kişi/LLM); yalnızca regresyon terfi ettirir")
def propose(body: CorrectionProposalIn, user: AuthenticatedUser = Depends(require_owner),
            db: Session = Depends(db_dep)) -> dict:
    try:
        c = self_maintenance.submit_proposal(
            db, user.actor, calc_type=body.calc_type, family=body.family, root_cause=body.root_cause,
            claims=[cl.model_dump() for cl in body.claims], statement=body.statement,
            proposed_by=f"{body.proposed_by}:{user.username}")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return self_maintenance.correction_public(c)


@router.get("/mismatches", summary="Yapılandırılmış LLM-motor uyuşmazlık olayları (yalnızca sahip)")
def mismatches(limit: int = Query(50, ge=1, le=500), user: AuthenticatedUser = Depends(require_owner),
               db: Session = Depends(db_dep)) -> list[dict]:
    rows = db.execute(select(CalcMismatchEvent).order_by(CalcMismatchEvent.created_at.desc()).limit(limit)).scalars()
    return [self_maintenance.event_public(e) for e in rows]


@router.get("/mismatches/{event_id}", summary="Uyuşmazlık olayı ayrıntısı (yalnızca sahip)")
def mismatch(event_id: str, user: AuthenticatedUser = Depends(require_owner), db: Session = Depends(db_dep)) -> dict:
    e = db.get(CalcMismatchEvent, _uuid(event_id))
    if e is None:
        raise HTTPException(404, "Olay bulunamadı.")
    return self_maintenance.event_public(e)
