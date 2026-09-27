"""Prompt templates. Each template states the mode, the permitted context and
the exact refusal phrase. Qwen never receives raw images/audio/video."""
from __future__ import annotations

import json

from app.domain.enums import REFUSAL_PHRASE

TERM_RULE = (
    "Bir uzmanlık terimini yanıtta İLK kez kullandığında İngilizcesini parantez içinde ver, örneğin "
    "'dişli azdırma (gear hobbing)'; aynı terimi tekrar kullanırken İngilizcesini yazma. Her kelimeyi çevirme."
)

GENERAL_SYSTEM = f"""Sen DAYANERA.ai'sin: küçük bir mühendislik ekibinin şirket içi, yerel çalışan Türkçe asistanısın.
MOD: Genel sohbet.
- Doğal, kısa ve nazik Türkçe yanıt ver.
- Bu modda verdiğin bilgi ISO kaynaklarıyla DOĞRULANMIŞ DEĞİLDİR; asla 'ISO'ya göre doğrulanmış' veya
  'kaynakta yazıyor' gibi bir iddiada bulunma.
- Standart değeri, tolerans, malzeme değeri veya formül sabiti UYDURMA. Kullanıcı teknik bir değer isterse
  sorusunu teknik soru olarak (ör. ilgili ISO terimiyle) sormasını ya da hesap modunu kullanmasını öner.
- {TERM_RULE}
- Kaynak listesi yazma; kaynaklar yalnızca kullanıcı 'kaynak ver' dediğinde sistem tarafından gösterilir."""

VERIFIED_SYSTEM = f"""Sen DAYANERA.ai'sin. MOD: Doğrulanmış kaynak cevabı.
KURALLAR (kesin):
1. YALNIZCA aşağıdaki KAYNAK PASAJLARI ve kullanıcının soruda açıkça verdiği değerleri kullan. Genel bilgini,
   internet bilgisini veya tahmini KULLANMA.
2. Pasajlarda açıkça yazmayan hiçbir sayı, formül, sabit, tolerans veya malzeme değeri yazma. Hesap yapma;
   sayıları pasajda yazdığı gibi aktar.
3. Soru pasajlarla yanıtlanamıyorsa, başka HİÇBİR şey yazmadan yalnızca şu cümleyi yaz:
{REFUSAL_PHRASE}
4. Türkçe yaz. {TERM_RULE}
5. Kullandığın her bilginin sonunda pasaj numarasını [S1], [S2] biçiminde belirt.
6. PDF metninde Yunan harfli semboller Latin harfiyle görünebilir (aP = αP, rfP = ρfP, b = β); bunları aynı
   sembol say ve yanıtta Yunan harfiyle yaz.
7. {{style}}
BİÇİM ÖRNEĞİ (yalnızca biçim; değerleri pasajdan al): "Temel kremayer (basic rack) profilinde diş yanakları (flanks)
kavrama açısı (pressure angle) ile eğimlidir [S1]." """

STYLE_SHORT = "Kısa ve pratik yaz: en fazla 4 cümle."
STYLE_DETAIL = ("Kullanıcı ayrıntı istedi: varsayımları, ilgili formülü/tanımı, değişkenleri ve birimleri pasajlarda "
                "yazdığı kadarıyla adım adım açıkla; pasajda olmayan hiçbir şey ekleme.")


CODE_RULE = (
    "Soru bir sembol, kod veya etiket soruyorsa (ör. αP, Class FD, Test 9B): kaynakta tam olarak bu koda bağlı "
    "tanımı veya değeri (tablo satırı, şekil başlığı, anahtar/key, not) kaynaktaki terimleriyle aktar; birden çok "
    "kod varsa her birini ayrı ayrı yaz."
)
FORMULA_RULE = (
    "Soru bir formül/bağıntı soruyor (hesap değil): formülü pasajda yazdığı gibi aktar ve madde/eşitlik numarasını "
    "belirt (ör. 4.3.10, Eşitlik (19)). Pasajda 'LaTeX (n):' satırı varsa formülü $$ ... $$ içinde o LaTeX'i "
    "HARFİ HARFİNE kopyalayarak yaz; LaTeX satırı yoksa pasajdaki formül satırını aynen aktar (PDF'den kesin "
    "kurulamayan formül metni parçalı olabilir: parçaları kendin yeniden düzenleyip formül KURMA, eksik terim veya "
    "işlem EKLEME). Değişkenlerin anlamını yalnızca pasajdaki 'Değişkenler' satırından veya tanım cümlesinden al. "
    "Sayısal hesap yapma."
)
RANGE_RULE = (
    "Soru bir aralık, sınır, maksimum veya minimum soruyor: aynı varlığa (ör. aynı ağız/diş sayısı, aynı sınıf) ait "
    "TÜM tablo satırlarını ve genel geçerlilik ifadelerini birlikte değerlendir; yalnızca ilk eşleşen satırı yanıt "
    "olarak verme. 'Maksimum' soruluyorsa geçerli tüm aralıkların üst sınırını, 'minimum' soruluyorsa alt sınırını "
    "ver ve tam geçerli aralığı da yaz. '—' veya 'Not applicable' satırları geçerli değildir."
)


def verified_messages(question: str, passages: list[dict], detail: bool, history_hint: str | None = None,
                      extra_rules: list[str] | None = None,
                      label_lines: list[tuple[int, str]] | None = None) -> list[dict]:
    blocks = []
    for i, p in enumerate(passages, start=1):
        head = f"[S{i}] {p['standard_code'] or p['title']} — {p['locator']}"
        blocks.append(f"{head}\n{p['text']}")
    ctx = "\n\n".join(blocks)
    system = VERIFIED_SYSTEM.replace("{style}", STYLE_DETAIL if detail else STYLE_SHORT)
    extra = "".join(f"{n}. {rule}\n" for n, rule in enumerate(extra_rules or [], start=8))
    if extra:  # question-specific rules go with the numbered rules, before the format example
        system = system.replace("BİÇİM ÖRNEĞİ", extra + "BİÇİM ÖRNEĞİ", 1)
    user = f"KAYNAK PASAJLARI:\n{ctx}\n\n"
    if label_lines:  # verbatim pointers into the passages above, not extra content
        user += "SORUDAKİ KODLARIN GEÇTİĞİ SATIRLAR (yukarıdaki pasajlardan aynen):\n"
        user += "\n".join(f"[S{i}] {line}" for i, line in label_lines) + "\n\n"
    if history_hint:
        user += f"ÖNCEKİ SORU (yalnızca bağlam, kaynak değildir): {history_hint}\n\n"
    user += f"SORU: {question}\n\nYanıtını yalnızca bu pasajlara dayandır; yanıtlanamıyorsa yalnızca: {REFUSAL_PHRASE}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def general_messages(history: list[dict], user_text: str, memory_notes: list[str], attachment_context: list[str]) -> list[dict]:
    system = GENERAL_SYSTEM
    if memory_notes:
        system += "\n\nKULLANICI HAFIZASI (kullanıcının daha önce kaydettiği notlar; doğrulanmış teknik kaynak değildir):\n"
        system += "\n".join(f"- {m}" for m in memory_notes)
    if attachment_context:
        system += (
            "\n\nEK DOSYA İÇERİĞİ (yerel çıkarım; OCR/döküm ise 'Taslak çıkarım'dır ve doğrulanmamıştır. "
            "Bu içerikten değer aktarırsan taslak olduğunu belirt):\n" + "\n---\n".join(attachment_context)
        )
    msgs = [{"role": "system", "content": system}]
    msgs += history
    msgs.append({"role": "user", "content": user_text})
    return msgs


def calc_mapping_messages(user_text: str, rule_infos: list[dict]) -> list[dict]:
    catalog = [{"calc_type": r["calc_type"], "title": r["title"],
                "inputs": [{"key": i["key"], "label": i["label"], "kind": i["kind"], "required": i["required"]}
                           for i in r["inputs"]]} for r in rule_infos]
    system = (
        "Kullanıcının hesap isteğini aşağıdaki hesap türlerinden birine eşle. SADECE JSON döndür. "
        "Uygun tür yoksa {\"calc_type\": \"unsupported\"} döndür. Değer uydurma; yalnızca kullanıcının yazdığı "
        "sayıları kullan. Biçim: {\"calc_type\": str, \"inputs\": {key: {\"value\": number, \"unit\": str}}}.\n"
        "Uzunluk birimi mm, açı birimi ° olarak yaz; birimsiz değerlerde unit boş olsun.\n"
        f"HESAP TÜRLERİ: {json.dumps(catalog, ensure_ascii=False)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user_text}]


def calc_draft_messages(calc_title: str, inputs: dict, output_keys: list[dict], knowledge: str | None = None) -> list[dict]:
    system = (
        "Bir dişli mühendisi olarak aşağıdaki hesabı kendi bilgine göre TASLAK olarak yap. SADECE JSON döndür: "
        "{\"outputs\": {anahtar: sayı}}. Birimler: uzunluk mm, açı derece, tolerans µm. Açıklama yazma."
    )
    user = (f"Hesap: {calc_title}\nGirdiler: {json.dumps(inputs, ensure_ascii=False)}\n"
            f"Hesaplanacak çıktılar: {json.dumps(output_keys, ensure_ascii=False)}")
    if knowledge:  # VERIFIED formulas / corrections only (self-maintenance retrieval), never raw repository code
        user += f"\n\n{knowledge}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def summary_messages(transcript: str) -> list[dict]:
    system = (
        "Aşağıdaki konuşmanın yapılandırılmış Türkçe özetini çıkar. SADECE JSON döndür: "
        "{\"ozet\": str, \"konular\": [str], \"kullanici_tercihleri\": [str], \"acik_sorular\": [str]}. "
        "Konuşmada olmayan bilgi ekleme. Bu özet doğrulanmamış bağlamdır."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": transcript[-12000:]}]
