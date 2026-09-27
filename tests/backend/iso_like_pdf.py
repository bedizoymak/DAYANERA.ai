"""Synthetic ISO-structured PDFs for the canonical ingestion tests.

The layout mimics ISO/IEC Directives Part 2 documents (bold numbered clauses, a
clause number and title set on one baseline, ruled tables with "Table n —"
captions, right-margin equation numbers, figure keys, a table of contents with
dot leaders) and the text-layer defect of older ISO PDFs: Greek letters set in
the base-14 Symbol font are stored as Latin codes ("a" for α). All wording is
invented; the standard codes are fictional so that nothing collides with the
other synthetic corpora of the test session.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

CODE_RACK = "ISO 90053:2026"
CODE_GEOM = "ISO 92177:2026"


class _Writer:
    def __init__(self, doc: pymupdf.Document):
        self.doc = doc
        self.page = None
        self.y = 0.0

    def new_page(self, header: str) -> None:
        self.page = self.doc.new_page(width=595, height=842)
        self.page.insert_text((48, 45), header, fontname="hebo", fontsize=9)
        self.y = 90.0

    def text(self, s: str, *, bold: bool = False, x: float = 48, size: float = 10, gap: float = 15) -> None:
        self.page.insert_text((x, self.y), s, fontname="hebo" if bold else "helv", fontsize=size)
        self.y += gap

    def runs(self, parts: list[tuple[str, str]], *, x: float = 48, size: float = 10, gap: float = 15) -> None:
        """One line made of (font, text) runs; 'symb' runs carry Symbol-encoded Greek."""
        for font, s in parts:
            self.page.insert_text((x, self.y), s, fontname=font, fontsize=size)
            x += pymupdf.get_text_length(s, fontname=font, fontsize=size)
        self.y += gap

    def clause(self, number: str, title: str) -> None:
        """Clause number and title on one baseline (separate text runs, as in ISO PDFs)."""
        self.page.insert_text((48, self.y), number, fontname="hebo", fontsize=10)
        self.page.insert_text((48 + 14 + 6 * len(number), self.y), title, fontname="hebo", fontsize=10)
        self.y += 20

    def table(self, caption: str, rows: list[list[list[tuple[str, str]] | str]], widths: list[float]) -> None:
        self.text(caption, bold=True, x=150)
        top, x0, row_h = self.y - 6, 60.0, 18.0
        xs = [x0]
        for w in widths:
            xs.append(xs[-1] + w)
        bottom = top + row_h * len(rows)
        for i in range(len(rows) + 1):
            self.page.draw_line((xs[0], top + i * row_h), (xs[-1], top + i * row_h), width=0.6)
        for x in xs:
            self.page.draw_line((x, top), (x, bottom), width=0.6)
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                parts = [("helv", cell)] if isinstance(cell, str) else cell
                cx = xs[c] + 4
                for font, s in parts:
                    self.page.insert_text((cx, top + r * row_h + 13), s, fontname=font, fontsize=9)
                    cx += pymupdf.get_text_length(s, fontname=font, fontsize=9)
        self.y = bottom + 22

    def equation(self, parts: list[tuple[str, str]], number: str) -> None:
        self.runs(parts, x=90, gap=0)
        self.page.insert_text((520, self.y), f"({number})", fontname="helv", fontsize=10)
        self.y += 22


def alpha(sub: str) -> list[tuple[str, str]]:
    return [("symb", "a"), ("tiit", sub)]


def rho(sub: str) -> list[tuple[str, str]]:
    return [("symb", "r"), ("tiit", sub)]


def write_rack_standard(path: Path) -> Path:
    """ISO-53-like basic rack standard with Symbol-encoded αP / αFP / ρfP."""
    doc = pymupdf.open()
    w = _Writer(doc)
    w.new_page(f"{CODE_RACK}(E)")
    w.text("INTERNATIONAL STANDARD", bold=True, size=14, gap=24)
    w.text(f"{CODE_RACK}", bold=True, size=12, gap=20)
    w.text("Synthetic cylindrical gears - Standard basic rack tooth profile", bold=True, gap=30)
    w.text("Contents", bold=True, gap=20)
    w.text("1   Scope ............................................................ 1")
    w.text("2   Normative references ....................................... 1")
    w.text("5   Standard basic rack tooth profile ........................ 2", gap=30)
    w.text("Foreword", bold=True, gap=20)
    w.text("This synthetic document exists only for automated ingestion tests of DAYANERA.")
    w.new_page(f"{CODE_RACK}(E)")
    w.clause("1", "Scope")
    w.text("This synthetic standard specifies a basic rack tooth profile for automated tests only.")
    w.clause("2", "Normative references")
    w.text("There are no normative references in this synthetic document.")
    w.clause("3", "Terms and definitions")
    w.text("3.1", bold=True, gap=13)
    w.text("synthetic basic rack", bold=True, gap=13)
    w.text("reference profile used by the ingestion tests of the verified corpus pipeline", gap=22)
    w.clause("4", "Symbols and units")
    w.table("Table 1 - Symbols and units",
            [["Symbol", "Description", "Unit"],
             [alpha("P"), "Pressure angle", "degrees"],
             [alpha("FP"), "Angle of undercut", "degrees"],
             [rho("fP"), "Fillet radius of the basic rack", "mm"],
             ["haP", "Addendum of standard basic rack tooth", "mm"]],
            [60, 300, 70])
    w.new_page(f"{CODE_RACK}(E)")
    w.clause("5", "Standard basic rack tooth profile")
    w.runs([("helv", "5.1   The flanks of the synthetic basic rack are inclined at the pressure angle, ")]
           + alpha("P") + [("helv", ".")])
    w.text("5.2   The synthetic basic rack has a pitch equal to pi times the module.")
    w.text("5.3   The flanks are straight between the tip line and the root line.")
    w.table("Table 2 - Standard basic rack proportions",
            [["Item", "Standard basic rack value"], [alpha("P"), "20°"], ["haP", "1 m"], ["cP", "0,25 m"],
             ["hfP", "1,25 m"], [rho("fP"), "0,38 m"]],
            [80, 180])
    w.text("5.4   On the line P-P the tooth thickness is equal to the space width, i.e. half the pitch.")
    w.equation([("tiit", "sP = eP = p/2 = "), ("symb", "p"), ("tiit", "m/2")], "1")
    w.text("Key", gap=13)
    w.text("1  Datum line", gap=13)
    w.text("2  Tip line", gap=13)
    w.text("Figure 1 - Synthetic basic rack tooth profile", bold=True, gap=22)
    w.text("Annex A", bold=True, x=260, gap=13)
    w.text("(informative)", x=262, gap=13)
    w.text("Synthetic basic rack recommendations", bold=True, x=180, gap=20)
    w.clause("A.1", "Profiles with undercut")
    w.runs([("helv", "A basic rack with undercut uses the angle of undercut, ")] + alpha("FP") + [("helv", ".")])
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def write_geometry_standard(path: Path) -> Path:
    """ISO-21771-like geometry standard: clause numbers + titles, numbered equations, gear symbols."""
    doc = pymupdf.open()
    w = _Writer(doc)
    w.new_page(f"{CODE_GEOM}(E)")
    w.text("INTERNATIONAL STANDARD", bold=True, size=14, gap=24)
    w.text(f"{CODE_GEOM}", bold=True, size=12, gap=20)
    w.text("Synthetic gears - Cylindrical involute gears and gear pairs - Concepts and geometry", bold=True, gap=30)
    w.clause("1", "Scope")
    w.text("This synthetic standard defines geometric quantities used by the automated ingestion tests.")
    w.clause("4", "Individual cylindrical gears")
    w.clause("4.2", "Reference surfaces")
    w.clause("4.2.4", "Reference cylinder, reference circle, reference diameter")
    w.text("The reference diameter, d, of a synthetic gear with number of teeth z is determined by")
    w.equation([("tiit", "d = z mn / cos "), ("symb", "b"), ("tiit", " = z mt")], "1")
    w.clause("4.2.7", "Module")
    w.text("The module, m, of the basic rack; for a spur gear m = mt = mn.")
    w.text("For a helical gear, the transverse module, mt, is found as")
    w.equation([("tiit", "mt = mn / cos "), ("symb", "b")], "2")
    w.new_page(f"{CODE_GEOM}(E)")
    w.clause("4.5", "Diameters of gear teeth")
    w.clause("4.5.3", "Tip cylinder, tip circle, tip diameter")
    w.text("The nominal dimension of the tip diameter, da, is")
    w.equation([("tiit", "da = d + 2 (x mn + haP + k mn)")], "33")
    w.clause("4.5.4", "Root cylinder, root circle, root diameter")
    w.text("The nominal dimension of the root diameter, df, is")
    w.equation([("tiit", "df = d - 2 (hfP - x mn)")], "34")
    w.clause("5", "Inspection classes")
    w.text("Class FD applies to heavy tempering; Class FE applies to severe tempering in this synthetic text.")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path
