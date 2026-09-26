"""Evidence requirements: every rule/constant must be traceable to an active
source passage of the verified ISO corpus.

Each requirement names the standard (regular expression over the detected
standard code) and phrases that must appear on one page of an active,
indexed, verified (or user-confirmed) version of that standard. The phrases
were taken from the loaded corpus text layer (see tests).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from app.calc.types import EvidenceMatch, EvidenceRequirement

_SPACE_RE = re.compile(r"[\s  -​  　﻿]+")


def norm_text(s: str) -> str:
    s = s.replace("‐", "-").replace("‑", "-").replace("­", "-")
    return _SPACE_RE.sub(" ", s).strip().casefold()


class EvidenceResolver(Protocol):
    def resolve(self, req: EvidenceRequirement) -> EvidenceMatch | None: ...


# --- ISO 21771:2007 - cylindrical involute gears and gear pairs ------------
ISO21771 = r"(^|\s)ISO 21771(:|\s|$)"
ISO53 = r"(^|\s)ISO 53(:|\s|$)"
ISO1328_1 = r"ISO 1328-1(:|\s|$)"
ISO286_1 = r"ISO 286-1(:|\s|$)"

R = EvidenceRequirement

REQ: dict[str, EvidenceRequirement] = {
    r.id: r
    for r in [
        R("iso21771.eq1", ISO21771, ("The reference diameter, d, is determined by",),
          "ISO 21771:2007 4.2.4, Eşitlik (1): referans çapı d = z·m_n / cos β", 18),
        R("iso21771.eq2", ISO21771, ("For a helical gear, the transverse module, mt, is found as",),
          "ISO 21771:2007 4.2.7, Eşitlik (2): alın modülü m_t = m_n / cos β", 19),
        R("iso21771.eq13", ISO21771,
          ("is the acute angle between the tangent to the involutes at their point of intersection with the reference circle",),
          "ISO 21771:2007 4.3.5, Eşitlik (13): alın kavrama açısı cos α_t = d_b / d", 24),
        R("iso21771.eq14", ISO21771,
          ("Normal pressure angle at a point, normal pressure angle", "this is equal to the pressure angle"),
          "ISO 21771:2007 4.3.6, Eşitlik (14): tan α_n = tan α_t · cos β", 24),
        R("iso21771.eq19", ISO21771, ("Base cylinder, base circle, base diameter", "is given by"),
          "ISO 21771:2007 4.3.10, Eşitlik (19): temel daire çapı d_b = d · cos α_t", 25),
        R("iso21771.eq23", ISO21771,
          ("is the length of the reference circle arc between two successive equal-handed tooth flanks",),
          "ISO 21771:2007 4.4.2.1, Eşitlik (23): alın adımı p_t = π · m_t", 26),
        R("iso21771.eq24", ISO21771, ("is the length of the helix arc between two successive equal-handed",),
          "ISO 21771:2007 4.4.2.2, Eşitlik (24): normal adım p_n = π · m_n", 26),
        R("iso21771.eq28", ISO21771,
          ("on the developed base cylinder tangential plane is the base pitch", "Transverse base pitch"),
          "ISO 21771:2007 4.4.5, Eşitlik (28): alın temel adımı p_bt = p_t · cos α_t", 27),
        R("iso21771.eq33", ISO21771, ("The nominal dimension of the tip diameter",),
          "ISO 21771:2007 4.5.3, Eşitlik (33): diş başı çapı d_a = d + 2(x·m_n + h_aP + k·m_n)", 28),
        R("iso21771.eq34", ISO21771, ("The nominal dimension of the root diameter",),
          "ISO 21771:2007 4.5.4, Eşitlik (34): diş dibi çapı d_f = d − 2(h_fP − x·m_n)", 28),
        R("iso21771.eq35", ISO21771,
          ("of cylindrical gear (or rack) teeth is the difference between tip and root radius",),
          "ISO 21771:2007 4.6.1, Eşitlik (35): diş yüksekliği h = h_aP + k·m_n + h_fP", 28),
        R("iso21771.eq36_37", ISO21771,
          ("are stated on the basis of the reference circle", "Equations (36) and (37)"),
          "ISO 21771:2007 4.6.2, Eşitlik (36)-(37): h_a = h_aP + x·m_n + k·m_n, h_f = h_fP − x·m_n", 29),
        R("iso21771.eq52", ISO21771, ("of a gear pair is the ratio of the number of teeth of the wheel",),
          "ISO 21771:2007 5.2.1, Eşitlik (52): dişli oranı u = z2 / z1", 32),
        R("iso21771.eq54", ISO21771,
          ("is that pressure angle whose vertex lies on the pitch circle", "is calculated from"),
          "ISO 21771:2007 5.2.4, Eşitlik (54): çalışma alın kavrama açısı α_wt", 33),
        # --- ISO 53:1998 - standard basic rack tooth profile ------------------
        R("iso53.table2", ISO53,
          ("Table 2 — Standard basic rack proportions", "aP 20° haP 1 m cP 0,25 m hfP 1,25 m rfP 0,38 m"),
          "ISO 53:1998 Tablo 2: α_P = 20°, h_aP = 1·m, c_P = 0,25·m, h_fP = 1,25·m, ρ_fP = 0,38·m", 5),
        # --- ISO 1328-1:2013 - flank tolerance classification ----------------
        R("iso1328.scope", ISO1328_1,
          ("These tolerances are applicable to", "5 ≤ z ≤ 1 000", "5 mm ≤ d ≤ 15 000 mm",
           "0,5 mm ≤ mn ≤ 70 mm", "4 mm ≤ b ≤ 1 200 mm", "β ≤ 45°"),
          "ISO 1328-1:2013 Madde 1: uygulama aralıkları", 9),
        R("iso1328.no_extrapolation", ISO1328_1, ("shall not be extrapolated beyond these limits",),
          "ISO 1328-1:2013 5.2.1: formüller aralık dışına ekstrapole edilmez", 34),
        R("iso1328.step", ISO1328_1, ("The step factor between two consecutive classes is",),
          "ISO 1328-1:2013 5.2.2: sınıflar arası adım faktörü √2, çarpan (√2)^(A−5)", 34),
        R("iso1328.rounding", ISO1328_1,
          ("if greater than 10 µm, round to the nearest integer micrometre",
           "round to the nearest 0,5 µm", "round to the nearest 0,1 µm"),
          "ISO 1328-1:2013 5.2.3: yuvarlama kuralları", 34),
        R("iso1328.f5", ISO1328_1, ("Single pitch tolerance, fpT, shall be calculated using Formula (5)",),
          "ISO 1328-1:2013 5.3.1, Formül (5): f_pT", 34),
        R("iso1328.f6", ISO1328_1,
          ("Total cumulative pitch (index) tolerance, FpT, shall be calculated using Formula (6)",),
          "ISO 1328-1:2013 5.3.2, Formül (6): F_pT", 34),
        R("iso1328.f7", ISO1328_1, ("Profile slope tolerance, fHαT, shall be calculated using Formula (7)",),
          "ISO 1328-1:2013 5.3.3.1, Formül (7): f_HαT", 34),
        R("iso1328.f8", ISO1328_1, ("Profile form tolerance, ffαT, shall be calculated using Formula (8)",),
          "ISO 1328-1:2013 5.3.3.2, Formül (8): f_fαT", 35),
        R("iso1328.f9", ISO1328_1, ("Total profile tolerance, FαT, shall be calculated as given by Formula (9)",),
          "ISO 1328-1:2013 5.3.3.3, Formül (9): F_αT", 35),
        R("iso1328.f10", ISO1328_1, ("Helix slope tolerance, fHβT, shall be calculated using Formula (10)",),
          "ISO 1328-1:2013 5.3.4.1, Formül (10): f_HβT", 35),
        R("iso1328.f11", ISO1328_1, ("Helix form tolerance, ffβT, shall be calculated using Formula (11)",),
          "ISO 1328-1:2013 5.3.4.2, Formül (11): f_fβT", 35),
        R("iso1328.f12", ISO1328_1, ("Total helix tolerance, FβT, shall be calculated as given by Formula (12)",),
          "ISO 1328-1:2013 5.3.4.3, Formül (12): F_βT", 35),
        # --- ISO 286-1:2010 - standard tolerance grades (Table 1) ------------
        R("iso286.table1", ISO286_1,
          ("Table 1 — Values of standard tolerance grades for nominal sizes up to 3 150 mm",
           "Standard tolerance values"),
          "ISO 286-1:2010 Tablo 1: standart tolerans değerleri (IT01–IT18, 3 150 mm'ye kadar)", 26),
        # --- ISO 286-1:2010 basic hole "H" / basic shaft "h" (Step 2: H7-type lookups) ----------
        R("iso286.basic_hole", ISO286_1,
          ("a basic hole is a hole for which the lower limit deviation is zero",),
          "ISO 286-1:2010 3.1.4 Not 2: temel delik (basic hole) alt sınır sapması sıfırdır", 8),
        R("iso286.basic_shaft", ISO286_1,
          ("a basic shaft is a shaft for which the upper limit deviation is zero",),
          "ISO 286-1:2010 3.1.6 Not 2: temel mil (basic shaft) üst sınır sapması sıfırdır", 8),
        R("iso286.hole_H", ISO286_1,
          ("the fundamental deviation can be chosen in the column H", "the lower limit deviation EI = 0",
           "ES = EI + IT"),
          "ISO 286-1:2010 Ek B örneği: H sütunu → EI = 0, ES = EI + IT", 40),
        R("iso286.shaft_h", ISO286_1, ("basic shaft “h”",),
          "ISO 286-1:2010 Şekil 6: temel mil “h”", 17),
        R("iso286.shaft_ei", ISO286_1, ("es = 0", "ei = es - IT"),
          "ISO 286-1:2010 Şekil 9: es = 0, ei = es − IT", 25),
    ]
}


@dataclass
class PageRecord:
    """A page of an active verified source (used by in-memory resolvers)."""

    document_id: str
    version_id: str
    version_number: int
    document_title: str
    standard_code: str
    page_number: int
    locator: str
    text: str
    confidence_status: str = "verified_source"


def find_in_pages(req: EvidenceRequirement, pages: list[PageRecord]) -> EvidenceMatch | None:
    code_re = re.compile(req.standard_code_like, re.IGNORECASE)
    phrases = [norm_text(p) for p in req.must_contain]
    candidates = [p for p in pages if p.standard_code and code_re.search(p.standard_code)]
    candidates.sort(key=lambda p: (p.page_number != req.page_hint, p.page_number))
    for page in candidates:
        nt = norm_text(page.text)
        if all(ph in nt for ph in phrases):
            idx = nt.find(phrases[0])
            excerpt_norm = _SPACE_RE.sub(" ", page.text).strip()
            start = max(0, idx - 80)
            excerpt = excerpt_norm[start: idx + len(phrases[0]) + 160]
            return EvidenceMatch(
                requirement_id=req.id,
                description=req.description,
                document_id=page.document_id,
                version_id=page.version_id,
                version_number=page.version_number,
                document_title=page.document_title,
                standard_code=page.standard_code,
                page_number=page.page_number,
                locator=page.locator,
                excerpt=excerpt,
                confidence_status=page.confidence_status,
                page_text=page.text,
            )
    return None


class InMemoryEvidenceResolver:
    """Resolver over an explicit page list (tests, offline tooling)."""

    def __init__(self, pages: list[PageRecord]):
        self.pages = pages

    def resolve(self, req: EvidenceRequirement) -> EvidenceMatch | None:
        return find_in_pages(req, self.pages)
