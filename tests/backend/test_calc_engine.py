"""Unit tests: deterministic calculation engine (no LLM, no database)."""
from __future__ import annotations

import math

import pytest

from app.calc.compare import compare
from app.calc.engine import CalculationEngine
from app.calc.evidence import REQ, InMemoryEvidenceResolver, PageRecord
from app.calc.iso286_table import TableParseError, lookup, parse_table1
from app.calc.parse import parse_calculation
from app.calc.rules import iso1328_round
from app.calc.types import CalcRequest, InputValue, Provenance
from app.domain.enums import REFUSAL_PHRASE
from synthetic_corpus import CODES, SYN_ROWS, iso286_table_page, synthetic_it_values


def pages_for(prefixes=("iso21771", "iso53", "iso1328", "iso286")) -> list[PageRecord]:
    pages = []
    for prefix in prefixes:
        code = CODES[prefix][0]
        n = 1
        for rid, req in REQ.items():
            if not rid.startswith(prefix):
                continue
            n += 1
            text = iso286_table_page() if rid == "iso286.table1" else "\n".join(req.must_contain)
            pages.append(PageRecord(document_id=prefix, version_id=f"{prefix}-v1", version_number=1,
                                    document_title=code, standard_code=code, page_number=n, locator=f"s. {n}", text=text))
    return pages


def ui(v, unit=""):
    return InputValue("x", v, unit, Provenance("user_input", "msg-1", "user_input"))


def run(calc_type, pages=None, **inputs):
    eng = CalculationEngine(InMemoryEvidenceResolver(pages if pages is not None else pages_for()))
    req = CalcRequest(calc_type, {k: (v if isinstance(v, InputValue) else ui(*v)) for k, v in inputs.items()})
    for k, v in req.inputs.items():
        v.key = k
    return eng.run(req)


# ---------------------------------------------------------------- valid ----
def test_spur_gear_geometry_values():
    r = run("cylindrical_gear_geometry", z=(20, ""), m_n=(2, "mm"))
    assert r.status == "ok", r.diagnostics
    out = r.output_map()
    assert out["d"] == pytest.approx(40.0)
    assert out["d_b"] == pytest.approx(40 * math.cos(math.radians(20)))
    assert out["d_a"] == pytest.approx(44.0)
    assert out["d_f"] == pytest.approx(35.0)
    assert out["h"] == pytest.approx(4.5)
    assert out["p_n"] == pytest.approx(math.pi * 2)
    # ISO 53 constants are traced to their source passage
    assert {c["key"] for c in r.constants} == {"alpha_n", "h_aP_star", "h_fP_star"}
    assert all(c["source"]["standard_code"] == "ISO 53:1998" for c in r.constants)
    assert any("β = 0°" in a for a in r.assumptions)
    ev_ids = {e["requirement_id"] for e in r.evidence}
    assert {"iso21771.eq1", "iso21771.eq33", "iso53.table2"} <= ev_ids
    assert r.trace and all(o.formula_id.startswith("ISO21771") for o in r.outputs)


def test_helical_gear_with_profile_shift_and_units_conversion():
    r = run("cylindrical_gear_geometry", z=(30, ""), m_n=(0.3, "cm"), beta=(15, "°"), x=(0.2, ""))
    assert r.status == "ok"
    mt = 3 / math.cos(math.radians(15))
    at = math.atan(math.tan(math.radians(20)) / math.cos(math.radians(15)))
    out = r.output_map()
    assert out["m_t"] == pytest.approx(mt)
    assert out["alpha_t"] == pytest.approx(math.degrees(at))
    assert out["d"] == pytest.approx(30 * mt)
    assert out["h_a"] == pytest.approx(3.6)
    assert out["h_f"] == pytest.approx(3.15)
    assert out["d_a"] == pytest.approx(30 * mt + 7.2)


def test_gear_pair_ratio_and_working_pressure_angle():
    r = run("gear_pair", z1=(45, ""), z2=(20, ""), m_n=(2, "mm"), a_w=(66, "mm"))
    assert r.status == "ok"
    out = r.output_map()
    assert out["u"] == pytest.approx(2.25)
    expected = math.degrees(math.acos(2 * 65 / (2 * 66) * math.cos(math.radians(20))))
    assert out["alpha_wt"] == pytest.approx(expected)
    assert any(d.code == "pinion_swap" for d in r.diagnostics)


def test_iso1328_flank_tolerances_class_6():
    r = run("iso1328_flank_tolerance", m_n=(3, "mm"), z=(40, ""), b=(30, "mm"), A=(6, ""))
    assert r.status == "ok", r.diagnostics
    f = math.sqrt(2)
    d = 120.0
    fpT = (0.001 * d + 0.4 * 3 + 5) * f
    FpT = (0.002 * d + 0.55 * math.sqrt(d) + 0.7 * 3 + 12) * f
    out = {o.key: o for o in r.outputs}
    assert out["f_pT"].unrounded == pytest.approx(fpT)
    assert out["f_pT"].value == 9.0  # 8.94 -> nearest 0.5 (5..10 µm band)
    assert out["F_pT"].unrounded == pytest.approx(FpT)
    assert out["F_pT"].value == 29.0  # 28.80 -> nearest integer (>10 µm)
    assert out["F_aT"].value == 12.0
    assert out["F_bT"].unit == "µm"


def test_iso1328_rounding_rules():
    assert iso1328_round(12.49) == 12.0
    assert iso1328_round(10.4) == 10.0
    assert iso1328_round(7.3) == 7.5
    assert iso1328_round(7.2) == 7.0
    assert iso1328_round(4.96) == 5.0
    assert iso1328_round(4.94) == 4.9


def test_iso286_it_lookup_from_source_table():
    r = run("iso286_it_tolerance", nominal_size=(40, "mm"), grade=(6, ""))
    assert r.status == "ok", r.diagnostics
    expected = float(synthetic_it_values(5)[7].replace(",", "."))  # row (30,50], IT6 = 8th value
    assert r.output_map()["IT"] == pytest.approx(expected)
    assert r.evidence[0]["requirement_id"] == "iso286.table1"


def test_iso286_table_parser_validates_structure():
    rows = parse_table1(iso286_table_page())
    assert [(r.above, r.up_to) for r in rows] == [(0.0 if lo is None else lo, hi) for lo, hi in SYN_ROWS]
    assert lookup(rows, 3, "IT7")[0].up_to == 3  # 'up to and including'
    assert lookup(rows, 3.01, "IT7")[0].up_to == 6
    assert lookup(rows, 51, "IT7") is None
    broken = iso286_table_page().replace("\n6\n", "\n7\n", 1)
    with pytest.raises(TableParseError):
        parse_table1(broken)


# ------------------------------------------------------------- refusals ----
def test_missing_source_is_refused_with_exact_phrase():
    pages = [p for p in pages_for() if p.standard_code != "ISO 21771:2007"]
    r = run("cylindrical_gear_geometry", pages=pages, z=(20, ""), m_n=(2, "mm"))
    assert r.status == "refused"
    assert r.message == REFUSAL_PHRASE
    assert r.outputs == []
    assert any(d.code == "missing_source" and "ISO 21771" in d.message for d in r.diagnostics)


def test_missing_iso53_only_matters_when_defaults_are_needed():
    pages = [p for p in pages_for() if p.standard_code != "ISO 53:1998"]
    refused = run("cylindrical_gear_geometry", pages=pages, z=(20, ""), m_n=(2, "mm"))
    assert refused.status == "refused"
    explicit = run("cylindrical_gear_geometry", pages=pages, z=(20, ""), m_n=(2, "mm"), alpha_n=(20, "°"),
                   h_aP_star=(1, ""), h_fP_star=(1.25, ""))
    assert explicit.status == "ok"


def test_unsupported_formula_is_refused():
    r = run("tooth_root_stress_iso6336", m_n=(2, "mm"))
    assert r.status == "refused"
    assert r.message == REFUSAL_PHRASE
    assert r.diagnostics[0].code == "unsupported_formula"


def test_out_of_scope_range_refused_not_extrapolated():
    r = run("iso1328_flank_tolerance", m_n=(0.2, "mm"), d=(40, "mm"), A=(6, ""))
    assert r.status == "refused"
    assert r.message == REFUSAL_PHRASE
    assert any("ekstrapole" in d.message for d in r.diagnostics)


def test_invalid_units_rejected():
    r = run("cylindrical_gear_geometry", z=(20, ""), m_n=(2, "kg"))
    assert r.status == "invalid_input"
    assert any(d.code == "invalid_unit" for d in r.diagnostics)
    r2 = run("cylindrical_gear_geometry", z=(20, "mm"), m_n=(2, "mm"))
    assert r2.status == "invalid_input"
    r3 = run("cylindrical_gear_geometry", z=(20, ""), m_n=(2, "mm"), beta=(15, "mm"))
    assert any(d.code == "invalid_unit" for d in r3.diagnostics)


def test_missing_required_and_non_integer_inputs():
    r = run("cylindrical_gear_geometry", m_n=(2, "mm"))
    assert r.status == "invalid_input" and any(d.code == "missing_input" for d in r.diagnostics)
    r2 = run("cylindrical_gear_geometry", z=(20.5, ""), m_n=(2, "mm"))
    assert any(d.code == "not_integer" for d in r2.diagnostics)


def test_draft_ocr_input_is_blocked_until_confirmed():
    draft = InputValue("m_n", 2.0, "mm", Provenance("extracted_value", "ev-1", "draft_extraction"))
    r = run("cylindrical_gear_geometry", z=(20, ""), m_n=draft)
    assert r.status == "invalid_input"
    assert any(d.code == "draft_input_blocked" for d in r.diagnostics)
    assert r.outputs == []
    confirmed = InputValue("m_n", 2.0, "mm", Provenance("extracted_value", "ev-1", "user_confirmed"))
    assert run("cylindrical_gear_geometry", z=(20, ""), m_n=confirmed).status == "ok"
    unverified_memory = InputValue("m_n", 2.0, "mm", Provenance("memory_item", "mi-1", "unverified"))
    assert run("cylindrical_gear_geometry", z=(20, ""), m_n=unverified_memory).status == "invalid_input"


# ------------------------------------------------------ Qwen comparison ----
def test_llm_draft_comparison_detects_mismatch_and_match():
    r = run("cylindrical_gear_geometry", z=(20, ""), m_n=(2, "mm"))
    ok = compare(r, {"d": 40.0, "d_a": 44.0, "d_f": "35,0"})
    assert ok["performed"] and not ok["mismatch"]
    bad = compare(r, {"d": 40.0, "d_a": 46.0})
    assert bad["mismatch"]
    assert [i for i in bad["items"] if i["status"] == "mismatch"][0]["key"] == "d_a"
    nonnum = compare(r, {"d": "kırk"})
    assert nonnum["mismatch"]
    refused = run("tooth_root_stress", m_n=(2, "mm"))
    assert compare(refused, {"x": 1})["performed"] is False


# ------------------------------------------------------ natural language ----
@pytest.mark.parametrize("text,calc_type,expect", [
    ("z=20, m=2 mm dişli geometrisini hesapla", "cylindrical_gear_geometry", {"z": 20, "m_n": 2}),
    ("z = 30 modül 3 β=15° x=0,2 hesapla", "cylindrical_gear_geometry", {"z": 30, "m_n": 3, "beta": 15, "x": 0.2}),
    ("z1=20 z2=45 m=2 a_w=66 mm hesapla", "gear_pair", {"z1": 20, "z2": 45, "a_w": 66}),
    ("m=3 mm, z=40, b=30 mm, tolerans sınıfı 6 için ISO 1328 toleranslarını hesapla", "iso1328_flank_tolerance",
     {"A": 6, "b": 30}),
    ("40 mm anma ölçüsü için IT6 kaç µm?", "iso286_it_tolerance", {"nominal_size": 40, "grade": 6}),
])
def test_parse_calculation_requests(text, calc_type, expect):
    p = parse_calculation(text)
    assert p.calc_type == calc_type
    for k, v in expect.items():
        assert p.inputs[k][0] == pytest.approx(v)


def test_parse_keeps_foreign_units_for_rejection():
    p = parse_calculation("m = 2 kg z=20 hesapla")
    assert p.inputs["m_n"] == (2.0, "kg")


def test_iso286_basic_hole_H_and_shaft_h_limits():
    """Step 2: 50 mm H7 / 40 mm h6 from the source-parsed Table 1 plus the basic hole/shaft rules."""
    it7_30_50 = float(synthetic_it_values(5)[8].replace(",", "."))  # row (30,50], IT7 = 9th value
    it6_30_50 = float(synthetic_it_values(5)[7].replace(",", "."))
    h = run("iso286_hole_H", nominal_size=(50, "mm"), grade=(7, ""))
    assert h.status == "ok", h.diagnostics
    out = h.output_map()
    assert out["EI"] == 0 and out["ES"] == pytest.approx(it7_30_50)
    assert out["upper_size"] == pytest.approx(50 + it7_30_50 / 1000)
    assert {e["requirement_id"] for e in h.evidence} == {"iso286.table1", "iso286.basic_hole", "iso286.hole_H"}
    s = run("iso286_shaft_h", nominal_size=(40, "mm"), grade=(6, ""))
    assert s.status == "ok"
    assert s.output_map()["es"] == 0 and s.output_map()["ei"] == pytest.approx(-it6_30_50)
    # a Qwen draft 30 µm off on a limit size is a mismatch (a 0.5 % relative tolerance would hide it)
    good = {k: v for k, v in out.items()}
    assert compare(h, good)["mismatch"] is False
    cmp = compare(h, {**good, "upper_size": out["upper_size"] - 0.03})
    assert cmp["mismatch"] is True
    assert [i["key"] for i in cmp["items"] if i["status"] == "mismatch"] == ["upper_size"]
    # without the basic-hole passage the engine refuses instead of assuming EI = 0
    pages = [p for p in pages_for() if "lower limit deviation is zero" not in p.text]
    r = run("iso286_hole_H", pages=pages, nominal_size=(50, "mm"), grade=(7, ""))
    assert r.status == "refused" and r.message == REFUSAL_PHRASE
