"""Retrieval over the active verified corpus (PostgreSQL full-text search).

Only chunks that are (a) in a verified-corpus knowledge area, (b) from an
active document, (c) of the active, successfully indexed version and (d) in
``verified_source`` / ``user_confirmed`` state are eligible as evidence.
Deleted, superseded, failed and draft-extraction data can never be cited.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.access import ScopeSet
from app.services.glossary import (
    QUESTION_WORDS,
    SCORING_ALIASES,
    TURKISH_STOPWORDS,
    ascii_lower,
    map_turkish_spans,
    map_turkish_terms,
    tr_lower,
)
from app.services.grounding import numbers_in


@dataclass
class Passage:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    version_number: int
    document_title: str
    standard_code: str | None
    page_number: int | None
    locator: str
    text: str
    confidence_status: str
    rank: float
    coverage: float = 0.0  # fraction of glossary/English concepts matched (relative filter)
    score: float = 0.0  # absolute relevance in [0, 1]: fraction of the question's significant terms supported
    matched: list[str] = field(default_factory=list)
    excerpt_start: int = 0  # offsets of the focused excerpt within the chunk text
    excerpt_end: int = 0

    def focus(self, plan: "QueryPlan", size: int = 800) -> None:
        s, e = focus_window(self.text, plan, size)
        self.excerpt_start, self.excerpt_end = s, e
        self.text = self.text[s:e]


@dataclass
class QueryPlan:
    concepts: list[list[str]]
    codes: list[str]
    raw_terms: list[str]
    tsquery_en: str
    tsquery_simple: str
    # significant question tokens NOT covered by a concept (e.g. "civata", "8.8", "m10", "h7");
    # a passage must literally contain them to count as supporting them
    literals: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.concepts and not self.codes and not self.raw_terms


_ASCII_WORD = re.compile(r"^[a-z][a-z0-9\-]{2,}$")


def _ts_phrase(phrase: str) -> str | None:
    words = re.findall(r"[a-z0-9]+", phrase.lower())
    if not words:
        return None
    return " <-> ".join(words) if len(words) > 1 else words[0]


_TOKEN = re.compile(r"[^\W_]+(?:[.,]\d+)?", re.UNICODE)  # words, codes like m10/h7 and decimals like 8.8
_CODE_SPAN = re.compile(r"\biso\s?(?:/\s?tr\s?)?\d{2,5}(?:-\d{1,2})?(?::\d{4})?", re.IGNORECASE)


def literal_terms(question: str, mapped_spans: list[tuple[int, int]], concept_words: set[str]) -> list[str]:
    """Significant tokens of the question that no glossary concept covers."""
    blocked = list(mapped_spans) + [m.span() for m in _CODE_SPAN.finditer(question)]
    low = tr_lower(question)
    out: list[str] = []
    for m in _TOKEN.finditer(low):
        if any(not (m.end() <= s or m.start() >= e) for s, e in blocked):
            continue
        tok = m.group(0).replace(",", ".")
        if len(tok) < 2 and not tok.isdigit():
            continue
        if tok in QUESTION_WORDS or tok in TURKISH_STOPWORDS or tok in concept_words:
            continue
        if tok not in out:
            out.append(tok)
    return out


def plan_query(question: str, context: str | None = None) -> QueryPlan:
    text_all = question if not context else f"{question}\n{context}"
    concepts: list[list[str]] = []
    mapped, mapped_spans = map_turkish_spans(question)
    for _tr, en in mapped:
        concepts.append(en)
    codes = [m.group(1) for m in re.finditer(r"\biso\s?(?:/tr\s?)?(\d{2,5}(?:-\d{1,2})?)", ascii_lower(text_all))]
    low = ascii_lower(question)
    words = re.findall(r"[\wµ\-]+", low)
    raw_terms = []
    for w in words:
        if w in TURKISH_STOPWORDS or len(w) < 2:
            continue
        if _ASCII_WORD.match(w) or re.match(r"^it\d{1,2}$", w):
            raw_terms.append(w)
    # English words typed directly become concepts when not covered by the glossary
    covered = {w for c in concepts for p in c for w in p.split()}
    english_like = [w for w in raw_terms if w not in covered and not re.search(r"[çğıöşü]", w)
                    and re.match(r"^[a-z]+$", w) and w not in {"nedir", "hangi", "olarak"}]
    if not concepts:
        concepts = [[w] for w in english_like[:6]]
    if context and not concepts:
        for _tr, en in map_turkish_terms(context):
            concepts.append(en)
    en_parts = []
    for alts in concepts:
        for alt in alts:
            p = _ts_phrase(alt)
            if p:
                en_parts.append(f"({p})")
    for w in english_like:
        p = _ts_phrase(w)
        if p:
            en_parts.append(p)
    simple_parts = [re.sub(r"[^a-z0-9]", "", w) for w in raw_terms]
    simple_parts = [w for w in simple_parts if w]
    for c in codes:
        simple_parts.append(c.split("-")[0])
    concept_words = {w for c in concepts for p in c for w in p.lower().split()}
    return QueryPlan(concepts=concepts, codes=codes, raw_terms=raw_terms,
                     tsquery_en=" | ".join(dict.fromkeys(en_parts)),
                     tsquery_simple=" | ".join(dict.fromkeys(simple_parts)),
                     literals=literal_terms(question, mapped_spans, concept_words))


def _concept_regex(alt: str) -> re.Pattern:
    words = re.findall(r"[a-z0-9]+", alt.lower())
    return re.compile(r"\b" + r"\W+".join(re.escape(w) + r"\w*" for w in words), re.IGNORECASE)


def focus_window(text: str, plan: QueryPlan, size: int = 800) -> tuple[int, int]:
    """Return (start, end) of the excerpt window that covers the most query concepts.

    Sending focused excerpts (instead of whole chunks) keeps the local CPU
    prompt small; the exact boundaries are stored with the answer.
    """
    if len(text) <= size:
        return 0, len(text)
    hits: list[tuple[int, int]] = []
    for idx, alts in enumerate(plan.concepts):
        for alt in alts:
            hits += [(m.start(), idx) for m in _concept_regex(alt).finditer(text)]
    if not hits:
        return 0, size
    hits.sort()
    best, best_start = -1, 0
    for pos, _ in hits:
        start = max(0, min(pos - size // 4, len(text) - size))
        covered = {i for p, i in hits if start <= p < start + size}
        if len(covered) > best:
            best, best_start = len(covered), start
    start = best_start
    nl = text.rfind("\n", 0, start)
    if nl != -1 and start - nl < 120:
        start = nl + 1
    end = min(len(text), start + size)
    nl2 = text.find("\n", end)
    if nl2 != -1 and nl2 - end < 120:
        end = nl2
    return start, end


def _coverage(plan: QueryPlan, passage_text: str) -> tuple[float, list[str]]:
    if not plan.concepts:
        return 1.0, []
    matched = []
    for alts in plan.concepts:
        for alt in alts:
            if _concept_regex(alt).search(passage_text):
                matched.append(alt)
                break
    return len(matched) / len(plan.concepts), matched


_SEARCH_SQL = """
SELECT c.id, c.document_id, c.version_id, v.version_number, d.title, d.standard_code, c.page_number,
       c.locator, c.text, c.confidence_status,
       ts_rank_cd(c.tsv, q.query, 32) AS rank
FROM document_chunks c
JOIN document_versions v ON v.id = c.version_id
JOIN documents d ON d.id = c.document_id
JOIN knowledge_areas ka ON ka.id = d.knowledge_area_id,
     (SELECT {query_expr} AS query) q
WHERE c.tsv @@ q.query
  AND ka.is_verified_corpus = true
  AND d.status = 'active'
  AND v.is_active = true AND v.state = 'active' AND v.ingestion_status = 'indexed'
  AND c.confidence_status IN ('verified_source', 'user_confirmed')
  AND {scope}
ORDER BY rank DESC
LIMIT 60
"""


def term_coverage(plan: QueryPlan, passage_text: str, matched_concepts: int) -> float:
    """Absolute relevance: share of the question's significant terms the passage supports."""
    total = len(plan.concepts) + len(plan.literals)
    if total == 0:
        return 1.0
    numbers = numbers_in(passage_text)
    hits = matched_concepts
    for tok in plan.literals:
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            canon = (tok.rstrip("0").rstrip(".") if "." in tok else tok).lstrip("0") or "0"
            hits += canon in numbers
        elif re.search(r"(?<!\w)" + re.escape(tok), passage_text, re.IGNORECASE):
            hits += 1
        else:
            aliases = next((v for stem, v in SCORING_ALIASES.items() if tok.startswith(stem)), [])
            if any(re.search(rf"\b{re.escape(a)}", passage_text, re.IGNORECASE) for a in aliases):
                hits += 1
    return hits / total


def search(db: Session, scopes: ScopeSet, plan: QueryPlan, *, top_k: int = 5,
           min_coverage: float = 0.5) -> list[Passage]:
    if plan.empty:
        return []
    exprs, params = [], {}
    if plan.tsquery_en:
        exprs.append("to_tsquery('english', :q_en)")
        params["q_en"] = plan.tsquery_en
    if plan.tsquery_simple:
        exprs.append("to_tsquery('simple', :q_simple)")
        params["q_simple"] = plan.tsquery_simple
    if not exprs:
        return []
    scope_clause, scope_params = scopes.sql_filter("d")
    params.update(scope_params)
    sql = _SEARCH_SQL.format(query_expr=" || ".join(exprs), scope=scope_clause)
    rows = db.execute(text(sql), params).all()
    if not rows:
        return []
    max_rank = max(r.rank for r in rows) or 1.0
    passages: list[tuple[float, Passage]] = []
    for r in rows:
        cov, matched = _coverage(plan, r.text)
        p = Passage(chunk_id=r.id, document_id=r.document_id, version_id=r.version_id, version_number=r.version_number,
                    document_title=r.title, standard_code=r.standard_code, page_number=r.page_number,
                    locator=r.locator, text=r.text, confidence_status=r.confidence_status, rank=float(r.rank),
                    coverage=cov, matched=matched)
        p.score = round(term_coverage(plan, r.text, len(matched)), 4)
        order = p.score + 0.15 * (float(r.rank) / max_rank)
        if plan.codes and p.standard_code and any(re.search(rf"\b{re.escape(c)}(\b|:)", p.standard_code) for c in plan.codes):
            order += 0.25
        passages.append((order, p))
    n = len(plan.concepts)
    min_cov = 1.0 if n <= 1 else min_coverage
    kept = [(o, p) for o, p in passages if p.coverage >= min_cov - 1e-9]
    if plan.codes:
        # The user named specific standard(s): only passages of those standards may
        # answer. If none of them is in the active corpus, nothing can be verified.
        kept = [(o, p) for o, p in kept if p.standard_code and any(
            re.search(rf"\b{re.escape(c)}(\b|:)", p.standard_code) for c in plan.codes)]
    kept.sort(key=lambda t: t[0], reverse=True)
    return [p for _o, p in kept[:top_k]]
