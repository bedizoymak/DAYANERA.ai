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
    FOCUS_ALIASES,
    GREEK_NAMES,
    QUESTION_WORDS,
    SCORING_ALIASES,
    TURKISH_STOPWORDS,
    ascii_lower,
    map_turkish_spans,
    map_turkish_terms,
    tr_lower,
)
from app.services.grounding import numbers_in

# --- symbols, class codes and labels ------------------------------------------
# ISO PDF text layers often lose the Symbol font: "αP" is stored as "aP", "ρfP" as "rfP".
# The Symbol-font letter mapping below links both spellings in either direction.
_GREEK_TO_LATIN = dict(zip("αβγδεζηθικλμνξπρστυφχψω", "abgdezhqiklmnxprstufcyw"))
_LATIN_TO_GREEK = {v: k for k, v in _GREEK_TO_LATIN.items()}
_GREEK_NAME_OF = {g: n for n, g in GREEK_NAMES.items() if n != "alfa"}
_STOP_CODES = {"ISO", "DIN", "EN", "BS", "TR", "TS", "IEC", "PDF", "SI"}
_SYMBOL_TOKEN = re.compile(r"(?<![^\W_])[^\W_]{1,7}(?![^\W_])", re.UNICODE)
_LABEL_WORDS = {"class": "class", "sınıf": "class", "test": "test", "type": "type", "tip": "type", "grade": "grade",
                "table": "table", "tablo": "table", "figure": "figure", "şekil": "figure", "annex": "annex", "ek": "annex"}
_LABEL = re.compile(r"(?<!\w)(?i:(class|sınıf|test|type|tip|grade|table|tablo|figure|şekil|annex|ek))\s+"
                    r"([A-Z0-9][A-Za-z0-9]{0,3})(?![\w])", re.UNICODE)
_TR_SUFFIXES = sorted({"lerinin", "larının", "lerini", "larını", "lerin", "ların", "leri", "ları", "lere", "lara",
                       "ler", "lar", "nin", "nın", "ini", "ını", "in", "ın", "i", "ı", "e", "a", "de", "da", "yi",
                       "ye", "ya", "si", "sı"}, key=len, reverse=True)


def _is_greek(ch: str) -> bool:
    return ch.lower() in _GREEK_TO_LATIN


def extract_symbols(question: str) -> list[str]:
    """Short technical symbols and codes as typed: αP, haP, ρfP, β, FD, FE, 9B, 4A, H7.

    Ordinary words (sentence-case or lowercase) are not symbols; ISO/DIN prefixes and
    standard numbers are excluded.
    """
    blocked = [m.span() for m in _CODE_SPAN.finditer(question)]
    out: list[str] = []
    for m in _SYMBOL_TOKEN.finditer(question):
        if any(s <= m.start() < e for s, e in blocked):
            continue
        tok = m.group(0)
        if tok.upper() in _STOP_CODES or tok.isdigit():
            continue
        if tok.lower() in GREEK_NAMES:
            tok = GREEK_NAMES[tok.lower()]  # "beta" -> "β"
        elif not (
            _is_greek(tok[0])  # β, αP, ρfP
            or (re.fullmatch(r"[A-Za-z]{2,5}", tok) and re.search(r"[a-z]", tok) and re.search(r"[A-Z]", tok[1:]))  # aP, hfP
            or (re.fullmatch(r"[A-Z]{2,3}", tok))  # FD, FE, NB
            or re.fullmatch(r"\d{1,2}[A-Za-z]{1,2}|[A-Za-z]{1,2}\d{1,2}", tok)  # 9B, 4A, H7, M10, IT7
        ):
            continue
        if tok not in out:
            out.append(tok)
    return out


def symbol_variants(sym: str) -> list[str]:
    """Equivalent spellings of a symbol: αP <-> aP, ρfP <-> rfP, β -> beta."""
    out = [sym]
    first, rest = sym[0], sym[1:]
    if _is_greek(first):
        if rest:
            out.append(_GREEK_TO_LATIN[first.lower()] + rest)
        elif first.lower() in _GREEK_NAME_OF:
            out.append(_GREEK_NAME_OF[first.lower()])
    elif rest and first in _LATIN_TO_GREEK and re.search(r"[A-Z]", rest):  # subscript style: aP, rfP
        out.append(_LATIN_TO_GREEK[first] + rest)
    return list(dict.fromkeys(out))


def extract_labels(question: str) -> list[str]:
    """Labelled identifiers such as "Class FD", "Test 9B", "Type A", normalised to English keywords."""
    out = []
    for m in _LABEL.finditer(question):
        label = f"{_LABEL_WORDS[tr_lower(m.group(1))]} {m.group(2)}"
        if label not in out:
            out.append(label)
    return out


def _token_re(v: str, flags: int = re.IGNORECASE) -> re.Pattern:
    # whole token: no letter/digit before, no letter after ("FD" matches "FD" and "FD2", not "FDX" / "feed")
    return re.compile(r"(?<![^\W_])" + re.escape(v) + r"(?![^\W\d_])", flags | re.UNICODE)


def _number_re(tok: str) -> re.Pattern:
    body = r"[.,]".join(re.escape(p) for p in tok.split("."))
    return re.compile(r"(?<![\d.,])" + body + r"(?![\d]|[.,]\d)")


def _label_re(label: str) -> re.Pattern:
    kw, code = label.split(" ", 1)
    words = [w for w, en in _LABEL_WORDS.items() if en == kw]
    return re.compile(r"(?<!\w)(?i:" + "|".join(words) + r")\w*\s+" + re.escape(code) + r"(?![A-Za-z0-9])", re.UNICODE)


def _stem_candidates(tok: str) -> list[str]:
    """English stems with a Turkish suffix typed by the user ("hoblar" -> "hob", "testlerin" -> "test")."""
    if not re.fullmatch(r"[a-z]+", tok):
        return []
    return [tok[: -len(s)] for s in _TR_SUFFIXES if tok.endswith(s) and len(tok) - len(s) >= 3]


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
    # short symbols / codes as typed (αP, FD, 9B) and labelled identifiers ("class FD", "test 9B"):
    # searched lexically (exact token) next to the concept search
    symbols: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    _pattern_cache: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def empty(self) -> bool:
        return not self.concepts and not self.codes and not self.raw_terms and not self.symbols

    def literal_patterns(self, tok: str, focus: bool = False) -> list[re.Pattern]:
        """Patterns that count as a passage supporting literal ``tok`` (variants, aliases, stems).

        ``focus=True`` adds FOCUS_ALIASES, which only position the excerpt window and never
        take part in the relevance score.
        """
        if focus:
            extra = next((v for stem, v in FOCUS_ALIASES.items() if tok.startswith(stem)), [])
            return self.literal_patterns(tok) + [re.compile(rf"\b{re.escape(a)}", re.IGNORECASE) for a in extra]
        if tok in self._pattern_cache:
            return self._pattern_cache[tok]
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            pats = [_number_re(tok)]
        else:
            sym = next((s for s in self.symbols if s.lower() == tok), None)
            if sym is not None:
                variants = symbol_variants(sym)
            elif tok in GREEK_NAMES:
                variants = [tok, GREEK_NAMES[tok]]
            elif _is_greek(tok[0]) and len(tok) > 1:
                variants = [tok, _GREEK_TO_LATIN[tok[0]] + tok[1:]]
            else:
                variants = [tok]
            # codes and 2-letter tokens must match whole tokens ("fe" must not hit "feature")
            exact = sym is not None or len(tok) <= 2 or any(_is_greek(c) for c in tok)
            pats = [_token_re(v) if exact else re.compile(r"(?<!\w)" + re.escape(v), re.IGNORECASE) for v in variants]
            aliases = next((v for stem, v in SCORING_ALIASES.items() if tok.startswith(stem)), [])
            pats += [re.compile(rf"\b{re.escape(a)}", re.IGNORECASE) for a in aliases]
            pats += [re.compile(r"(?<!\w)" + re.escape(s), re.IGNORECASE) for s in _stem_candidates(tok)]
        self._pattern_cache[tok] = pats
        return pats


_ASCII_WORD = re.compile(r"^[a-z][a-z0-9\-]{2,}$")


def _ts_phrase(phrase: str) -> str | None:
    words = re.findall(r"[^\W_]+", phrase.lower())  # ASCII words, plus Greek symbol letters (β)
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
    english_like = [w for w in dict.fromkeys(raw_terms) if w not in covered and not re.search(r"[çğıöşü]", w)
                    and re.match(r"^[a-z]+$", w) and w not in {"nedir", "hangi", "olarak"}
                    and w not in QUESTION_WORDS]
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
    symbols = extract_symbols(question)
    for sym in symbols:  # exact lexical search for short codes the word filter above drops (FD, aP, 9B)
        simple_parts += [re.sub(r"[^\w]", "", v.lower()) for v in symbol_variants(sym)]
    simple_parts = [w for w in simple_parts if w]
    for c in codes:
        simple_parts.append(c.split("-")[0])
    concept_words = {w for c in concepts for p in c for w in p.lower().split()}
    return QueryPlan(concepts=concepts, codes=codes, raw_terms=raw_terms,
                     tsquery_en=" | ".join(dict.fromkeys(en_parts)),
                     tsquery_simple=" | ".join(dict.fromkeys(simple_parts)),
                     literals=literal_terms(question, mapped_spans, concept_words),
                     symbols=symbols, labels=extract_labels(question))


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
    # concept and label hits weigh 2, literal hits (numbers, symbols, plain words) 1: the window
    # lands where most distinct question terms meet, e.g. on the "5-, 6- and 7-threads, module
    # range ..." line of a page instead of its first "module" mention
    hits: list[tuple[int, tuple, int]] = []
    for idx, alts in enumerate(plan.concepts):
        for alt in alts:
            hits += [(m.start(), ("c", idx), 2) for m in _concept_regex(alt).finditer(text)]
    for tok in plan.literals:
        for pat in plan.literal_patterns(tok, focus=True):
            hits += [(m.start(), ("l", tok), 1) for m in pat.finditer(text)]
    for lab in plan.labels:
        hits += [(m.start(), ("b", lab), 2) for m in _label_re(lab).finditer(text)]
    if not hits:
        return 0, size
    hits.sort(key=lambda h: h[0])
    best, best_start = (-1, -1), 0
    for pos, _key, _w in hits:
        start = max(0, min(pos - size // 4, len(text) - size))
        inside = [(k, w) for p, k, w in hits if start <= p < start + size]
        # distinct terms first; on a tie the denser window wins (a list of "Grade 4A; Grade 3A; ...")
        score = (sum(dict(inside).values()), len(inside))
        if score > best:
            best, best_start = score, start
    start = best_start
    nl = text.rfind("\n", 0, start)
    if nl != -1 and start - nl < 120:
        start = nl + 1
    end = min(len(text), best_start + size)  # snapping the start to a line must not drop covered hits at the end
    nl2 = text.find("\n", end)
    if nl2 != -1 and nl2 - end < 120:
        end = nl2
    return start, _extend_table_rows(text, end)


# a table cell / row line of a one-token-per-line table: "2 < m ≤ 3,5", "13", "—", "Not applicable"
_ROW_LINE = re.compile(r"^(?:not\s+applicable|[^A-Za-z]*|[^A-Za-z]*\b[A-Za-z]{1,2}\b[^A-Za-z]*)$", re.IGNORECASE)


def _extend_table_rows(text: str, end: int, max_extra: int = 400) -> int:
    """Keep the rows of a table block together: continue the excerpt through directly
    following row-like lines (numbers, ranges, dashes) and stop at the next row label
    ("5-, 6-, 7-threads") or prose, so an answer never sees only the first row of a block."""
    limit = min(len(text), end + max_extra)
    pos = end
    while pos < limit:
        line_end = text.find("\n", pos + 1)
        line_end = len(text) if line_end == -1 else line_end
        line = text[pos:line_end].strip()
        if line and (len(line) > 24 or not _ROW_LINE.match(line)):
            break
        if line_end > limit:
            break
        pos = line_end
    return pos


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
  {code_filter}
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
        elif any(p.search(passage_text) for p in plan.literal_patterns(tok)):
            hits += 1  # the token, a symbol variant (αP ~ aP), a scoring alias or an English stem (hoblar ~ hob)
    return hits / total


def label_lines(plan: QueryPlan, passage_texts: list[str], limit: int = 8) -> list[tuple[int, str]]:
    """Verbatim lines that contain a labelled code of the question ("Class FD heavy tempering"),
    as (1-based passage number, line). Captions, keys and notes name what a code stands for."""
    out: list[tuple[int, str]] = []
    pats = [_label_re(lab) for lab in plan.labels]
    for i, body in enumerate(passage_texts, start=1):
        for raw in body.splitlines():
            line = raw.strip()
            if pats and any(p.search(line) for p in pats) and (i, line) not in out:
                out.append((i, line))
    return out[:limit]


def lexical_boost(plan: QueryPlan, passage_text: str) -> float:
    """Ranking bonus for exact occurrences of the typed symbols / labels ("Class FD", "αP").

    Ordering only: it never changes the relevance score used by the answer gate.
    """
    bonus = 0.0
    for sym in plan.symbols:
        if any(_token_re(v, 0).search(passage_text) for v in symbol_variants(sym)):
            bonus += 0.05
    for lab in plan.labels:
        if _label_re(lab).search(passage_text):
            bonus += 0.1
    return min(bonus, 0.3)


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
    code_filter = ""
    if plan.codes:
        # the user named standard(s): search only inside them so the LIMIT is not spent on other documents
        # (same rule as the Python filter below; codes are digits and '-' only)
        code_filter = "AND d.standard_code ~* :code_re"
        params["code_re"] = r"\m(" + "|".join(dict.fromkeys(plan.codes)) + r")(\M|:)"
    sql = _SEARCH_SQL.format(query_expr=" || ".join(exprs), scope=scope_clause, code_filter=code_filter)
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
        order = p.score + 0.15 * (float(r.rank) / max_rank) + lexical_boost(plan, r.text)
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
