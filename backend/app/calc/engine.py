"""Calculation engine: validation, evidence resolution and execution."""
from __future__ import annotations

import logging
import math
from dataclasses import asdict

from app.calc.evidence import REQ, EvidenceResolver
from app.calc.rules import RULES, RuleContext, RuleInputError, RuleRefusal
from app.calc.presentation import enrich_outputs, trace_to_latex
from app.calc.types import CalcRequest, CalcResult, Diagnostic
from app.calc.units import InvalidUnitError, to_canonical
from app.domain.enums import REFUSAL_PHRASE

log = logging.getLogger(__name__)

ENGINE_VERSION = "1.0.0"

_ALLOWED_PROVENANCE_STATUS = {
    "extracted_value": {"user_confirmed"},
    "memory_item": {"user_confirmed", "verified_source"},
}


class CalculationEngine:
    def __init__(self, resolver: EvidenceResolver):
        self.resolver = resolver

    @staticmethod
    def list_types() -> list[dict]:
        return [r.info() for r in RULES.values()]

    def evidence_available(self, calc_type: str) -> dict[str, bool]:
        rule = RULES[calc_type]
        return {rid: self.resolver.resolve(REQ[rid]) is not None for rid in rule.evidence_ids(set(), {})}

    def run(self, request: CalcRequest) -> CalcResult:
        rule = RULES.get(request.calc_type)
        if rule is None:
            return CalcResult(
                calc_type=request.calc_type, status="refused", engine_version=ENGINE_VERSION,
                message=REFUSAL_PHRASE,
                diagnostics=[Diagnostic("error", "unsupported_formula",
                                        "Bu hesap türü doğrulanmış ISO korpusunda tanımlı değil; motor desteklemiyor.")],
            )
        result = CalcResult(calc_type=rule.calc_type, status="ok", engine_version=ENGINE_VERSION, message="")
        specs = {s.key: s for s in rule.inputs}

        # ---- 1. input validation (keys, units, provenance, ranges) ----
        values: dict[str, float] = {}
        errors: list[Diagnostic] = []
        for key, iv in request.inputs.items():
            spec = specs.get(key)
            if spec is None:
                errors.append(Diagnostic("error", "unknown_input", f"Bilinmeyen giriş: {key}"))
                continue
            prov = iv.provenance
            allowed = _ALLOWED_PROVENANCE_STATUS.get(prov.kind)
            if allowed is not None and prov.status not in allowed:
                errors.append(Diagnostic(
                    "error", "draft_input_blocked",
                    f"{spec.label}: kaynak değer '{prov.status}' durumunda. Taslak çıkarım (draft_extraction) değerleri "
                    "yetkili kullanıcı onayı olmadan hesapta kullanılamaz.",
                ))
                continue
            if prov.kind not in ("user_input", "extracted_value", "memory_item"):
                errors.append(Diagnostic("error", "invalid_provenance", f"{spec.label}: geçersiz kaynak türü {prov.kind}"))
                continue
            if not isinstance(iv.value, (int, float)) or not math.isfinite(float(iv.value)):
                errors.append(Diagnostic("error", "invalid_value", f"{spec.label}: sayısal değer gerekli"))
                continue
            try:
                canon = to_canonical(spec.kind, float(iv.value), iv.unit)
            except InvalidUnitError as exc:
                errors.append(Diagnostic("error", "invalid_unit", f"{spec.label}: {exc}"))
                continue
            if spec.kind in ("integer", "grade") and canon != int(canon):
                errors.append(Diagnostic("error", "not_integer", f"{spec.label}: tam sayı olmalı"))
                continue
            if spec.min_value is not None and canon < spec.min_value or spec.max_value is not None and canon > spec.max_value:
                errors.append(Diagnostic("error", "out_of_range",
                                         f"{spec.label}: {canon} izin verilen aralık dışında ({spec.min_value} – {spec.max_value})"))
                continue
            values[key] = canon
            result.inputs.append({
                "key": key, "label": spec.label, "value": iv.value, "unit": iv.unit or "",
                "canonical_value": canon, "provenance": asdict(prov),
            })
        for spec in rule.inputs:
            if spec.required and spec.key not in request.inputs:
                errors.append(Diagnostic("error", "missing_input", f"Zorunlu giriş eksik: {spec.label}"))
        if errors:
            result.status = "invalid_input"
            result.message = "Hesap yapılmadı: giriş doğrulaması başarısız."
            result.diagnostics = errors
            return result

        provided = set(values)
        # ---- 2. evidence resolution (active verified corpus only) ----
        evidence = {}
        missing = []
        for rid in rule.evidence_ids(provided, values):
            match = self.resolver.resolve(REQ[rid])
            if match is None:
                missing.append(REQ[rid])
            else:
                evidence[rid] = match
        if missing:
            result.status = "refused"
            result.message = REFUSAL_PHRASE
            result.diagnostics = [
                Diagnostic("error", "missing_source",
                           f"Etkin doğrulanmış kaynakta bulunamadı: {req.description}") for req in missing
            ]
            result.evidence = [m.public() for m in evidence.values()]
            return result

        ctx = RuleContext(values=dict(values), provided=provided, evidence=evidence)
        try:
            rule.apply_defaults(ctx)
            outputs = rule.compute(ctx)
        except RuleRefusal as exc:
            result.status = "refused"
            result.message = REFUSAL_PHRASE
            result.diagnostics = ctx.diagnostics + [Diagnostic("error", "rule_refusal", str(exc))]
            result.evidence = [m.public() for m in evidence.values()]
            return result
        except RuleInputError as exc:
            result.status = "invalid_input"
            result.message = "Hesap yapılmadı: giriş doğrulaması başarısız."
            result.diagnostics = ctx.diagnostics + [Diagnostic("error", "invalid_input", str(exc))]
            return result
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            result.status = "error"
            result.message = "Hesap motoru girişleri işleyemedi."
            result.diagnostics = [Diagnostic("error", "math_error", str(exc))]
            return result

        result.outputs = outputs
        result.assumptions = ctx.assumptions
        result.constants = ctx.constants
        result.trace = ctx.trace
        enrich_outputs(result.outputs, ctx.values)
        result.trace_latex = trace_to_latex(ctx.trace)
        result.diagnostics = ctx.diagnostics + [
            Diagnostic("info", "validated", "Tüm formül ve sabitler etkin doğrulanmış ISO kaynak pasajlarına bağlandı.")
        ]
        result.evidence = [m.public() for m in evidence.values()]
        result.message = "Hesap motoru sonucu (doğrulanmış)"
        return result
