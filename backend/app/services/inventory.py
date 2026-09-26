"""Corpus inventory: which documents/standards are active and visible.

Used to answer "which standards do you have?" without the LLM and to detect
requested ISO codes that are not in the active verified corpus.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.access import ScopeSet

_ACTIVE_SQL = """
SELECT d.standard_code, d.title, d.original_filename, ka.is_verified_corpus
FROM documents d
LEFT JOIN knowledge_areas ka ON ka.id = d.knowledge_area_id
WHERE d.status = 'active' AND {scope}
"""

_REQUESTED = re.compile(r"\biso\s?(/\s?tr\s?)?(\d{2,5}(?:-\d{1,2})?)", re.IGNORECASE)


@dataclass
class InventoryDoc:
    code: str | None
    title: str
    verified_corpus: bool


def _natural_key(code: str | None, title: str) -> tuple:
    if not code:
        return (1, [], title.casefold())
    parts = [int(n) for n in re.findall(r"\d+", code.split(":")[0])]
    return (0, [("/TR" in code or "/TS" in code)] + parts, code)


def _clean_title(code: str | None, title: str) -> str:
    """Titles of watched documents start with their code ("ISO 53:1998 — ..."); avoid repeating it."""
    if code and title.startswith(code):
        rest = title[len(code):].lstrip(" —-:")
        return rest or title
    return title


def list_active_documents(db: Session, scopes: ScopeSet) -> list[InventoryDoc]:
    clause, params = scopes.sql_filter("d")
    rows = db.execute(text(_ACTIVE_SQL.format(scope=clause)), params).all()
    docs = [InventoryDoc(r.standard_code, _clean_title(r.standard_code, r.title or r.original_filename),
                         bool(r.is_verified_corpus)) for r in rows]
    docs.sort(key=lambda d: _natural_key(d.code, d.title))
    return docs


def active_corpus_codes(db: Session, scopes: ScopeSet) -> list[str]:
    return [d.code for d in list_active_documents(db, scopes) if d.verified_corpus and d.code]


def requested_codes(question: str) -> list[tuple[str, str]]:
    """ISO codes named in a question as (display, number) pairs, e.g. ("ISO 2768", "2768")."""
    out: list[tuple[str, str]] = []
    for m in _REQUESTED.finditer(question):
        number = m.group(2)
        display = f"ISO/TR {number}" if m.group(1) else f"ISO {number}"
        if (display, number) not in out:
            out.append((display, number))
    return out


def code_present(number: str, available: list[str]) -> bool:
    """'286' matches 'ISO 286-1:2010'; '286-1' matches 'ISO 286-1:2010' but not 'ISO 286-2:2010'."""
    tail = r"(?:\b|:)" if "-" in number else r"(?:\b|:|-)"
    rx = re.compile(rf"\b{re.escape(number)}{tail}")
    return any(rx.search(code) for code in available)


def missing_codes(question: str, available: list[str]) -> tuple[list[str], list[str]]:
    """Return (codes_requested, codes_missing) as display strings."""
    req = requested_codes(question)
    return [d for d, _ in req], [d for d, n in req if not code_present(n, available)]


def format_inventory(docs: list[InventoryDoc], max_other: int = 25) -> str:
    verified = [d for d in docs if d.verified_corpus]
    other = [d for d in docs if not d.verified_corpus]
    lines = [f"Doğrulanmış ISO korpusunda (iso booklets) {len(verified)} etkin belge var:"]
    if not verified:
        lines = ["Doğrulanmış ISO korpusunda henüz etkin belge yok. 'iso booklets' klasörüne PDF ekleyin."]
    for d in verified:
        lines.append(f"- {d.code or '(kod yok)'} — {d.title}")
    if other:
        lines += ["", f"Arşivdeki diğer {len(other)} belge (ekler/yüklemeler; doğrulanmış kaynak değildir):"]
        for d in other[:max_other]:
            lines.append(f"- {d.code or '(kod yok)'} — {d.title}")
        if len(other) > max_other:
            lines.append(f"- … ve {len(other) - max_other} belge daha (Belge arşivi sayfasında)")
    lines += ["", ("Bu standartlarla ilgili teknik soru sorabilir veya hesap isteyebilirsiniz; "
                   "kaynakları görmek için yanıttan sonra “kaynak ver” yazın.")]
    return "\n".join(lines)
