"""Deterministic regression for correction candidates.

A correction is never learned from one example. The runner generates a test
matrix for the correction's formula family (for the base circle: α_n ∈ {20°,
25°} × m_n ∈ {1, 2, 3} × z ∈ {17, 20, 30, 40, 80}, plus helical and
profile-shift variants where the relations apply), runs the FULL engine on
every case (evidence resolution included: a missing ISO passage blocks the
verification instead of passing it) and checks

1. every claim of the correction against the engine output, with the claim
   evaluated on an environment derived independently from the case inputs;
2. the family invariants on the engine outputs (e.g. p_bt·z = π·d_b);
3. invariance of outputs that must not depend on an input (d_b vs x);
4. the comparator: an exact draft passes, a draft off by more than the
   tolerance is flagged as a mismatch;
5. reference values quoted by the correction statement.

Expected values come only from the engine and the VERIFIED registry, never from
an LLM. Nothing here can be influenced by a model.
"""
from __future__ import annotations

import hashlib
import itertools
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from app.calc.compare import compare
from app.calc.engine import ENGINE_VERSION, CalculationEngine
from app.calc.evidence import EvidenceResolver
from app.calc.rules import RULES
from app.calc.types import CalcRequest, EvidenceMatch, EvidenceRequirement, InputValue, Provenance
from app.knowledge import expr
from app.knowledge.corrections import FAMILY_INVARIANT_UNDER, CorrectionSpec
from app.knowledge.registry import FORMULAS_FILE, get_registry
from app.knowledge.validation import build_env, close, get_report

MAX_FAILURES = 12


def registry_fingerprint() -> str:
    return hashlib.sha256(FORMULAS_FILE.read_bytes()).hexdigest()[:16]


class CachingResolver:
    """Resolve each evidence requirement once per regression run."""

    def __init__(self, inner: EvidenceResolver):
        self.inner = inner
        self.cache: dict[str, EvidenceMatch | None] = {}

    def resolve(self, req: EvidenceRequirement) -> EvidenceMatch | None:
        if req.id not in self.cache:
            self.cache[req.id] = self.inner.resolve(req)
        return self.cache[req.id]


# --------------------------------------------------------------------------- matrices
def _cylindrical_cases(with_k: bool) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    base = list(itertools.product((20.0, 25.0), (1.0, 2.0, 3.0), (17, 20, 30, 40, 80)))
    std = {"k": 0.0, "h_aP_star": 1.0, "h_fP_star": 1.25}
    for an, mn, z in base:
        cases.append({"alpha_n": an, "m_n": mn, "z": z, "beta": 0.0, "x": 0.0, **std, "_group": "spur"})
    for beta in (15.0, 30.0):
        for an, mn, z in base:
            cases.append({"alpha_n": an, "m_n": mn, "z": z, "beta": beta, "x": 0.0, **std, "_group": "helical"})
    for x in (-0.3, 0.5):
        for i, (an, mn, z) in enumerate(base):
            cases.append({"alpha_n": an, "m_n": mn, "z": z, "beta": 0.0, "x": x, **std, "_group": "profile_shift",
                          "_twin": i})
    if with_k:
        for an, mn, z in base[:10]:
            cases.append({"alpha_n": an, "m_n": mn, "z": z, "beta": 0.0, "x": 0.2, "k": -0.1, "h_aP_star": 0.8,
                          "h_fP_star": 1.0, "_group": "stub_tooth_tip_alteration"})
    return cases


def _pair_cases() -> list[dict[str, Any]]:
    out = []
    for z1, z2, mn, an, beta in itertools.product((17, 20, 30), (40, 80), (1.0, 2.0, 3.0), (20.0, 25.0), (0.0, 15.0)):
        a = mn * (z1 + z2) / (2 * math.cos(math.radians(beta)))
        out.append({"z1": z1, "z2": z2, "m_n": mn, "alpha_n": an, "beta": beta, "a_w": a * 1.02,
                    "_group": "helical" if beta else "spur"})
    return out


def _iso1328_cases() -> list[dict[str, Any]]:
    return [{"A": a, "m_n": mn, "d": d, "b": b, "_group": "class_range"}
            for a, mn, d, b in itertools.product((1, 4, 6, 8, 11), (0.5, 2.0, 10.0), (10.0, 150.0, 2000.0), (10.0, 100.0))]


def cases_for(calc_type: str, family: str) -> list[dict[str, Any]]:
    if calc_type == "cylindrical_gear_geometry":
        return _cylindrical_cases(with_k=family in ("tooth_depth", "tip_root_diameter"))
    if calc_type == "gear_pair":
        return _pair_cases()
    if calc_type == "iso1328_flank_tolerance":
        return _iso1328_cases()
    return []


def _unit(calc_type: str, key: str) -> str:
    spec = next(s for s in RULES[calc_type].inputs if s.key == key)
    return {"length": "mm", "angle": "°"}.get(spec.kind, "")


def _registry_env(calc_type: str, case: dict[str, Any]) -> dict[str, float]:
    env = {}
    for spec in RULES[calc_type].inputs:
        if spec.key in case:
            env[spec.key] = math.radians(case[spec.key]) if spec.kind == "angle" else float(case[spec.key])
    return env


# --------------------------------------------------------------------------- runner
@dataclass
class RegressionResult:
    status: str  # passed | failed | blocked | not_applicable
    cases: int = 0
    checks: int = 0
    groups: dict[str, int] = field(default_factory=dict)
    failures: list[dict[str, Any]] = field(default_factory=list)
    blocked_reason: list[str] = field(default_factory=list)
    reason: str = ""
    engine_version: str = ENGINE_VERSION
    registry_fingerprint: str = ""
    duration_ms: int = 0
    matrix: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_regression(spec: CorrectionSpec, resolver: EvidenceResolver) -> RegressionResult:
    t0 = time.perf_counter()
    registry, report = get_registry(), get_report()
    res = RegressionResult(status="passed", registry_fingerprint=registry_fingerprint())
    bindings = {b.output_key: (r, b) for r, b in registry.rules_for_engine(spec.calc_type)}
    # ---- authority gate: every claimed output needs a VERIFIED, engine-bound registry rule
    if not spec.claims:
        res.status, res.reason = "not_applicable", "Düzeltmede doğrulanabilir iddia yok."
        return res
    for claim in spec.claims:
        bound = bindings.get(claim["output"])
        if bound is None or report.status(bound[0].id) != "VERIFIED":
            res.status = "not_applicable"
            res.reason = (f"{claim['output']} için motor tarafından hesaplanan VERIFIED bir kayıt kuralı yok; "
                          "yetkili doğrulama yapılamaz.")
            return res
        try:
            unknown = expr.names(claim["expression"]) - set(registry.variables)
        except expr.ExpressionError as exc:
            res.status, res.reason = "failed", f"İddia ifadesi geçersiz: {exc}"
            res.failures.append({"check": "claim_syntax", "claim": claim, "detail": str(exc)})
            return res
        if unknown:
            res.status, res.reason = "failed", f"İddia tanımsız değişken kullanıyor: {sorted(unknown)}"
            res.failures.append({"check": "claim_variables", "claim": claim})
            return res
    cases = cases_for(spec.calc_type, spec.family)
    if not cases:
        res.status, res.reason = "not_applicable", f"{spec.calc_type} için regresyon matrisi tanımlı değil."
        return res
    derive = [r for r in registry.rules if r.derive and r.kind == "formula" and report.status(r.id) == "VERIFIED"]
    engine = CalculationEngine(CachingResolver(resolver))
    outputs_by_case: list[dict[str, float]] = []

    def fail(check: str, case: dict[str, Any], **detail: Any) -> None:
        if len(res.failures) < MAX_FAILURES:
            res.failures.append({"check": check, "case": {k: v for k, v in case.items() if not k.startswith("_")},
                                 **detail})
        res.status = "failed"

    for case in cases:
        prov = Provenance("user_input", "regression", "user_input", note="self-maintenance regression")
        inputs = {k: InputValue(k, float(v), _unit(spec.calc_type, k), prov) for k, v in case.items() if not k.startswith("_")}
        result = engine.run(CalcRequest(spec.calc_type, inputs))
        if result.status == "refused":
            res.status = "blocked"
            res.blocked_reason = sorted({d.message for d in result.diagnostics if d.level == "error"})
            break
        res.cases += 1
        res.groups[case["_group"]] = res.groups.get(case["_group"], 0) + 1
        if result.status != "ok":
            fail("engine_status", case, status=result.status, diagnostics=[d.message for d in result.diagnostics])
            outputs_by_case.append({})
            continue
        outs: dict[str, float] = {}
        for o in result.outputs:
            raw = o.unrounded if o.unrounded is not None else o.value
            unit = bindings[o.key][1].unit if o.key in bindings else None
            outs[o.key] = math.radians(raw) if unit == "deg" else raw
        outputs_by_case.append(outs)
        indep = build_env(_registry_env(spec.calc_type, case), derive)
        # 1. claims vs engine (claim evaluated independently of the engine)
        for claim in spec.claims:
            res.checks += 1
            try:
                predicted = expr.evaluate(claim["expression"], indep)
            except expr.ExpressionError as exc:
                fail("claim_evaluation", case, output=claim["output"], detail=str(exc))
                continue
            if not close(predicted, outs[claim["output"]]):
                fail("claim_vs_engine", case, output=claim["output"], expression=claim["expression"],
                     claimed=predicted, engine=outs[claim["output"]])
        # 2. invariants on engine outputs
        eng_env = {**_registry_env(spec.calc_type, case), **outs}
        for inv in spec.invariants:
            res.checks += 1
            try:
                value = expr.evaluate(inv["expr"], eng_env)
                scale = abs(expr.evaluate(inv["scale"], eng_env)) or 1.0
            except expr.ExpressionError as exc:
                fail("invariant_evaluation", case, invariant=inv["text"], detail=str(exc))
                continue
            if abs(value) > 1e-9 * max(1.0, scale):
                fail("invariant", case, invariant=inv["text"], residual=value)
        # 3. invariance under inputs that must not matter
        twin = case.get("_twin")
        if twin is not None and twin < len(outputs_by_case):
            for var, keys in FAMILY_INVARIANT_UNDER.get(spec.family, {}).items():
                if case.get(var, 0) == cases[twin].get(var, 0):
                    continue
                for key in keys:
                    if key in outs and key in outputs_by_case[twin]:
                        res.checks += 1
                        if not close(outs[key], outputs_by_case[twin][key]):
                            fail("invariance", case, output=key, varied=var, value=outs[key],
                                 reference=outputs_by_case[twin][key])
        # 4. comparator sensitivity on the correction's outputs
        exact = {o.key: round(o.value, 4) for o in result.outputs}
        res.checks += 1
        if compare(result, exact)["mismatch"]:
            fail("comparator_false_positive", case, draft=exact)
        for o in result.outputs:
            if o.key not in spec.output_keys:
                continue
            tol = o.abs_tol if o.abs_tol is not None else None
            cmp_ok = compare(result, {**exact, o.key: o.value})  # baseline
            delta = max(0.03 * abs(o.value), 2 * (tol or max(_abs_tol(o.unit), 0.005 * abs(o.value))))
            for sign in (1, -1):
                res.checks += 1
                flagged = compare(result, {**exact, o.key: o.value + sign * delta})
                bad = [i["key"] for i in flagged["items"] if i["status"] == "mismatch"]
                if not flagged["mismatch"] or bad != [o.key] or cmp_ok["mismatch"]:
                    fail("comparator_missed_error", case, output=o.key, perturbation=sign * delta)
    # 5. reference values quoted in the statement
    for rv in spec.reference_values:
        res.checks += 1
        actual = expr.evaluate(rv["expr"], {})
        if round(actual, rv["digits"]) != round(rv["value"], rv["digits"]):
            res.status = "failed"
            res.failures.append({"check": "reference_value", "text": rv["text"], "stated": rv["value"], "actual": actual})
    if res.status == "passed" and res.cases == 0:
        res.status, res.reason = "blocked", "Hiç vaka çalıştırılamadı."
    res.matrix = {"family": spec.family, "calc_type": spec.calc_type, "planned_cases": len(cases),
                  "claims": [c["output"] for c in spec.claims], "invariants": [i["text"] for i in spec.invariants]}
    res.duration_ms = int((time.perf_counter() - t0) * 1000)
    return res


def _abs_tol(unit: str) -> float:
    from app.calc.compare import ABS_TOL_BY_UNIT

    return ABS_TOL_BY_UNIT.get(unit, 0.001)
