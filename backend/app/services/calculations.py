"""Calculation service: DB evidence resolver, provenance resolution, Qwen
draft comparison, persistence and audit."""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.calc.compare import compare
from app.calc.engine import ENGINE_VERSION, CalculationEngine
from app.calc.evidence import PageRecord, find_in_pages
from app.calc.rules import RULES
from app.calc.types import CalcRequest, CalcResult, EvidenceMatch, EvidenceRequirement, InputValue, Provenance
from app.db.models import Calculation, Document, ExtractedValue, MemoryItem
from app.inference.base import GenerationOptions, LLMProvider, ProviderError
from app.inference.prompts import calc_draft_messages, calc_mapping_messages
from app.services import audit
from app.services.access import ScopeSet, can_view_document
from app.services.auth import AuthenticatedUser

log = logging.getLogger(__name__)

_PAGES_SQL = """
SELECT p.page_number, p.locator, p.text, p.confidence_status, v.id AS version_id, v.version_number,
       d.id AS document_id, d.title, d.standard_code
FROM document_pages p
JOIN document_versions v ON v.id = p.version_id
JOIN documents d ON d.id = p.document_id
JOIN knowledge_areas ka ON ka.id = d.knowledge_area_id
WHERE ka.is_verified_corpus = true
  AND d.status = 'active'
  AND v.is_active = true AND v.state = 'active' AND v.ingestion_status = 'indexed'
  AND p.confidence_status IN ('verified_source', 'user_confirmed')
  AND d.standard_code ~* :code_re
  AND {scope}
"""


class DbEvidenceResolver:
    """Resolves evidence only from active, indexed, verified pages within the user's scope."""

    def __init__(self, db: Session, scopes: ScopeSet):
        self.db = db
        self.scopes = scopes
        self._cache: dict[str, list[PageRecord]] = {}

    def _pages(self, code_re: str) -> list[PageRecord]:
        if code_re not in self._cache:
            scope_clause, params = self.scopes.sql_filter("d")
            rows = self.db.execute(text(_PAGES_SQL.format(scope=scope_clause)), {"code_re": code_re, **params}).all()
            self._cache[code_re] = [
                PageRecord(document_id=str(r.document_id), version_id=str(r.version_id), version_number=r.version_number,
                           document_title=r.title, standard_code=r.standard_code, page_number=r.page_number,
                           locator=r.locator, text=r.text, confidence_status=r.confidence_status)
                for r in rows
            ]
        return self._cache[code_re]

    def resolve(self, req: EvidenceRequirement) -> EvidenceMatch | None:
        return find_in_pages(req, self._pages(req.standard_code_like))


@dataclass
class InputSpecIn:
    value: float
    unit: str
    provenance_kind: str = "user_input"
    ref_id: str | None = None


def resolve_inputs(db: Session, scopes: ScopeSet, raw: dict[str, InputSpecIn], message_id: uuid.UUID | None) -> dict[str, InputValue]:
    out: dict[str, InputValue] = {}
    for key, spec in raw.items():
        kind = spec.provenance_kind
        if kind == "extracted_value":
            ev = db.get(ExtractedValue, uuid.UUID(spec.ref_id)) if spec.ref_id else None
            doc = db.get(Document, ev.document_id) if ev else None
            if ev is None or doc is None or not can_view_document(scopes, doc):
                out[key] = InputValue(key, spec.value, spec.unit, Provenance("extracted_value", spec.ref_id, "missing"))
                continue
            if ev.status == "user_confirmed":
                value = ev.confirmed_value_numeric if ev.confirmed_value_numeric is not None else ev.value_numeric
                unit = ev.confirmed_unit if ev.confirmed_unit is not None else (ev.unit or "")
            else:
                value, unit = spec.value, spec.unit
            out[key] = InputValue(key, value, unit, Provenance(
                "extracted_value", str(ev.id), ev.status,
                note=f"{doc.title} · {ev.locator} (sürüm {ev.version_id})"))
        elif kind == "memory_item":
            mi = db.get(MemoryItem, uuid.UUID(spec.ref_id)) if spec.ref_id else None
            if mi is None or (not scopes.is_owner and mi.owner_id != scopes.user_id and mi.visibility != "shared"):
                out[key] = InputValue(key, spec.value, spec.unit, Provenance("memory_item", spec.ref_id, "missing"))
                continue
            st = mi.structured or {}
            out[key] = InputValue(key, st.get("value", spec.value), st.get("unit", spec.unit),
                                  Provenance("memory_item", str(mi.id), mi.status, note=mi.title))
        else:
            out[key] = InputValue(key, spec.value, spec.unit,
                                  Provenance("user_input", str(message_id) if message_id else None, "user_input"))
    return out


def _parse_json(content: str) -> dict | None:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def llm_map_request(provider: LLMProvider, user_text: str) -> tuple[str | None, dict[str, tuple[float, str]]]:
    res = provider.chat([_m(x) for x in calc_mapping_messages(user_text, CalculationEngine.list_types())],
                        GenerationOptions(temperature=0.0, json_mode=True, num_predict=300))
    data = _parse_json(res.content) or {}
    ct = data.get("calc_type")
    if ct not in RULES:
        return None, {}
    inputs = {}
    for k, v in (data.get("inputs") or {}).items():
        try:
            inputs[k] = (float(v.get("value")), str(v.get("unit") or ""))
        except (TypeError, ValueError, AttributeError):
            continue
    return ct, inputs


def _m(d: dict):
    from app.inference.base import ChatMessage

    return ChatMessage(role=d["role"], content=d["content"])


def llm_draft(provider: LLMProvider, calc_type: str, inputs: dict[str, InputValue]) -> dict[str, Any]:
    rule = RULES[calc_type]
    in_desc = {k: {"value": v.value, "unit": v.unit} for k, v in inputs.items()}
    outputs = {
        "cylindrical_gear_geometry": ["m_t", "alpha_t", "d", "d_b", "p_n", "p_t", "p_bt", "h_a", "h_f", "h", "d_a", "d_f"],
        "gear_pair": ["u", "d1", "d2", "alpha_t", "alpha_wt"],
        "iso1328_flank_tolerance": ["f_pT", "F_pT", "f_HaT", "f_faT", "F_aT", "f_HbT", "f_fbT", "F_bT"],
        "iso286_it_tolerance": ["IT"],
        "iso286_hole_H": ["IT", "EI", "ES", "lower_size", "upper_size"],
        "iso286_shaft_h": ["IT", "es", "ei", "lower_size", "upper_size"],
    }[calc_type]
    res = provider.chat([_m(x) for x in calc_draft_messages(rule.title, in_desc, [{"key": k} for k in outputs])],
                        GenerationOptions(temperature=0.0, json_mode=True, num_predict=400))
    data = _parse_json(res.content) or {}
    outs = data.get("outputs") if isinstance(data.get("outputs"), dict) else {}
    return {"outputs": outs, "raw": res.content[:4000], "model": res.model, "latency_ms": res.latency_ms}


def run_calculation(
    db: Session,
    user: AuthenticatedUser,
    scopes: ScopeSet,
    calc_type: str,
    inputs: dict[str, InputValue],
    *,
    provider: LLMProvider | None,
    compare_with_llm: bool,
    conversation_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
    extra_assumptions: list[str] | None = None,
    draft_record: dict[str, Any] | None = None,
) -> tuple[Calculation, CalcResult, dict[str, Any]]:
    engine = CalculationEngine(DbEvidenceResolver(db, scopes))
    result = engine.run(CalcRequest(calc_type=calc_type, inputs=inputs))
    if extra_assumptions and result.status == "ok":
        result.assumptions = list(extra_assumptions) + result.assumptions
    draft: dict[str, Any] | None = draft_record
    if draft is None and compare_with_llm and provider is not None and result.status == "ok":
        try:
            draft = llm_draft(provider, calc_type, inputs)
        except ProviderError as exc:
            draft = {"error": exc.code, "message": exc.user_message}
    comparison = compare(result, (draft or {}).get("outputs")) if draft and "outputs" in draft else {
        "performed": False, "reason": (draft or {}).get("error", "not_requested"), "mismatch": False, "items": []}
    calc = Calculation(
        user_id=user.id, conversation_id=conversation_id, message_id=message_id, calc_type=calc_type,
        engine_version=ENGINE_VERSION, status=result.status,
        inputs={k: {"value": v.value, "unit": v.unit, "provenance": v.provenance.__dict__} for k, v in inputs.items()},
        result=result.to_dict(), llm_draft=draft, comparison=comparison, mismatch=bool(comparison.get("mismatch")),
    )
    db.add(calc)
    db.flush()
    audit.record(db, user.actor, "calculation.run", target_type="calculation", target_id=calc.id,
                 outcome="success" if result.status == "ok" else "failure",
                 details={"calc_type": calc_type, "status": result.status,
                          "diagnostics": [d.code for d in result.diagnostics if d.level == "error"],
                          "evidence": [{"doc": e["document_id"], "version": e["version_id"], "page": e["page_number"]}
                                       for e in result.evidence]})
    if any(d.code == "draft_input_blocked" for d in result.diagnostics):
        audit.record(db, user.actor, "calculation.draft_input_rejected", outcome="denied", target_type="calculation",
                     target_id=calc.id, details={"inputs": [k for k, v in inputs.items()
                                                           if v.provenance.kind == "extracted_value"]})
    if calc.mismatch:
        audit.record(db, user.actor, "calculation.llm_mismatch", outcome="info", target_type="calculation",
                     target_id=calc.id, details={"items": [i for i in comparison["items"] if i.get("status") != "match"]})
    return calc, result, comparison


def format_result_text(result: CalcResult, comparison: dict[str, Any]) -> str:
    from app.domain.enums import REFUSAL_PHRASE

    if result.status == "refused":
        return REFUSAL_PHRASE
    if result.status != "ok":
        msgs = "; ".join(d.message for d in result.diagnostics if d.level == "error")
        return f"Hesap yapılmadı: {msgs}"
    lines = [f"**{RULES[result.calc_type].title}** — deterministik hesap motoru sonucu:"]
    for o in result.outputs:
        lines.append(f"- {o.label}: **{o.display}**")
    if comparison.get("performed"):
        if comparison.get("mismatch"):
            lines.append("")
            lines.append("⚠️ Qwen'in taslak hesabı motor sonucuyla UYUŞMADI. Gösterilen değerler doğrulanmış hesap "
                         "motoruna aittir; uyuşmazlık denetim kaydına işlendi.")
        else:
            lines.append("")
            lines.append("Qwen taslağı motor sonucuyla tolerans içinde uyumlu.")
    elif comparison.get("reason") not in (None, "not_requested", "engine_not_ok"):
        lines.append("")
        lines.append("Qwen taslağı alınamadı; sonuç yalnızca hesap motoruna dayanıyor.")
    lines.append("")
    lines.append("Ayrıntılar (girdiler, formüller, birimler, kaynaklar) için 'Ayrıntılı çözüm'ü açın.")
    return "\n".join(lines)
