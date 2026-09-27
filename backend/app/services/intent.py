"""Deterministic message classification (testable, no LLM)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.calc.parse import TABLE_LOOKUPS, ParsedCalc, parse_calculation
from app.services.glossary import has_technical_terms, tr_lower
from app.services.memory import parse_memory_command

SOURCES_RE = re.compile(
    r"(kaynak(?:ları|lar|ı|ını|larını)?\s*(?:ver|göster|belirt|yaz|paylaş|nedir|listele)\w*|"
    r"hangi\s+kaynak\w*|kaynağ\w+\s+(?:ver|göster)\w*|referans(?:ları|ı)?\s*(?:ver|göster)\w*|"
    r"\bcite\b|\bsources?\b|\bcitation\w*)",
    re.IGNORECASE,
)
DETAIL_RE = re.compile(r"(ayrıntılı|detaylı|adım\s+adım|ayrıntı|detay\s+ver|açıklamalı|step\s+by\s+step|in\s+detail)",
                       re.IGNORECASE)
NOTE_CMD = re.compile(r"^\s*(?:öneri\s+notu|geliştirme\s+önerisi|öneri\s+kaydet|öneri\s+notu\s+oluştur)\s*[:,\-–]\s*(.+)$",
                      re.IGNORECASE | re.DOTALL)
_FILLER = {"lütfen", "lutfen", "bana", "şimdi", "simdi", "da", "de", "bir", "önceki", "onceki", "cevabın", "cevabin",
           "yanıtın", "yanitin", "bunun", "için", "icin", "misin", "mısın", "please", "the", "for", "that", "bu", "şu",
           "cevap", "yanıt", "ve"}

# --- corpus inventory ("which standards do you have?") -------------------
_DOC = r"(?:iso\s+)?(?:standart|standard|norm|kaynak|doküman|döküman|dokuman|belge)\w*"
_DOC_NO_SOURCE = r"(?:iso\s+)?(?:standart|standard|norm|doküman|döküman|dokuman|belge)\w*"
INVENTORY_PATTERNS = [
    re.compile(p, re.UNICODE) for p in (
        rf"\bhangi\s+{_DOC}\s+(?:var|yüklü|mevcut|bulunuyor|bulunur|kayıtlı|elinde|sende|sistemde|biliyorsun|tanıyorsun)",
        rf"\belinde(?:ki)?\s+(?:hangi\s+|ne\s+|neler\s+)?{_DOC}",
        rf"\b(?:sende|sistemde|korpusta|korpusunda)\s+(?:hangi|ne|neler)\s+{_DOC}",
        r"\bneleri\s+biliyorsun",
        r"\bkaynak\s+set\w*\s+(?:ne|neler|hangi)\w*",
        rf"\b{_DOC_NO_SOURCE}\s+listele\w*",
        rf"\blistele\w*\s+{_DOC_NO_SOURCE}",
        rf"\byüklü\s+(?:olan\s+)?{_DOC_NO_SOURCE}",
        rf"\b{_DOC_NO_SOURCE}\s+(?:neler|nelerdir)\b",
        r"\bwhich\s+(?:iso\s+)?(?:standards|documents|norms|sources)\s+(?:do\s+you\s+have|are\s+(?:loaded|available|indexed))",
        r"\blist\s+(?:the\s+|all\s+|your\s+)?(?:iso\s+)?(?:standards|documents|sources)\b",
        r"\bwhat\s+(?:iso\s+)?(?:standards|documents)\s+(?:do\s+you\s+have|are\s+(?:loaded|available))",
    )
]
_ISO_CODE = re.compile(r"\biso\s*(?:/\s*tr\s*)?\d")


def is_inventory_question(text: str) -> bool:
    """True for questions about which documents/standards are loaded.

    Never true when a concrete technical question is present: the message must
    not contain an ISO code or any digit outside the matched inventory phrase.
    """
    low = tr_lower(text).replace("ıso", "iso")
    spans = [m.span() for p in INVENTORY_PATTERNS for m in p.finditer(low)]
    if not spans or _ISO_CODE.search(low):
        return False
    rest = list(low)
    for s, e in spans:
        rest[s:e] = [" "] * (e - s)
    return not re.search(r"\d", "".join(rest))


# --- lookup vs. numeric calculation ------------------------------------------
# References to a standard or to a part of it ("ISO 53:1998", "Table 2", "Eşitlik (1)",
# "Test 9B") carry digits that are NOT calculation inputs.
_STANDARD_REF = re.compile(
    r"\b(?:ISO|DIN|(?-i:EN)|BS)(?:\s*/\s*T[RS])?\s*\d{2,5}(?:-\d{1,2})?(?::\s*\d{4})?"  # not Turkish "en 20 mm"
    r"|\b(?:table|tablo|figure|şekil|equation|eşitlik|denklem|formula|formül|clause|madde|"
    r"test|grade|class|sınıf|type|tip|note|not)\b\s*\(?\s*\d+(?:\.\d+)*[A-Za-z]?\s*\)?",
    re.IGNORECASE | re.UNICODE)
# "how is X calculated / what is the formula": the relation is asked, not a number
FORMULA_RE = re.compile(
    r"(nasıl\s+(?:hesaplan|bulun|belirlen|elde\s+edil|tanımlan|ifade\s+edil)\w*"
    r"|formül\w*|bağıntı\w*|denklem\w*|eşitliğ\w*|eşitlik\w*"
    r"|\bhow\s+(?:is|are|do\s+you|to)\b.*\b(?:calculat|determin|comput|defin)\w*"
    r"|\b(?:formula|equation|relation(?:ship)?)s?\b)",
    re.IGNORECASE | re.UNICODE)
# "maksimum / minimum / aralık / range": answer from ALL rows of the entity, not the first one
RANGE_RE = re.compile(
    r"(maksimum|minimum|\bmaks\.?\b|\bmax(?:imum)?\b|\bmin(?:imum)?\b|en\s+(?:büyük|yüksek|küçük|düşük)"
    r"|aralı[kğ]\w*|\brange\w*|üst\s+sınır\w*|alt\s+sınır\w*|sınır\s+değer\w*|\blimits?\b)",
    re.IGNORECASE | re.UNICODE)


def has_parameter_digits(text: str) -> bool:
    """True when a digit remains after removing standard / table / figure references."""
    return bool(re.search(r"\d", _STANDARD_REF.sub(" ", text)))


@dataclass
class Intent:
    kind: str  # sources_only | memory_command | note_command | corpus_inventory | calculation | technical | general
    wants_sources: bool = False
    wants_detail: bool = False
    memory_text: str | None = None
    note_text: str | None = None
    calc: ParsedCalc | None = None
    question: str = ""
    # routing detail for calculation / technical messages:
    # numeric_calculation | standards_formula_lookup | standards_range_lookup | standards_value_lookup
    subtype: str | None = None


def _lookup_subtype(question: str) -> str:
    if FORMULA_RE.search(question):
        return "standards_formula_lookup"
    if RANGE_RE.search(question):
        return "standards_range_lookup"
    return "standards_value_lookup"


def classify(message: str) -> Intent:
    text = message.strip()
    wants_sources = bool(SOURCES_RE.search(text))
    wants_detail = bool(DETAIL_RE.search(text))
    mem = parse_memory_command(text)
    if mem:
        return Intent("memory_command", memory_text=mem, question=text)
    note = NOTE_CMD.match(text)
    if note:
        return Intent("note_command", note_text=note.group(1).strip(), question=text)
    if is_inventory_question(text):
        return Intent("corpus_inventory", question=text)
    question = SOURCES_RE.sub(" ", text) if wants_sources else text
    question = re.sub(r"\s+", " ", question).strip(" ,.;:!?")
    if wants_sources:
        rest = [w for w in re.findall(r"[\wçğıöşü]+", tr_lower(question)) if w not in _FILLER]
        if len(rest) < 3:
            return Intent("sources_only", wants_sources=True, question=question)
    calc = parse_calculation(question)
    technical = has_technical_terms(question)
    if calc.calc_type and (calc.has_calc_verb or "=" in question or calc.calc_type in TABLE_LOOKUPS):
        return Intent("calculation", wants_sources, wants_detail, calc=calc, question=question,
                      subtype="numeric_calculation")
    # A formula question without any numeric input ("d nasıl hesaplanır?") is a source lookup:
    # it must never reach the engine's input validation / range checks.
    formula_only = bool(FORMULA_RE.search(question)) and not calc.inputs
    if calc.has_calc_verb and technical and not formula_only and has_parameter_digits(question):
        return Intent("calculation", wants_sources, wants_detail, calc=calc, question=question,
                      subtype="numeric_calculation")
    if technical:
        return Intent("technical", wants_sources, wants_detail, calc=calc, question=question,
                      subtype=_lookup_subtype(question))
    return Intent("general", wants_sources, wants_detail, question=question)
