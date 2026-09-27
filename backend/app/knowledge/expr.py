"""Safe evaluator for the normalized formula expressions of the knowledge registry.

Registry expressions are plain arithmetic over declared variable names, the
constant ``pi`` and a whitelist of math functions. Angles are radians. The
expression is parsed with :mod:`ast` and walked by hand: nothing is ever passed
to ``eval``/``exec``, and attribute access, subscripts, lambdas, comprehensions
and calls to anything outside the whitelist are rejected at parse time.
"""
from __future__ import annotations

import ast
import math
from collections.abc import Callable, Mapping
from functools import lru_cache


class ExpressionError(ValueError):
    """The expression is malformed, uses a forbidden construct or cannot be evaluated."""


def involute(alpha: float) -> float:
    """Involute function inv α = tan α − α (α in radians)."""
    return math.tan(alpha) - alpha


def involute_inverse(value: float) -> float:
    """α with inv α = value (0 ≤ α < π/2), by bisection: monotone, no derivative needed."""
    if value < 0 or not math.isfinite(value):
        raise ExpressionError("inv⁻¹ tanımsız (negatif veya sonsuz argüman)")
    lo, hi = 0.0, math.pi / 2 - 1e-12
    for _ in range(200):
        mid = (lo + hi) / 2
        if involute(mid) < value:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


FUNCTIONS: dict[str, Callable[..., float]] = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "sqrt": math.sqrt, "abs": abs, "radians": math.radians, "degrees": math.degrees,
    "inv": involute, "inv_inverse": involute_inverse, "min": min, "max": max, "fmod": math.fmod,
}
CONSTANTS: dict[str, float] = {"pi": math.pi}

_BINOPS: dict[type, Callable[[float, float], float]] = {
    ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b, ast.Pow: lambda a, b: a ** b,
}
_UNARY: dict[type, Callable[[float], float]] = {ast.USub: lambda a: -a, ast.UAdd: lambda a: a}


def _check(node: ast.AST) -> None:
    if isinstance(node, ast.Expression):
        _check(node.body)
    elif isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOPS:
            raise ExpressionError(f"izin verilmeyen işlem: {type(node.op).__name__}")
        _check(node.left)
        _check(node.right)
    elif isinstance(node, ast.UnaryOp):
        if type(node.op) not in _UNARY:
            raise ExpressionError(f"izin verilmeyen tekli işlem: {type(node.op).__name__}")
        _check(node.operand)
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS or node.keywords:
            raise ExpressionError("yalnızca beyaz listedeki matematik fonksiyonları çağrılabilir")
        for arg in node.args:
            _check(arg)
    elif isinstance(node, ast.Name):
        if node.id in FUNCTIONS:
            raise ExpressionError(f"fonksiyon adı değişken olarak kullanılamaz: {node.id}")
    elif isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise ExpressionError("yalnızca sayısal sabitler kullanılabilir")
    else:
        raise ExpressionError(f"izin verilmeyen ifade öğesi: {type(node).__name__}")


@lru_cache(maxsize=1024)
def parse(expression: str) -> ast.Expression:
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"ifade çözümlenemedi: {expression!r}") from exc
    _check(tree)
    return tree


def names(expression: str) -> set[str]:
    """Variable names the expression reads (functions and ``pi`` excluded)."""
    return {n.id for n in ast.walk(parse(expression)) if isinstance(n, ast.Name)
            and n.id not in FUNCTIONS and n.id not in CONSTANTS}


def _eval(node: ast.AST, env: Mapping[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.BinOp):
        return _BINOPS[type(node.op)](_eval(node.left, env), _eval(node.right, env))
    if isinstance(node, ast.UnaryOp):
        return _UNARY[type(node.op)](_eval(node.operand, env))
    if isinstance(node, ast.Call):
        return FUNCTIONS[node.func.id](*[_eval(a, env) for a in node.args])  # type: ignore[union-attr]
    if isinstance(node, ast.Name):
        if node.id in CONSTANTS:
            return CONSTANTS[node.id]
        if node.id not in env:
            raise ExpressionError(f"tanımsız değişken: {node.id}")
        return float(env[node.id])
    if isinstance(node, ast.Constant):
        return float(node.value)
    raise ExpressionError(f"izin verilmeyen ifade öğesi: {type(node).__name__}")  # pragma: no cover


def evaluate(expression: str, env: Mapping[str, float]) -> float:
    try:
        value = _eval(parse(expression), env)
    except ExpressionError:
        raise
    except (ValueError, ZeroDivisionError, OverflowError, TypeError) as exc:
        raise ExpressionError(f"{expression!r} değerlendirilemedi: {exc}") from exc
    if isinstance(value, complex) or not math.isfinite(value):
        raise ExpressionError(f"{expression!r} sonlu bir reel sayı vermedi")
    return float(value)
