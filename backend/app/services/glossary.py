"""Turkish → English engineering glossary for cross-lingual retrieval.

The ISO corpus is English while users ask in Turkish. Retrieval therefore
maps Turkish terms (with suffix tolerance) to English search phrases. The
same lexicon drives the technical-question classifier.
"""
from __future__ import annotations

import re

# Turkish stem phrase -> English search phrases
GLOSSARY: dict[str, list[str]] = {
    "dişli çifti": ["gear pair"],
    "düz dişli": ["spur gear"],
    "helis dişli": ["helical gear"],
    "helisel dişli": ["helical gear"],
    "silindirik dişli": ["cylindrical gear"],
    "iç dişli": ["internal gear"],
    "dış dişli": ["external gear"],
    "sonsuz vida": ["worm"],
    "sonsuz vida dişlisi": ["worm gear", "wormgear"],
    "dişli": ["gear"],
    "pinyon": ["pinion"],
    "çark": ["wheel"],
    "evolvent": ["involute"],
    "modül": ["module"],
    "modul": ["module"],
    "normal modül": ["normal module"],
    "alın modülü": ["transverse module"],
    "diş sayısı": ["number of teeth"],
    "diş sayı": ["number of teeth"],
    "basınç açısı": ["pressure angle"],
    "kavrama açısı": ["pressure angle"],
    "helis açısı": ["helix angle"],
    "temel kremayer": ["basic rack"],
    "kremayer": ["rack", "basic rack"],
    "referans profil": ["basic rack tooth profile"],
    "referans çap": ["reference diameter"],
    "bölüm dairesi": ["reference circle", "reference diameter"],
    "temel daire": ["base circle", "base diameter"],
    "temel çap": ["base diameter"],
    "diş başı çapı": ["tip diameter"],
    "diş dibi çapı": ["root diameter"],
    "diş başı yüksekliği": ["addendum"],
    "diş dibi yüksekliği": ["dedendum"],
    "diş başı": ["addendum", "tip"],
    "diş dibi": ["dedendum", "root"],
    "diş yüksekliği": ["tooth depth"],
    "diş kalınlığı": ["tooth thickness"],
    "diş boşluğu": ["space width"],
    "diş genişliği": ["facewidth", "face width"],
    "diş profili": ["tooth profile"],
    "dip boşluğu": ["bottom clearance", "clearance"],
    "taban yarıçapı": ["fillet radius"],
    "dip yuvarlatma": ["fillet radius", "root fillet"],
    "profil kaydırma": ["profile shift"],
    "eksen mesafesi": ["centre distance"],
    "merkez mesafesi": ["centre distance"],
    "dişli oranı": ["gear ratio"],
    "çevrim oranı": ["transmission ratio"],
    "kavrama oranı": ["contact ratio"],
    "adım": ["pitch"],
    "temel adım": ["base pitch"],
    "tek adım": ["single pitch"],
    "kümülatif adım": ["cumulative pitch"],
    "adım sapması": ["pitch deviation"],
    "profil sapması": ["profile deviation"],
    "helis sapması": ["helix deviation"],
    "salgı": ["runout"],
    "sapma": ["deviation"],
    "tolerans sınıfı": ["tolerance class", "flank tolerance class"],
    "kalite sınıfı": ["tolerance class", "accuracy grade"],
    "tolerans": ["tolerance"],
    "yanak": ["flank"],
    "diş yanağı": ["tooth flank", "flank"],
    "profil": ["profile"],
    "helis": ["helix"],
    "eğim": ["slope"],
    "form": ["form"],
    "toplam": ["total"],
    "azdırma freze": ["hob", "gear hob"],
    "azdırma": ["hob", "hobbing"],
    "freze": ["cutter", "hob"],
    "kama yuvası": ["keyway"],
    "taşlama": ["grinding"],
    "taşlama yanığı": ["grinding temper", "temper etch"],
    "temper dağlama": ["temper etch"],
    "dağlama": ["etch", "etching"],
    "nital": ["nital"],
    "yüzey": ["surface"],
    "sertlik": ["hardness"],
    "muayene": ["inspection"],
    "ölçüm": ["measurement", "measuring"],
    "ölçme": ["measurement", "measuring"],
    "kontrol": ["inspection", "checking"],
    "doğruluk": ["accuracy"],
    "hassasiyet": ["accuracy"],
    "standart tolerans": ["standard tolerance"],
    "tolerans derecesi": ["standard tolerance grade"],
    "temel sapma": ["fundamental deviation"],
    "anma ölçüsü": ["nominal size"],
    "geçme": ["fit"],
    "alıştırma": ["fit"],
    "delik": ["hole"],
    "mil": ["shaft"],
    "sınır sapma": ["limit deviation"],
    "geometrik tolerans": ["geometrical tolerance", "geometrical tolerancing"],
    "konum toleransı": ["position tolerance"],
    "düzlemsellik": ["flatness"],
    "silindiriklik": ["cylindricity"],
    "diklik": ["perpendicularity"],
    "paralellik": ["parallelism"],
    "eş eksenlilik": ["coaxiality"],
    "datum": ["datum"],
    "referans": ["datum", "reference"],
    "malzeme": ["material"],
    "ısıl işlem": ["heat treatment"],
    "karbürizasyon": ["carburizing", "carburization"],
    "sementasyon": ["carburizing", "case carburizing"],
    "yüzey pürüzlülüğü": ["surface roughness"],
    "pürüzlülük": ["roughness"],
    "gerilme": ["stress"],
    "diş dibi gerilmesi": ["tooth root stress"],
    "güvenlik katsayısı": ["safety factor"],
    "emniyet katsayısı": ["safety factor"],
    "katsayı": ["factor", "coefficient"],
    "mukavemet": ["strength", "load capacity"],
    "taşıma kapasitesi": ["load capacity"],
    "tork": ["torque"],
    "moment": ["torque", "moment"],
    "yük": ["load"],
    "güç": ["power"],
    "devir": ["speed"],
    "ömür": ["life"],
    "yorulma": ["fatigue"],
    "çukurlaşma": ["pitting"],
    "aşınma": ["wear"],
    "yağlama": ["lubrication"],
    "yağ": ["oil", "lubricant"],
    "rulman": ["bearing"],
    "yatak": ["bearing"],
    "flanş": ["flange"],
    "gürültü": ["noise"],
    "titreşim": ["vibration"],
    "verim": ["efficiency"],
    "sıcaklık": ["temperature"],
    "çap": ["diameter"],
    "genişlik": ["width"],
    "kalınlık": ["thickness"],
    "yükseklik": ["height", "depth"],
    "açı": ["angle"],
    "boşluk": ["backlash", "clearance"],
    "mastar": ["gauge"],
    "tanım": ["definition"],
    "sembol": ["symbol"],
    "tablo": ["table"],
    "formül": ["formula", "equation"],
    "eşitlik": ["equation"],
    "kapsam": ["scope"],
    "uygulama alanı": ["scope", "range of application"],
}

TECH_EXTRA_TERMS = [
    "iso", "din", "standart", "tolerans", "dişli", "modül", "gear", "module", "tolerance", "hob", "azdırma",
    "helis", "helix", "profil", "profile", "mm", "µm", "mikron", "evolvent", "involute", "kremayer", "rack",
    "pinyon", "pinion", "taşlama", "grinding", "ölçüm", "muayene", "inspection", "gps", "it0", "it1", "it2",
    "it3", "it4", "it5", "it6", "it7", "it8", "it9", "it10", "it11", "it12", "geçme", "fit", "sapma", "deviation",
]

TURKISH_STOPWORDS = {
    "ve", "veya", "ile", "için", "icin", "bir", "bu", "şu", "su", "o", "ne", "nedir", "neden", "nasıl", "nasil",
    "kaç", "kac", "mi", "mı", "mu", "mü", "midir", "mıdır", "hangi", "hangisi", "olan", "olarak", "gibi", "göre",
    "gore", "de", "da", "ki", "ise", "değeri", "degeri", "değer", "deger", "ver", "verir", "misin", "lütfen",
    "lutfen", "bana", "bunu", "şunu", "var", "yok", "en", "çok", "cok", "daha", "kadar", "hakkında", "hakkinda",
    "açıkla", "acikla", "anlat", "söyle", "soyle", "nelerdir", "neler", "kaynak", "kaynağı", "kaynakları",
    "standardı", "standardında", "standartta", "standart", "the", "a", "an", "of", "for", "and", "or", "in", "on",
    "is", "are", "what", "how", "which", "to", "with", "by", "be", "does", "do", "iso",
}

_TR_FOLD = str.maketrans({"İ": "i", "I": "ı"})


def tr_lower(s: str) -> str:
    return s.translate(_TR_FOLD).lower()


def _stem_regex(phrase: str) -> re.Pattern:
    """Suffix-tolerant Turkish matching ("açısı" also matches "açısını").

    Short stems (<= 4 letters, e.g. "mil") accept at most four suffix letters
    and must end at a word boundary so that "mil" does not match "milimetre".
    """
    words = phrase.split()
    parts = []
    for w in words:
        if len(w) <= 4:
            parts.append(re.escape(w) + r"\w{0,4}(?!\w)")
        else:
            parts.append(re.escape(w[:-1] if w[-1] in "ıiuü" else w) + r"\w*")
    return re.compile(r"(?<!\w)" + r"\s+".join(parts), re.UNICODE)


_GLOSSARY_RE = sorted(((k, _stem_regex(k), v) for k, v in GLOSSARY.items()), key=lambda t: -len(t[0]))


def map_turkish_terms(text: str) -> list[tuple[str, list[str]]]:
    """Return matched (turkish_phrase, english_phrases), longest phrases first, non-overlapping."""
    low = tr_lower(text)
    taken: list[tuple[int, int]] = []
    out = []
    for key, rx, en in _GLOSSARY_RE:
        for m in rx.finditer(low):
            if any(not (m.end() <= s or m.start() >= e) for s, e in taken):
                continue
            taken.append((m.start(), m.end()))
            out.append((key, en))
            break
    return out


def ascii_lower(s: str) -> str:
    """Lowercase for Latin acronyms and codes ("ISO", "IT7"): plain str.lower().

    Turkish folding would turn "ISO" into "ıso"; use tr_lower() for Turkish
    words and this function for standard codes and English terms.
    """
    return s.lower()


# Turkish entries too generic to annotate ("adım adım" is "step by step", not "pitch")
_GENERIC_TR = {"adım", "tablo", "formül", "eşitlik", "tanım", "sembol", "kapsam", "uygulama alanı", "kontrol", "form",
               "toplam", "referans", "yüzey", "malzeme", "ölçüm", "ölçme", "yük", "güç", "açı", "çap", "boşluk", "yağ",
               "genişlik", "kalınlık", "yükseklik", "katsayı", "devir", "ömür", "moment", "sapma", "doğruluk",
               "hassasiyet", "dişli", "profil", "helis", "eğim", "freze", "delik", "mil", "datum"}


def annotate_first_use(text: str, max_terms: int = 8) -> str:
    """Add the English term in parentheses at the FIRST use of each specialist
    Turkish term when the model did not already provide it (e.g.
    "basınç açısı" -> "basınç açısı (pressure angle)"). Repeated uses are left as is."""
    low = tr_lower(text)
    if len(low) != len(text):  # defensive: index mapping requires equal length
        return text
    taken: list[tuple[int, int]] = []
    inserts: list[tuple[int, str]] = []
    for key, rx, en in _GLOSSARY_RE:
        if key in _GENERIC_TR or len(inserts) >= max_terms:
            continue
        m = rx.search(low)
        if not m or any(not (m.end() <= s or m.start() >= e) for s, e in taken):
            continue
        taken.append((m.start(), m.end()))
        english = en[0]
        if english.lower() in text.lower():
            continue
        end = m.end()
        # extend to the end of the word (Turkish suffixes)
        while end < len(text) and (text[end].isalnum() or text[end] in "'’"):
            end += 1
        if text[end:end + 2].lstrip().startswith("("):
            continue
        inserts.append((end, f" ({english})"))
    for pos, ins in sorted(inserts, reverse=True):
        text = text[:pos] + ins + text[pos:]
    return text


_GENERIC_EN = {"of", "and", "the", "number", "total", "form", "table", "class", "fit", "definition", "symbol", "scope",
               "range", "application", "reference", "control", "checking", "datum", "equation", "formula", "power",
               "life", "speed", "moment", "height", "oil", "noise", "surface", "material", "test", "factor"}
ENGLISH_TECH_WORDS = {w for alts in GLOSSARY.values() for p in alts for w in p.split()} - _GENERIC_EN


def has_technical_terms(text: str) -> bool:
    if map_turkish_terms(text):
        return True
    low = ascii_lower(text)
    if re.search(r"\biso\s?(?:/tr\s?)?\d{2,5}", low) or re.search(r"\bit\s?\d{1,2}\b", low):
        return True
    words = set(re.findall(r"[\wµ]+", low)) | set(re.findall(r"[\wµ]+", tr_lower(text)))
    return bool(words & (set(TECH_EXTRA_TERMS) | ENGLISH_TECH_WORDS))
