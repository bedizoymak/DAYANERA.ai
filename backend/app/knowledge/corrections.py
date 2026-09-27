"""Correction candidates: general, machine-checkable rules built from the registry.

A correction is keyed by ``family:root_cause`` (for example
``base_circle:LLM_ARITHMETIC_ERROR``), never by a numerical example. Its
content is

* ``claims``: one normalized expression per output (by default the VERIFIED
  registry expression). Claims are what the regression evaluates against the
  deterministic engine, so a wrong claim (for instance one proposed by an LLM)
  is rejected, never trusted.
* ``invariants``: relations that must hold between engine outputs.
* ``statement``: short Turkish guidance for future Qwen drafts, rendered from
  the same VERIFIED rules and ISO citations.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

from app.knowledge.registry import Registry, get_registry
from app.knowledge.validation import ValidationReport, get_report

CLASS_GUIDANCE_TR: dict[str, str] = {
    "DEGREE_RADIAN_ERROR": "Trigonometrik fonksiyona açıyı radyan olarak ver: θ_rad = θ°·π/180 (cos 20 ≠ cos 20°).",
    "ANGLE_UNIT_ERROR": "Açı çıktılarını derece (°) olarak raporla; radyan veya grad verme.",
    "WRONG_PRESSURE_ANGLE": "Girdideki α_n'yi ve ondan türetilen α_t'yi kullan; verilmemişse ISO 53 α_P = 20°.",
    "NORMAL_TRANSVERSE_CONFUSION": "Alın kesitinde (d, d_b, p_t, p_bt) m_t ve α_t; normal kesitte (p_n, h_a, h_f) m_n ve α_n kullan.",
    "MODULE_CONVERSION_ERROR": "m_t = m_n / cos β (bölme; çarpma değil).",
    "WRONG_BASE_CIRCLE_FORMULA": "d_b = d·cos α_t: kosinüs (sin/tan/kare değil) ve çap (yarıçap değil).",
    "WRONG_BASE_PITCH_FORMULA": "p_bt = p_t·cos α_t = π·d_b / z.",
    "PROFILE_SHIFT_OMITTED": "x·m_n: h_a'ya eklenir, h_f'den çıkarılır, d_a ve d_f'ye 2·x·m_n eklenir; d_b ve p_bt x'ten bağımsızdır.",
    "INTERNAL_EXTERNAL_SIGN_ERROR": "Dış dişlide d_a = d + 2·h_a ve d_f = d − 2·h_f; iç dişli işaretlerini kullanma.",
    "HELIX_ANGLE_OMITTED": "Helisel dişlide β'yı kullan: d = z·m_n / cos β, tan α_t = tan α_n / cos β.",
    "ROUNDING_ERROR": "Ara sonuçları yuvarlama; yalnız nihai değeri yuvarla (ISO 1328-1 için 5.2.3).",
    "UNIT_ERROR": "Uzunluk mm, tolerans µm; birim dönüştürme.",
    "FORMULA_SELECTION_ERROR": "İstenen büyüklüğün kendi formülünü kullan (ör. d_b için d; d_a veya d_f değil).",
    "LLM_ARITHMETIC_ERROR": "Formül doğru olsa bile sayısal değeri dikkatle hesapla ve aşağıdaki değişmezlerle kontrol et.",
    "UNKNOWN": "Değerleri yalnız verilen formüllerle hesapla ve değişmezlerle kontrol et.",
}

# relations that must hold between engine outputs (registry variables, radians)
FAMILY_INVARIANTS: dict[str, list[dict[str, str]]] = {
    "base_circle": [
        {"expr": "d_b - d*cos(alpha_t)", "scale": "d", "text": "d_b = d·cos α_t"},
        {"expr": "p_bt - p_t*cos(alpha_t)", "scale": "p_t", "text": "p_bt = p_t·cos α_t"},
        {"expr": "p_bt*z - pi*d_b", "scale": "d", "text": "p_bt·z = π·d_b"},
    ],
    "transverse_conversion": [
        {"expr": "m_t*cos(beta) - m_n", "scale": "m_n", "text": "m_t·cos β = m_n"},
        {"expr": "tan(alpha_t)*cos(beta) - tan(alpha_n)", "scale": "1", "text": "tan α_t·cos β = tan α_n"},
    ],
    "reference_diameter": [{"expr": "d - z*m_t", "scale": "d", "text": "d = z·m_t"}],
    "pitch": [
        {"expr": "p_t - pi*d/z", "scale": "p_t", "text": "p_t = π·d / z"},
        {"expr": "p_n - p_t*cos(beta)", "scale": "p_t", "text": "p_n = p_t·cos β"},
    ],
    "tooth_depth": [{"expr": "h - (h_a + h_f)", "scale": "h", "text": "h = h_a + h_f"}],
    "tip_root_diameter": [
        {"expr": "d_a - (d + 2*h_a)", "scale": "d", "text": "d_a = d + 2·h_a"},
        {"expr": "d_f - (d - 2*h_f)", "scale": "d", "text": "d_f = d − 2·h_f"},
    ],
    "gear_pair": [{"expr": "u*z1 - z2", "scale": "z2", "text": "u·z1 = z2"}],
    "iso1328_tolerance": [
        {"expr": "F_aT - sqrt(f_HaT**2 + f_faT**2)", "scale": "F_aT", "text": "F_αT = √(f_HαT² + f_fαT²)"},
    ],
}
# outputs that must not change when the named inputs change (checked on paired regression cases)
FAMILY_INVARIANT_UNDER: dict[str, dict[str, tuple[str, ...]]] = {
    "base_circle": {"x": ("d_b", "p_bt")},
    "reference_diameter": {"x": ("d",)},
    "pitch": {"x": ("p_n", "p_t")},
}
# reference values the statement may quote; recomputed and checked by the regression
REFERENCE_VALUES: dict[str, list[dict[str, Any]]] = {
    "base_circle": [{"expr": "cos(radians(20))", "text": "cos 20° (ISO 53 α_P)", "digits": 5}],
    "transverse_conversion": [{"expr": "tan(radians(20))", "text": "tan 20° (ISO 53 α_P)", "digits": 5}],
}
REFERENCE_CLASSES = ("LLM_ARITHMETIC_ERROR", "DEGREE_RADIAN_ERROR", "WRONG_PRESSURE_ANGLE", "UNKNOWN")


@dataclass
class CorrectionSpec:
    key: str
    family: str
    root_cause: str
    calc_type: str
    output_keys: list[str]
    formula_ids: list[str]
    title: str
    statement: str
    claims: list[dict[str, str]]
    invariants: list[dict[str, str]]
    reference_values: list[dict[str, Any]] = field(default_factory=list)
    source: str = "diagnosis"
    proposed_by: str = "system"

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("key", "family", "root_cause", "calc_type", "output_keys", "formula_ids",
                                              "title", "statement", "claims", "invariants", "reference_values",
                                              "source", "proposed_by")}


def _fmt(value: float, digits: int) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def family_rules(family: str, calc_type: str, registry: Registry, report: ValidationReport) -> list[tuple[Any, Any]]:
    """VERIFIED rules of a family that the engine computes for ``calc_type``."""
    return [(r, b) for r, b in registry.rules_for_engine(calc_type)
            if r.family == family and report.status(r.id) == "VERIFIED"]


def render_statement(family: str, root_cause: str, rules: list[Any], invariants: list[dict[str, str]],
                     reference_values: list[dict[str, Any]]) -> str:
    parts = [f"{r.name_tr}: {r.display} [{r.iso_citation}]" for r in rules]
    for r in rules:
        parts += [a for a in r.assumptions if a not in parts]
    if invariants:
        parts.append("Kontrol: " + "; ".join(i["text"] for i in invariants) + ".")
    for rv in reference_values:
        parts.append(f"{rv['text']} = {_fmt(rv['value'], rv['digits'])}.")
    parts.append(CLASS_GUIDANCE_TR.get(root_cause, CLASS_GUIDANCE_TR["UNKNOWN"]))
    return " ".join(p if p.endswith((".", "]")) else p + "." for p in parts)


def build_from_diagnosis(calc_type: str, diagnosis: dict[str, Any], registry: Registry | None = None,
                         report: ValidationReport | None = None) -> list[CorrectionSpec]:
    """One correction candidate per (family, root cause) of the diagnosis."""
    registry = registry or get_registry()
    report = report or get_report()
    root = diagnosis["primary_class"]
    out = []
    for family in diagnosis.get("families") or []:
        rules = family_rules(family, calc_type, registry, report)
        if not rules:
            continue
        keys = sorted({b.output_key for _r, b in rules})
        claims = [{"output": b.output_key, "rule_id": r.id, "expression": r.expression or ""} for r, b in rules]
        invariants = FAMILY_INVARIANTS.get(family, [])
        refs = [{**rv, "value": math.cos(math.radians(20)) if "cos" in rv["expr"] else math.tan(math.radians(20))}
                for rv in REFERENCE_VALUES.get(family, [])] if root in REFERENCE_CLASSES else []
        unique_rules = list({r.id: r for r, _b in rules}.values())
        out.append(CorrectionSpec(
            key=f"{family}:{root}", family=family, root_cause=root, calc_type=calc_type, output_keys=keys,
            formula_ids=[r.id for r in unique_rules],
            title=f"{unique_rules[0].name_tr if len(unique_rules) == 1 else family} — {root}",
            statement=render_statement(family, root, unique_rules, invariants, refs),
            claims=claims, invariants=invariants, reference_values=refs,
        ))
    return out


def build_proposal(calc_type: str, family: str, root_cause: str, claims: list[dict[str, str]], statement: str,
                   proposed_by: str) -> CorrectionSpec:
    """A correction proposed from outside the registry (a person or an LLM). It is only a candidate:
    the regression decides, and a claim that disagrees with the engine is rejected."""
    digest = hashlib.sha256(json.dumps(claims, sort_keys=True).encode()).hexdigest()[:10]
    rule_ids = sorted({c.get("rule_id") for c in claims if c.get("rule_id")})
    return CorrectionSpec(
        key=f"{family}:{root_cause}:proposal:{digest}", family=family, root_cause=root_cause, calc_type=calc_type,
        output_keys=sorted({c["output"] for c in claims}), formula_ids=rule_ids,
        title=f"Öneri — {family} / {root_cause}", statement=statement.strip()[:2000],
        claims=[{"output": c["output"], "rule_id": c.get("rule_id", ""), "expression": c["expression"]} for c in claims],
        invariants=FAMILY_INVARIANTS.get(family, []), source="proposal", proposed_by=proposed_by,
    )
