"""Retrieval over the active verified corpus (PostgreSQL full-text search).

Only chunks that are (a) in a verified-corpus knowledge area, (b) from an
active document, (c) of the active, successfully indexed version that an
owner approved (``corpus_status = 'verified'``) and (d) in ``verified_source``
/ ``user_confirmed`` state are eligible as evidence. Deleted, superseded,
failed, unapproved and draft-extraction data can never be cited.

Stages (Haystack-style joiner, implemented natively on PostgreSQL):
  1. query plan: ISO codes, concepts, symbols, clause numbers, equation labels
  2. exact channel: symbols / clause numbers / equation labels as exact lexemes
  3. lexical channel: tsvector + GIN (english + simple), heading-weighted
  4. metadata filters: verified corpus, lifecycle, scope, requested standard
  5. reciprocal rank fusion of the channels
  6. deterministic rerank: term coverage, exact-token and metadata boosts
A dense (vector) channel is deliberately absent: see docs/RAG_INGESTION.md.
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


_TR_LABEL_AFTER = re.compile(r"\s+(?:tip|sınıf|seri|temel\s+sapma|harf|profil|type|class|series)\w*",
                             re.IGNORECASE | re.UNICODE)


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
            # Turkish order puts the identifier first: "H temel sapmalı", "D tipi", "A sınıfı"
            or (re.fullmatch(r"[A-Z]", tok) and _TR_LABEL_AFTER.match(question, m.end()))
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
    # lineage and diagnostics
    clause: str | None = None
    heading: str | None = None
    content_type: str = "text"
    channels: dict = field(default_factory=dict)  # channel -> 1-based rank
    fused: float = 0.0  # reciprocal-rank-fusion score
    boosts: dict = field(default_factory=dict)

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
    # clause numbers ("4.2.4", "A.1") and equation numbers ("1" for "Eşitlik (1)") named in the question
    clauses: list[str] = field(default_factory=list)
    equations: list[str] = field(default_factory=list)
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
                     symbols=symbols, labels=extract_labels(question),
                     clauses=extract_clauses(question), equations=extract_equations(question))


# "madde 5.9", "clause 4.2", "Ek A.1" or a bare multi-level number "4.2.4" (never "0.25" or "1.25":
# single-dot numbers are values unless a clause keyword precedes them)
_CLAUSE_Q = re.compile(r"(?i:\b(?:madde|maddesi|clause|subclause|bölüm|kısım|ek|annex)\s*)"
                       r"(\d{1,2}(?:\.\d{1,2}){0,4}|[A-Z](?:\.\d{1,2}){0,3})(?![\w.])"
                       r"|(?<![\w.,])(\d{1,2}(?:\.\d{1,2}){2,4})(?![\w.])", re.UNICODE)
_EQUATION_Q = re.compile(r"(?i:\b(?:eşitlik|eşitliğ\w*|denklem\w*|equation|formula|formül\w*)\s*)"
                         r"\(?\s*((?:[A-Z]\.)?\d{1,3}[a-z]?)\s*\)?", re.UNICODE)


def extract_clauses(question: str) -> list[str]:
    q = _CODE_SPAN.sub(" ", question)  # "ISO 286-2:2010" is not a clause
    out = []
    for m in _CLAUSE_Q.finditer(q):
        c = m.group(1) or m.group(2)
        if c and len(c) == 1 and c.isalpha():
            c = f"Annex {c}"
        if c and c not in out:
            out.append(c)
    return out


def extract_equations(question: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in _EQUATION_Q.finditer(question)))


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


# Evidence eligibility (metadata filter), shared by every channel and by the calculation engine's
# evidence resolver: verified-corpus area, active document, active indexed version APPROVED by an owner.
ELIGIBLE_VERSION_SQL = """ka.is_verified_corpus = true
  AND d.status = 'active'
  AND v.is_active = true AND v.state = 'active' AND v.ingestion_status = 'indexed'
  AND v.corpus_status = 'verified'"""

_COLUMNS = """c.id, c.document_id, c.version_id, v.version_number, d.title, d.standard_code, c.page_number,
       c.locator, c.text, c.confidence_status, c.clause, c.heading, c.content_type"""

_SEARCH_SQL = """
SELECT {columns},
       ts_rank_cd(c.tsv, q.query, 32) AS rank
FROM document_chunks c
JOIN document_versions v ON v.id = c.version_id
JOIN documents d ON d.id = c.document_id
JOIN knowledge_areas ka ON ka.id = d.knowledge_area_id,
     (SELECT {query_expr} AS query) q
WHERE c.tsv @@ q.query
  AND {eligible}
  AND c.confidence_status IN ('verified_source', 'user_confirmed')
  AND {scope}
  {code_filter}
ORDER BY rank DESC
LIMIT 60
"""

# exact channel: engineering symbols / clause numbers as exact 'simple' lexemes, the clause column,
# and numbered equations inside formula chunks
_EXACT_SQL = """
SELECT {columns},
       (CASE WHEN c.clause = ANY(CAST(:clauses AS text[])) THEN 1.0 ELSE 0.0 END
        + CASE WHEN {eq_cond} THEN 1.0 ELSE 0.0 END
        + {exact_rank}) AS rank
FROM document_chunks c
JOIN document_versions v ON v.id = c.version_id
JOIN documents d ON d.id = c.document_id
JOIN knowledge_areas ka ON ka.id = d.knowledge_area_id
WHERE ({exact_match} c.clause = ANY(CAST(:clauses AS text[])) OR {eq_cond})
  AND {eligible}
  AND c.confidence_status IN ('verified_source', 'user_confirmed')
  AND {scope}
  {code_filter}
ORDER BY rank DESC
LIMIT 40
"""
# symbol definitions from the SAME standard's symbol tables ("| αP | Pressure angle | degrees |")
_DEFINITION_SQL = """
SELECT c.document_id, c.text
FROM document_chunks c
JOIN document_versions v ON v.id = c.version_id
JOIN documents d ON d.id = c.document_id
JOIN knowledge_areas ka ON ka.id = d.knowledge_area_id
WHERE c.document_id = ANY(CAST(:docs AS uuid[])) AND c.content_type = 'table'
  AND {eligible}
  AND c.confidence_status IN ('verified_source', 'user_confirmed')
"""
RRF_K = 60  # reciprocal rank fusion constant (Cormack et al. 2009; Haystack's DocumentJoiner default)
STRONG_SUPPORT = 0.6  # absolute term support that keeps a candidate although one concept is missing


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


def _exact_terms(plan: QueryPlan) -> str:
    terms = [re.sub(r"[^\w.]", "", v.lower()) for s in plan.symbols for v in symbol_variants(s)]
    terms += [c.lower() for c in plan.clauses if not c.startswith("Annex")]
    return " | ".join(dict.fromkeys(t for t in terms if t and not t.endswith(".")))


# A formula chunk IS the equation the question names ("Eşitlik (2) formülü"); a table chunk IS the table.
# These concepts are satisfied by the chunk's structure (content_type) rather than by a word in its text.
_STRUCTURAL_CONCEPTS = {"formula": {"formula", "equation"}, "table": {"table"}}


def structural_matches(plan: QueryPlan, content_type: str) -> list[str]:
    kinds = _STRUCTURAL_CONCEPTS.get(content_type)
    if not kinds:
        return []
    return [alts[0] for alts in plan.concepts if set(a.lower() for a in alts) <= kinds]


def symbol_definitions(db: Session, plan: QueryPlan, doc_ids: set) -> dict:
    """{document_id: {symbol: description}} from the verified symbol tables of those documents."""
    wanted = {v for s in plan.symbols for v in symbol_variants(s)}
    out: dict = {}
    if not wanted or not doc_ids:
        return out
    rows = db.execute(text(_DEFINITION_SQL.format(eligible=ELIGIBLE_VERSION_SQL)), {"docs": list(doc_ids)}).all()
    for doc_id, body in rows:
        for ln in body.splitlines():
            if not ln.startswith("|"):
                continue
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[0] in wanted and re.search(r"[A-Za-z]{3,}", cells[1]):
                out.setdefault(doc_id, {})[cells[0]] = cells[1]
    return out


def resolve_symbol_concepts(plan: QueryPlan, passage_text: str, definitions: dict[str, str],
                            matched: list[str]) -> list[str]:
    """Concepts a passage carries through a symbol its own standard defines: a Table 2 row "| αP | 20° |"
    speaks about the pressure angle because Table 1 of that standard defines αP as "Pressure angle"."""
    extra = []
    for sym, desc in definitions.items():
        if not _token_re(sym, 0).search(passage_text):
            continue
        for alts in plan.concepts:
            if any(a in matched or a in extra for a in alts):
                continue
            hit = next((a for a in alts if _concept_regex(a).search(desc)), None)
            if hit:
                extra.append(hit)
    return extra


def symbol_row(plan: QueryPlan, text: str) -> bool:
    """A table row keyed by a symbol of the question ("| ρfP | 0,38 m |")."""
    wanted = {v for s in plan.symbols for v in symbol_variants(s)}
    return any(ln.strip().strip("|").split("|")[0].strip() in wanted
               for ln in text.splitlines() if ln.startswith("|"))


_HEADING_LINE = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2}){0,5}|[A-Z](?:\.\d{1,2}){1,5})\s+([^\n]{2,80})$", re.M)


def _strip_clause(heading: str) -> str:
    return re.sub(r"^(?:\d{1,2}(?:\.\d{1,2}){0,5}|[a-z](?:\.\d{1,2}){1,5}|annex [a-z])\s+", "", heading).strip()


def question_phrases(plan: QueryPlan) -> list[str]:
    """English concept alternatives and adjacent English literal pairs, lowercased ("reference diameter")."""
    out = [a.lower() for alts in plan.concepts for a in alts]
    lits = [t for t in plan.literals if re.fullmatch(r"[a-z]{3,}", t)]
    out += [f"{a} {b}" for a, b in zip(lits, lits[1:])]
    return list(dict.fromkeys(out))


# ordering-only preferences by intent subtype (app/services/intent.py): the chunk kind that holds the answer
_SUBTYPE_KIND = {"standards_formula_lookup": "formula", "standards_range_lookup": "table",
                 "standards_value_lookup": "table"}


def metadata_boosts(plan: QueryPlan, p: Passage, subtype: str | None) -> dict[str, float]:
    b: dict[str, float] = {}
    lex = lexical_boost(plan, p.text)
    if lex:
        b["exact_tokens"] = lex
    if plan.codes and p.standard_code and any(re.search(rf"\b{re.escape(c)}(\b|:)", p.standard_code)
                                              for c in plan.codes):
        b["standard_code"] = 0.25
    if p.clause and any(p.clause == c or p.clause.startswith(c + ".") for c in plan.clauses):
        b["clause"] = 0.2
    if plan.equations and p.content_type == "formula" and any(f"({e})" in p.text for e in plan.equations):
        b["equation"] = 0.2
    if subtype and _SUBTYPE_KIND.get(subtype) == p.content_type:
        b["content_type"] = 0.1 if subtype != "standards_value_lookup" else 0.05
    if p.content_type == "table" and plan.symbols and symbol_row(plan, p.text):
        b["symbol_row"] = 0.15
    if structural_matches(plan, p.content_type):
        b["requested_structure"] = 0.2  # the question asks for a table / formula and this chunk is one
    heading = (p.heading or "").lower()
    phrases = question_phrases(plan)
    if heading and any(ph in heading for ph in phrases if " " in ph or len(ph) >= 5):
        b["heading_phrase"] = 0.1  # "reference diameter" in "4.2.4 Reference cylinder, ..., reference diameter"
    titles = {m.group(1).strip().lower() for m in _HEADING_LINE.finditer(p.text)} | {_strip_clause(heading)}
    if titles & set(phrases):
        b["defines_term"] = 0.2  # the clause titled exactly as asked ("5.5 Backlash" for "backlash nedir?")
    if p.content_type == "front_matter":
        b["front_matter"] = -0.2  # contents / foreword mention every term but define none
    return b


def search(db: Session, scopes: ScopeSet, plan: QueryPlan, *, top_k: int = 5,
           min_coverage: float = 0.5, subtype: str | None = None) -> list[Passage]:
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
    fmt = {"columns": _COLUMNS, "eligible": ELIGIBLE_VERSION_SQL, "scope": scope_clause, "code_filter": code_filter}
    channels: dict[str, list] = {"lexical": db.execute(text(_SEARCH_SQL.format(
        query_expr=" || ".join(exprs), **fmt)), params).all()}
    q_exact = _exact_terms(plan)
    if q_exact or plan.clauses or plan.equations:
        exact_params = {**params, "clauses": list(plan.clauses), "q_exact": q_exact,
                        "eq_re": r"\((" + "|".join(re.escape(e) for e in plan.equations) + r")\)"}
        channels["exact"] = db.execute(text(_EXACT_SQL.format(
            exact_rank="ts_rank(c.tsv, to_tsquery('simple', :q_exact))" if q_exact else "0.0",
            exact_match="c.tsv @@ to_tsquery('simple', :q_exact) OR" if q_exact else "",
            eq_cond="(c.content_type = 'formula' AND c.text ~ :eq_re)" if plan.equations else "FALSE",
            **fmt)), exact_params).all()
    # reciprocal rank fusion: every channel votes 1 / (k + rank)
    rows: dict = {}
    ranks: dict = {}
    for name, result in channels.items():
        for i, r in enumerate(result, start=1):
            rows.setdefault(r.id, r)
            ranks.setdefault(r.id, {})[name] = i
    if not rows:
        return []
    fused = {cid: sum(1.0 / (RRF_K + rk) for rk in ch.values()) for cid, ch in ranks.items()}
    lo, hi = min(fused.values()), max(fused.values())
    definitions = symbol_definitions(db, plan, {r.document_id for r in rows.values()}) if plan.concepts else {}
    passages: list[tuple[float, Passage]] = []
    for cid, r in rows.items():
        # the clause heading is part of the chunk's context (lineage; weight A in the tsvector): Table 2 of
        # ISO 53 speaks about the "tooth profile" of clause 5 even though its caption does not say so
        context = f"{r.heading}\n{r.text}" if r.heading else r.text
        cov, matched = _coverage(plan, context)
        extra = [m for m in structural_matches(plan, r.content_type or "text") if m not in matched]
        extra += resolve_symbol_concepts(plan, r.text, definitions.get(r.document_id, {}), matched + extra)
        if extra and plan.concepts:
            matched = matched + extra
            cov = len(matched) / len(plan.concepts)
        p = Passage(chunk_id=r.id, document_id=r.document_id, version_id=r.version_id, version_number=r.version_number,
                    document_title=r.title, standard_code=r.standard_code, page_number=r.page_number,
                    locator=r.locator, text=r.text, confidence_status=r.confidence_status, rank=float(r.rank),
                    coverage=cov, matched=matched, clause=r.clause, heading=r.heading,
                    content_type=r.content_type or "text", channels=ranks[cid], fused=round(fused[cid], 6))
        p.score = round(term_coverage(plan, context, len(matched)), 4)
        p.boosts = metadata_boosts(plan, p, subtype)
        fused_norm = (fused[cid] - lo) / (hi - lo) if hi > lo else 1.0
        order = p.score + 0.15 * fused_norm + sum(p.boosts.values())
        passages.append((order, p))
    n = len(plan.concepts)
    min_cov = 1.0 if n <= 1 else min_coverage
    # a clause-sized chunk may lack a context concept that sits in the neighbouring clause of the same page
    # ("helical gear" beside 4.2.4): strong support of the question's terms keeps it a candidate
    kept = [(o, p) for o, p in passages if p.coverage >= min_cov - 1e-9 or p.score >= STRONG_SUPPORT]
    if plan.codes:
        # The user named specific standard(s): only passages of those standards may
        # answer. If none of them is in the active corpus, nothing can be verified.
        kept = [(o, p) for o, p in kept if p.standard_code and any(
            re.search(rf"\b{re.escape(c)}(\b|:)", p.standard_code) for c in plan.codes)]
    kept.sort(key=lambda t: t[0], reverse=True)
    return [p for _o, p in kept[:top_k]]
