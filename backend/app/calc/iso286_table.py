"""Parser for ISO 286-1:2010 Table 1 (standard tolerance values).

Values are NOT hard-coded: they are parsed at runtime from the text layer of
the active, verified source page. The parse is structurally validated
(row boundaries chain, 18 or 20 values per row, tolerances strictly
increasing across grades and non-decreasing down the size ranges). Any
inconsistency makes the lookup refuse instead of guessing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

GRADES_20 = ["IT01", "IT0"] + [f"IT{i}" for i in range(1, 19)]
GRADES_18 = [f"IT{i}" for i in range(1, 19)]
MM_GRADES = {f"IT{i}" for i in range(12, 19)}  # IT12..IT18 are given in mm

_UNICODE_SPACES = re.compile(r"[  -​  　﻿\t]")
_THOUSANDS = re.compile(r"^\d{1,3} \d{3}$")
_NUM = re.compile(r"^\d+(,\d+)?$")


class TableParseError(ValueError):
    pass


@dataclass
class ToleranceRow:
    above: float  # exclusive lower bound (0 for the first row "—")
    up_to: float  # inclusive upper bound
    values_um: dict[str, float]


def _tokens(page_text: str) -> list[str]:
    text = _UNICODE_SPACES.sub(" ", page_text)
    start = text.find("Standard tolerance values")
    if start < 0:
        raise TableParseError("Tablo başlığı bulunamadı")
    lines = [ln.strip() for ln in text[start:].splitlines()]
    # skip header lines until the unit line 'mm' that follows 'µm'
    try:
        um_idx = next(i for i, ln in enumerate(lines) if ln in ("µm", "μm"))
    except StopIteration as exc:
        raise TableParseError("Birim başlığı (µm) bulunamadı") from exc
    if um_idx + 1 >= len(lines) or lines[um_idx + 1] != "mm":
        raise TableParseError("Birim başlığı (mm) bulunamadı")
    toks: list[str] = []
    for ln in lines[um_idx + 2:]:
        if not ln:
            continue
        if _THOUSANDS.match(ln):
            toks.append(ln.replace(" ", ""))
            continue
        for part in ln.split(" "):
            if part:
                toks.append(part)
    return toks


def _num(tok: str) -> float:
    return float(tok.replace(",", "."))


def parse_table1(page_text: str) -> list[ToleranceRow]:
    toks = _tokens(page_text)
    # keep only the leading numeric / dash stream (the table body)
    body: list[str] = []
    for t in toks:
        if t == "—" or _NUM.match(t):
            body.append(t)
        else:
            break
    rows: list[ToleranceRow] = []
    i = 0
    expected_lower: float | None = None
    while i < len(body):
        lower_tok = body[i]
        lower = 0.0 if lower_tok == "—" else _num(lower_tok)
        if expected_lower is not None and abs(lower - expected_lower) > 1e-9:
            raise TableParseError(f"Satır sınırı zinciri bozuk ({lower} != {expected_lower})")
        if i + 1 >= len(body):
            raise TableParseError("Eksik üst sınır")
        upper = _num(body[i + 1])
        if upper <= lower:
            raise TableParseError("Üst sınır alt sınırdan büyük olmalı")
        vstart = i + 2
        chosen = None
        for count in (20, 18):
            end = vstart + count
            if end == len(body):
                chosen = count
                break
            if end + 1 < len(body) and body[end] != "—" and _NUM.match(body[end]):
                if abs(_num(body[end]) - upper) < 1e-9 and _num(body[end + 1]) > upper:
                    chosen = count
                    break
        if chosen is None:
            raise TableParseError(f"{lower}-{upper} mm satırı çözümlenemedi")
        grades = GRADES_20 if chosen == 20 else GRADES_18
        vals = body[vstart: vstart + chosen]
        values_um = {}
        for g, tok in zip(grades, vals):
            v = _num(tok)
            values_um[g] = v * 1000.0 if g in MM_GRADES else v
        seq = [values_um[g] for g in grades]
        if any(b <= a for a, b in zip(seq, seq[1:])):
            raise TableParseError(f"{lower}-{upper} mm satırında değerler kalite derecesine göre artmıyor")
        rows.append(ToleranceRow(above=lower, up_to=upper, values_um=values_um))
        expected_lower = upper
        i = vstart + chosen
    if not rows:
        raise TableParseError("Tablo satırı bulunamadı")
    for prev, cur in zip(rows, rows[1:]):
        for g, v in cur.values_um.items():
            if g in prev.values_um and v < prev.values_um[g]:
                raise TableParseError(f"{g} sütunu boyut aralıklarına göre azalıyor")
    return rows


def normalize_grade(grade: str) -> str:
    g = grade.strip().upper().replace(" ", "")
    if not g.startswith("IT"):
        g = "IT" + g
    if g not in GRADES_20:
        raise ValueError(f"Geçersiz standart tolerans derecesi: {grade} (IT01, IT0, IT1 … IT18)")
    return g


def lookup(rows: list[ToleranceRow], nominal_mm: float, grade: str) -> tuple[ToleranceRow, float] | None:
    g = normalize_grade(grade)
    for row in rows:
        if row.above < nominal_mm <= row.up_to:
            if g not in row.values_um:
                return None
            return row, row.values_um[g]
    return None
