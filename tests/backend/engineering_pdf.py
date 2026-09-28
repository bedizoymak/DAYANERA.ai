"""Synthetic engineering standard for the chunking tests (invented text, fictional code).

It reproduces the layout situations that broke the previous chunker, with real PDF
geometry (not just text): running headers/footers on every page, a clause that
continues across a page break, a displayed equation with typographic subscripts
(smaller, lowered glyph runs), Symbol-font Greek, a drawn fraction bar, a "where"
legend, a symbol table, a bold-header table continued on the next page with
"(continued)", a figure key, NOTE and EXAMPLE paragraphs and a long clause that
must be split at sentence boundaries. Nothing here is taken from a real standard.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

CODE = "ISO 97771:2026"
BSI_HEADER = "BS ISO 97771:2026"
LONG_SENTENCE = ("The synthetic verification procedure records the measured flank deviation of every inspected tooth "
                 "at the reference cylinder and compares it with the permitted synthetic limit value. ")


class Doc:
    def __init__(self) -> None:
        self.doc = pymupdf.open()
        self.page = None
        self.y = 0.0
        self.n = 0

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=595, height=842)
        self.n += 1
        self.page.insert_text((48, 40), BSI_HEADER, fontname="helv", fontsize=8)  # BSI running header
        self.page.insert_text((470, 58), f"{CODE}(E)", fontname="hebo", fontsize=9)  # ISO running header
        self.page.insert_text((48, 812), "© ISO 2026 - All rights reserved\x08", fontname="helv", fontsize=8)
        self.page.insert_text((540, 812), str(self.n), fontname="helv", fontsize=8)  # page number
        self.y = 100.0

    def text(self, s: str, *, bold: bool = False, size: float = 10, gap: float = 14, x: float = 48) -> None:
        self.page.insert_text((x, self.y), s, fontname="hebo" if bold else "helv", fontsize=size)
        self.y += gap

    def clause(self, num: str, title: str) -> None:
        self.page.insert_text((48, self.y), num, fontname="hebo", fontsize=10)
        self.page.insert_text((48 + 16 + 6 * len(num), self.y), title, fontname="hebo", fontsize=10)
        self.y += 20

    def sym(self, x: float, base: str, sub: str, *, greek: bool = False, size: float = 10) -> float:
        """A symbol with a real typographic subscript: base glyph + smaller glyphs 2.5 pt lower."""
        font = "symb" if greek else "tiit"
        self.page.insert_text((x, self.y), base, fontname=font, fontsize=size)
        x += pymupdf.get_text_length(base, fontname=font, fontsize=size)
        if sub:
            self.page.insert_text((x, self.y + 2.5), sub, fontname="helv", fontsize=size * 0.75)
            x += pymupdf.get_text_length(sub, fontname="helv", fontsize=size * 0.75)
        return x

    def put(self, x: float, s: str, font: str = "helv", size: float = 10, dy: float = 0.0) -> float:
        self.page.insert_text((x, self.y + dy), s, fontname=font, fontsize=size)
        return x + pymupdf.get_text_length(s, fontname=font, fontsize=size)

    def eq_number(self, n: str) -> None:
        self.page.insert_text((520, self.y), f"({n})", fontname="helv", fontsize=10)

    def table(self, caption: str, header: list[str], rows: list[list[str]], widths: list[float]) -> None:
        self.text(caption, bold=True, x=150, gap=18)
        top, x0, row_h = self.y - 6, 60.0, 18.0
        xs = [x0]
        for w in widths:
            xs.append(xs[-1] + w)
        all_rows = [header] + rows
        bottom = top + row_h * len(all_rows)
        for i in range(len(all_rows) + 1):
            self.page.draw_line((xs[0], top + i * row_h), (xs[-1], top + i * row_h), width=0.6)
        for x in xs:
            self.page.draw_line((x, top), (x, bottom), width=0.6)
        for r, row in enumerate(all_rows):
            for c, cell in enumerate(row):
                self.page.insert_text((xs[c] + 4, top + r * row_h + 13), cell, fontname="hebo" if r == 0 else "helv",
                                      fontsize=9)
        self.y = bottom + 22

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(str(path))
        return path


def write_engineering_standard(path: Path) -> Path:
    d = Doc()
    # ---------------------------------------------------------------- page 1
    d.new_page()
    d.clause("1", "Scope")
    d.text("This synthetic standard defines the geometry quantities used by the chunking tests of DAYANERA.")
    d.clause("3", "Symbols and units")
    d.table("Table 1 - Symbols and units", ["Symbol", "Description", "Unit"],
            [["db", "base diameter", "mm"], ["d", "reference diameter", "mm"], ["z", "number of teeth", "-"],
             ["mn", "normal module", "mm"]], [60, 280, 60])
    d.clause("4", "Individual gears")
    d.clause("4.3", "Involute helicoids")
    d.clause("4.3.1", "Base cylinder, base circle, base diameter")
    d.text("The base cylinder is the synthetic cylinder coaxial with the gear axis that generates the involutes.")
    d.text("Quantities associated with the base cylinder are denoted by the subscript b. The base diameter,")
    # "db, is given by" continues the sentence on the same row (inline symbol with subscript)
    d.y -= 14
    x = d.put(468, "")
    x = d.sym(x + 2, "d", "b")
    d.put(x, ", is given by")
    d.y += 34
    # equation (19): db = d cos αt   (subscripts lowered and smaller, α from the Symbol font)
    x = d.sym(90, "d", "b")
    x = d.put(x + 4, "=", "symb")
    x = d.sym(x + 4, "d", "")
    x = d.put(x + 4, "cos")
    x = d.sym(x + 3, "a", "t", greek=True)
    d.eq_number("19")
    d.y += 30
    # equation (20): db = z mn cos αt / cos β   (a drawn fraction bar)
    x = d.sym(90, "d", "b")
    x = d.put(x + 4, "=", "symb")
    num_x = x + 6
    d.y -= 7
    x2 = d.sym(num_x, "z", "")
    x2 = d.sym(x2 + 3, "m", "n")
    x2 = d.put(x2 + 3, "cos")
    x2 = d.sym(x2 + 3, "a", "t", greek=True)
    d.y += 7
    d.page.draw_line((num_x - 2, d.y - 3.5), (x2 + 2, d.y - 3.5), width=0.5)
    d.y += 7
    xd = d.put(num_x + 12, "cos")
    d.sym(xd + 3, "b", "", greek=True)
    d.y -= 7
    d.eq_number("20")
    d.y += 30
    d.text("where", gap=13)
    for sym_, desc in (("db", "is the base diameter;"), ("d", "is the reference diameter;"),
                       ("αt", "is the transverse pressure angle.")):
        if sym_ == "αt":
            d.sym(60, "a", "t", greek=True)
        else:
            d.sym(60, sym_[0], sym_[1:])
        d.put(110, desc)
        d.y += 13
    d.y += 10
    d.clause("4.4", "Flank inspection")
    d.text("NOTE   The synthetic flank inspection uses the base diameter defined above.", gap=18)
    # a sentence that runs over the page break (no closing punctuation on page 1)
    d.y = 780
    d.text("During the synthetic inspection the measured profile deviation of the tooth flank is")
    # ---------------------------------------------------------------- page 2
    d.new_page()
    d.text("evaluated along the path of contact between the start and the end of the active profile.")
    d.text("Key", gap=13)
    d.text("1   datum line", gap=13)
    d.text("2   tip line", gap=13)
    d.text("Figure 2 - Synthetic basic rack", bold=True, gap=24)
    d.text("EXAMPLE   For z = 30 and mn = 2 mm the synthetic reference diameter is 60 mm.", gap=20)
    d.clause("5", "Tolerances")
    d.text("Deviations in micrometres", x=150, gap=14)
    rows = [[f"{m}", f"{10 + m}", "mm"] for m in range(1, 13)]
    d.table("Table 3 - Synthetic flank tolerance values", ["Module", "Tolerance", "Unit"], rows, [80, 120, 60])
    # ---------------------------------------------------------------- page 3: the table continues
    d.new_page()
    rows2 = [[f"{m}", f"{10 + m}", "mm"] for m in range(13, 25)]
    d.table("Table 3 (continued)", ["Module", "Tolerance", "Unit"], rows2, [80, 120, 60])
    d.clause("6", "Verification procedure")
    for _ in range(3):
        for _ in range(4):
            d.text(LONG_SENTENCE[:98], gap=13)
            d.text(LONG_SENTENCE[98:], gap=13)
        d.y += 4
    return d.save(path)
