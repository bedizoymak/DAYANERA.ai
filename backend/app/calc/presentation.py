"""Deterministic mathematical presentation for calculation results.

This module deliberately does not evaluate expressions.  The calculation
engine remains the source of every numeric value; these helpers only describe
already-computed values for the UI.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

from app.calc.types import OutputValue


def _num(value: float, decimals: int = 4) -> str:
    """Format numbers for TeX with a dot decimal separator."""
    text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _value(values: Mapping[str, float], key: str, default: float = 0.0) -> float:
    return float(values.get(key, default))


def _rhs(equation: str) -> str:
    return equation.split("=", 1)[1].strip() if "=" in equation else equation


def _unit(unit: str) -> str:
    if unit in {"µm", "μm", "Âµm", "Î¼m", "um"}:
        return r"\mathrm{\mu m}"
    if unit:
        return rf"\mathrm{{{unit}}}"
    return ""


def _result(symbol: str, value: float, unit: str) -> str:
    suffix = rf"\, {_unit(unit)}" if unit else ""
    return rf"{symbol} = {_num(value)}{suffix}"


def _substitution(symbol: str, rhs: str, result: float, unit: str) -> tuple[str, str]:
    substitution = rf"{symbol} = {rhs}"
    return substitution, rf"{substitution} = {_num(result)}{('\\, ' + _unit(unit)) if unit else ''}"


def _gear_presentation(output: OutputValue, values: Mapping[str, float]) -> tuple[str, str, str] | None:
    """Return canonical, substituted and final equations for gear outputs."""
    key = output.key
    mn = _num(_value(values, "m_n"))
    beta = _num(_value(values, "beta"))
    alpha_n = _num(_value(values, "alpha_n"))
    z = _num(_value(values, "z"), 0)
    mt = _num(_value(values, "m_t", output.value))
    alpha_t = _num(_value(values, "alpha_t", output.value))
    d = _num(_value(values, "d", output.value))
    x = _num(_value(values, "x"))
    k = _num(_value(values, "k"))
    ha_p = _num(_value(values, "h_aP"))
    hf_p = _num(_value(values, "h_fP"))

    formulas: dict[str, tuple[str, str]] = {
        "m_t": (r"m_t = \frac{m_n}{\cos\beta}", rf"m_t = \frac{{{mn}}}{{\cos {beta}^\circ}}"),
        "alpha_t": (
            r"\alpha_t = \arctan\left(\frac{\tan\alpha_n}{\cos\beta}\right)",
            rf"\alpha_t = \arctan\left(\frac{{\tan {alpha_n}^\circ}}{{\cos {beta}^\circ}}\right)",
        ),
        "d": (
            r"d = \frac{z\,m_n}{\cos\beta}",
            rf"d = \frac{{{z}\,\cdot\,{mn}}}{{\cos {beta}^\circ}}",
        ),
        "d_b": (r"d_b = d\cos\alpha_t", rf"d_b = {_num(_value(values, 'd', 0.0))}\cos {alpha_t}^\circ"),
        "p_n": (r"p_n = \pi m_n", rf"p_n = \pi\, {mn}"),
        "p_t": (r"p_t = \pi m_t", rf"p_t = \pi\, {mt}"),
        "p_bt": (r"p_{bt} = p_t\cos\alpha_t", rf"p_{{bt}} = {_num(_value(values, 'p_t', 0.0))}\cos {alpha_t}^\circ"),
        "h_a": (r"h_a = h_{aP} + x m_n + k m_n", rf"h_a = {ha_p} + {x}\cdot {mn} + {k}\cdot {mn}"),
        "h_f": (r"h_f = h_{fP} - x m_n", rf"h_f = {hf_p} - {x}\cdot {mn}"),
        "h": (r"h = h_{aP} + k m_n + h_{fP}", rf"h = {ha_p} + {k}\cdot {mn} + {hf_p}"),
        "d_a": (r"d_a = d + 2(x m_n + h_{aP} + k m_n)",
                rf"d_a = {_num(_value(values, 'd', 0.0))} + 2\left({x}\cdot {mn} + {ha_p} + {k}\cdot {mn}\right)"),
        "d_f": (r"d_f = d - 2(h_{fP} - x m_n)",
                rf"d_f = {_num(_value(values, 'd', 0.0))} - 2\left({hf_p} - {x}\cdot {mn}\right)"),
    }
    pair = formulas.get(key)
    if not pair:
        return None
    formula, substitution = pair
    symbol = formula.split("=", 1)[0].strip()
    return formula, substitution, _result(symbol, output.value, output.unit)


_GREEK = {
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "μ": r"\mu",
    "µ": r"\mu",
    "π": r"\pi",
    "√": r"\sqrt",
}


def expression_to_latex(expression: str) -> str:
    """Convert legacy engine expressions to readable TeX without evaluation.

    The converter is intentionally conservative and is used only as a
    backwards-compatible fallback for calculation types without a dedicated
    presentation template.
    """
    text = expression.strip()
    for source, target in _GREEK.items():
        text = text.replace(source, target)
    text = text.replace("·", r"\cdot ").replace("−", "-").replace("→", r"\rightarrow ")
    text = text.replace("âˆ’", "-").replace("âˆš", r"\sqrt ").replace("Â²", "^2")
    text = re.sub(r"\barctan\b", r"\arctan", text)
    text = re.sub(r"\b(cos|tan|arctan|acos|asin)\b", r"\\\1", text)
    text = re.sub(r"\b([A-Za-z]+)_([A-Za-z0-9]+)\b", r"\1_{\2}", text)
    text = re.sub(r"\b([A-Za-z])([0-9]+)\b", r"\1_{\2}", text)
    text = re.sub(r"(?<![A-Za-z])(-?\d+(?:[\.,]\d+)?)°", r"\1^\\circ", text)
    return text


def enrich_outputs(outputs: list[OutputValue], values: Mapping[str, float]) -> None:
    """Attach optional presentation fields in-place after numeric computation."""
    context = dict(values)
    for output in outputs:
        context[output.key] = output.value
    if "h_aP_star" in context and "m_n" in context:
        context["h_aP"] = context["h_aP_star"] * context["m_n"]
    if "h_fP_star" in context and "m_n" in context:
        context["h_fP"] = context["h_fP_star"] * context["m_n"]

    for output in outputs:
        presentation = _gear_presentation(output, context)
        if presentation:
            output.formula_latex, output.substitution_latex, output.result_latex = presentation
        else:
            output.formula_latex = expression_to_latex(output.expression)
            output.substitution_latex = None
            output.result_latex = _result(output.key, output.value, output.unit)


def trace_to_latex(trace: list[str]) -> list[str]:
    """Make legacy traces display-safe while preserving the original trace."""
    return [expression_to_latex(item) for item in trace]
