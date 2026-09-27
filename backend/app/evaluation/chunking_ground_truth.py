"""Ground truth for the chunking benchmark (old chunker vs engineering chunker).

Only identifiers are recorded: standard codes, physical PDF page numbers, clause
and equation numbers, table labels and short regular expressions that identify the
evidence unit. No passages of the (copyrighted) standards are stored here.
Every page/regex was checked against the PDF text layer (``verify_ground_truth``
in ``chunking_benchmark.py`` re-checks them on every run and reports misses).

Query format (same as ``pilot_ground_truth.QUERIES``):
  relevant  [(standard, page, regex)]  a passage is relevant when its page range covers
            ``page`` of that standard and its text matches ``regex``; [] = must abstain
  answer    regexes the context handed to the model must contain
  kind      formula | table | definition | text   (what the answer unit is)
  formula   (standard, equation number) of the expected equation (formula questions)
"""
from __future__ import annotations

from app.evaluation.pilot_ground_truth import QUERIES as PILOT_QUERIES

G = r"(?:α|a)"

ENGINEERING_QUERIES = [
    # --- the two examples of the task statement ---------------------------------------------------
    {"id": "en-base-circle-diameter", "q": "What is the base circle diameter formula?", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 21771", "19"),
     "relevant": [("ISO 21771", 25, r"\(19\)")], "answer": [r"\(19\)", r"(?i)base diameter"]},
    {"id": "en-53-dedendum", "q": "ISO 53 basic rack dedendum coefficient", "kind": "table",
     "subtype": "standards_value_lookup",
     "relevant": [("ISO 53", 5, r"h\s?fP\W{0,6}1,25 m"), ("ISO 53", 7, r"h\s?fP\W{0,12}1,25 m")],
     "answer": [r"1,25 m"]},
    # --- ISO 21771 formulas in English -------------------------------------------------------------
    {"id": "en-21771-tip", "q": "How is the tip diameter calculated according to ISO 21771?", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 21771", "33"),
     "relevant": [("ISO 21771", 28, r"\(33\)")], "answer": [r"\(33\)", r"(?i)tip diameter"]},
    {"id": "en-21771-root", "q": "ISO 21771 root diameter df formula", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 21771", "34"),
     "relevant": [("ISO 21771", 28, r"\(34\)")], "answer": [r"\(34\)", r"(?i)root diameter"]},
    {"id": "en-21771-mt", "q": "ISO 21771 transverse module formula", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 21771", "2"),
     "relevant": [("ISO 21771", 19, r"\(2\)")], "answer": [r"\(2\)", r"(?i)transverse module"]},
    {"id": "en-21771-d", "q": "reference diameter formula ISO 21771", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 21771", "1"),
     "relevant": [("ISO 21771", 18, r"\(1\)")], "answer": [r"\(1\)", r"(?i)reference diameter"]},
    {"id": "en-21771-alphat", "q": "How is the transverse pressure angle at the reference circle expressed in ISO 21771?",
     "kind": "formula", "subtype": "standards_formula_lookup", "formula": ("ISO 21771", "13"),
     "relevant": [("ISO 21771", 24, r"\(13\)")], "answer": [r"\(13\)"]},
    {"id": "en-21771-pn", "q": "normal pitch pn formula in ISO 21771", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 21771", "24"),
     "relevant": [("ISO 21771", 26, r"\(24\)")], "answer": [r"\(24\)"]},
    {"id": "en-21771-h", "q": "tooth depth h formula ISO 21771", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 21771", "35"),
     "relevant": [("ISO 21771", 28, r"\(35\)")], "answer": [r"\(35\)"]},
    # --- ISO 1328-1 tolerance formulas -------------------------------------------------------------
    {"id": "en-1328-fpt", "q": "ISO 1328-1 single pitch tolerance fpT formula", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 1328-1", "5"),
     "relevant": [("ISO 1328-1", 34, r"\(5\)")], "answer": [r"\(5\)", r"f\s?pT"]},
    {"id": "en-1328-FpT", "q": "ISO 1328-1 total cumulative pitch tolerance FpT formula", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 1328-1", "6"),
     "relevant": [("ISO 1328-1", 34, r"\(6\)")], "answer": [r"\(6\)", r"F\s?pT"]},
    {"id": "en-1328-FaT", "q": "ISO 1328-1 total profile tolerance formula", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 1328-1", "9"),
     "relevant": [("ISO 1328-1", 35, r"\(9\)")], "answer": [r"\(9\)"]},
    {"id": "en-1328-dM", "q": "ISO 1328-1 measurement diameter dM for external gears", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 1328-1", "1"),
     "relevant": [("ISO 1328-1", 29, r"\(1\)")], "answer": [r"\(1\)"]},
    {"id": "tr-1328-fpt", "q": "ISO 1328-1'e göre tekil adım toleransı fpT formülü nedir?", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 1328-1", "5"),
     "relevant": [("ISO 1328-1", 34, r"\(5\)")], "answer": [r"\(5\)"]},
    # --- ISO 4468 test formulas ---------------------------------------------------------------------
    {"id": "en-4468-test7", "q": "ISO 4468 formula used for test 7", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 4468", "2"),
     "relevant": [("ISO 4468", 10, r"\(2\)")], "answer": [r"\(2\)", r"(?i)test 7"]},
    # --- tables / definitions -----------------------------------------------------------------------
    {"id": "en-286-2-H", "q": "ISO 286-2 limit deviations for holes with fundamental deviation H", "kind": "table",
     "subtype": "standards_value_lookup",
     "relevant": [("ISO 286-2", 18, r"Table 6")], "answer": [r"Table 6"]},
    {"id": "en-1101-flatness", "q": "ISO 1101 flatness specification tolerance zone", "kind": "definition",
     "subtype": None, "relevant": [("ISO 1101", 74, r"(?i)flatness")], "answer": [r"(?i)flatness"]},
    {"id": "en-14104-FD", "q": "ISO 14104 Class FD heavy tempering", "kind": "text", "subtype": None,
     "relevant": [("ISO 14104", 17, r"(?i)\bFD\b"), ("ISO 14104", 18, r"(?i)\bFD\b"),
                  ("ISO 14104", 19, r"(?i)\bFD\b"), ("ISO 14104", 14, r"FD2")],
     "answer": [r"FD"]},
    {"id": "en-53-symbols", "q": "ISO 53 symbol for the fillet radius of the basic rack", "kind": "definition",
     "subtype": "standards_value_lookup",
     "relevant": [("ISO 53", 4, r"(?i)fillet radius of the basic rack")], "answer": [r"(?i)fillet radius"]},
    # --- negatives ------------------------------------------------------------------------------------
    {"id": "neg-21771-lubricant", "q": "ISO 21771 lubricant viscosity requirement for gearboxes", "kind": "text",
     "subtype": None, "relevant": [], "answer": []},
    {"id": "neg-1328-price", "q": "ISO 1328-1 price of a gear inspection machine", "kind": "text", "subtype": None,
     "relevant": [], "answer": []},
]

# Held-out set: written AFTER the retrieval changes were tuned on the queries above and never used
# for tuning. Reported separately (set = "heldout") so the benchmark shows how much of the gain generalises.
HELDOUT_QUERIES = [
    {"id": "ho-21771-alphayt", "q": "ISO 21771 transverse pressure angle at a point Y formula", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 21771", "12"),
     "relevant": [("ISO 21771", 24, r"\(12\)")], "answer": [r"\(12\)"]},
    {"id": "ho-21771-roll", "q": "roll angle of the involute formula", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 21771", "16"),
     "relevant": [("ISO 21771", 25, r"\(16\)")], "answer": [r"\(16\)"]},
    {"id": "ho-21771-inv", "q": "ISO 21771 involute function inv α", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 21771", "18"),
     "relevant": [("ISO 21771", 25, r"\(18\)")], "answer": [r"\(18\)"]},
    {"id": "ho-21771-pt", "q": "Alın adımı pt nasıl hesaplanır ISO 21771?", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 21771", "23"),
     "relevant": [("ISO 21771", 26, r"\(23\)")], "answer": [r"\(23\)"]},
    {"id": "ho-1328-lambda", "q": "ISO 1328-1 profile form filter cutoff formula", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 1328-1", "3"),
     "relevant": [("ISO 1328-1", 29, r"\(3\)")], "answer": [r"\(3\)"]},
    {"id": "ho-1328-fHa", "q": "profile slope tolerance fHαT formula ISO 1328-1", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 1328-1", "7"),
     "relevant": [("ISO 1328-1", 34, r"\(7\)")], "answer": [r"\(7\)"]},
    {"id": "ho-1328-fHb", "q": "ISO 1328-1 helix slope tolerance formula", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 1328-1", "10"),
     "relevant": [("ISO 1328-1", 35, r"\(10\)")], "answer": [r"\(10\)"]},
    {"id": "ho-4468-test8a", "q": "ISO 4468 tolerance formula for test 8A", "kind": "formula",
     "subtype": "standards_formula_lookup", "formula": ("ISO 4468", "5"),
     "relevant": [("ISO 4468", 10, r"\(5\)")], "answer": [r"\(5\)"]},
    {"id": "ho-286-2-shaft-h", "q": "ISO 286-2 limit deviations for shafts, fundamental deviation h", "kind": "table",
     "subtype": "standards_value_lookup", "relevant": [("ISO 286-2", 37, r"Table 22")], "answer": [r"Table 22"]},
    {"id": "ho-2490-dims", "q": "ISO 2490 nominal dimensions of small-bore single-thread gear hobs", "kind": "table",
     "subtype": "standards_value_lookup", "relevant": [("ISO 2490", 7, r"Table 1")], "answer": [r"Table 1"]},
    {"id": "ho-286-1-IT", "q": "ISO 286-1 values of standard tolerance grades IT table", "kind": "table",
     "subtype": "standards_value_lookup", "relevant": [("ISO 286-1", 26, r"Table 1")], "answer": [r"Table 1"]},
    {"id": "ho-1101-straightness", "q": "ISO 1101 straightness specification", "kind": "definition", "subtype": None,
     "relevant": [("ISO 1101", 71, r"(?i)straightness")], "answer": [r"(?i)straightness"]},
    {"id": "ho-neg-53-hardness", "q": "ISO 53 surface hardness requirement HRC for case hardened gears", "kind": "text",
     "subtype": None, "relevant": [], "answer": []},
    {"id": "ho-neg-286-bolt", "q": "ISO 286-2 tightening torque for M10 bolts", "kind": "text", "subtype": None,
     "relevant": [], "answer": []},
]
for q in HELDOUT_QUERIES:
    q["set"] = "heldout"

# pilot queries are formula/value/definition questions of ISO 53 and ISO 21771 (Turkish)
for q in PILOT_QUERIES:
    q.setdefault("kind", "formula" if q.get("subtype") == "standards_formula_lookup" else "table")

QUERIES = list(PILOT_QUERIES) + ENGINEERING_QUERIES + HELDOUT_QUERIES

# Structural ground truth for chunk-level metrics ---------------------------------------------------
# formulas: (standard, equation number, page, words that introduce it (lead-in), clause)
FORMULAS = [
    ("ISO 53", "1", 5, r"half the pitch", "5.4"),
    ("ISO 53", "2", 6, None, "5.9"),
    ("ISO 21771", "1", 18, r"reference diameter, d, is determined by", "4.2.4"),
    ("ISO 21771", "2", 19, r"transverse module, mt, is found as", "4.2.7"),
    ("ISO 21771", "13", 24, r"It is expressed by", "4.3.5"),
    ("ISO 21771", "19", 25, r"base diameter, db, is given by", "4.3.10"),
    ("ISO 21771", "24", 26, None, "4.4.2.2"),
    ("ISO 21771", "33", 28, r"tip diameter, da, is", "4.5.3"),
    ("ISO 21771", "34", 28, r"root diameter, df, is", "4.5.4"),
    ("ISO 1328-1", "1", 29, None, None),
    ("ISO 1328-1", "5", 34, None, None),
    ("ISO 1328-1", "6", 34, None, None),
    ("ISO 1328-1", "9", 35, None, None),
    ("ISO 4468", "1", 10, r"use Formula \(1\)", "5.2"),
    ("ISO 4468", "2", 10, r"use Formula \(2\)", "5.3"),
]
# canonical forms checked against the rendered pages (grouping-insensitive comparison)
CANONICAL = {
    ("ISO 53", "1"): "sP = eP = p/2 = πm/2",
    ("ISO 53", "2"): "ρfPmax = cP/(1 − sin αP)",
    ("ISO 21771", "1"): "d = |z| mt = |z| mn/cos β",
    ("ISO 21771", "2"): "mt = mn/cos β",
    ("ISO 21771", "13"): "cos αt = db/d",
    ("ISO 21771", "19"): "db = d cos αt = |z| mt cos αt = |z| mn cos αt/cos β = |z| mn/√(tan^2 αn + cos^2 β)",
    ("ISO 21771", "24"): "pn = π mn = pt cos β",
    ("ISO 21771", "33"): "da = d + 2 z/|z| (x mn + haP + k mn)",
    ("ISO 21771", "34"): "df = d − 2 z/|z| (hfP − x mn)",
    ("ISO 1328-1", "1"): "dM = da − 2 mn",
    ("ISO 1328-1", "5"): "fpT = (0,001 d + 0,4 mn + 5) (√2)^(A − 5)",
    ("ISO 1328-1", "6"): "FpT = (0,002 d + 0,55 √d + 0,7 mn + 12) (√2)^(A − 5)",
    ("ISO 1328-1", "9"): "FαT = √(fHαT^2 + ffαT^2)",
}
# table rows: (standard, table label, page, [cells that must appear on one row, in order], header words)
TABLE_ROWS = [
    ("ISO 53", "Table 1", 4, [G + "P", "Pressure angle", "degrees"], ["Symbol", "Description", "Unit"]),
    ("ISO 53", "Table 1", 4, [r"ρfP|rfP", "Fillet radius of the basic rack", "mm"], ["Symbol", "Description", "Unit"]),
    ("ISO 53", "Table 2", 5, [r"h\s?fP", r"1,25 m"], ["Item"]),
    ("ISO 53", "Table 2", 5, [r"ρfP|rfP", r"0,38 m"], ["Item"]),
    ("ISO 53", "Table A.1", 7, [r"h\s?fP", r"1,25 m", r"1,25 m", r"1,25 m", r"1,4 m"], ["Type"]),
    ("ISO 21771", "3.1 Symbols", 6, [r"d\b", "reference diameter", r"4\.2\.4"], ["Symbol", "Description"]),
    ("ISO 21771", "3.1 Symbols", 6, [r"db", "base diameter", r"4\.3\.10"], ["Symbol", "Description"]),
    ("ISO 21771", "3.1 Symbols", 9, [r"αt|at", "transverse pressure angle"], ["Symbol", "Description"]),
]
