"""Deterministic message classification (testable, no LLM)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.calc.parse import ParsedCalc, parse_calculation
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


@dataclass
class Intent:
    kind: str  # sources_only | memory_command | note_command | calculation | technical | general
    wants_sources: bool = False
    wants_detail: bool = False
    memory_text: str | None = None
    note_text: str | None = None
    calc: ParsedCalc | None = None
    question: str = ""


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
    question = SOURCES_RE.sub(" ", text) if wants_sources else text
    question = re.sub(r"\s+", " ", question).strip(" ,.;:!?")
    if wants_sources:
        rest = [w for w in re.findall(r"[\wçğıöşü]+", tr_lower(question)) if w not in _FILLER]
        if len(rest) < 3:
            return Intent("sources_only", wants_sources=True, question=question)
    calc = parse_calculation(question)
    technical = has_technical_terms(question)
    if calc.calc_type and (calc.has_calc_verb or "=" in question):
        return Intent("calculation", wants_sources, wants_detail, calc=calc, question=question)
    if calc.has_calc_verb and technical and re.search(r"\d", question):
        return Intent("calculation", wants_sources, wants_detail, calc=calc, question=question)
    if technical:
        return Intent("technical", wants_sources, wants_detail, calc=calc, question=question)
    return Intent("general", wants_sources, wants_detail, question=question)
