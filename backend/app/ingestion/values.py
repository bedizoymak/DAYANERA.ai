"""Candidate engineering values from OCR / transcription text.

Every value found here is stored as ``draft_extraction`` ("Taslak çıkarım")
and cannot enter calculations, verified memory or active technical facts
until an authorized user confirms it in the review queue.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

NUM = r"-?\d+(?:[.,]\d+)?"

_LABELLED = re.compile(
    r"(?P<label>\b(?:m_?n|m|z[12]?|d_?[abfw]|d|b|x|k|α_?n?|alpha|β|beta|a_?w|h_?a|h_?f|h|Ra|Rz|A)\b)\s*[=:]\s*"
    r"(?P<num>" + NUM + r")\s*(?P<unit>mm|µm|μm|um|°|deg)?",
    re.UNICODE,
)
_NAMED = re.compile(
    r"(?P<label>modül|module|diş sayısı|number of teeth|helis açısı|helix angle|basınç açısı|pressure angle|"
    r"diş genişliği|face ?width|eksen mesafesi|centre distance|center distance)\s*[=:]?\s*"
    r"(?P<num>" + NUM + r")\s*(?P<unit>mm|µm|μm|um|°|derece)?",
    re.IGNORECASE | re.UNICODE,
)
_DIAMETER = re.compile(r"(?P<label>[Øø⌀])\s*(?P<num>\d+(?:[.,]\d+)?)(?:\s*(?P<fit>[A-Za-z]{1,2}\d{1,2}))?")
_PLUSMINUS = re.compile(r"(?P<label>±)\s*(?P<num>\d+(?:[.,]\d+)?)\s*(?P<unit>mm|µm|μm|um)?")
_UNIT_ONLY = re.compile(r"(?P<num>(?<![\w.,])\d+(?:[.,]\d+)?)\s*(?P<unit>mm|µm|μm|um|°)(?!\w)")

MAX_PER_PAGE = 60


@dataclass
class Candidate:
    label: str
    raw_text: str
    value: float | None
    unit: str | None
    quantity_kind: str | None
    context: str


def _kind(unit: str | None, label: str) -> str | None:
    u = (unit or "").lower()
    if u in ("mm", "µm", "μm", "um") or label in ("Ø", "ø", "⌀"):
        return "length"
    if u in ("°", "deg", "derece") or label.lower() in ("α", "alpha", "β", "beta", "helis açısı", "helix angle",
                                                          "basınç açısı", "pressure angle"):
        return "angle"
    if label.lower().startswith("z") or "diş sayısı" in label.lower() or "teeth" in label.lower():
        return "count"
    return None


def extract_candidates(text: str) -> list[Candidate]:
    found: list[Candidate] = []
    seen: set[tuple] = set()
    spans: list[tuple[int, int]] = []

    def add(m: re.Match, label: str, unit: str | None, extra: str | None = None):
        if len(found) >= MAX_PER_PAGE:
            return
        num = m.group("num")
        try:
            value = float(num.replace(",", "."))
        except ValueError:
            value = None
        unit = unit or ("mm" if label in ("Ø", "ø", "⌀") else None)
        key = (label.lower(), num, unit)
        if key in seen:
            return
        for s, e in spans:
            if m.start() >= s and m.end() <= e:
                return
        seen.add(key)
        spans.append((m.start(), m.end()))
        ctx = text[max(0, m.start() - 50): m.end() + 50].replace("\n", " ")
        raw = m.group(0) + (f" ({extra})" if extra else "")
        found.append(Candidate(label=label, raw_text=raw.strip(), value=value, unit=unit,
                               quantity_kind=_kind(unit, label), context=ctx.strip()))

    for m in _LABELLED.finditer(text):
        add(m, m.group("label"), m.group("unit"))
    for m in _NAMED.finditer(text):
        add(m, m.group("label"), m.group("unit"))
    for m in _DIAMETER.finditer(text):
        add(m, "Ø", "mm", m.group("fit"))
    for m in _PLUSMINUS.finditer(text):
        add(m, "±", m.group("unit"))
    for m in _UNIT_ONLY.finditer(text):
        words = re.findall(r"[^\W\d_][\w]*", text[max(0, m.start() - 40): m.start()])
        label = words[-1] if words else "değer"
        add(m, label[:40], m.group("unit"))
    return found
