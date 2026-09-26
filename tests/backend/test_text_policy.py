"""Unit tests: grounding validator, term annotation, intent classification, glossary."""
from __future__ import annotations

import pytest

from app.domain.enums import REFUSAL_PHRASE
from app.services.glossary import annotate_first_use, has_technical_terms, map_turkish_terms
from app.services.grounding import validate_answer
from app.services.intent import classify
from app.services.retrieval import plan_query

P = ["The pressure angle of the standard basic rack tooth profile is 20°. hfP 1,25 m"]


def test_grounded_answer_accepted_and_markers_stripped():
    g = validate_answer("Basınç açısı 20°'dir [S1]. Diş dibi 1,25·m [S1].", P, "soru")
    assert g.accepted and g.cited == [1]
    assert "[S1]" not in g.text and "20°" in g.text


def test_unsupported_number_rejected():
    g = validate_answer("Basınç açısı 25°'dir [S1].", P, "soru")
    assert not g.accepted and g.text == REFUSAL_PHRASE and g.unsupported_numbers == ["25"]


def test_numbers_from_user_question_are_allowed():
    g = validate_answer("Modül 3 mm için tablo değeri 20° olarak verilir [S1].", P, "modül 3 mm için?")
    assert g.accepted


def test_invalid_citation_and_refusal_normalization():
    assert validate_answer("x [S7]", P, "q").reason.startswith("invalid_citation")
    r = validate_answer("Maalesef bu kaynak setinde doğrulayamadım.", P, "q")
    assert r.text == REFUSAL_PHRASE and not r.accepted
    assert validate_answer("", P, "q").text == REFUSAL_PHRASE


def test_list_enumerators_are_not_treated_as_values():
    g = validate_answer("1. Basınç açısı 20° [S1]\n2. Diş dibi 1,25 m [S1]", P, "q")
    assert g.accepted


def test_first_use_english_annotation():
    out = annotate_first_use("Temel kremayer profilinde basınç açısı 20°. Basınç açısı değişmez.")
    assert out.startswith("Temel kremayer (basic rack)")
    assert out.count("(pressure angle)") == 1
    assert annotate_first_use("Adım adım açıklayalım.") == "Adım adım açıklayalım."
    already = "Basınç açısı (pressure angle) 20°."
    assert annotate_first_use(already) == already


@pytest.mark.parametrize("text,kind,sources", [
    ("Merhaba, nasılsın?", "general", False),
    ("kaynak ver", "sources_only", True),
    ("Kaynakları göster lütfen", "sources_only", True),
    ("ISO 53'e göre temel kremayer diş dibi yüksekliği nedir? kaynak ver", "technical", True),
    ("z=20, m=2 mm dişli geometrisini hesapla", "calculation", False),
    ("40 mm anma ölçüsü için IT6 kaç µm?", "calculation", False),
    ("hatırla: raporlarda birimleri mm yaz", "memory_command", False),
    ("öneri notu: OCR kuyruğuna toplu onay", "note_command", False),
    ("What is the zeta flange width?", "technical", False),
    ("Dişli mukavemet hesabını yap, tork 250 Nm", "calculation", False),
])
def test_intent_classification(text, kind, sources):
    it = classify(text)
    assert it.kind == kind
    assert it.wants_sources is sources


def test_turkish_glossary_with_suffixes_and_iso_codes():
    terms = dict(map_turkish_terms("Basınç açısını ve temel kremayerin diş dibi yüksekliğini öğrenmek istiyorum"))
    assert "basınç açısı" in terms and "temel kremayer" in terms and "diş dibi yüksekliği" in terms
    assert not map_turkish_terms("milimetre cinsinden")  # 'mil' (shaft) must not match 'milimetre'
    plan = plan_query("ISO 53 standart temel kremayer basınç açısı")
    assert plan.codes == ["53"]  # uppercase ISO is not folded to 'ıso'
    assert has_technical_terms("ISO 286 nedir") and not has_technical_terms("Paris'in başkenti neresidir?")
