"""Deterministic root-cause diagnosis of LLM-draft vs engine mismatches.

For every mismatching output the diagnosis evaluates explicit error models
(hypotheses) such as "degrees fed to cos() as radians" or "normal pressure
angle used in the transverse plane" on the engine's resolved inputs. A
hypothesis explains the draft when it reproduces the drafted value within the
comparator tolerance AND differs from the correct value (a hypothesis that
coincides with the right answer, e.g. normal/transverse confusion at β = 0,
cannot explain anything and is reported as not applicable).

When no structural hypothesis reproduces the value but the value is physically
plausible for the correct formula family, the class is LLM_ARITHMETIC_ERROR.
Nothing here asks an LLM: an LLM may *suggest* a class (stored separately as a
non-authoritative hint), it never decides one.
"""
from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from typing import Any

from app.calc.compare import ABS_TOL_BY_UNIT, REL_TOL
from app.calc.rules import RULES, RuleContext
from app.knowledge.registry import get_registry

ROOT_CAUSES = (
    "ANGLE_UNIT_ERROR", "DEGREE_RADIAN_ERROR", "WRONG_PRESSURE_ANGLE", "NORMAL_TRANSVERSE_CONFUSION",
    "MODULE_CONVERSION_ERROR", "WRONG_BASE_CIRCLE_FORMULA", "WRONG_BASE_PITCH_FORMULA", "PROFILE_SHIFT_OMITTED",
    "INTERNAL_EXTERNAL_SIGN_ERROR", "HELIX_ANGLE_OMITTED", "ROUNDING_ERROR", "UNIT_ERROR", "FORMULA_SELECTION_ERROR",
    "LLM_ARITHMETIC_ERROR", "UNKNOWN",
)
CLASS_LABEL_TR = {
    "ANGLE_UNIT_ERROR": "açı birimi hatası", "DEGREE_RADIAN_ERROR": "derece/radyan karışıklığı",
    "WRONG_PRESSURE_ANGLE": "yanlış kavrama açısı", "NORMAL_TRANSVERSE_CONFUSION": "normal/alın kesit karışıklığı",
    "MODULE_CONVERSION_ERROR": "modül dönüşüm hatası", "WRONG_BASE_CIRCLE_FORMULA": "yanlış temel daire formülü",
    "WRONG_BASE_PITCH_FORMULA": "yanlış temel adım formülü", "PROFILE_SHIFT_OMITTED": "profil kaydırma atlandı",
    "INTERNAL_EXTERNAL_SIGN_ERROR": "iç/dış dişli işaret hatası", "HELIX_ANGLE_OMITTED": "helis açısı atlandı",
    "ROUNDING_ERROR": "yuvarlama hatası", "UNIT_ERROR": "birim hatası", "FORMULA_SELECTION_ERROR": "yanlış formül seçimi",
    "LLM_ARITHMETIC_ERROR": "LLM aritmetik hatası", "UNKNOWN": "belirlenemedi",
}
# structural (explainable) classes win over the fallbacks when choosing the primary class
_FALLBACK = ("LLM_ARITHMETIC_ERROR", "UNKNOWN")
STANDARD_PRESSURE_ANGLES = (14.5, 15.0, 17.5, 20.0, 22.5, 25.0, 30.0)

Hyp = tuple[str, str, Callable[[dict[str, float]], float | None]]


def resolved_inputs(calc_type: str, canonical: dict[str, float]) -> dict[str, float]:
    """The engine's inputs after its own defaults (ISO 53 rack, β = 0, x = 0 ...), canonical units."""
    rule = RULES[calc_type]
    ctx = RuleContext(values=dict(canonical), provided=set(canonical), evidence={})
    rule.apply_defaults(ctx)
    return dict(ctx.values)


def _cyl_vars(inp: dict[str, float]) -> dict[str, float]:
    """Cylindrical-gear variables in radians, computed with the VERIFIED relations."""
    v = dict(inp)
    an, b = math.radians(inp.get("alpha_n", 20.0)), math.radians(inp.get("beta", 0.0))
    mn, z = inp.get("m_n", 0.0), inp.get("z", 0.0)
    x, k = inp.get("x", 0.0), inp.get("k", 0.0)
    ha, hf = inp.get("h_aP_star", 1.0), inp.get("h_fP_star", 1.25)
    mt = mn / math.cos(b)
    at = math.atan(math.tan(an) / math.cos(b))
    d = z * mt
    v.update(an=an, b=b, mn=mn, z=z, x=x, k=k, ha=ha, hf=hf, mt=mt, at=at, d=d, db=d * math.cos(at),
             pn=math.pi * mn, pt=math.pi * mt, da=d + 2 * (x + ha + k) * mn, df=d - 2 * (hf - x) * mn)
    return v


def _helical(v: dict[str, float]) -> bool:
    return abs(v.get("b", 0.0)) > 1e-12


def _shifted(v: dict[str, float]) -> bool:
    return abs(v.get("x", 0.0)) > 1e-12


def _hyps_cylindrical(key: str) -> list[Hyp]:
    cos, sin, tan, deg = math.cos, math.sin, math.tan, math.degrees
    H: dict[str, list[Hyp]] = {
        "d_b": [
            ("DEGREE_RADIAN_ERROR", "cos(α_t) derece değeri radyan sanıldı: d·cos(α°)", lambda v: v["d"] * cos(deg(v["at"]))),
            ("DEGREE_RADIAN_ERROR", "radyan değeri derece sanıldı: d·cos(α_rad °)", lambda v: v["d"] * cos(math.radians(v["at"]))),
            ("NORMAL_TRANSVERSE_CONFUSION", "α_n kullanıldı: d·cos α_n", lambda v: v["d"] * cos(v["an"]) if _helical(v) else None),
            ("HELIX_ANGLE_OMITTED", "β yok sayıldı: z·m_n·cos α_n", lambda v: v["z"] * v["mn"] * cos(v["an"]) if _helical(v) else None),
            ("MODULE_CONVERSION_ERROR", "m_t yerine m_n: z·m_n·cos α_t", lambda v: v["z"] * v["mn"] * cos(v["at"]) if _helical(v) else None),
            ("WRONG_BASE_CIRCLE_FORMULA", "d·sin α_t", lambda v: v["d"] * sin(v["at"])),
            ("WRONG_BASE_CIRCLE_FORMULA", "d / cos α_t", lambda v: v["d"] / cos(v["at"])),
            ("WRONG_BASE_CIRCLE_FORMULA", "d·cos² α_t", lambda v: v["d"] * cos(v["at"]) ** 2),
            ("WRONG_BASE_CIRCLE_FORMULA", "d·tan α_t", lambda v: v["d"] * tan(v["at"])),
            ("WRONG_BASE_CIRCLE_FORMULA", "yarıçap çap yerine: (d/2)·cos α_t", lambda v: v["d"] / 2 * cos(v["at"])),
            ("FORMULA_SELECTION_ERROR", "diş başı çapından: d_a·cos α_t", lambda v: v["da"] * cos(v["at"])),
            ("FORMULA_SELECTION_ERROR", "diş dibi çapından: d_f·cos α_t", lambda v: v["df"] * cos(v["at"])),
            ("FORMULA_SELECTION_ERROR", "profil kaydırma temel daireye eklendi: (d + 2x·m_n)·cos α_t",
             lambda v: (v["d"] + 2 * v["x"] * v["mn"]) * cos(v["at"]) if _shifted(v) else None),
        ],
        "p_bt": [
            ("DEGREE_RADIAN_ERROR", "cos(α_t) derece değeri radyan sanıldı: p_t·cos(α°)", lambda v: v["pt"] * cos(deg(v["at"]))),
            ("NORMAL_TRANSVERSE_CONFUSION", "normal temel adım p_bn = p_n·cos α_n verildi", lambda v: v["pn"] * cos(v["an"]) if _helical(v) else None),
            ("NORMAL_TRANSVERSE_CONFUSION", "α_n kullanıldı: p_t·cos α_n", lambda v: v["pt"] * cos(v["an"]) if _helical(v) else None),
            ("WRONG_BASE_PITCH_FORMULA", "p_t·sin α_t", lambda v: v["pt"] * sin(v["at"])),
            ("WRONG_BASE_PITCH_FORMULA", "p_t / cos α_t", lambda v: v["pt"] / cos(v["at"])),
            ("WRONG_BASE_PITCH_FORMULA", "π·d_b (z'ye bölünmedi)", lambda v: math.pi * v["db"]),
            ("WRONG_BASE_PITCH_FORMULA", "p_t·cos² α_t", lambda v: v["pt"] * cos(v["at"]) ** 2),
        ],
        "d": [
            ("HELIX_ANGLE_OMITTED", "β yok sayıldı: z·m_n", lambda v: v["z"] * v["mn"] if _helical(v) else None),
            ("MODULE_CONVERSION_ERROR", "z·m_n·cos β", lambda v: v["z"] * v["mn"] * cos(v["b"]) if _helical(v) else None),
            ("DEGREE_RADIAN_ERROR", "cos β derece/radyan: z·m_n / cos(β°)", lambda v: v["z"] * v["mn"] / cos(deg(v["b"])) if _helical(v) else None),
            ("FORMULA_SELECTION_ERROR", "yarıçap çap yerine: z·m_t / 2", lambda v: v["d"] / 2),
        ],
        "m_t": [
            ("MODULE_CONVERSION_ERROR", "m_n·cos β", lambda v: v["mn"] * cos(v["b"]) if _helical(v) else None),
            ("HELIX_ANGLE_OMITTED", "m_t = m_n alındı", lambda v: v["mn"] if _helical(v) else None),
            ("DEGREE_RADIAN_ERROR", "m_n / cos(β°)", lambda v: v["mn"] / cos(deg(v["b"])) if _helical(v) else None),
        ],
        "alpha_t": [
            ("NORMAL_TRANSVERSE_CONFUSION", "α_t = α_n alındı", lambda v: deg(v["an"]) if _helical(v) else None),
            ("NORMAL_TRANSVERSE_CONFUSION", "ters dönüşüm: arctan(tan α_n·cos β)", lambda v: deg(math.atan(tan(v["an"]) * cos(v["b"]))) if _helical(v) else None),
            ("ANGLE_UNIT_ERROR", "radyan olarak raporlandı", lambda v: v["at"]),
            ("DEGREE_RADIAN_ERROR", "arctan(α_n / cos β) (tan eksik)", lambda v: deg(math.atan(v["an"] / cos(v["b"])))),
        ],
        "p_n": [("NORMAL_TRANSVERSE_CONFUSION", "π·m_t verildi", lambda v: v["pt"] if _helical(v) else None)],
        "p_t": [("NORMAL_TRANSVERSE_CONFUSION", "π·m_n verildi", lambda v: v["pn"] if _helical(v) else None)],
        "h_a": [
            ("PROFILE_SHIFT_OMITTED", "x·m_n eklenmedi", lambda v: (v["ha"] + v["k"]) * v["mn"] if _shifted(v) else None),
            ("INTERNAL_EXTERNAL_SIGN_ERROR", "x işareti ters: h_aP − x·m_n", lambda v: (v["ha"] - v["x"] + v["k"]) * v["mn"] if _shifted(v) else None),
            ("NORMAL_TRANSVERSE_CONFUSION", "m_t ile: (h_aP* + x + k)·m_t", lambda v: (v["ha"] + v["x"] + v["k"]) * v["mt"] if _helical(v) else None),
        ],
        "h_f": [
            ("PROFILE_SHIFT_OMITTED", "x·m_n çıkarılmadı", lambda v: v["hf"] * v["mn"] if _shifted(v) else None),
            ("INTERNAL_EXTERNAL_SIGN_ERROR", "x işareti ters: h_fP + x·m_n", lambda v: (v["hf"] + v["x"]) * v["mn"] if _shifted(v) else None),
            ("NORMAL_TRANSVERSE_CONFUSION", "m_t ile: (h_fP* − x)·m_t", lambda v: (v["hf"] - v["x"]) * v["mt"] if _helical(v) else None),
        ],
        "h": [
            ("FORMULA_SELECTION_ERROR", "k yok sayıldı: (h_aP* + h_fP*)·m_n", lambda v: (v["ha"] + v["hf"]) * v["mn"] if abs(v["k"]) > 1e-12 else None),
            ("NORMAL_TRANSVERSE_CONFUSION", "m_t ile", lambda v: (v["ha"] + v["hf"] + v["k"]) * v["mt"] if _helical(v) else None),
        ],
        "d_a": [
            ("PROFILE_SHIFT_OMITTED", "x yok sayıldı: d + 2(h_aP + k·m_n)", lambda v: v["d"] + 2 * (v["ha"] + v["k"]) * v["mn"] if _shifted(v) else None),
            ("INTERNAL_EXTERNAL_SIGN_ERROR", "iç dişli işareti: d − 2h_a", lambda v: v["d"] - 2 * (v["ha"] + v["x"] + v["k"]) * v["mn"]),
            ("NORMAL_TRANSVERSE_CONFUSION", "m_t ile: d + 2(x + h_aP* + k)·m_t", lambda v: v["d"] + 2 * (v["x"] + v["ha"] + v["k"]) * v["mt"] if _helical(v) else None),
            ("HELIX_ANGLE_OMITTED", "d = z·m_n alındı", lambda v: v["z"] * v["mn"] + 2 * (v["x"] + v["ha"] + v["k"]) * v["mn"] if _helical(v) else None),
            ("FORMULA_SELECTION_ERROR", "diş dibi yüksekliği kullanıldı: d + 2h_f", lambda v: v["d"] + 2 * (v["hf"] - v["x"]) * v["mn"]),
        ],
        "d_f": [
            ("PROFILE_SHIFT_OMITTED", "x yok sayıldı: d − 2h_fP", lambda v: v["d"] - 2 * v["hf"] * v["mn"] if _shifted(v) else None),
            ("INTERNAL_EXTERNAL_SIGN_ERROR", "iç dişli işareti: d + 2h_f", lambda v: v["d"] + 2 * (v["hf"] - v["x"]) * v["mn"]),
            ("INTERNAL_EXTERNAL_SIGN_ERROR", "x işareti ters: d − 2(h_fP + x·m_n)", lambda v: v["d"] - 2 * (v["hf"] + v["x"]) * v["mn"] if _shifted(v) else None),
            ("NORMAL_TRANSVERSE_CONFUSION", "m_t ile", lambda v: v["d"] - 2 * (v["hf"] - v["x"]) * v["mt"] if _helical(v) else None),
            ("HELIX_ANGLE_OMITTED", "d = z·m_n alındı", lambda v: v["z"] * v["mn"] - 2 * (v["hf"] - v["x"]) * v["mn"] if _helical(v) else None),
            ("FORMULA_SELECTION_ERROR", "diş başı yüksekliği kullanıldı: d − 2h_a", lambda v: v["d"] - 2 * (v["ha"] + v["x"] + v["k"]) * v["mn"]),
        ],
    }
    return H.get(key, [])


def _hyps_pair(key: str) -> list[Hyp]:
    H: dict[str, list[Hyp]] = {
        "u": [("FORMULA_SELECTION_ERROR", "ters oran z1/z2", lambda v: min(v["z1"], v["z2"]) / max(v["z1"], v["z2"]))],
        "alpha_wt": [("ANGLE_UNIT_ERROR", "radyan olarak raporlandı", lambda v: v.get("alpha_wt_rad"))],
    }
    return H.get(key, [])


def _hyps_iso1328(key: str) -> list[Hyp]:
    return [
        ("ROUNDING_ERROR", "ISO 1328-1 5.2.3 yuvarlaması uygulanmadı", lambda v: v.get(f"{key}__unrounded")),
        ("FORMULA_SELECTION_ERROR", "sınıf çarpanı (√2)^A alındı", lambda v: v.get(f"{key}__unrounded", 0) * math.sqrt(2) ** 5
         if v.get(f"{key}__unrounded") else None),
        ("FORMULA_SELECTION_ERROR", "sınıf çarpanı 2^(A−5) alındı", lambda v: v.get(f"{key}__unrounded", 0) / math.sqrt(2) ** (v["A"] - 5)
         * 2 ** (v["A"] - 5) if v.get(f"{key}__unrounded") else None),
    ]


def _generic(unit: str) -> list[tuple[str, str, Callable[[float], float]]]:
    out: list[tuple[str, str, Callable[[float], float]]] = []
    if unit == "°":
        out += [("ANGLE_UNIT_ERROR", "radyan olarak raporlandı", math.radians),
                ("ANGLE_UNIT_ERROR", "grad olarak raporlandı", lambda e: e * 10 / 9)]
    if unit in ("mm", "µm"):
        out += [("UNIT_ERROR", "inç (÷25,4)", lambda e: e / 25.4), ("UNIT_ERROR", "×25,4", lambda e: e * 25.4),
                ("UNIT_ERROR", "cm (÷10)", lambda e: e / 10), ("UNIT_ERROR", "×10", lambda e: e * 10),
                ("UNIT_ERROR", "mm↔µm (×1000)", lambda e: e * 1000), ("UNIT_ERROR", "mm↔µm (÷1000)", lambda e: e / 1000)]
    return out


def tolerance(unit: str, engine_value: float, abs_tol: float | None = None) -> float:
    if abs_tol is not None:
        return abs_tol
    return max(ABS_TOL_BY_UNIT.get(unit, 0.001), REL_TOL * abs(engine_value))


def _within(a: float, b: float, tol: float) -> bool:
    return math.isfinite(a) and abs(a - b) <= tol


def _implied_angle(key: str, llm: float, v: dict[str, float]) -> float | None:
    base = {"d_b": v.get("d"), "p_bt": v.get("pt")}.get(key)
    if not base or not 0 < llm / base <= 1:
        return None
    return math.degrees(math.acos(llm / base))


def _rounding_match(llm: float, eng: float, tol: float) -> int | None:
    for n in (0, 1, 2, 3):
        if abs(llm - round(eng, n)) <= 10 ** -(n + 3) and abs(llm - eng) > tol:
            return n
    return None


def _plausible(key: str, llm: float, eng: float, v: dict[str, float]) -> bool:
    if not math.isfinite(llm) or (eng > 0 > llm) or (eng < 0 < llm):
        return False
    if key == "d_b":
        return 0 < llm < v["d"]
    if key == "p_bt":
        return 0 < llm < v["pt"]
    return abs(llm - eng) <= 0.5 * max(abs(eng), 1e-9)


def _invariants(calc_type: str, llm: dict[str, float], v: dict[str, float]) -> list[dict[str, Any]]:
    if calc_type != "cylindrical_gear_geometry":
        return []
    z = v.get("z", 0.0)
    checks = [
        ("p_bt·z = π·d_b", ("p_bt", "d_b"), lambda g: (g["p_bt"] * z, math.pi * g["d_b"])),
        ("d_b/d = p_bt/p_t (aynı cos α_t)", ("d_b", "d", "p_bt", "p_t"), lambda g: (g["d_b"] / g["d"], g["p_bt"] / g["p_t"])),
        ("p_t = π·d/z", ("p_t", "d"), lambda g: (g["p_t"], math.pi * g["d"] / z)),
        ("h = h_a + h_f", ("h", "h_a", "h_f"), lambda g: (g["h"], g["h_a"] + g["h_f"])),
        ("d_a − d_f = 2h (k = 0)", ("d_a", "d_f", "h"), lambda g: (g["d_a"] - g["d_f"], 2 * g["h"])),
    ]
    out = []
    for name, keys, fn in checks:
        if not all(k in llm for k in keys) or (name.startswith("d_a") and abs(v.get("k", 0)) > 1e-12):
            continue
        try:
            lhs, rhs = fn(llm)
        except ZeroDivisionError:
            continue
        rel = abs(lhs - rhs) / max(abs(rhs), 1e-12)
        out.append({"name": name, "lhs": lhs, "rhs": rhs, "rel_error": rel, "holds": rel <= 2e-3})
    return out


def diagnose(calc_type: str, canonical_inputs: dict[str, float], outputs: list[dict[str, Any]],
             comparison: dict[str, Any]) -> dict[str, Any]:
    """Classify every mismatching field of a comparison.

    ``outputs`` are the engine outputs (``OutputValue`` dicts); ``comparison`` is
    the persisted comparator record of :func:`app.calc.compare.compare`.
    """
    registry = get_registry()
    inp = resolved_inputs(calc_type, canonical_inputs) if calc_type in RULES else dict(canonical_inputs)
    if calc_type == "cylindrical_gear_geometry":
        v = _cyl_vars(inp)
        hyps: Callable[[str], list[Hyp]] = _hyps_cylindrical
    elif calc_type == "gear_pair":
        v = dict(inp)
        hyps = _hyps_pair
    elif calc_type == "iso1328_flank_tolerance":
        v = dict(inp)
        hyps = _hyps_iso1328
    else:
        v = dict(inp)
        hyps = lambda _k: []  # noqa: E731 - table lookups: only the generic hypotheses apply
    by_key = {o["key"]: o for o in outputs}
    for o in outputs:
        if o.get("unrounded") is not None:
            v[f"{o['key']}__unrounded"] = o["unrounded"]
        if o["key"] == "alpha_wt":
            v["alpha_wt_rad"] = math.radians(o["value"])
    llm_values = {i["key"]: i["llm"] for i in comparison.get("items", [])
                  if isinstance(i.get("llm"), (int, float)) and math.isfinite(i["llm"])}
    rule_by_key = {b.output_key: r for r, b in registry.rules_for_engine(calc_type)}
    fields: list[dict[str, Any]] = []
    for item in comparison.get("items", []):
        if item.get("status") not in ("mismatch", "non_numeric"):
            continue
        key = item["key"]
        out = by_key.get(key, {})
        unit = out.get("unit", "")
        eng = float(item["engine"])
        tol = float(item.get("tolerance") or tolerance(unit, eng, out.get("abs_tol")))
        field_rec: dict[str, Any] = {"key": key, "unit": unit, "engine": eng, "llm": item.get("llm"), "tolerance": tol,
                                     "formula_id": rule_by_key[key].id if key in rule_by_key else None,
                                     "tested": [], "not_applicable": [], "matched": None}
        if item["status"] == "non_numeric":
            field_rec.update(cls="UNKNOWN", reason="sayısal olmayan taslak değeri")
            fields.append(field_rec)
            continue
        llm = float(item["llm"])
        field_rec.update(abs_error=abs(llm - eng), rel_error=abs(llm - eng) / max(abs(eng), 1e-12))
        matched = None
        for cls, label, fn in hyps(key):
            try:
                pred = fn(v)
            except (ZeroDivisionError, ValueError, KeyError, OverflowError):
                pred = None
            if pred is None or not math.isfinite(pred):
                field_rec["not_applicable"].append({"class": cls, "hypothesis": label})
                continue
            if _within(pred, eng, tol):  # coincides with the right answer: cannot explain a mismatch
                field_rec["not_applicable"].append({"class": cls, "hypothesis": label, "reason": "doğru sonuçla çakışıyor"})
                continue
            ok = _within(llm, pred, tol)
            field_rec["tested"].append({"class": cls, "hypothesis": label, "predicted": pred, "matches": ok})
            if ok and matched is None:
                matched = {"class": cls, "hypothesis": label, "predicted": pred}
        if matched is None:
            for cls, label, fn in _generic(unit):
                pred = fn(eng)
                ok = _within(llm, pred, max(tol, 1e-9 * abs(pred)))
                field_rec["tested"].append({"class": cls, "hypothesis": label, "predicted": pred, "matches": ok})
                if ok and matched is None:
                    matched = {"class": cls, "hypothesis": label, "predicted": pred}
        if matched is None and (n := _rounding_match(llm, eng, tol)) is not None:
            matched = {"class": "ROUNDING_ERROR", "hypothesis": f"{n} ondalığa erken yuvarlama", "predicted": round(eng, n)}
        implied = _implied_angle(key, llm, v) if calc_type == "cylindrical_gear_geometry" else None
        if implied is not None:
            field_rec["implied_angle_deg"] = implied
            if matched is None:
                alt = next((a for a in STANDARD_PRESSURE_ANGLES
                            if abs(implied - a) <= 0.05 and abs(a - math.degrees(v["at"])) > 0.05), None)
                if alt is not None:
                    matched = {"class": "WRONG_PRESSURE_ANGLE", "hypothesis": f"α = {alt:g}° kullanıldı", "predicted": None}
        if matched is None and key == "p_bt" and "d_b" in llm_values and "d_b" in {i["key"] for i in comparison["items"]
                                                                                    if i.get("status") == "mismatch"}:
            pred = math.pi * llm_values["d_b"] / v.get("z", 1.0)
            if _within(llm, pred, tol):
                matched = {"class": "PROPAGATED", "hypothesis": "hatalı d_b taslağından türetildi: π·d_b/z", "predicted": pred}
        if matched is not None:
            field_rec.update(matched=matched, cls=matched["class"])
        elif _plausible(key, llm, eng, v):
            field_rec.update(cls="LLM_ARITHMETIC_ERROR",
                             reason="doğru formül ailesinin fiziksel aralığında; yapısal hipotezlerin hiçbiri değeri üretmiyor")
        else:
            field_rec.update(cls="UNKNOWN", reason="hiçbir hipotez açıklamıyor ve değer fiziksel aralık dışında")
        fields.append(field_rec)

    invariants = _invariants(calc_type, llm_values, v)
    counts = Counter(f["cls"] for f in fields if f["cls"] != "PROPAGATED")
    structural = [c for c in counts if c not in _FALLBACK]
    if structural:
        primary = max(structural, key=lambda c: (counts[c], -ROOT_CAUSES.index(c)))
    elif counts:
        primary = "LLM_ARITHMETIC_ERROR" if "LLM_ARITHMETIC_ERROR" in counts else "UNKNOWN"
    else:
        primary = "UNKNOWN"
    for f in fields:  # propagated fields inherit the class of their source
        if f["cls"] == "PROPAGATED":
            f["cls"] = primary
            f["propagated"] = True
    formula_ids = sorted({f["formula_id"] for f in fields if f.get("formula_id")})
    rules = [registry.get(fid) for fid in formula_ids]
    ruled_out = sorted({t["class"] for f in fields for t in f["tested"] if not t["matches"]} - {primary})
    not_applicable = sorted({n["class"] for f in fields for n in f["not_applicable"]} - {primary})
    return {
        "method": "deterministic_hypothesis_testing/1",
        "primary_class": primary,
        "fields": fields,
        "invariants": invariants,
        "draft_internally_consistent": all(i["holds"] for i in invariants) if invariants else None,
        "formula_ids": formula_ids,
        "families": sorted({r.family for r in rules if r}),
        "authoritative_sources": sorted({e for r in rules if r for e in r.evidence_ids}),
        "ruled_out_classes": ruled_out,
        "not_applicable_classes": not_applicable,
        "resolved_inputs": {k: inp[k] for k in sorted(inp) if isinstance(inp[k], (int, float))},
        "note": "Sınıf deterministik hipotez testinden gelir; LLM önerisi yetkili doğrulama değildir.",
    }
