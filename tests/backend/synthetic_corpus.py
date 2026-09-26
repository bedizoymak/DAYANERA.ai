"""Synthetic ISO-like PDFs for tests.

The pages contain only the short evidence phrases required by the engine's
evidence catalog (app/calc/evidence.py) plus neutral filler text, and a
synthetic ISO 286-1 style table with made-up (monotonic) values. No licensed
ISO content is reproduced beyond those identifier phrases.
"""
from __future__ import annotations

from pathlib import Path

from app.calc.evidence import REQ
from conftest import make_pdf

CODES = {
    "iso21771": ("ISO 21771:2007", "Gears - Cylindrical involute gears and gear pairs (synthetic test copy)"),
    "iso53": ("ISO 53:1998", "Cylindrical gears - Standard basic rack tooth profile (synthetic test copy)"),
    "iso1328": ("ISO 1328-1:2013", "Cylindrical gears - flank tolerance classification (synthetic test copy)"),
    "iso286": ("ISO 286-1:2010", "GPS - ISO code system for tolerances (synthetic test copy)"),
}

# synthetic Table-1 layout: header lines, units, then one token per line
SYN_ROWS = [(None, 3), (3, 6), (6, 10), (10, 18), (18, 30), (30, 50)]


def synthetic_it_values(row_idx: int) -> list[str]:
    """20 strictly increasing values: IT01..IT11 in µm, IT12..IT18 in mm."""
    base = 1.0 + row_idx * 0.25
    um = [round(base * (1.5 ** i), 1) for i in range(13)]
    mm = [round(um[-1] * (1.6 ** (i + 1)) / 1000.0, 3) for i in range(7)]
    fmt = lambda v: (f"{v:g}").replace(".", ",")  # noqa: E731
    return [fmt(v) for v in um] + [fmt(v) for v in mm]


def iso286_table_page() -> str:
    lines = ["Table 1 — Values of standard tolerance grades for nominal sizes up to 3 150 mm",
             "Nominal size", "mm", "Standard tolerance values", "Above", "Up to", "and", "including", "µm", "mm"]
    for i, (lo, hi) in enumerate(SYN_ROWS):
        lines.append("—" if lo is None else str(lo))
        lines.append(str(hi))
        lines.extend(synthetic_it_values(i))
    return "\n".join(lines)


def _pages_for(prefix: str) -> list[str]:
    code, title = CODES[prefix]
    pages = [f"INTERNATIONAL STANDARD\n{code}\n{title}\nReference number {code}(E)\nThis synthetic document is used only by automated tests."]
    for rid, req in REQ.items():
        if not rid.startswith(prefix):
            continue
        if rid == "iso286.table1":
            pages.append(f"{code}\n" + iso286_table_page())
            continue
        body = "\n".join(req.must_contain)
        pages.append(f"{code}\nSection text for {rid}.\n{body}\nFurther explanatory text for automated tests.")
    return pages


def write_synthetic(folder: Path, prefix: str, filename: str | None = None) -> Path:
    return make_pdf(folder / (filename or f"{prefix}-synthetic.pdf"), _pages_for(prefix))


def write_all(folder: Path) -> list[Path]:
    return [write_synthetic(folder, p) for p in CODES]
