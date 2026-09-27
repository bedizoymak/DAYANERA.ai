"""Authority validation of the knowledge registry.

Every status is computed here from evidence, in the DAYANERA authority order:

1. a DAYANERA ISO evidence requirement (``app.calc.evidence.REQ``) for the rule
2. numerical agreement of the ISO-backed deterministic engine over a
   validation matrix (the engine math runs offline; the ISO passage itself is
   checked at runtime by the engine's evidence resolver)
3. independent external implementations, compared numerically inside the
   domain where their convention applies
4. derived relations of already verified rules

``VERIFIED``  ISO evidence requirement + engine agreement.
``CANDIDATE`` no DAYANERA ISO anchor or no engine binding, but supported by at
              least one numerically agreeing, tested implementation or derived
              only from VERIFIED rules.
``UNVERIFIED`` implementation conventions, or nothing independent supports it.
``CONFLICT``  the engine disagrees with the normalized rule (never expected;
              it would block retrieval and be reported loudly).
``REJECTED``  a claim that failed an independent mathematical check.

External code that disagrees with an authoritative rule never changes the
engine: the disagreement is recorded as a conflict record instead.
"""
from __future__ import annotations

import inspect
import itertools
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

from app.calc.evidence import REQ
from app.calc.rules import RULES, RuleContext, RuleInputError, RuleRefusal
from app.knowledge import expr
from app.knowledge.registry import Observation, Registry, Rule, get_registry

log = logging.getLogger("dayanera.knowledge")

REL_TOL = 1e-9
ZERO_TOL = 1e-9
REPO_ROOT = Path(__file__).resolve().parents[3]


def close(a: float, b: float, rel: float = REL_TOL) -> bool:
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


def rel_error(a: float, b: float) -> float:
    return abs(a - b) / max(1.0, abs(b))


# --------------------------------------------------------------------------- matrices
def _r(deg: float) -> float:
    return math.radians(deg)


def _cylindrical() -> list[dict[str, float]]:
    out = [{"alpha_n": _r(an), "m_n": float(mn), "z": float(z), "beta": _r(b), "x": x, "k": 0.0,
            "h_aP_star": 1.0, "h_fP_star": 1.25}
           for an, mn, z, b, x in itertools.product((20, 25), (1, 2, 3), (17, 20, 30, 40, 80), (0, 15, 30),
                                                    (-0.3, 0.0, 0.5))]
    # non-standard racks and tip alteration: exercise k, h_aP*, h_fP*
    for b, (k, ha, hf) in itertools.product((0, 20), ((-0.1, 0.8, 1.0), (0.05, 1.0, 1.4))):
        out.append({"alpha_n": _r(20), "m_n": 2.0, "z": 30.0, "beta": _r(b), "x": 0.2, "k": k,
                    "h_aP_star": ha, "h_fP_star": hf})
    return out


def _cylindrical_dy() -> list[dict[str, float]]:
    return [{**c, "d_y_frac": f} for c in _cylindrical() if c["k"] == 0.0 and c["x"] in (0.0, 0.5)
            and c["beta"] in (0.0, _r(15)) for f in (0.05, 0.35, 1.0)]


def _pair() -> list[dict[str, float]]:
    return [{"z1": float(z1), "z2": float(z2), "m_n": float(mn), "alpha_n": _r(an), "beta": _r(b), "a_w_factor": f}
            for z1, z2, mn, an, b, f in itertools.product((17, 20, 30), (40, 80), (1, 2, 3), (20, 25), (0, 15),
                                                          (1.0, 1.02))]


def _pair_shift() -> list[dict[str, float]]:
    return [{"z1": float(z1), "z2": float(z2), "m_n": 2.0, "alpha_n": _r(an), "beta": _r(b), "x1": x1, "x2": x2}
            for z1, z2, an, b, (x1, x2) in itertools.product((17, 20, 30), (40, 80), (20, 25), (0, 15),
                                                             ((0.0, 0.0), (0.3, 0.2), (0.5, -0.2), (0.2, 0.6)))]


def _internal() -> list[dict[str, float]]:
    return [{"z1": float(z1), "z2": float(z2), "m_n": float(mn), "alpha_n": _r(20), "beta": 0.0, "x1": x1, "x2": x2}
            for z1, z2, mn, (x1, x2) in itertools.product((12, 18, 20), (48, 60, 72), (1, 2),
                                                          ((0.0, 0.0), (0.2, 0.1), (0.3, 0.5)))]


def _involute() -> list[dict[str, float]]:
    return [{"alpha": _r(a), "r_b": rb} for a, rb in itertools.product((1, 5, 10, 14.5, 20, 25, 30, 40, 50, 60),
                                                                        (10.0, 28.19))]


def _involute_roll() -> list[dict[str, float]]:
    return [{"r_b": rb, "phi": p} for rb, p in itertools.product((10.0, 28.19), (0.0, 0.1, 0.5, 1.0, 1.5))]


def _helical() -> list[dict[str, float]]:
    return [{"z": float(z), "m_n": float(mn), "beta": _r(b), "b": bw, "alpha_n": _r(20)}
            for z, mn, b, bw in itertools.product((20, 40), (1, 3), (5, 15, 30, 45), (10.0, 25.0))]


def _planetary() -> list[dict[str, float]]:
    return [{"z_sun": float(s), "z_planet": float(p), "n_p": float(n)}
            for s, p, n in itertools.product((12, 13, 18, 24), (12, 14, 15, 18), (2, 3, 4))]


def _worm() -> list[dict[str, float]]:
    return [{"m": float(m), "z_1": float(z1), "q": float(q)}
            for m, z1, q in itertools.product((1, 2, 4), (1, 2, 3, 4), (8, 10, 16))]


def _iso1328() -> list[dict[str, float]]:
    return [{"A": float(a), "m_n": mn, "d": d, "b": b}
            for a, mn, d, b in itertools.product((1, 3, 5, 7, 9, 11), (0.5, 2.0, 10.0, 70.0), (5.0, 60.0, 500.0, 15000.0),
                                                 (4.0, 20.0, 1200.0))]


MATRICES: dict[str, tuple[Callable[[], list[dict[str, float]]], tuple[tuple[str, str], ...]]] = {
    "cylindrical": (_cylindrical, ()),
    "cylindrical_dy": (_cylindrical_dy, (("d_y", "d_b + (d_a - d_b)*d_y_frac"),)),
    "pair": (_pair, (("a_w", "a*a_w_factor"),)),
    "pair_shift": (_pair_shift, (("alpha_wt", "inv_inverse(inv_alpha_wt)"),)),
    "internal": (_internal, (("alpha_wt_int", "inv_inverse(inv_alpha_wt_int)"),)),
    "involute": (_involute, ()),
    "involute_roll": (_involute_roll, ()),
    "helical": (_helical, ()),
    "planetary": (_planetary, ()),
    "worm": (_worm, (("d_1", "m*q"),)),
    "iso1328": (_iso1328, ()),
}


def build_env(base: dict[str, float], derive_rules: list[Rule], extras: tuple[tuple[str, str], ...] = ()) -> dict[str, float]:
    """Complete a context with every variable the derive rules (and matrix extras) can supply."""
    env = dict(base)
    changed = True
    while changed:
        changed = False
        for r in derive_rules:
            if r.output in env or not r.inputs() <= env.keys():
                continue
            try:
                env[r.output] = expr.evaluate(r.expression, env)  # type: ignore[arg-type]
                changed = True
            except expr.ExpressionError:
                continue
        for var, e in extras:
            if var not in env and expr.names(e) <= env.keys():
                env[var] = expr.evaluate(e, env)
                changed = True
    return env


@cache
def matrix_envs(name: str) -> tuple[dict[str, float], ...]:
    make, extras = MATRICES[name]
    derive = [r for r in get_registry().rules if r.derive and r.kind == "formula"]
    return tuple(build_env(c, derive, extras) for c in make())


# --------------------------------------------------------------------------- engine (offline math)
def engine_outputs(calc_type: str, env: dict[str, float]) -> dict[str, float]:
    """Run the deterministic rule math of ``calc_type`` on registry-unit inputs.

    Evidence resolution is skipped: this validates the engine's arithmetic. The
    ISO passage itself is resolved by the engine at runtime (and by the
    regression runner, which uses the full engine).
    """
    rule = RULES[calc_type]
    values: dict[str, float] = {}
    for spec in rule.inputs:
        if spec.key in env:
            values[spec.key] = math.degrees(env[spec.key]) if spec.kind == "angle" else float(env[spec.key])
    ctx = RuleContext(values=values, provided=set(values), evidence={})
    rule.apply_defaults(ctx)
    return {o.key: (o.unrounded if o.unrounded is not None else o.value) for o in rule.compute(ctx)}


def engine_value(binding_unit: str | None, value: float) -> float:
    return math.radians(value) if binding_unit == "deg" else value


@cache
def _engine_matrix(calc_type: str, matrix: str) -> tuple[dict[str, float] | None, ...]:
    out: list[dict[str, float] | None] = []
    for env in matrix_envs(matrix):
        try:
            out.append(engine_outputs(calc_type, env))
        except (RuleRefusal, RuleInputError, ValueError, ZeroDivisionError, KeyError):
            out.append(None)
    return tuple(out)


def engine_source(calc_type: str, output_key: str | None = None) -> dict[str, Any]:
    """Where the engine computes an output: module, qualname, file and line."""
    rule = RULES[calc_type]
    fn = type(rule).compute
    lines, start = inspect.getsourcelines(fn)
    line = start
    if output_key:
        needle = f'_out("{output_key}"'
        line = next((start + i for i, text in enumerate(lines) if needle in text), start)
    path = Path(inspect.getsourcefile(fn) or "")
    try:
        rel = path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        rel = path.name
    return {"calc_type": calc_type, "output_key": output_key, "function": f"{fn.__module__}.{fn.__qualname__}",
            "file": rel, "line": line}


# --------------------------------------------------------------------------- results
@dataclass
class ObservationResult:
    observation: Observation
    agreement: str  # SUPPORTS | CONFLICT | NOT_EVALUATED
    cases: int = 0
    max_rel_error: float = 0.0
    counterexample: dict[str, float] | None = None
    detail: str = ""

    def public(self, registry: Registry) -> dict[str, Any]:
        repo = registry.repository(self.observation.repo) or {}
        o = self.observation
        permalink = (f"{repo['origin']}/blob/{repo['commit']}/{o.file}" + (f"#L{o.line}" if o.line else "")
                     if repo.get("commit") and str(repo.get("origin", "")).startswith("https://") else None)
        return {**o.public(), "agreement": self.agreement, "cases": self.cases,
                "max_rel_error": self.max_rel_error, "counterexample": self.counterexample, "detail": self.detail,
                "commit": repo.get("commit"), "license": repo.get("license"), "origin": repo.get("origin"),
                "permalink": permalink}


@dataclass
class RuleValidation:
    rule: Rule
    status: str = "UNVERIFIED"
    confidence: str = "none"
    iso_anchor: bool = False
    missing_evidence_ids: list[str] = field(default_factory=list)
    engine_cases: int = 0
    engine_max_rel_error: float = 0.0
    engine_agrees: bool | None = None
    engine_counterexample: dict[str, float] | None = None
    identity_failures: list[str] = field(default_factory=list)
    consistency: dict[str, Any] | None = None
    derivative_check: dict[str, Any] | None = None
    observations: list[ObservationResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def supporting_repositories(self) -> list[str]:
        return sorted({o.observation.repo for o in self.observations if o.agreement == "SUPPORTS"})

    @property
    def conflicts(self) -> list[ObservationResult]:
        return [o for o in self.observations if o.agreement == "CONFLICT"]


def _display_inputs(env: dict[str, float], names: set[str], angles: set[str]) -> dict[str, float]:
    return {k: (round(math.degrees(env[k]), 6) if k in angles else round(env[k], 9)) for k in sorted(names) if k in env}


def _in_domain(env: dict[str, float], domain: dict[str, float]) -> bool:
    return all(k in env and abs(env[k] - v) <= 1e-12 for k, v in domain.items())


def _agree(kind: str, a: float, b: float) -> bool:
    if kind == "sign_condition":
        sa = 0 if abs(a) < ZERO_TOL else (1 if a > 0 else -1)
        sb = 0 if abs(b) < ZERO_TOL else (1 if b > 0 else -1)
        return sa == sb
    if kind == "zero_condition":
        return (abs(a) < ZERO_TOL) == (abs(b) < ZERO_TOL)
    return close(a, b)


def _compare_expressions(rule: Rule, candidate: str, envs: tuple[dict[str, float], ...],
                         domain: dict[str, float], angles: set[str]) -> tuple[int, float, dict | None, str]:
    cases, worst, counter = 0, 0.0, None
    needed = expr.names(candidate) | rule.inputs()
    for env in envs:
        if not _in_domain(env, domain) or not needed <= env.keys():
            continue
        try:
            ref = expr.evaluate(rule.expression, env)  # type: ignore[arg-type]
            got = expr.evaluate(candidate, env)
        except expr.ExpressionError as exc:
            return cases, worst, counter, f"değerlendirilemedi: {exc}"
        cases += 1
        err = rel_error(got, ref)
        if not _agree(rule.kind, got, ref):
            if counter is None:
                counter = {"inputs": _display_inputs(env, needed, angles), "expected": ref, "observed": got}
            worst = max(worst, err if rule.kind == "formula" else 1.0)
    return cases, worst, counter, ""


def _check_observation(rule: Rule, obs: Observation, angles: set[str]) -> ObservationResult:
    if rule.kind == "constants":
        if obs.constant is None or obs.value is None:
            return ObservationResult(obs, "NOT_EVALUATED", detail="sabit değeri yok")
        ref = float(rule.constants[obs.constant])
        ok = abs(float(obs.value) - ref) <= 1e-12
        return ObservationResult(obs, "SUPPORTS" if ok else "CONFLICT", cases=1,
                                 max_rel_error=0.0 if ok else rel_error(float(obs.value), ref),
                                 counterexample=None if ok else {"constant": obs.constant, "expected": ref,
                                                                 "observed": obs.value})
    if not obs.expression or not rule.expression:
        return ObservationResult(obs, "NOT_EVALUATED", detail=obs.note or "sayısal karşılaştırma yapılamaz")
    cases, worst, counter, err = _compare_expressions(rule, obs.expression, matrix_envs(rule.matrix), obs.domain, angles)
    if err:
        return ObservationResult(obs, "NOT_EVALUATED", detail=err)
    if cases == 0:
        return ObservationResult(obs, "NOT_EVALUATED", detail="doğrulama matrisinde bu alanın vakası yok")
    return ObservationResult(obs, "CONFLICT" if counter else "SUPPORTS", cases=cases, max_rel_error=worst,
                             counterexample=counter)


def _engine_check(rule: Rule, v: RuleValidation, angles: set[str]) -> None:
    cases, worst, counter, agrees = 0, 0.0, None, True
    envs = matrix_envs(rule.matrix)
    for binding in rule.engine:
        results = _engine_matrix(binding.calc_type, rule.matrix)
        for env, outs in zip(envs, results):
            if outs is None or binding.output_key not in outs or not rule.inputs() <= env.keys():
                continue
            ref = expr.evaluate(rule.expression, env)  # type: ignore[arg-type]
            got = engine_value(binding.unit, outs[binding.output_key])
            cases += 1
            err = rel_error(got, ref)
            worst = max(worst, err)
            if not close(got, ref):
                agrees = False
                counter = counter or {"binding": f"{binding.calc_type}:{binding.output_key}",
                                      "inputs": _display_inputs(env, rule.inputs(), angles),
                                      "registry": ref, "engine": got}
    v.engine_cases, v.engine_max_rel_error, v.engine_counterexample = cases, worst, counter
    v.engine_agrees = agrees and cases > 0


def _engine_defaults_check(rule: Rule, v: RuleValidation) -> None:
    spec = rule.engine_defaults or {}
    engine_rule = RULES[spec["calc_type"]]
    ctx = RuleContext(values={"z": 20.0, "m_n": 2.0}, provided={"z", "m_n"}, evidence={})
    engine_rule.apply_defaults(ctx)
    mismatches = {k: (rule.constants[k], ctx.values.get(ek)) for k, ek in spec["map"].items()
                  if ctx.values.get(ek) is None or not close(float(ctx.values[ek]), float(rule.constants[k]))}
    v.engine_cases = len(spec["map"])
    v.engine_agrees = not mismatches
    if mismatches:
        v.engine_counterexample = {k: {"registry": a, "engine": b} for k, (a, b) in mismatches.items()}
    unused = sorted(set(rule.constants) - set(spec["map"]))
    if unused:
        v.notes.append(f"Motor bu sabitleri kullanmıyor (yalnızca kayıt): {', '.join(unused)}")


def _consistency(name: str) -> dict[str, Any]:
    """Named consistency checks of CANDIDATE rules against the VERIFIED engine."""
    cases, worst = 0, 0.0
    if name == "pair_shift_roundtrip":
        for env in matrix_envs("pair_shift"):
            outs = engine_outputs("gear_pair", {**env, "a_w": env["a_w"]})
            got = math.radians(outs["alpha_wt"])
            cases += 1
            worst = max(worst, rel_error(got, env["alpha_wt"]))
        desc = "a_w (inv bağıntısı) → motor ISO 21771 Eş.(54) → α_wt geri elde edilir"
    elif name == "pair_reference_centre_distance":
        for env in matrix_envs("pair"):
            outs = engine_outputs("gear_pair", {k: v for k, v in env.items() if k != "a_w"})
            cases += 1
            worst = max(worst, rel_error((outs["d1"] + outs["d2"]) / 2, env["a"]))
        desc = "a = (d1 + d2)/2, d1 ve d2 motordan (ISO 21771 Eş.(1))"
    else:  # pragma: no cover - registry check
        raise KeyError(name)
    return {"name": name, "description": desc, "cases": cases, "max_rel_error": worst, "agrees": worst <= 1e-8}


def _derivative_check(rule: Rule) -> dict[str, Any]:
    wrt, of = rule.derivative_wrt or "", rule.derivative_of or ""
    cases, worst, counter = 0, 0.0, None
    for env in matrix_envs(rule.matrix):
        x0 = env[wrt]
        h = 1e-6 * max(1.0, abs(x0))
        numeric = (expr.evaluate(of, {**env, wrt: x0 + h}) - expr.evaluate(of, {**env, wrt: x0 - h})) / (2 * h)
        claimed = expr.evaluate(rule.expression, env)  # type: ignore[arg-type]
        cases += 1
        err = abs(claimed - numeric) / max(1e-12, abs(numeric))
        if err > worst:
            worst = err
            counter = {wrt: round(math.degrees(x0), 6), "claimed": claimed, "numeric": numeric}
    return {"cases": cases, "max_rel_error": worst, "agrees": worst <= 1e-5, "worst_case": counter,
            "method": "central difference, h = 1e-6·max(1, |x|)"}


def validate_rule(rule: Rule, registry: Registry) -> RuleValidation:
    angles = registry.angle_variables()
    v = RuleValidation(rule=rule)
    v.missing_evidence_ids = [e for e in rule.evidence_ids if e not in REQ]
    v.iso_anchor = bool(rule.evidence_ids) and not v.missing_evidence_ids
    if rule.kind == "derivative_claim":
        v.derivative_check = _derivative_check(rule)
    elif rule.kind == "constants":
        if rule.engine_defaults:
            _engine_defaults_check(rule, v)
    elif rule.engine:
        _engine_check(rule, v, angles)
    if rule.expression and rule.kind == "formula":
        for ident in rule.identities:
            _cases, _worst, counter, err = _compare_expressions(rule, ident, matrix_envs(rule.matrix), {}, angles)
            if counter or err:
                v.identity_failures.append(ident)
    if rule.engine_consistency:
        v.consistency = _consistency(rule.engine_consistency)
    v.observations = [_check_observation(rule, o, angles) for o in rule.observations]
    return v


def _status(v: RuleValidation, verified: set[str]) -> tuple[str, str]:
    rule = v.rule
    if rule.kind == "derivative_claim":
        return ("CANDIDATE", "identity_verified") if v.derivative_check and v.derivative_check["agrees"] \
            else ("REJECTED", "failed_mathematical_check")
    if v.identity_failures or v.engine_agrees is False:
        return "CONFLICT", "engine_or_identity_disagreement"
    if rule.authority_scope == "implementation_convention":
        return "UNVERIFIED", "implementation_convention"
    if v.iso_anchor and v.engine_agrees:
        return "VERIFIED", "iso_evidence_and_engine"
    supports = len(v.supporting_repositories)
    if v.consistency and not v.consistency["agrees"]:
        return "CONFLICT", "inconsistent_with_engine"
    if supports >= 2:
        return "CANDIDATE", "multiple_independent_implementations" + ("+engine_consistent" if v.consistency else "")
    if supports == 1:
        return "CANDIDATE", "single_tested_implementation" + ("+engine_consistent" if v.consistency else "")
    if rule.derived_from and all(d in verified for d in rule.derived_from):
        return "CANDIDATE", "derived_from_verified_rules"
    return "UNVERIFIED", "no_independent_support"


@dataclass
class ValidationReport:
    registry: Registry
    results: dict[str, RuleValidation]

    def status(self, rule_id: str) -> str:
        return self.results[rule_id].status

    def verified(self) -> list[Rule]:
        return [v.rule for v in self.results.values() if v.status == "VERIFIED"]

    def conflicts(self) -> list[dict[str, Any]]:
        out = []
        for v in self.results.values():
            authority = "ISO kanıt gereksinimi + deterministik motor" if v.status == "VERIFIED" else \
                "yok (dış uygulamalar arası ayrışma; DAYANERA hesapta kullanmaz)"
            for o in v.conflicts:
                ob = o.observation
                out.append({
                    "id": f"conflict:{v.rule.id}:{ob.repo}:{ob.symbol}:{ob.target}",
                    "status": "CONFLICT", "rule_id": v.rule.id, "rule_status": v.status, "authority": authority,
                    **o.public(self.registry),
                    "resolution": "DAYANERA motoru değiştirilmedi; dış uygulama benimsenmedi.",
                })
        return out

    def summary(self) -> dict[str, Any]:
        by_status = {s: 0 for s in ("VERIFIED", "CANDIDATE", "UNVERIFIED", "CONFLICT", "REJECTED")}
        obs = {"SUPPORTS": 0, "CONFLICT": 0, "NOT_EVALUATED": 0}
        repos: dict[str, dict[str, int]] = {}
        engine_cases = 0
        for v in self.results.values():
            by_status[v.status] += 1
            engine_cases += v.engine_cases
            for o in v.observations:
                obs[o.agreement] += 1
                repos.setdefault(o.observation.repo, {"SUPPORTS": 0, "CONFLICT": 0, "NOT_EVALUATED": 0})[o.agreement] += 1
        return {"rules": len(self.results), "by_status": by_status, "conflict_records": obs["CONFLICT"],
                "observations": obs, "repositories": repos, "engine_checked_cases": engine_cases}


def validate(registry: Registry | None = None) -> ValidationReport:
    registry = registry or get_registry()
    results = {r.id: validate_rule(r, registry) for r in registry.rules}
    verified: set[str] = set()
    for _ in range(2):  # derived_from may point at rules later in the file
        for v in results.values():
            v.status, v.confidence = _status(v, verified)
            if v.status == "VERIFIED":
                verified.add(v.rule.id)
    for v in results.values():  # an implementation of a rejected claim is a conflict with the authority
        if v.status == "REJECTED":
            for o in v.observations:
                if o.agreement == "SUPPORTS":
                    o.agreement, o.detail = "CONFLICT", "reddedilen iddiayı uyguluyor (bağımsız matematiksel kontrol başarısız)"
                    o.counterexample = (v.derivative_check or {}).get("worst_case")
                    o.max_rel_error = (v.derivative_check or {}).get("max_rel_error", 0.0)
    report = ValidationReport(registry=registry, results=results)
    s = report.summary()
    log.info("knowledge.validation rules=%d verified=%d candidate=%d unverified=%d conflict=%d rejected=%d "
             "conflict_records=%d engine_cases=%d", s["rules"], s["by_status"]["VERIFIED"], s["by_status"]["CANDIDATE"],
             s["by_status"]["UNVERIFIED"], s["by_status"]["CONFLICT"], s["by_status"]["REJECTED"],
             s["conflict_records"], s["engine_checked_cases"])
    for v in results.values():
        if v.status == "CONFLICT":
            log.error("knowledge.validation.engine_conflict rule=%s counterexample=%s", v.rule.id, v.engine_counterexample)
    return report


@lru_cache(maxsize=1)
def get_report() -> ValidationReport:
    return validate()


# --------------------------------------------------------------------------- provenance
def provenance(rule_id: str, resolver: Any = None) -> dict[str, Any] | None:
    """Answer "where did this formula come from?" for one registry rule.

    With an evidence ``resolver`` (``DbEvidenceResolver``) the ISO passages are
    resolved live against the active verified corpus of the caller's scope.
    """
    report = get_report()
    v = report.results.get(rule_id)
    if v is None:
        return None
    rule, registry = v.rule, report.registry
    iso_passages = []
    for rid in rule.evidence_ids:
        req = REQ.get(rid)
        entry: dict[str, Any] = {"requirement_id": rid, "description": req.description if req else None,
                                 "defined": req is not None}
        if req is not None and resolver is not None:
            match = resolver.resolve(req)
            entry["active_in_corpus"] = match is not None
            if match is not None:
                entry["source"] = {k: val for k, val in match.public().items() if k != "excerpt"}
                entry["excerpt"] = match.excerpt[:400]
        iso_passages.append(entry)
    engine = [engine_source(b.calc_type, b.output_key) | {"unit": b.unit or registry.variables.get(rule.output or "", {}).get("unit")}
              for b in rule.engine]
    if rule.engine_defaults:
        engine.append(engine_source(rule.engine_defaults["calc_type"]) | {"kind": "defaults"})
    return {
        "id": rule.id, "name": rule.name, "name_tr": rule.name_tr, "family": rule.family, "kind": rule.kind,
        "status": v.status, "confidence": v.confidence, "display": rule.display, "expression": rule.expression,
        "output": rule.output, "constants": rule.constants or None,
        "variables": {n: registry.variables.get(n) for n in sorted(rule.inputs() | ({rule.output} if rule.output else set()))},
        "assumptions": list(rule.assumptions), "applicability": rule.applicability, "valid_ranges": rule.valid_ranges,
        "iso": {"citation": rule.iso_citation, "evidence": iso_passages, "anchored": v.iso_anchor},
        "engine": engine,
        "validation": {
            "engine_cases": v.engine_cases, "engine_max_rel_error": v.engine_max_rel_error,
            "engine_agrees": v.engine_agrees, "engine_counterexample": v.engine_counterexample,
            "identities": list(rule.identities), "identity_failures": v.identity_failures,
            "consistency": v.consistency, "derivative_check": v.derivative_check, "notes": v.notes,
            "matrix": rule.matrix, "tolerance": {"relative": REL_TOL},
        },
        "external": [o.public(registry) for o in v.observations],
        "supporting_repositories": v.supporting_repositories,
        "conflicts": [o.public(registry) for o in v.conflicts],
        "dayanera_tests": list(rule.dayanera_tests),
        "implementation_notes": rule.implementation_notes,
    }


def snapshot(report: ValidationReport) -> dict[str, Any]:
    """Deterministic, reviewable validation result (docs/knowledge/formula_registry_validation.json)."""
    from app.calc.engine import ENGINE_VERSION
    from app.knowledge.regression import registry_fingerprint

    conflict_keys = ("id", "rule_id", "rule_status", "repo", "file", "symbol", "target", "line", "commit", "license",
                     "max_rel_error", "counterexample", "suspected_error_class", "permalink")
    return {
        "schema": "dayanera.formula_registry_validation/1",
        "engine_version": ENGINE_VERSION,
        "registry_fingerprint": registry_fingerprint(),
        "summary": report.summary(),
        "rules": [{**rule_summary(v), "engine_cases": v.engine_cases,
                   "engine_max_rel_error": v.engine_max_rel_error} for v in report.results.values()],
        "conflicts": [{k: c.get(k) for k in conflict_keys} for c in report.conflicts()],
    }


def rule_summary(v: RuleValidation) -> dict[str, Any]:
    r = v.rule
    return {"id": r.id, "name": r.name, "name_tr": r.name_tr, "family": r.family, "kind": r.kind, "status": v.status,
            "confidence": v.confidence, "display": r.display, "iso_citation": r.iso_citation,
            "engine": [f"{b.calc_type}:{b.output_key}" for b in r.engine],
            "supporting_repositories": v.supporting_repositories, "conflicts": len(v.conflicts)}
