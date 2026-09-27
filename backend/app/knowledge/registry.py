"""Engineering knowledge registry: normalized formulas with provenance.

The registry data (``data/gear_formulas.json``) is machine-readable knowledge
distilled from DAYANERA's ISO evidence requirements, the deterministic engine
and statically analysed reference implementations. Statuses are NOT stored in
the data: :mod:`app.knowledge.validation` computes them from evidence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.knowledge import expr

DATA_DIR = Path(__file__).resolve().parent / "registry_data"
FORMULAS_FILE = DATA_DIR / "gear_formulas.json"
REPOSITORIES_FILE = DATA_DIR / "reference_repositories.json"

STATUSES = ("UNVERIFIED", "CANDIDATE", "VERIFIED", "CONFLICT", "REJECTED")
RULE_KINDS = ("formula", "constants", "sign_condition", "zero_condition", "derivative_claim")


class RegistryError(ValueError):
    """The registry data is malformed (checked at load time, fails loudly)."""


@dataclass(frozen=True)
class Observation:
    """One external implementation of (a special case of) a rule, cited by location."""

    repo: str
    file: str
    symbol: str
    target: str
    expression: str | None = None
    domain: dict[str, float] = field(default_factory=dict)
    constant: str | None = None
    value: float | None = None
    convention: str | None = None
    note: str | None = None
    tests: tuple[str, ...] = ()
    line: int | None = None
    fingerprint: str | None = None
    suspected_error_class: str | None = None

    @property
    def key(self) -> str:
        return f"{self.repo}:{self.file}:{self.symbol}:{self.target}"

    def public(self) -> dict[str, Any]:
        return {"repo": self.repo, "file": self.file, "symbol": self.symbol, "target": self.target,
                "line": self.line, "fingerprint": self.fingerprint, "expression": self.expression,
                "domain": self.domain, "constant": self.constant, "value": self.value,
                "convention": self.convention, "note": self.note, "tests": list(self.tests),
                "suspected_error_class": self.suspected_error_class}


@dataclass(frozen=True)
class EngineBinding:
    calc_type: str
    output_key: str
    unit: str | None = None  # engine unit when it differs from the registry variable unit ("deg")


@dataclass(frozen=True)
class Rule:
    id: str
    family: str
    name: str
    name_tr: str
    kind: str
    display: str
    matrix: str
    output: str | None = None
    expression: str | None = None
    identities: tuple[str, ...] = ()
    derive: bool = False
    derived_from: tuple[str, ...] = ()
    constants: dict[str, float] = field(default_factory=dict)
    engine: tuple[EngineBinding, ...] = ()
    engine_defaults: dict[str, Any] | None = None
    engine_consistency: str | None = None
    iso_citation: str = ""
    evidence_ids: tuple[str, ...] = ()
    authority_scope: str | None = None
    derivative_of: str | None = None
    derivative_wrt: str | None = None
    assumptions: tuple[str, ...] = ()
    applicability: dict[str, Any] = field(default_factory=dict)
    valid_ranges: dict[str, Any] = field(default_factory=dict)
    observations: tuple[Observation, ...] = ()
    dayanera_tests: tuple[str, ...] = ()
    implementation_notes: str | None = None

    def inputs(self) -> set[str]:
        return expr.names(self.expression) if self.expression else set()


@dataclass(frozen=True)
class Registry:
    rules: tuple[Rule, ...]
    variables: dict[str, dict[str, Any]]
    repositories: tuple[dict[str, Any], ...]
    conventions: dict[str, str]

    def get(self, rule_id: str) -> Rule | None:
        return next((r for r in self.rules if r.id == rule_id), None)

    def repository(self, name: str) -> dict[str, Any] | None:
        return next((r for r in self.repositories if r["name"] == name), None)

    def rules_for_engine(self, calc_type: str) -> list[tuple[Rule, EngineBinding]]:
        return [(r, b) for r in self.rules for b in r.engine if b.calc_type == calc_type]

    def angle_variables(self) -> set[str]:
        return {k for k, v in self.variables.items() if v.get("unit") == "rad"}


def _obs(raw: dict[str, Any]) -> Observation:
    return Observation(
        repo=raw["repo"], file=raw["file"], symbol=raw.get("symbol", ""), target=raw["target"],
        expression=raw.get("expression"), domain=dict(raw.get("domain") or {}), constant=raw.get("constant"),
        value=raw.get("value"), convention=raw.get("convention"), note=raw.get("note"),
        tests=tuple(raw.get("tests") or ()), line=raw.get("line"), fingerprint=raw.get("fingerprint"),
        suspected_error_class=raw.get("suspected_error_class"),
    )


def _rule(raw: dict[str, Any]) -> Rule:
    iso = raw.get("iso") or {}
    return Rule(
        id=raw["id"], family=raw["family"], name=raw["name"], name_tr=raw.get("name_tr", raw["name"]),
        kind=raw["kind"], display=raw["display"], matrix=raw.get("matrix", ""), output=raw.get("output"),
        expression=raw.get("expression"), identities=tuple(raw.get("identities") or ()),
        derive=bool(raw.get("derive", False)), derived_from=tuple(raw.get("derived_from") or ()),
        constants=dict(raw.get("constants") or {}),
        engine=tuple(EngineBinding(b["calc_type"], b["output_key"], b.get("unit")) for b in raw.get("engine") or ()),
        engine_defaults=raw.get("engine_defaults"), engine_consistency=raw.get("engine_consistency"),
        iso_citation=iso.get("citation", ""), evidence_ids=tuple(iso.get("evidence_ids") or ()),
        authority_scope=raw.get("authority_scope"), derivative_of=raw.get("of"), derivative_wrt=raw.get("wrt"),
        assumptions=tuple(raw.get("assumptions") or ()), applicability=dict(raw.get("applicability") or {}),
        valid_ranges=dict(raw.get("valid_ranges") or {}),
        observations=tuple(_obs(o) for o in raw.get("observations") or ()),
        dayanera_tests=tuple(raw.get("dayanera_tests") or ()), implementation_notes=raw.get("implementation_notes"),
    )


def check(registry: Registry) -> list[str]:
    """Structural problems of the registry data (empty list = well formed)."""
    problems: list[str] = []
    ids = [r.id for r in registry.rules]
    for dup in {i for i in ids if ids.count(i) > 1}:
        problems.append(f"duplicate rule id {dup}")
    known_repos = {r["name"] for r in registry.repositories if r.get("inspected")}
    declared = set(registry.variables)
    for r in registry.rules:
        if r.kind not in RULE_KINDS:
            problems.append(f"{r.id}: unknown kind {r.kind}")
        exprs = [e for e in (r.expression, *r.identities, r.derivative_of) if e]
        exprs += [o.expression for o in r.observations if o.expression]
        for e in exprs:
            try:
                unknown = expr.names(e) - declared
            except expr.ExpressionError as exc:
                problems.append(f"{r.id}: {exc}")
                continue
            if unknown:
                problems.append(f"{r.id}: undeclared variables {sorted(unknown)} in {e!r}")
        if r.kind != "constants" and (not r.output or r.output not in declared):
            problems.append(f"{r.id}: output {r.output!r} is not a declared variable")
        if r.kind == "constants" and not r.constants:
            problems.append(f"{r.id}: constants rule without constants")
        for o in r.observations:
            if o.repo not in known_repos:
                problems.append(f"{r.id}: observation cites uninspected repository {o.repo}")
            if o.constant is not None and o.constant not in r.constants:
                problems.append(f"{r.id}: observation constant {o.constant} not in rule constants")
        for dep in r.derived_from:
            if dep not in ids:
                problems.append(f"{r.id}: derived_from unknown rule {dep}")
    return problems


def load(path: Path = FORMULAS_FILE, repositories: Path = REPOSITORIES_FILE) -> Registry:
    data = json.loads(path.read_text(encoding="utf-8"))
    repos = json.loads(repositories.read_text(encoding="utf-8"))
    reg = Registry(rules=tuple(_rule(r) for r in data["rules"]), variables=data["variables"],
                   repositories=tuple(repos["repositories"]), conventions=data.get("conventions", {}))
    problems = check(reg)
    if problems:
        raise RegistryError("; ".join(problems))
    return reg


@lru_cache(maxsize=1)
def get_registry() -> Registry:
    return load()
