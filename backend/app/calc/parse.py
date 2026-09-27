"""Deterministic natural-language parameter extraction for calculations.

Maps Turkish/English user text such as "z=20, m=2 mm, β=15° dişli geometrisini
hesapla" to a structured request. Units are captured when written; missing
units fall back to the ISO default unit of the quantity (mm / degrees) and
are recorded as explicit assumptions. Unknown/foreign units are passed
through so that the engine rejects them (e.g. "m = 2 kg").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

NUM = r"(-?\d+(?:[.,]\d+)?)"
UNIT = r"(?:\s*(mm|cm|µm|μm|um|mikron|metre|kg|g|nm|inch|in|°|derece|deg|rad|radyan)\b|\s*(°))?"
I = re.IGNORECASE | re.UNICODE

_PATTERNS: dict[str, list[re.Pattern]] = {
    "z1": [re.compile(r"\bz\s*(?:1|₁)\s*[=:]\s*(\d+)", I), re.compile(r"pinyon[^0-9]{0,25}?(\d+)\s*diş", I)],
    "z2": [re.compile(r"\bz\s*(?:2|₂)\s*[=:]\s*(\d+)", I), re.compile(r"çark[^0-9]{0,25}?(\d+)\s*diş", I)],
    "z": [
        re.compile(r"\bz\s*[=:]\s*(\d+)", I),
        re.compile(r"diş\s*sayısı\s*(?:[=:]|olan|ise)?\s*(\d+)", I),
        re.compile(r"(\d+)\s*dişli\b", I),
        re.compile(r"(\d+)\s*(?:teeth|diş)\b", I),
        re.compile(r"number\s*of\s*teeth\s*[=:]?\s*(\d+)", I),
    ],
    "m_n": [
        re.compile(r"\bm\s*_?\s*n?\s*[=:]\s*" + NUM + UNIT, I),
        re.compile(r"(?:normal\s*)?mod[üu]l(?:[üu])?\s*(?:[=:]|olan|ise)?\s*" + NUM + UNIT, I),
        re.compile(r"\bmodule\s*(?:[=:]|of)?\s*" + NUM + UNIT, I),
    ],
    "alpha_n": [re.compile(
        r"(?:α\s*_?\s*n?|alfa\s*_?\s*n?|alpha\s*_?\s*n?|basınç\s*açısı|kavrama\s*açısı|pressure\s*angle)\s*(?:[=:]|olan|ise)?\s*"
        + NUM + UNIT, I)],
    "beta": [re.compile(r"(?:β|\bbeta|helis\s*açısı|helix\s*angle)\s*(?:[=:]|olan|ise)?\s*" + NUM + UNIT, I)],
    "x": [re.compile(r"(?:\bx\s*[=:]\s*|profil\s*kaydırma(?:\s*katsayısı)?\s*(?:[=:]|olan|ise)?\s*)" + NUM, I)],
    "k": [re.compile(r"\bk\s*[=:]\s*" + NUM, I)],
    "b": [re.compile(
        r"(?:\bb\s*[=:]\s*|diş\s*genişliği\s*(?:[=:]|olan|ise)?\s*|face\s*width\s*[=:]?\s*|facewidth\s*[=:]?\s*)"
        + NUM + UNIT, I)],
    "d": [re.compile(
        r"(?:\bd\s*[=:]\s*|referans\s*çap(?:ı)?\s*(?:[=:]|olan|ise)?\s*|reference\s*diameter\s*[=:]?\s*)" + NUM + UNIT, I)],
    "a_w": [re.compile(
        r"(?:\ba\s*_?\s*w\s*[=:]\s*|eksen\s*mesafesi\s*(?:[=:]|olan|ise)?\s*|cent(?:re|er)\s*distance\s*[=:]?\s*)"
        + NUM + UNIT, I)],
    "A": [
        re.compile(r"(?:tolerans\s*sınıfı|kalite\s*sınıfı|flank\s*tolerance\s*class|\bsınıf(?:ı)?|\bclass)\s*(?:[=:]|olan|ise)?\s*(\d{1,2})\b", I),
        re.compile(r"\bA\s*=\s*(\d{1,2})\b"),
    ],
    "grade": [re.compile(r"\bIT\s*(01|0|1[0-8]|[1-9])\b", I)],
    "nominal_size": [
        re.compile(r"(?:anma\s*ölçüsü|nominal\s*(?:size|ölçü)|ø|Ø|\bD\s*=)\s*(?:[=:]|olan|ise)?\s*" + NUM + UNIT, I),
        re.compile(NUM + r"\s*(mm)\s*(?:anma|nominal|çap|mil|delik|için)", I),
        re.compile(NUM + r"\s*(mm)\s*(?=[Hh]\d{1,2}\b)"),  # "50 mm H7"
    ],
}

# ISO 286 tolerance class of the basic hole "H" or basic shaft "h" (case-sensitive!)
_TOL_CLASS = re.compile(r"(?<![A-Za-z])([Hh])\s?(\d{1,2})(?!\d)")
_FIT_NOTATION = re.compile(r"\b[A-Za-z]{1,2}\d{1,2}\s*/\s*[A-Za-z]{1,2}\d{1,2}\b")  # H7/g6 fits: not handled here
# deterministic ISO 286 table lookups: a question form ("... nedir?") is enough, no calc verb needed
TABLE_LOOKUPS = {"iso286_it_tolerance", "iso286_hole_H", "iso286_shaft_h"}

_KIND = {
    "z": "integer", "z1": "integer", "z2": "integer", "m_n": "length", "alpha_n": "angle", "beta": "angle",
    "x": "dimensionless", "k": "dimensionless", "b": "length", "d": "length", "a_w": "length",
    "A": "grade", "grade": "grade", "nominal_size": "length",
}
_DEFAULT_UNIT = {"length": "mm", "angle": "°", "integer": "", "dimensionless": "", "grade": ""}

CALC_VERBS = re.compile(
    r"(hesapla|hesabı|hesabını|hesaplar|hesap\s*yap|hesap\s*et|kaç\s*(?:olur|mm|µm|derece)|bulur\s*musun|bul\b|"
    r"calculate|compute|determine)", I)
TOLERANCE_HINT = re.compile(r"(tolerans|toleransı|1328|f_?pT|F_?pT|F_?αT|profil\s*tol|helis\s*tol|tolerance)", I)
# the user asks for the transverse module itself (m_t needs only m_n and β, never z)
TRANSVERSE_MODULE_TARGET = re.compile(r"(\bm\s*_?\s*t\b|transverse\s+module|alın\s+modül)", I)


@dataclass
class ParsedCalc:
    calc_type: str | None
    inputs: dict[str, tuple[float, str]] = field(default_factory=dict)
    assumed_units: list[str] = field(default_factory=list)
    has_calc_verb: bool = False


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def extract_parameters(text: str) -> tuple[dict[str, tuple[float, str]], list[str]]:
    found: dict[str, tuple[float, str]] = {}
    assumed: list[str] = []
    for key, patterns in _PATTERNS.items():
        for pat in patterns:
            m = pat.search(text)
            if not m:
                continue
            groups = [g for g in m.groups()]
            raw = groups[0]
            unit = next((g for g in groups[1:] if g), None)
            if key == "grade":
                value = -1.0 if raw == "01" else float(int(raw))
                found[key] = (value, "")
                break
            value = _num(raw)
            kind = _KIND[key]
            if unit is None:
                unit = _DEFAULT_UNIT[kind]
                if kind in ("length", "angle"):
                    assumed.append(f"{key}: birim yazılmadı, {unit} kabul edildi")
            found[key] = (value, unit)
            break
    # disambiguation: z1/z2 imply a pair; drop plain z captured from "z1"/"z2"
    if "z1" in found or "z2" in found:
        found.pop("z", None)
    return found, assumed


def parse_calculation(text: str) -> ParsedCalc:
    inputs, assumed = extract_parameters(text)
    has_verb = bool(CALC_VERBS.search(text))
    calc_type: str | None = None
    tol_class = _TOL_CLASS.search(text)
    if tol_class and "nominal_size" in inputs and "grade" not in inputs and not _FIT_NOTATION.search(text):
        calc_type = "iso286_hole_H" if tol_class.group(1) == "H" else "iso286_shaft_h"
        inputs["grade"] = (float(int(tol_class.group(2))), "")
    elif "grade" in inputs and "nominal_size" in inputs:
        calc_type = "iso286_it_tolerance"
    elif ("A" in inputs or TOLERANCE_HINT.search(text)) and "m_n" in inputs and ("d" in inputs or "z" in inputs):
        calc_type = "iso1328_flank_tolerance"
    elif "z1" in inputs and "z2" in inputs and "m_n" in inputs:
        calc_type = "gear_pair"
    elif "z" in inputs and "m_n" in inputs:
        calc_type = "cylindrical_gear_geometry"
    elif "m_n" in inputs and "beta" in inputs and TRANSVERSE_MODULE_TARGET.search(text):
        calc_type = "transverse_module"
    if calc_type:
        from app.calc.rules import RULES

        allowed = {s.key for s in RULES[calc_type].inputs}
        inputs = {k: v for k, v in inputs.items() if k in allowed}
        assumed = [a for a in assumed if a.split(":")[0] in allowed]
    return ParsedCalc(calc_type=calc_type, inputs=inputs, assumed_units=assumed, has_calc_verb=has_verb)
