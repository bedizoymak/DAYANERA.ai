from app.calc.parse import parse_calculation
from app.calc.presentation import enrich_outputs, expression_to_latex
from app.calc.types import OutputValue


def test_gear_math_presentation_contains_symbols_substitution_and_units():
    outputs = [
        OutputValue("m_t", "", 2.2068, "mm", "ISO", "m_t = m_n / cos β", "2.2068 mm"),
        OutputValue("alpha_t", "", 21.8802, "°", "ISO", "alpha_t", "21.8802°"),
        OutputValue("d_b", "", 40.0, "mm", "ISO", "d_b = d·cos α_t", "40 mm"),
        OutputValue("p_bt", "", 5.0, "mm", "ISO", "p_bt = p_t·cos α_t", "5 mm"),
        OutputValue("h_a", "", 2.0, "mm", "ISO", "h_a", "2 mm"),
        OutputValue("d_a", "", 44.0, "mm", "ISO", "d_a", "44 mm"),
    ]
    enrich_outputs(outputs, {"z": 20, "m_n": 2, "beta": 25, "alpha_n": 20, "x": 0, "k": 0,
                             "h_aP_star": 1, "h_fP_star": 1.25, "d": 44.137})

    by_key = {o.key: o for o in outputs}
    assert by_key["m_t"].formula_latex == r"m_t = \frac{m_n}{\cos\beta}"
    assert r"\cos 25^\circ" in (by_key["m_t"].substitution_latex or "")
    assert r"\alpha_t" in (by_key["alpha_t"].formula_latex or "")
    assert r"d_b" in (by_key["d_b"].formula_latex or "")
    assert r"p_{bt}" in (by_key["p_bt"].formula_latex or "")
    assert r"h_{aP}" in (by_key["h_a"].formula_latex or "")
    assert r"\mathrm{mm}" in (by_key["d_a"].result_latex or "")


def test_legacy_expression_fallback_is_deterministic_and_readable():
    rendered = expression_to_latex("d_b = d·cos α_t = 44 mm")
    assert rendered == r"d_{b} = d\cdot \cos \alpha_{t} = 44 mm"


def test_gear_default_coefficients_can_be_explicitly_provided():
    parsed = parse_calculation("z=20, mn=2 mm, beta=25°, alpha_n=20°, h_aP*=1, h_fP*=1.25 hesapla")
    assert parsed.calc_type == "cylindrical_gear_geometry"
    assert parsed.inputs["h_aP_star"] == (1.0, "")
    assert parsed.inputs["h_fP_star"] == (1.25, "")
