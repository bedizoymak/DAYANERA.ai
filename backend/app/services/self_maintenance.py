"""Self-maintenance: mismatch events -> diagnosis -> correction candidate ->
deterministic regression -> correction memory -> targeted retrieval.

Authority rules enforced here:

* the engine's numbers are always what the user sees; a mismatch never changes them;
* only :func:`verify_correction` changes a correction's status, and it only
  runs the deterministic regression (no LLM call is possible on that path);
* only VERIFIED corrections are retrieved into future Qwen prompts, and a
  verification goes stale (re-run) when the engine version or the formula
  registry changes;
* mismatch events store numeric canonical inputs, never message text.

Lifecycle transitions are written to the append-only audit log.
"""
from __future__ import annotations

import logging
import math
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.calc.engine import ENGINE_VERSION
from app.calc.types import CalcResult
from app.db.models import CalcMismatchEvent, Calculation, EngineeringCorrection
from app.knowledge.corrections import CorrectionSpec, build_from_diagnosis, build_proposal
from app.knowledge.diagnosis import ROOT_CAUSES, diagnose
from app.knowledge.regression import registry_fingerprint, run_regression
from app.knowledge.validation import get_report
from app.services import audit
from app.services.access import ScopeSet
from app.services.audit import Actor

log = logging.getLogger("dayanera.selfmaint")

CORRECTION_STATUSES = ("CANDIDATE", "TESTING", "VERIFIED", "REJECTED")
MAX_RETRIEVED_CORRECTIONS = 3


def _now() -> datetime:
    return datetime.now(UTC)


def system_scopes() -> ScopeSet:
    """Knowledge verification is corpus-wide: the verified ISO corpus is not user-specific."""
    return ScopeSet(is_owner=True, user_id=uuid.UUID(int=0))


def _resolver(db: Session):
    from app.services.calculations import DbEvidenceResolver

    return DbEvidenceResolver(db, system_scopes())


def spec_of(c: EngineeringCorrection) -> CorrectionSpec:
    return CorrectionSpec(key=c.correction_key, family=c.family, root_cause=c.root_cause, calc_type=c.calc_type,
                          output_keys=list(c.output_keys or []), formula_ids=list(c.formula_ids or []), title=c.title,
                          statement=c.statement, claims=list(c.claims or []), invariants=list(c.invariants or []),
                          reference_values=list(c.reference_values or []), source=c.source, proposed_by=c.proposed_by)


def is_stale(c: EngineeringCorrection) -> bool:
    """A verdict holds only for the engine version and registry it was reached with."""
    return c.engine_version != ENGINE_VERSION or c.registry_fingerprint != registry_fingerprint()


def ensure_correction(db: Session, spec: CorrectionSpec, actor: Actor) -> tuple[EngineeringCorrection, bool]:
    c = db.execute(select(EngineeringCorrection).where(EngineeringCorrection.correction_key == spec.key)).scalar_one_or_none()
    if c is not None:
        return c, False
    c = EngineeringCorrection(
        correction_key=spec.key, family=spec.family, root_cause=spec.root_cause, calc_type=spec.calc_type,
        output_keys=spec.output_keys, formula_ids=spec.formula_ids, title=spec.title, statement=spec.statement,
        claims=spec.claims, invariants=spec.invariants, reference_values=spec.reference_values, source=spec.source,
        proposed_by=spec.proposed_by, status="CANDIDATE", regression_status="not_run", regression_result={},
        occurrences=0,
    )
    db.add(c)
    db.flush()
    audit.record(db, actor, "knowledge.correction.created", target_type="engineering_correction", target_id=c.id,
                 details={"key": c.correction_key, "source": c.source, "proposed_by": c.proposed_by,
                          "formula_ids": c.formula_ids})
    log.info("selfmaint.correction.candidate key=%s source=%s", c.correction_key, c.source)
    return c, True


def _transition(db: Session, c: EngineeringCorrection, status: str, actor: Actor, **details: Any) -> None:
    old = c.status
    c.status = status
    c.updated_at = _now()
    audit.record(db, actor, "knowledge.correction.status", target_type="engineering_correction", target_id=c.id,
                 details={"key": c.correction_key, "from": old, "to": status, **details})


def verify_correction(db: Session, c: EngineeringCorrection, actor: Actor) -> EngineeringCorrection:
    """CANDIDATE -> TESTING -> VERIFIED | REJECTED, decided only by the deterministic regression.

    A blocked run (an ISO passage missing from the active corpus) or a
    correction without an authoritative VERIFIED formula returns to CANDIDATE:
    it is neither trusted nor rejected.
    """
    _transition(db, c, "TESTING", actor, engine_version=ENGINE_VERSION)
    c.regression_status = "running"
    db.flush()
    result = run_regression(spec_of(c), _resolver(db))
    c.regression_result = result.as_dict()
    c.regression_status = result.status
    c.engine_version = ENGINE_VERSION
    c.registry_fingerprint = result.registry_fingerprint
    summary = {"regression": result.status, "cases": result.cases, "checks": result.checks,
               "failures": len(result.failures), "duration_ms": result.duration_ms}
    if result.status == "passed":
        c.verified_at = _now()
        c.rejected_reason = None
        _transition(db, c, "VERIFIED", actor, **summary)
        log.info("selfmaint.correction.verified key=%s cases=%d checks=%d ms=%d", c.correction_key, result.cases,
                 result.checks, result.duration_ms)
    elif result.status == "failed":
        c.verified_at = None
        first = result.failures[0] if result.failures else {}
        c.rejected_reason = result.reason or f"Regresyon başarısız: {first.get('check', '?')}"
        _transition(db, c, "REJECTED", actor, **summary)
        log.warning("selfmaint.correction.rejected key=%s first_failure=%s", c.correction_key, first.get("check"))
    else:
        c.verified_at = None
        _transition(db, c, "CANDIDATE", actor, **summary, reason=result.reason or "; ".join(result.blocked_reason)[:500])
        log.info("selfmaint.correction.%s key=%s", result.status, c.correction_key)
    db.flush()
    return c


def _needs_run(c: EngineeringCorrection) -> bool:
    if c.status in ("CANDIDATE", "TESTING"):
        return True
    return is_stale(c)  # VERIFIED / REJECTED verdicts are re-checked after an engine or registry change


def _authority_sources(result: CalcResult, source_ids: list[str]) -> list[dict[str, Any]]:
    keep = set(source_ids)
    return [{"requirement_id": e["requirement_id"], "standard_code": e.get("standard_code"),
             "document_id": e.get("document_id"), "version_id": e.get("version_id"),
             "page_number": e.get("page_number"), "locator": e.get("locator")}
            for e in result.evidence if e.get("requirement_id") in keep]


def process_mismatch(db: Session, calc: Calculation, result: CalcResult, comparison: dict[str, Any],
                     draft: dict[str, Any] | None, actor: Actor) -> dict[str, Any]:
    """Turn a detected mismatch into a structured event, a diagnosis and a verified (or not) correction."""
    canonical = {i["key"]: i["canonical_value"] for i in result.inputs if isinstance(i.get("canonical_value"), (int, float))}
    outputs = result.to_dict()["outputs"]
    diagnosis = diagnose(result.calc_type, canonical, outputs, comparison)
    report = get_report()
    items = comparison.get("items", [])
    bad = [i["key"] for i in items if i.get("status") in ("mismatch", "non_numeric")]
    field_class = {f["key"]: f["cls"] for f in diagnosis["fields"]}
    fields = [{"key": i["key"], "engine": i.get("engine"), "llm": i.get("llm"), "status": i.get("status"),
               "abs_error": i.get("abs_diff"),
               "rel_error": (abs(i["llm"] - i["engine"]) / max(abs(i["engine"]), 1e-12)
                             if isinstance(i.get("llm"), (int, float)) and math.isfinite(i["llm"]) else None),
               "tolerance": i.get("tolerance"), "class": field_class.get(i["key"])} for i in items]
    event = CalcMismatchEvent(
        calculation_id=calc.id, calc_type=result.calc_type, engine_version=result.engine_version,
        model=(draft or {}).get("model"), normalized_inputs=diagnosis["resolved_inputs"],
        assumptions=list(result.assumptions), llm_values=(draft or {}).get("outputs") or {},
        engine_values={o["key"]: o["value"] for o in outputs}, fields=fields, mismatching_fields=bad,
        formula_ids=diagnosis["formula_ids"],
        authority_sources=_authority_sources(result, diagnosis["authoritative_sources"]),
        supporting_evidence=[{"rule_id": fid, "status": report.status(fid),
                              "supporting_repositories": report.results[fid].supporting_repositories}
                             for fid in diagnosis["formula_ids"] if fid in report.results],
        suspected_class=diagnosis["primary_class"], diagnosis=diagnosis, disposition="open",
    )
    db.add(event)
    db.flush()
    log.info("selfmaint.mismatch event=%s calc_type=%s fields=%s class=%s", event.id, result.calc_type, bad,
             diagnosis["primary_class"])
    specs = build_from_diagnosis(result.calc_type, diagnosis)
    corrections: list[EngineeringCorrection] = []
    for spec in specs:
        c, _created = ensure_correction(db, spec, actor)
        c.occurrences = (c.occurrences or 0) + 1
        c.last_seen_at = _now()
        if _needs_run(c):
            verify_correction(db, c, actor)
        corrections.append(c)
    primary = corrections[0] if corrections else None
    if primary is None:
        event.disposition, event.regression_status = "no_authority", "not_applicable"
    else:
        event.correction_id = primary.id
        event.regression_status = primary.regression_status
        if primary.status == "VERIFIED":
            event.disposition = "known_verified_correction" if primary.occurrences > 1 else "verified_correction"
        elif primary.status == "REJECTED":
            event.disposition = "rejected_correction"
        else:
            event.disposition = "no_authority" if primary.regression_status == "not_applicable" else "blocked"
    audit.record(db, actor, "knowledge.mismatch.recorded", target_type="calc_mismatch_event", target_id=event.id,
                 details={"calculation_id": str(calc.id), "class": event.suspected_class, "fields": bad,
                          "correction": primary.correction_key if primary else None, "disposition": event.disposition})
    db.flush()
    return {
        "event_id": str(event.id), "suspected_class": event.suspected_class, "fields": bad,
        "draft_internally_consistent": diagnosis.get("draft_internally_consistent"),
        "formula_ids": diagnosis["formula_ids"], "disposition": event.disposition,
        "corrections": [correction_public(c, brief=True) for c in corrections],
    }


def submit_proposal(db: Session, actor: Actor, *, calc_type: str, family: str, root_cause: str,
                    claims: list[dict[str, str]], statement: str, proposed_by: str) -> EngineeringCorrection:
    """A correction proposed by a person or an LLM: a candidate that only the regression can promote."""
    if root_cause not in ROOT_CAUSES:
        raise ValueError(f"bilinmeyen kök neden: {root_cause}")
    spec = build_proposal(calc_type, family, root_cause, claims, statement, proposed_by)
    c, _ = ensure_correction(db, spec, actor)
    return verify_correction(db, c, actor)


def reverify_stale(db: Session, actor: Actor) -> list[str]:
    """Re-run every correction whose verdict predates the current engine or registry."""
    rows = db.execute(select(EngineeringCorrection)).scalars().all()
    done = []
    for c in rows:
        if _needs_run(c):
            verify_correction(db, c, actor)
            done.append(c.correction_key)
    return done


# --------------------------------------------------------------------------- retrieval
def retrieve_context(db: Session, calc_type: str, output_keys: list[str], mode: str) -> dict[str, Any]:
    """VERIFIED knowledge for a Qwen calculation draft: never candidates, never repository code."""
    if mode == "off":
        return {"text": "", "formula_ids": [], "correction_ids": []}
    report = get_report()
    rows = db.execute(select(EngineeringCorrection).where(EngineeringCorrection.calc_type == calc_type,
                                                          EngineeringCorrection.status == "VERIFIED")
                      .order_by(EngineeringCorrection.occurrences.desc(), EngineeringCorrection.updated_at.desc())
                      ).scalars().all()
    wanted = set(output_keys)
    corrections = [c for c in rows if not is_stale(c) and wanted & set(c.output_keys or [])][:MAX_RETRIEVED_CORRECTIONS]
    families = {c.family for c in corrections}
    formulas = []
    for rule, binding in report.registry.rules_for_engine(calc_type):
        if binding.output_key not in wanted or report.status(rule.id) != "VERIFIED":
            continue
        if mode == "targeted" and rule.family not in families:
            continue
        if rule.id not in {f.id for f in formulas}:
            formulas.append(rule)
    lines: list[str] = []
    if formulas:
        lines.append("DOĞRULANMIŞ FORMÜLLER (DAYANERA formül kaydı; ISO kaynaklı, hesap motoruyla doğrulanmış):")
        lines += [f"- {r.display} [{r.iso_citation}]" for r in formulas]
    if corrections:
        lines.append("DOĞRULANMIŞ DÜZELTME NOTLARI (önceki uyuşmazlıklardan; deterministik regresyonla doğrulandı):")
        lines += [f"- {c.statement}" for c in corrections]
    if lines:
        lines.append("Sayısal otorite deterministik hesap motorudur; taslağın motorla karşılaştırılacaktır.")
    ctx = {"text": "\n".join(lines), "formula_ids": [r.id for r in formulas],
           "correction_ids": [str(c.id) for c in corrections], "correction_keys": [c.correction_key for c in corrections]}
    if lines:
        log.info("selfmaint.retrieval calc_type=%s formulas=%d corrections=%d chars=%d", calc_type, len(formulas),
                 len(corrections), len(ctx["text"]))
    return ctx


# --------------------------------------------------------------------------- views
def correction_public(c: EngineeringCorrection, brief: bool = False) -> dict[str, Any]:
    reg = c.regression_result or {}
    out = {"id": str(c.id), "key": c.correction_key, "family": c.family, "root_cause": c.root_cause,
           "calc_type": c.calc_type, "status": c.status, "regression_status": c.regression_status,
           "cases": reg.get("cases", 0), "checks": reg.get("checks", 0), "occurrences": c.occurrences,
           "stale": is_stale(c) if c.status in ("VERIFIED", "REJECTED") else False}
    if brief:
        return out
    return {**out, "title": c.title, "statement": c.statement, "output_keys": c.output_keys,
            "formula_ids": c.formula_ids, "claims": c.claims, "invariants": c.invariants,
            "reference_values": c.reference_values, "source": c.source, "proposed_by": c.proposed_by,
            "engine_version": c.engine_version, "registry_fingerprint": c.registry_fingerprint,
            "regression": reg, "rejected_reason": c.rejected_reason,
            "verified_at": c.verified_at.isoformat() if c.verified_at else None,
            "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
            "created_at": c.created_at.isoformat() if c.created_at else None}


def event_public(e: CalcMismatchEvent) -> dict[str, Any]:
    return {"id": str(e.id), "calculation_id": str(e.calculation_id) if e.calculation_id else None,
            "created_at": e.created_at.isoformat() if e.created_at else None, "calc_type": e.calc_type,
            "engine_version": e.engine_version, "model": e.model, "normalized_inputs": e.normalized_inputs,
            "assumptions": e.assumptions, "llm_values": e.llm_values, "engine_values": e.engine_values,
            "fields": e.fields, "mismatching_fields": e.mismatching_fields, "formula_ids": e.formula_ids,
            "authority_sources": e.authority_sources, "supporting_evidence": e.supporting_evidence,
            "suspected_class": e.suspected_class, "llm_suggested_class": e.llm_suggested_class,
            "diagnosis": e.diagnosis, "correction_id": str(e.correction_id) if e.correction_id else None,
            "regression_status": e.regression_status, "disposition": e.disposition}


def stats(db: Session) -> dict[str, Any]:
    report = get_report()
    summary = report.summary()
    corr = dict(db.execute(select(EngineeringCorrection.status, func.count()).group_by(EngineeringCorrection.status)).all())
    classes = dict(db.execute(select(CalcMismatchEvent.suspected_class, func.count())
                              .group_by(CalcMismatchEvent.suspected_class)).all())
    return {
        "registry": {"rules": summary["rules"], "by_status": summary["by_status"],
                     "conflict_records": summary["conflict_records"], "observations": summary["observations"],
                     "engine_checked_cases": summary["engine_checked_cases"]},
        "mismatch_events": sum(classes.values()), "mismatch_classes": classes,
        "corrections": {s: corr.get(s, 0) for s in CORRECTION_STATUSES},
        "engine_version": ENGINE_VERSION, "registry_fingerprint": registry_fingerprint(),
    }
