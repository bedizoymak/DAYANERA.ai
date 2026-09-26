"""Detect ISO standard identifiers and human-friendly titles."""
from __future__ import annotations

import re
from collections import Counter

_CODE = re.compile(r"\b(?:BS\s+(?:EN\s+)?)?(ISO(?:/TR|/TS|_TR|_TS)?\s?\d{2,5}(?:-\d{1,2})?(?::\d{4})?)", re.UNICODE)
_FILENAME_CODE = re.compile(r"\bISO(?:_(TR|TS))?\s(\d{2,5}),\s\d+,\s(\d{4})\b")


def _clean(code: str) -> str:
    code = code.replace("_TR", "/TR").replace("_TS", "/TS")
    code = re.sub(r"^ISO(/T[RS])?\s?", lambda m: "ISO" + (m.group(1) or "") + " ", code)
    return code.strip()


def _is_noise(code: str) -> bool:
    m = re.match(r"ISO(?:/T[RS])? (\d+)$", code)
    return bool(m and 1950 <= int(m.group(1)) <= 2035)


def detect_standard_code(first_pages_text: str, filename: str) -> str | None:
    counts: Counter[str] = Counter()
    for m in _CODE.finditer(first_pages_text):
        code = _clean(m.group(1))
        if _is_noise(code):
            continue
        counts[code] += 2 if ":" in code else 1
    if counts:
        best = counts.most_common()
        top_score = best[0][1]
        tied = [c for c, s in best if s == top_score]
        tied.sort(key=lambda c: (":" not in c, -len(c)))
        return tied[0]
    m = _FILENAME_CODE.search(filename)
    if m:
        prefix = "ISO/" + m.group(1) + " " if m.group(1) else "ISO "
        return f"{prefix}{m.group(2)}:{m.group(3)}"
    return None


def clean_title(filename: str, standard_code: str | None) -> str:
    base = filename.rsplit(".", 1)[0]
    first = base.split(" -- ")[0].strip()
    first = re.sub(r"_\s", ": ", first).strip(" —-_,")
    if not first:
        first = base[:120]
    if standard_code and standard_code not in first:
        return f"{standard_code} — {first}"
    return first
