"""Deterministic engineering-symbol recovery.

Older ISO PDFs typeset Greek letters with Symbol-encoded fonts ("MT-Symbol",
"MT-Symbol-Italic") that carry no Unicode mapping: the text layer then stores
"aP" for αP and "rfP" for ρfP. The Adobe Symbol encoding fixes the letter
positions, so the Greek letter is recovered from the font name alone — no
model, no guessing. Only ASCII letters of such fonts are mapped; glyphs whose
meaning is not certain (∞ vs •) are left untouched and counted for review.

Layout parsers (Docling, find_tables) also split a symbol from its subscript
("h aP", "d a"); ``rejoin_subscripts`` restores a split only when the joined
form occurs as a token in the same document's text layer.
"""
from __future__ import annotations

import re

# Adobe Symbol encoding: Latin code position -> Greek letter
_SYMBOL_LETTERS = dict(zip(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
    "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖαβχδεφγηιϕκλμνοπθρστυϖωξψζ",
))
_SYMBOL_FONT = re.compile(r"symbol", re.IGNORECASE)


def is_symbol_font(font: str) -> bool:
    return bool(_SYMBOL_FONT.search(font or ""))


def map_symbol_span(font: str, text: str) -> tuple[str, int]:
    """Greek text of a Symbol-encoded span and the number of mapped glyphs.

    Spans that already contain non-ASCII letters come from a font with a
    Unicode map and are returned unchanged.
    """
    if not is_symbol_font(font) or any(ord(c) > 127 and c.isalpha() for c in text):
        return text, 0
    out = []
    mapped = 0
    for c in text:
        g = _SYMBOL_LETTERS.get(c)
        if g is not None:
            out.append(g)
            mapped += 1
        else:
            out.append(c)
    return "".join(out), mapped


# short English words that must never be produced by joining a split symbol
_WORDS = {"as", "at", "an", "is", "in", "be", "on", "of", "or", "to", "it", "by", "if", "no", "so", "we", "up",
          "do", "go", "me", "my", "us", "am", "he", "the", "and", "for", "are", "see", "per"}
_SYMBOL_TOKEN = re.compile(r"(?<![^\W_])[^\W\d_][^\W_]{1,5}(?![^\W_])")


def symbol_lexicon(text: str) -> set[str]:
    """Symbol-like tokens of a document text layer (haP, hFfP, da, αFP, mn)."""
    out = set()
    for m in _SYMBOL_TOKEN.finditer(text):
        tok = m.group(0)
        if tok.lower() in _WORDS or len(tok) > 6:
            continue
        greek = any("α" <= c.lower() <= "ω" for c in tok)
        mixed = bool(re.search(r"[A-Z]", tok[1:])) and bool(re.search(r"[a-z]", tok))
        short = len(tok) <= 3 and tok.islower()
        if greek or mixed or short or re.search(r"\d", tok):
            out.add(tok)
    return out


_SPLIT = re.compile(r"(?<![^\W_])([^\W\d_]) ([^\W_]{1,4})(?![^\W_])")


def rejoin_subscripts(text: str, lexicon: set[str]) -> str:
    """'h aP' -> 'haP', 'd a' -> 'da' when the joined token is in ``lexicon``."""
    def repl(m: re.Match) -> str:
        joined = m.group(1) + m.group(2)
        return joined if joined in lexicon and joined.lower() not in _WORDS else m.group(0)

    prev = None
    while prev != text:  # "d F a" style double splits resolve in two passes
        prev, text = text, _SPLIT.sub(repl, text)
    return text
