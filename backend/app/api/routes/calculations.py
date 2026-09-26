"""Deterministic calculation endpoints."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_dep, deny, provider_dep, scopes_dep
from app.api.schemas import CalcIn, CalcOut
from app.calc.engine import CalculationEngine
from app.db.models import Calculation
from app.inference.base import LLMProvider
from app.services.access import ScopeSet
from app.services.auth import AuthenticatedUser
from app.services.calculations import DbEvidenceResolver, InputSpecIn, resolve_inputs, run_calculation

router = APIRouter(prefix="/calculations", tags=["calculations"])


def _out(c: Calculation) -> dict:
    return {"id": str(c.id), "calc_type": c.calc_type, "status": c.status, "engine_version": c.engine_version,
            "result": c.result, "inputs": c.inputs, "llm_draft": c.llm_draft, "comparison": c.comparison,
            "mismatch": c.mismatch, "created_at": c.created_at.isoformat(),
            "message_id": str(c.message_id) if c.message_id else None}


@router.get("/types", summary="Desteklenen hesap türleri ve kaynak kullanılabilirliği")
def types(user: AuthenticatedUser = Depends(current_user), scopes: ScopeSet = Depends(scopes_dep),
          db: Session = Depends(db_dep)) -> list[dict]:
    engine = CalculationEngine(DbEvidenceResolver(db, scopes))
    out = []
    for info in engine.list_types():
        avail = engine.evidence_available(info["calc_type"])
        out.append({**info, "evidence_available": avail, "available": all(avail.values())})
    return out


@router.post("", response_model=CalcOut, status_code=201, summary="Deterministik hesap çalıştır")
def run(body: CalcIn, user: AuthenticatedUser = Depends(current_user), scopes: ScopeSet = Depends(scopes_dep),
        db: Session = Depends(db_dep), provider: LLMProvider = Depends(provider_dep)) -> dict:
    raw = {k: InputSpecIn(value=v.value, unit=v.unit, provenance_kind=v.provenance, ref_id=v.ref_id)
           for k, v in body.inputs.items()}
    inputs = resolve_inputs(db, scopes, raw, None)
    draft_record = {"outputs": body.llm_draft_outputs, "source": "request"} if body.llm_draft_outputs is not None else None
    calc, _result, _cmp = run_calculation(
        db, user, scopes, body.calc_type, inputs, provider=provider, compare_with_llm=body.compare_with_llm,
        conversation_id=uuid.UUID(body.conversation_id) if body.conversation_id else None, draft_record=draft_record)
    db.commit()
    return _out(calc)


@router.get("", response_model=list[CalcOut], summary="Hesap geçmişi")
def history(limit: int = Query(50, le=500), user: AuthenticatedUser = Depends(current_user),
            db: Session = Depends(db_dep)) -> list[dict]:
    q = select(Calculation).order_by(Calculation.created_at.desc()).limit(limit)
    if not user.is_owner:
        q = q.where(Calculation.user_id == user.id)
    return [_out(c) for c in db.execute(q).scalars()]


@router.get("/{calc_id}", response_model=CalcOut, summary="Hesap ayrıntısı")
def get_calc(calc_id: str, request: Request, user: AuthenticatedUser = Depends(current_user),
             db: Session = Depends(db_dep)) -> dict:
    try:
        c = db.get(Calculation, uuid.UUID(calc_id))
    except ValueError as exc:
        raise HTTPException(404, "Bulunamadı.") from exc
    if c is None:
        raise HTTPException(404, "Hesap bulunamadı.")
    if not user.is_owner and c.user_id != user.id:
        deny(user, request, "calculation_scope", target_id=str(c.id))
    return _out(c)
