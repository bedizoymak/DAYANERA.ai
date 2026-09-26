"""Compare an LLM-drafted calculation with the engine's authoritative result."""
from __future__ import annotations

import math
from typing import Any

from app.calc.types import CalcResult

REL_TOL = 0.005
ABS_TOL_BY_UNIT = {"mm": 0.01, "°": 0.05, "µm": 0.5, "": 0.001}


def compare(result: CalcResult, draft_outputs: dict[str, Any] | None) -> dict[str, Any]:
    """Return a persisted comparison record.

    ``mismatch`` is True when any drafted value differs from the engine value
    beyond tolerance, or when the draft contains non-numeric values for keys
    the engine computed. Missing keys are reported but are not mismatches.
    """
    if result.status != "ok":
        return {"performed": False, "reason": "engine_not_ok", "mismatch": False, "items": []}
    if not draft_outputs:
        return {"performed": False, "reason": "no_llm_draft", "mismatch": False, "items": []}
    items = []
    mismatch = False
    for out in result.outputs:
        if out.key not in draft_outputs:
            items.append({"key": out.key, "engine": out.value, "llm": None, "status": "missing_in_draft"})
            continue
        raw = draft_outputs[out.key]
        try:
            llm_val = float(str(raw).replace(",", "."))
        except (TypeError, ValueError):
            items.append({"key": out.key, "engine": out.value, "llm": raw, "status": "non_numeric"})
            mismatch = True
            continue
        tol = max(ABS_TOL_BY_UNIT.get(out.unit, 0.001), REL_TOL * abs(out.value))
        diff = abs(llm_val - out.value)
        ok = math.isfinite(llm_val) and diff <= tol
        items.append({"key": out.key, "engine": out.value, "llm": llm_val, "abs_diff": diff,
                      "tolerance": tol, "status": "match" if ok else "mismatch"})
        if not ok:
            mismatch = True
    extra = sorted(set(draft_outputs) - {o.key for o in result.outputs})
    return {"performed": True, "mismatch": mismatch, "items": items, "extra_keys_in_draft": extra,
            "rel_tol": REL_TOL}
