"""Typed request/result structures for the calculation engine."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ProvenanceKind = Literal[
    "user_input", "extracted_value", "memory_item", "derived", "standard_constant", "assumption"
]


@dataclass
class Provenance:
    kind: ProvenanceKind
    ref_id: str | None = None
    status: str | None = None  # confidence status for extracted values / memory items
    note: str | None = None


@dataclass
class InputValue:
    key: str
    value: float
    unit: str
    provenance: Provenance


@dataclass
class CalcRequest:
    calc_type: str
    inputs: dict[str, InputValue]


@dataclass(frozen=True)
class InputSpec:
    key: str
    label: str
    kind: Literal["length", "angle", "dimensionless", "integer", "grade"]
    required: bool = True
    min_value: float | None = None
    max_value: float | None = None
    description: str = ""


@dataclass(frozen=True)
class EvidenceRequirement:
    id: str
    standard_code_like: str
    must_contain: tuple[str, ...]
    description: str
    page_hint: int | None = None


@dataclass
class EvidenceMatch:
    requirement_id: str
    description: str
    document_id: str
    version_id: str
    version_number: int
    document_title: str
    standard_code: str | None
    page_number: int | None
    locator: str
    excerpt: str
    confidence_status: str
    page_text: str = field(default="", repr=False)

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("page_text", None)
        return d


@dataclass
class OutputValue:
    key: str
    label: str
    value: float
    unit: str
    formula_id: str
    expression: str
    display: str
    unrounded: float | None = None
    abs_tol: float | None = None  # Qwen-draft comparison: fixed absolute tolerance (no relative term)


@dataclass
class Diagnostic:
    level: Literal["info", "warning", "error"]
    code: str
    message: str


@dataclass
class CalcResult:
    calc_type: str
    status: Literal["ok", "refused", "invalid_input", "error"]
    engine_version: str
    message: str
    outputs: list[OutputValue] = field(default_factory=list)
    inputs: list[dict[str, Any]] = field(default_factory=list)
    constants: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def output_map(self) -> dict[str, float]:
        return {o.key: o.value for o in self.outputs}
