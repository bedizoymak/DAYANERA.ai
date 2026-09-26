"""Minimal, explicit unit handling for the calculation engine."""
from __future__ import annotations

import math

LENGTH_UNITS = {
    "mm": 1.0,
    "milimetre": 1.0,
    "millimetre": 1.0,
    "millimeter": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "metre": 1000.0,
    "µm": 0.001,
    "μm": 0.001,
    "um": 0.001,
    "mikron": 0.001,
    "mikrometre": 0.001,
}
ANGLE_UNITS = {
    "deg": 1.0,
    "°": 1.0,
    "derece": 1.0,
    "degree": 1.0,
    "degrees": 1.0,
    "rad": 180.0 / math.pi,
    "radyan": 180.0 / math.pi,
}
DIMENSIONLESS_UNITS = {"", "1", "-", "adet", "none"}

CANONICAL = {"length": "mm", "angle": "deg", "dimensionless": "1", "integer": "1", "grade": "1"}


class InvalidUnitError(ValueError):
    pass


def normalize_unit_token(unit: str | None) -> str:
    return (unit or "").strip().lower().replace("derecesi", "derece")


def to_canonical(kind: str, value: float, unit: str | None) -> float:
    """Convert ``value`` given in ``unit`` to the canonical unit of ``kind``."""
    u = normalize_unit_token(unit)
    if kind == "length":
        if u not in LENGTH_UNITS:
            raise InvalidUnitError(f"'{unit}' bir uzunluk birimi değil (izin verilen: mm, cm, m, µm).")
        return value * LENGTH_UNITS[u]
    if kind == "angle":
        if u not in ANGLE_UNITS:
            raise InvalidUnitError(f"'{unit}' bir açı birimi değil (izin verilen: ° / deg / rad).")
        return value * ANGLE_UNITS[u]
    if kind in ("dimensionless", "integer", "grade"):
        if u not in DIMENSIONLESS_UNITS:
            raise InvalidUnitError(f"Bu büyüklük birimsizdir; '{unit}' birimi geçersiz.")
        return value
    raise InvalidUnitError(f"Bilinmeyen büyüklük türü: {kind}")


def fmt_num(value: float, decimals: int = 4) -> str:
    """Turkish-style decimal comma formatting."""
    s = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s.replace(".", ",")
