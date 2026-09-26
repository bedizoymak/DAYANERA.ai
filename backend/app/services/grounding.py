"""Anti-hallucination checks for verified-source answers.

A drafted answer is accepted only if every number it states appears in the
retrieved source passages or in the user's own question. Otherwise it is
replaced by the exact refusal phrase. Citation markers [S1]..[Sn] are
validated and stripped from the displayed text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.enums import REFUSAL_PHRASE

_NUM = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)(?![\w])")
_MARKER = re.compile(r"\[\s*S\s*(\d{1,2})\s*\]", re.IGNORECASE)
_LIST_ENUM = re.compile(r"^\s*(\d{1,2})[.)]\s", re.MULTILINE)
_REFUSAL_NORM = REFUSAL_PHRASE.casefold()


def _canon(num: str) -> str:
    s = num.replace(",", ".")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.lstrip("0") or "0"


def numbers_in(text: str) -> set[str]:
    return {_canon(m.group(1)) for m in _NUM.finditer(text)}


@dataclass
class GroundingResult:
    accepted: bool
    text: str
    cited: list[int] = field(default_factory=list)
    unsupported_numbers: list[str] = field(default_factory=list)
    reason: str | None = None


def is_refusal(answer: str) -> bool:
    a = answer.strip().casefold()
    return _REFUSAL_NORM in a or a.startswith("bu kaynak setinde doğrulayamad")


def validate_answer(answer: str, passages_text: list[str], question: str, extra_allowed: list[str] | None = None) -> GroundingResult:
    if not answer or not answer.strip():
        return GroundingResult(False, REFUSAL_PHRASE, reason="empty_answer")
    if is_refusal(answer):
        return GroundingResult(False, REFUSAL_PHRASE, reason="model_refused")
    cited = sorted({int(m.group(1)) for m in _MARKER.finditer(answer)})
    invalid = [c for c in cited if c < 1 or c > len(passages_text)]
    if invalid:
        return GroundingResult(False, REFUSAL_PHRASE, cited=cited, reason=f"invalid_citation:{invalid}")
    body = _MARKER.sub("", answer)
    enumerators = {_canon(m.group(1)) for m in _LIST_ENUM.finditer(body)}
    allowed = set()
    for t in passages_text + [question] + (extra_allowed or []):
        allowed |= numbers_in(t)
    stated = numbers_in(body)
    unsupported = sorted(n for n in stated if n not in allowed and n not in enumerators)
    if unsupported:
        return GroundingResult(False, REFUSAL_PHRASE, cited=cited, unsupported_numbers=unsupported,
                               reason="unsupported_numbers")
    body = re.sub(r"\\\(\s*(.*?)\s*\\\)", r"\1", body)  # strip LaTeX inline delimiters \( x \)
    body = re.sub(r"\\\[\s*(.*?)\s*\\\]", r"\1", body, flags=re.S)
    clean = re.sub(r"[ \t]+([.,;:])", r"\1", body)
    clean = re.sub(r"([.!?])\s*[,;]+", r"\1", clean)  # "20°dir. [S1]," -> "20°dir."
    clean = re.sub(r"[ \t]{2,}", " ", clean).strip()
    return GroundingResult(True, clean, cited=cited)
