"""Deterministic reconstruction of displayed equations from PDF geometry (no model).

ISO PDFs typeset an equation as positioned glyph runs plus vector drawings:
fraction bars are horizontal strokes, ``|z|`` bars are short vertical strokes,
a square root is a drawn radical polygon, and sub-/superscripts are smaller glyph
runs set below/above the baseline. This module rebuilds that 2-D layout into a
small expression tree and serialises it three ways:

* ``raw``    the glyph runs in page order, exactly as the text layer stores them
             (provenance; never edited)
* ``plain``  a normalised one-line form in the document's own symbol spelling
             ("db = d cos αt", subscripts joined as in the symbol list), used for
             full-text search and shown to the language model
* ``latex``  a LaTeX form ("d_{b} = d \\cos \\alpha_{t}") for rendering

Nothing is invented: every output character comes from a glyph of the PDF or
from a drawn primitive (bar, radical, vertical rule). ``glyphs_conserved``
checks this. When the layout cannot be explained completely (a glyph off the
baseline that is neither a script nor part of a fraction/root, an unknown glyph,
OCR input) the result is ``needs_review`` and no LaTeX is produced.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

FORMULA_VERSION = "formula-geometry/1"

GREEK_LATEX = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\varepsilon", "ζ": r"\zeta",
    "η": r"\eta", "θ": r"\theta", "ϑ": r"\vartheta", "ι": r"\iota", "κ": r"\kappa", "λ": r"\lambda",
    "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ϖ": r"\varpi", "ρ": r"\rho", "σ": r"\sigma",
    "ς": r"\varsigma", "τ": r"\tau", "υ": r"\upsilon", "φ": r"\varphi", "ϕ": r"\phi", "χ": r"\chi",
    "ψ": r"\psi", "ω": r"\omega", "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda",
    "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma", "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
    "µ": r"\mu",
}
OPERATOR_LATEX = {
    "−": "-", "-": "-", "+": "+", "=": "=", "×": r"\times", "·": r"\cdot", "⋅": r"\cdot", "/": "/",
    "≤": r"\leq", "≥": r"\geq", "<": "<", ">": ">", "±": r"\pm", "∓": r"\mp", "≈": r"\approx",
    "≠": r"\neq", "∞": r"\infty", "°": r"^{\circ}", "′": "'", "″": "''", "(": "(", ")": ")",
    "[": "[", "]": "]", "{": r"\{", "}": r"\}", ",": ",", ";": ";", ":": ":", "|": "|", "%": r"\%",
    "!": "!", "∗": r"\ast", "*": r"\ast", "∑": r"\sum", "Σ": r"\Sigma", "∆": r"\Delta", "√": r"\sqrt{}", "…": r"\ldots",
}
FUNCTIONS = ("arccos", "arcsin", "arctan", "cos", "sin", "tan", "cot", "inv", "exp", "ln", "log", "max", "min",
             "sgn", "lim", "sinh", "cosh", "tanh")
_LATEX_FUNC = {"inv": r"\operatorname{inv}", "sgn": r"\operatorname{sgn}"}
_ATOM = re.compile(
    r"(?P<func>" + "|".join(sorted(FUNCTIONS, key=len, reverse=True)) + r")(?![a-z])"
    r"|(?P<num>\d+(?:[.,]\d+)?)"
    r"|(?P<word>[A-Za-z]{2,})"
    r"|(?P<ch>\S)")
# math fonts whose glyphs have no reliable Unicode meaning in the text layer (ISOamsr "W" is ⩾)
UNCERTAIN_FONT = re.compile(r"isoams|mathpi|greekwithmath|mt-?extra|cmsy|cmex|msam|msbm|euclid|wingding|dingbat",
                            re.IGNORECASE)
_KNOWN = set(GREEK_LATEX) | set(OPERATOR_LATEX) | set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                                                       "0123456789.")


# --------------------------------------------------------------------------- input model
@dataclass
class Glyph:
    """One positioned glyph run of the equation region (a span, or a piece of one)."""

    text: str
    bbox: tuple[float, float, float, float]
    size: float
    baseline: float
    font: str = ""

    @property
    def xc(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def yc(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


@dataclass
class Prims:
    """Drawn primitives of the equation region."""

    hbars: list[tuple[float, float, float]] = field(default_factory=list)  # (x0, x1, y)
    vbars: list[tuple[float, float, float]] = field(default_factory=list)  # (x, y0, y1)
    radicals: list[tuple[float, float, float, float]] = field(default_factory=list)  # bbox


def primitives(drawings: list[dict], region: tuple[float, float, float, float]) -> Prims:
    """Fraction bars, vertical rules and radical signs among PyMuPDF drawings inside ``region``.

    White fills are masks and are ignored. A path with a diagonal stroke is a radical sign: its
    own horizontal top edge is the vinculum, never a fraction bar."""
    x0, y0, x1, y1 = region
    out = Prims()
    for d in drawings:
        r = d.get("rect")
        if r is None or r.x1 < x0 - 2 or r.x0 > x1 + 2 or r.y1 < y0 - 2 or r.y0 > y1 + 2:
            continue
        fill = d.get("fill")
        if fill is not None and all(c > 0.9 for c in fill) and d.get("color") is None:
            continue  # white-out mask
        segs = []
        for it in d.get("items", []):
            if it[0] == "l":
                segs.append((it[1].x, it[1].y, it[2].x, it[2].y))
            elif it[0] == "re":
                rr = it[1]
                if rr.height <= 1.2 and rr.width >= 3:
                    segs.append((rr.x0, (rr.y0 + rr.y1) / 2, rr.x1, (rr.y0 + rr.y1) / 2))
        diagonal = any(abs(a - c) > 1.0 and abs(b - e) > 1.0 for a, b, c, e in segs)
        if diagonal and r.height >= 5 and r.width >= 6:
            # the radical's own vinculum is its top edge; horizontal strokes of the same path at another
            # height are separate bars (some PDFs draw a root and a fraction bar as one path)
            flat = [(min(a, c), max(a, c), (b + e) / 2) for a, b, c, e in segs if abs(b - e) < 0.6 and abs(c - a) >= 3]
            top = min((h[2] for h in flat), default=r.y0)
            extra = [h for h in flat if h[2] - top > 1.5]
            y_bottom = max((h[2] for h in extra), default=None)
            box = (r.x0, r.y0, r.x1, r.y1 if y_bottom is None else min(r.y1, min(h[2] for h in extra) - 0.5))
            out.hbars += extra
            # a radical is often drawn twice (fill + outline): keep one
            if not any(abs(box[0] - q[0]) < 2 and abs(box[2] - q[2]) < 2 and abs(box[1] - q[1]) < 2 for q in out.radicals):
                out.radicals.append(box)
            continue
        for a, b, c, e in segs:
            if abs(b - e) < 0.6 and abs(c - a) >= 3:
                out.hbars.append((min(a, c), max(a, c), (b + e) / 2))
            elif abs(a - c) < 0.6 and 5 <= abs(e - b) <= 40:
                out.vbars.append(((a + c) / 2, min(b, e), max(b, e)))
    out.hbars.sort(key=lambda h: h[1] - h[0], reverse=True)
    return out


# --------------------------------------------------------------------------- expression tree
@dataclass
class Node:
    kind: str  # atom | seq | frac | sqrt | abs
    text: str = ""
    atype: str = ""  # func | num | word | greek | letter | op | other
    children: list["Node"] = field(default_factory=list)
    sub: list["Node"] = field(default_factory=list)
    sup: list["Node"] = field(default_factory=list)
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    size: float = 10.0
    baseline: float = 0.0

    @property
    def xc(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def yc(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


def _atype(kind: str, text: str) -> str:
    if kind == "ch":
        if text in GREEK_LATEX:
            return "greek"
        if text.isalpha() and text.isascii():
            return "letter"
        if text in OPERATOR_LATEX:
            return "op"
        return "other"
    return kind


# pieces of tall brackets set glyph by glyph (U+239B..U+23AD): one column of pieces is one delimiter
_BRACKET_PIECES = {"⎛": "(", "⎜": "(", "⎝": "(", "⎞": ")", "⎟": ")", "⎠": ")", "⎡": "[", "⎢": "[", "⎣": "[",
                   "⎤": "]", "⎥": "]", "⎦": "]", "⎧": "{", "⎨": "{", "⎩": "{", "⎪": "{", "⎫": "}", "⎬": "}",
                   "⎭": "}"}


def _collapse_brackets(nodes: list[Node]) -> list[Node]:
    pieces = [n for n in nodes if n.kind == "atom" and n.text in _BRACKET_PIECES]
    if not pieces:
        return nodes
    rest = [n for n in nodes if n not in pieces]
    cols: list[list[Node]] = []
    for p in sorted(pieces, key=lambda n: (n.bbox[0], n.bbox[1])):
        col = next((c for c in cols if _BRACKET_PIECES[c[0].text] == _BRACKET_PIECES[p.text]
                    and abs(c[0].xc - p.xc) < 0.6 * p.size), None)
        if col is None:
            cols.append([p])
        else:
            col.append(p)
    for col in cols:
        box = _union(col)
        rest.append(Node("atom", _BRACKET_PIECES[col[0].text], "op", bbox=box, size=col[0].size,
                         baseline=statistics.median(n.baseline for n in col)))
    return rest


def atoms(glyphs: list[Glyph]) -> list[Node]:
    """Split glyph runs into atoms (functions, numbers, words, single symbols) with estimated x ranges."""
    out: list[Node] = []
    for g in glyphs:
        text = g.text
        n = max(1, len(text))
        w = (g.bbox[2] - g.bbox[0]) / n
        uncertain = bool(UNCERTAIN_FONT.search(g.font or ""))
        for m in _ATOM.finditer(text):
            kind = "uncertain" if uncertain else (m.lastgroup or "ch")
            s, e = m.span()
            bbox = (g.bbox[0] + s * w, g.bbox[1], g.bbox[0] + e * w, g.bbox[3])
            tok = m.group(0)
            if kind == "word" and ("Ital" in g.font or "Oblique" in g.font or "tiit" in g.font.lower()):
                kind = "letter"  # an italic letter run is set exactly as printed ("sP"); never split or spaced
            out.append(Node("atom", tok, _atype(kind, tok) if kind != "letter" else "letter", bbox=bbox,
                            size=g.size, baseline=g.baseline))
    return out


# words that introduce an equation on its own row ("with", "or", "Hence", "⎯ at A:"): kept as a text prefix
CONNECTORS = {"with", "from", "or", "and", "hence", "otherwise", "where", "then", "for", "at", "produced",
              "alternatively", "thus", "therefore", "i.e", "e.g"}


def split_prefix(nodes: list[Node]) -> tuple[list[str], list[Node]]:
    """Leading connector words and list dashes of the equation row; returns (prefix words, remaining nodes)."""
    row = sorted(nodes, key=lambda n: n.bbox[0])
    prefix: list[str] = []
    for n in row:
        t = n.text
        if n.atype == "word" and t.lower() in CONNECTORS or t in ("⎯", "—", "–") and not prefix and n is row[0]                 or prefix and t == ":" or prefix and prefix[-1].lower() == "at" and n.atype == "letter" and t.isupper():
            prefix.append(t)
            continue
        break
    if not prefix or len(prefix) == len(row):
        return [], nodes
    drop = set(id(n) for n in row[:len(prefix)])
    return prefix, [n for n in nodes if id(n) not in drop]


def merge_numbers(nodes: list[Node]) -> list[Node]:
    """'0,' + '001' set as touching runs -> one number '0,001' (decimal comma of ISO documents)."""
    row = sorted(nodes, key=lambda n: n.bbox[0])
    out: list[Node] = []
    for n in row:
        prev = out[-1] if out else None
        touching = (prev is not None and prev.kind == n.kind == "atom" and n.bbox[0] - prev.bbox[2] < 0.3 * n.size
                    and abs(n.baseline - prev.baseline) < 0.5 and not prev.sub and not prev.sup)
        if touching and n.atype == "num" and prev.atype == "num" and re.fullmatch(r"\d+[,.]", prev.text):
            prev.text += n.text
            prev.bbox = _union([prev, n])
            continue
        if touching and prev.atype == "num" and n.text in (",", ".") and re.fullmatch(r"\d+", prev.text):
            prev.text += n.text  # completed by the digits that follow
            prev.bbox = _union([prev, n])
            continue
        out.append(n)
    extra = []
    for n in out:
        if n.kind == "atom" and n.atype == "num" and n.text[-1:] in (",", ".") and len(n.text) > 1:
            n.text = n.text[:-1]  # no digits followed: the separator is punctuation ("u = z2/z1,")
            extra.append(Node("atom", ",", "op", bbox=(n.bbox[2], n.bbox[1], n.bbox[2] + 1, n.bbox[3]), size=n.size,
                              baseline=n.baseline))
    return out + extra


def _union(nodes: list[Node]) -> tuple[float, float, float, float]:
    return (min(n.bbox[0] for n in nodes), min(n.bbox[1] for n in nodes),
            max(n.bbox[2] for n in nodes), max(n.bbox[3] for n in nodes))


@dataclass
class Build:
    reasons: list[str] = field(default_factory=list)
    structures: list[str] = field(default_factory=list)


def _group(nodes: list[Node], prims: Prims, st: Build, depth: int = 0) -> Node:
    """Nodes of one region -> sequence node with 2-D structures resolved."""
    if depth > 6:
        st.reasons.append("nesting_too_deep")
        return Node("seq", children=sorted(nodes, key=lambda n: n.bbox[0]))
    nodes = list(nodes)
    ref = _ref_size(nodes) if nodes else 10.0
    # 1. radicals: glyphs inside the drawn radical sign form its body
    for rad in list(prims.radicals):
        body = [n for n in nodes if rad[0] + 1 < n.xc < rad[2] + 1 and rad[1] - 1 < n.yc < rad[3] + 1]
        if not body:
            continue
        prims.radicals.remove(rad)
        nodes = [n for n in nodes if n not in body]
        inner = Prims([h for h in prims.hbars if rad[0] <= h[0] and h[1] <= rad[2] + 1 and rad[1] < h[2] < rad[3]],
                      [v for v in prims.vbars if rad[0] < v[0] < rad[2] and rad[1] - 1 <= v[1]], [])
        prims.hbars = [h for h in prims.hbars if h not in inner.hbars]
        prims.vbars = [v for v in prims.vbars if v not in inner.vbars]
        node = Node("sqrt", children=[_group(body, inner, st, depth + 1)], bbox=rad, size=ref,
                    baseline=statistics.median(n.baseline for n in body))
        nodes.append(node)
        st.structures.append("sqrt")
    # 2. vertical rules in pairs on one row: |z|
    vb = sorted(prims.vbars, key=lambda v: v[0])
    used_v: set[int] = set()
    for i, left in enumerate(vb):
        if i in used_v:
            continue
        for j in range(i + 1, len(vb)):
            right = vb[j]
            if j in used_v or abs(left[1] - right[1]) > 2 or abs(left[2] - right[2]) > 2:
                continue
            body = [n for n in nodes if left[0] < n.xc < right[0] and left[1] - 1 < n.yc < left[2] + 1]
            if body:
                used_v |= {i, j}
                nodes = [n for n in nodes if n not in body]
                nodes.append(Node("abs", children=[_group(body, Prims(), st, depth + 1)],
                                  bbox=(left[0], left[1], right[0], left[2]), size=ref,
                                  baseline=statistics.median(n.baseline for n in body)))
                st.structures.append("abs")
            break
    for i, v in enumerate(vb):
        if i not in used_v:  # a single rule is a literal bar
            nodes.append(Node("atom", "|", "op", bbox=(v[0] - 0.5, v[1], v[0] + 0.5, v[2]), size=ref,
                              baseline=v[2] - 0.2 * (v[2] - v[1])))
    prims.vbars = []
    # 3. fraction bars, longest first: glyphs above / below within the bar's width
    for bar in list(prims.hbars):
        x0, x1, y = bar
        num = [n for n in nodes if x0 - 1 <= n.xc <= x1 + 1 and n.bbox[3] <= y + 1.5 and n.bbox[1] >= y - 2.6 * ref]
        den = [n for n in nodes if x0 - 1 <= n.xc <= x1 + 1 and n.bbox[1] >= y - 1.5 and n.bbox[3] <= y + 2.6 * ref]
        if not num or not den:
            continue
        prims.hbars.remove(bar)
        nodes = [n for n in nodes if n not in num and n not in den]
        for n in nodes:  # a glyph cut by the bar belongs to neither side: the layout is not understood
            if n.kind == "atom" and x0 <= n.xc <= x1 and n.bbox[1] < y < n.bbox[3] and n.atype != "op":
                st.reasons.append(f"glyph_straddles_bar:{n.text}")
        inner_n = Prims([h for h in prims.hbars if x0 - 1 <= h[0] and h[1] <= x1 + 1 and h[2] < y], [], [])
        inner_d = Prims([h for h in prims.hbars if x0 - 1 <= h[0] and h[1] <= x1 + 1 and h[2] > y], [], [])
        prims.hbars = [h for h in prims.hbars if h not in inner_n.hbars and h not in inner_d.hbars]
        nodes.append(Node("frac", children=[_group(num, inner_n, st, depth + 1), _group(den, inner_d, st, depth + 1)],
                          bbox=(x0, min(n.bbox[1] for n in num), x1, max(n.bbox[3] for n in den)), size=ref,
                          baseline=y + 0.3 * ref))
        st.structures.append("fraction")
    return _scripts(nodes, st)


def _scripts(nodes: list[Node], st: Build) -> Node:
    """Main line of a region: attach sub-/superscripts, check that the rest shares one baseline."""
    nodes = sorted(nodes, key=lambda n: n.bbox[0])
    if not nodes:
        return Node("seq")
    ref = _ref_size(nodes)
    # the main line's baseline: every full-size element (operators and fractions/roots included), so that
    # one stray raised glyph cannot shift it
    full = [n for n in nodes if n.size >= 0.9 * ref or n.kind != "atom"]
    base_line = statistics.median(n.baseline for n in (full or nodes))
    out: list[Node] = []
    raised: tuple[Node, float] | None = None  # (host, baseline) of a running raised group: (√2)^(A − 5)
    for n in nodes:
        small = n.kind == "atom" and n.size < 0.86 * ref
        host = out[-1] if out else None
        if raised is not None and n.kind == "atom" and abs(n.baseline - raised[1]) < 0.12 * ref:
            _attach(raised[0], "sup", n)
            continue
        raised = None
        if host is not None and n.kind == "atom" and n.text == "(" and _can_carry_scripts(host) \
                and n.baseline < host.baseline - 0.2 * ref:
            _attach(host, "sup", n)  # a raised parenthesised exponent group
            raised = (host, n.baseline)
            continue
        if host is not None and n.kind == "atom" and _can_carry_scripts(host):
            ref_base = host.baseline
            if small and n.baseline > ref_base + 0.1 * ref:
                _attach(host, "sub", n)
                continue
            # superscripts are usually smaller; a full-size glyph raised clearly above its neighbour is one too
            if n.baseline < ref_base - (0.15 if small else 0.2) * ref and n.atype != "op":
                if n.text == "o" and host.kind == "atom" and host.atype == "num":
                    st.reasons.append("ambiguous_glyph:o_after_number(degree?)")  # never rewritten to "°"
                _attach(host, "sup", n)
                continue
        if n.kind == "atom" and n.atype != "op" and abs(n.baseline - base_line) > 0.45 * ref:
            st.reasons.append(f"glyph_off_baseline:{n.text}")
        out.append(n)
    return Node("seq", children=out, bbox=_union(nodes), size=ref, baseline=base_line)


def _can_carry_scripts(host: Node) -> bool:
    """Symbols, numbers, functions, closing brackets and composite structures carry scripts; an operator or
    an opening bracket never does (a tall "(" has a low baseline that would make the next letter look raised)."""
    return host.kind != "atom" or host.atype in ("letter", "greek", "num", "func", "word") or host.text in ")]}|"


def _ref_size(nodes: list[Node]) -> float:
    """Body size of a region: the largest letter/number glyph (big brackets and operators do not count)."""
    body = [n.size for n in nodes if n.kind == "atom" and n.atype in ("letter", "greek", "num", "func", "word")]
    return max(body) if body else max(n.size for n in nodes)


def _attach(host: Node, where: str, n: Node) -> None:
    target = host.sub if where == "sub" else host.sup
    target.append(n)


# --------------------------------------------------------------------------- serialisation
def _needs_space(prev: Node, cur: Node) -> bool:
    """A space only where the page shows a gap: touching runs ("π" + "m/2") stay joined, as in the text layer."""
    if prev.kind == "atom" and prev.atype == "op" and prev.text in "([|" or cur.kind == "atom" and cur.text in ")],;°′″|":
        return False
    if prev.kind == "atom" and prev.atype == "num" and cur.kind == "atom" and cur.text == "°":
        return False
    binary = {"=", "+", "−", "-", "±", "∓", "×", "·", "⋅", "≤", "≥", "<", ">", "≈", "≠"}
    if prev.kind == "atom" and prev.text in binary or cur.kind == "atom" and cur.text in binary:
        return True  # "da − 2mn": binary operators are always set apart
    if cur.kind == "atom" and cur.atype == "func" or prev.kind == "atom" and prev.atype == "func":
        return True  # "tan β cos αt"
    if cur.kind == "atom" and prev.kind == "atom" and cur.bbox[0] - prev.bbox[2] < 0.15 * max(cur.size, 1.0):
        return False  # touching runs ("π" + "m", "x" + "mn") are one product, as in the text layer
    return True


def plain(node: Node) -> str:
    if node.kind == "atom":
        s = node.text
        if node.sub:
            s += "".join(plain(x) for x in node.sub)
        if node.sup:
            sup = plain(Node("seq", children=node.sup)) if len(node.sup) > 1 else plain(node.sup[0])
            s += sup if sup in ("°", "′", "″") else f"^{sup}"
        return s
    if node.kind == "seq":
        out, prev = "", None
        for c in node.children:
            if prev is not None and _needs_space(prev, c):
                out += " "
            out += plain(c)
            prev = c
        return re.sub(r"\s+", " ", out).strip()
    if node.kind == "frac":
        n, d = (plain(c) for c in node.children)
        return f"{_wrap(n)}/{_wrap(d)}" + _plain_sup(node)
    if node.kind == "sqrt":
        return f"√({plain(node.children[0])})" + _plain_sup(node)
    if node.kind == "abs":
        return f"|{plain(node.children[0])}|" + _plain_sup(node)
    return ""


def _plain_sup(node: Node) -> str:
    if not node.sup:
        return ""
    sup = plain(Node("seq", children=node.sup)) if len(node.sup) > 1 else plain(node.sup[0])
    return f"^{sup}"


def _wrap(s: str) -> str:
    if re.fullmatch(r"[^\s+−\-=/]+", s) or _enclosed(s):
        return s
    return f"({s})"


def _enclosed(s: str) -> bool:
    """'[a − b]' or '(a − b)': one bracket pair around the whole expression."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    if len(s) < 2 or s[0] not in pairs or s[-1] != pairs[s[0]]:
        return False
    depth = 0
    for i, c in enumerate(s):
        depth += c in "([{"
        depth -= c in ")]}"
        if depth == 0 and i < len(s) - 1:
            return False
    return True


def _latex_atom(n: Node) -> str:
    t = n.text
    if n.atype == "func":
        s = _LATEX_FUNC.get(t, "\\" + t)
    elif n.atype == "greek":
        s = GREEK_LATEX[t]
    elif n.atype == "num":
        s = t.replace(",", "{,}")
    elif n.atype == "word":
        s = rf"\text{{{t}}}"
    elif n.atype == "op":
        s = OPERATOR_LATEX.get(t, t)
    else:
        s = t
    if n.sub:
        s += "_{" + _join_latex([latex(x) for x in n.sub]) + "}"
    if n.sup:
        sup = latex(Node("seq", children=n.sup)) if any(x.text == "(" for x in n.sup) \
            else _join_latex([latex(x) for x in n.sup])
        s += sup if sup.startswith("^") else "^{" + sup + "}"
    return s


def _join_latex(parts: list[str]) -> str:
    """Concatenate script parts; a letter after a command needs a space (alpha T, not alphaT)."""
    out = ""
    for part in parts:
        if re.search(r"\\[A-Za-z]+$", out) and re.match(r"[A-Za-z0-9]", part):
            out += " "
        out += part
    return out


def latex(node: Node) -> str:
    if node.kind == "atom":
        return _latex_atom(node)
    if node.kind == "seq":
        return " ".join(latex(c) for c in node.children).replace("( ", "(").replace(" )", ")")
    sup = "^{" + latex(Node("seq", children=node.sup)) + "}" if node.sup else ""
    if node.kind == "frac":
        n, d = (latex(c) for c in node.children)
        return rf"\frac{{{n}}}{{{d}}}" + sup
    if node.kind == "sqrt":
        return rf"\sqrt{{{latex(node.children[0])}}}" + sup
    if node.kind == "abs":
        return rf"\lvert {latex(node.children[0])} \rvert" + sup
    return ""


def _walk(node: Node):
    """Nodes of the tree; scripts are part of their base symbol and are not visited on their own."""
    yield node
    for c in node.children:
        yield from _walk(c)


def symbols_of(node: Node) -> list[str]:
    """Engineering symbols of the expression in document spelling (base letter + subscript): db, αt, mn."""
    out: list[str] = []
    for n in _walk(node):
        if n.kind == "atom" and n.atype in ("letter", "greek"):
            s = n.text + "".join(plain(x) for x in n.sub)
            if s not in out:
                out.append(s)
    return out


# --------------------------------------------------------------------------- public API
@dataclass
class FormulaResult:
    number: str | None
    raw: str
    plain: str
    latex: str | None
    status: str  # exact | reconstructed | needs_review
    confidence: float
    reasons: list[str] = field(default_factory=list)
    structures: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    lhs: str | None = None
    source: str = "pymupdf-geometry"
    prefix: str | None = None  # connector words of the equation row ("with", "or")

    def as_dict(self) -> dict:
        return {"number": self.number, "raw": self.raw, "plain": self.plain, "latex": self.latex,
                "status": self.status, "confidence": self.confidence, "reasons": self.reasons[:12],
                "structures": sorted(set(self.structures)), "symbols": self.symbols, "lhs": self.lhs,
                "source": self.source, "prefix": self.prefix, "version": FORMULA_VERSION}


_SQUASH = re.compile(r"[\s()\[\]{}|/√^⎛⎜⎝⎞⎟⎠⎡⎢⎣⎤⎥⎦⎧⎨⎩⎪⎫⎬⎭]")


def glyphs_conserved(raw_glyphs: str, rebuilt: str) -> bool:
    """Every character of the rebuilt plain form comes from the source glyphs (same multiset, ignoring
    whitespace and the structural characters added for drawn bars and radicals)."""
    a = sorted(_SQUASH.sub("", raw_glyphs))
    b = sorted(_SQUASH.sub("", rebuilt))
    return a == b


def reconstruct(glyphs: list[Glyph], prims: Prims, number: str | None, *, raw: str | None = None,
                ocr: bool = False, extra_reasons: list[str] | None = None) -> FormulaResult:
    """Rebuild one displayed equation. ``glyphs`` excludes the equation number itself."""
    raw_text = raw if raw is not None else " ".join(g.text.strip() for g in glyphs if g.text.strip())
    glyphs = [g for g in glyphs if g.text.strip()]
    if not glyphs:
        return FormulaResult(number, raw_text, "", None, "needs_review", 0.0, ["no_glyphs"])
    if ocr:
        return FormulaResult(number, raw_text, raw_text, None, "needs_review", 0.3, ["ocr_source"],
                             source="ocr")
    st = Build(reasons=list(extra_reasons or []))
    prefix, nodes = split_prefix(merge_numbers(_collapse_brackets(atoms(glyphs))))
    if prefix:  # the prefix words are provenance of the row, not part of the expression
        glyphs = [g for g in glyphs if not any(w in g.text for w in prefix)] or glyphs
    unknown = sorted({n.text for n in nodes if n.atype == "other" and n.text not in _KNOWN})
    if unknown:
        st.reasons.append("unknown_glyphs:" + "".join(unknown))
    uncertain = sorted({n.text for n in nodes if n.atype == "uncertain"})
    if uncertain:
        st.reasons.append("uncertain_font_glyphs:" + "".join(uncertain))  # never rewritten (W may be ⩾)
    work = Prims(list(prims.hbars), list(prims.vbars), list(prims.radicals))
    tree = _group(nodes, work, st)
    for x0, x1, y in work.hbars:  # a bar with glyphs directly above AND below that no fraction explained
        near = [n for n in nodes if n.kind == "atom" and x0 <= n.xc <= x1]
        ref = _ref_size(nodes)
        if any(y - 2 * ref < n.yc < y for n in near) and any(y < n.yc < y + 2 * ref for n in near):
            st.reasons.append("unexplained_bar")
    text = plain(tree)
    if not glyphs_conserved("".join(n.text for n in nodes), text):
        st.reasons.append("glyphs_not_conserved")
    lhs = _lhs(tree)
    syms = symbols_of(tree)
    if st.reasons:
        return FormulaResult(number, raw_text, text, None, "needs_review", 0.4, st.reasons, st.structures, syms, lhs)
    tex = latex(tree)
    bad = latex_problems(tex)
    if bad:
        return FormulaResult(number, raw_text, text, None, "needs_review", 0.4, bad, st.structures, syms, lhs)
    status = "reconstructed" if st.structures else "exact"
    res = FormulaResult(number, raw_text, text, tex, status, 0.9 if st.structures else 0.97, [],
                        st.structures, syms, lhs)
    res.prefix = " ".join(prefix) or None
    return res


_ALLOWED_CMDS = ({c[1:].split("{")[0] for c in list(GREEK_LATEX.values()) + list(OPERATOR_LATEX.values())
                  if c.startswith("\\")} | set(FUNCTIONS)
                 | {"frac", "sqrt", "lvert", "rvert", "text", "operatorname", "circ", "max", "min"})


def latex_problems(tex: str) -> list[str]:
    """Structural sanity of generated LaTeX: balanced braces and only known commands (no fused ones)."""
    out = []
    depth = 0
    for c in tex:
        depth += c == "{"
        depth -= c == "}"
        if depth < 0:
            break
    if depth != 0:
        out.append("latex_unbalanced_braces")
    unknown = sorted({m.group(1) for m in re.finditer(r"\\([A-Za-z]+)", tex)} - _ALLOWED_CMDS)
    if unknown:
        out.append("latex_unknown_command:" + ",".join(unknown[:3]))
    return out


def _lhs(tree: Node) -> str | None:
    """The quantity an equation defines: the symbol left of the first '=' when it stands alone."""
    kids = tree.children
    for i, c in enumerate(kids):
        if c.kind == "atom" and c.text == "=":
            left = kids[:i]
            if len(left) == 2 and left[0].kind == "atom" and left[0].atype == "func":
                left = left[1:]  # "cos αt = db/d" defines αt
            if len(left) == 1 and left[0].kind == "atom" and left[0].atype in ("letter", "greek"):
                return left[0].text + "".join(plain(x) for x in left[0].sub)
            return None
    return None


def linear_fallback(text: str, number: str | None, reason: str) -> FormulaResult:
    """A formula the geometry path did not see (OCR, model output): plain = raw, never LaTeX."""
    return FormulaResult(number, text, text, None, "needs_review", 0.3, [reason])
