"""Retrieval / source provenance endpoint (search only, no generation)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import current_user, db_dep, scopes_dep
from app.api.schemas import RetrievalIn
from app.services import audit
from app.services.access import ScopeSet
from app.services.auth import AuthenticatedUser
from app.services.retrieval import plan_query, search

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.post("/search", summary="Etkin doğrulanmış korpusta pasaj ara (kökenle)")
def retrieval_search(body: RetrievalIn, user: AuthenticatedUser = Depends(current_user),
                     scopes: ScopeSet = Depends(scopes_dep), db: Session = Depends(db_dep)) -> dict:
    plan = plan_query(body.query)
    passages = search(db, scopes, plan, top_k=body.top_k)
    audit.record(db, user.actor, "retrieval.search", target_type="corpus",
                 details={"hits": len(passages), "concepts": plan.concepts})
    db.commit()
    return {"plan": {"concepts": plan.concepts, "codes": plan.codes, "tsquery_en": plan.tsquery_en},
            "passages": [{"document_id": str(p.document_id), "version_id": str(p.version_id),
                          "version_number": p.version_number, "document_title": p.document_title,
                          "standard_code": p.standard_code, "page_number": p.page_number, "locator": p.locator,
                          "excerpt": p.text[:1200], "score": p.score, "coverage": p.coverage,
                          "confidence_status": p.confidence_status} for p in passages]}
