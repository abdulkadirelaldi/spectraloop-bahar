"""
AKGYS niyet (intent) eşleştirme — saf mantık, ağır bağımlılık yok.

Bu modül bilerek yalnızca `re` kullanır; böylece ses/model bağımlılıkları
kurulu olmadan birim testi yazılabilir (bkz. test_intent.py).
"""

import re
from dataclasses import dataclass

# Uyandırma sözcükleri (Whisper yazımı sapabildiği için varyantlar dahil)
WAKE_WORDS = ["spectraloop", "spektraloop", "spectra loop", "spectra", "asistan", "sistem"]

# Onay / ret sözcükleri (kritik komut doğrulaması için)
YES_WORDS = ["evet", "onayla", "onaylıyorum", "onaylıyor", "tamam", "olur", "başlat", "devam et", "devam"]
NO_WORDS  = ["hayır", "iptal", "vazgeç", "yapma", "olmaz", "dur"]


@dataclass
class Intent:
    kind: str              # "scenario" | "query" | "chat"
    scenario: str = None   # kind == "scenario" ise senaryo anahtarı
    response: str = None    # seslendirilecek onay cümlesi
    critical: bool = False  # True ise yürütmeden önce sesli onay gerekir


# (regex, senaryo, yanıt, kritik) — LİSTE SIRASI = ÖNCELİK.
# Kritik komutlar (acil durdurma, tahliye, çoklu arıza) yürütülmeden önce
# sesli onay ister; bu yüzden "durdur" gibi geniş kalıplar güvenlidir.
COMMAND_TABLE = [
    (r"acil\s*dur|hemen\s*dur|derhal\s*dur|\bdurdur\b|\bdur\b",       "acildurak",  "Acil durdurma başlatılıyor!",                     True),
    (r"tahliye|boşalt|yolcu(ları)?\s*çıkar",                          "tahliye",    "Tahliye simülasyonu başlatılıyor.",               True),
    (r"\bz1\b|çoklu\s*arıza|tüm\s*arıza|kritik\s*arıza|hepsi",        "z1",         "Z1 çoklu arıza simülasyonu başlatılıyor.",        True),
    (r"\bf1\b|\bbms\b|ısınma|termal|batarya|akü",                     "bms",        "F1 BMS ısınma simülasyonu başlatılıyor.",         False),
    (r"\bf2\b|levitasyon|kaldırma|sapma|manyetik",                    "levitasyon", "F2 levitasyon sapması simülasyonu başlatılıyor.", False),
    (r"\bf3\b|navigasyon|enkoder|konumlama|gps",                      "navigasyon", "F3 navigasyon arızası simülasyonu başlatılıyor.", False),
    (r"basınç|basinc|kabin\s*basınc",                                 "basinc",     "Basınç düşüşü simülasyonu başlatılıyor.",         False),
    (r"oksijen|maske|nefes|hava\s*kalites",                           "oksijen",    "Oksijen maskesi simülasyonu başlatılıyor.",       False),
    (r"sarsıntı|sarsinti|titreşim|deprem",                            "sarsinti",   "Sarsıntı simülasyonu başlatılıyor.",              False),
    (r"normal|sıfırla|sifirla|başa\s*dön|resetle|eski\s*hal",        "normal",     "Normal moda geçiliyor.",                          False),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), s, r, c) for p, s, r, c in COMMAND_TABLE]

# Durum sorgusu — belirsiz sözcükler ("nasıl", "söyle") bilerek dışarıda.
_QUERY_RE = re.compile(
    r"durum|risk|\bhız\b|\bhiz\b|hangi\s*istasyon|nerede(yiz)?|kaçıncı|"
    r"bilgi\s*ver|rapor|ne\s*kadar\s*kaldı|varış|sıcaklık|basınç\s*ne",
    re.IGNORECASE,
)

_WAKE_RE = re.compile("|".join(re.escape(w) for w in sorted(WAKE_WORDS, key=len, reverse=True)), re.IGNORECASE)


def has_wake_word(text: str) -> bool:
    return bool(_WAKE_RE.search(text or ""))


def strip_wake_words(text: str) -> str:
    """Uyandırma sözcüklerini metinden temizler ('Spectraloop acil dur' -> 'acil dur')."""
    cleaned = _WAKE_RE.sub(" ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" ,.!?;:").strip()


def match_intent(text: str) -> Intent:
    """Metni bir Intent'e eşler. Sıra öncelik belirler; ilk eşleşen kazanır."""
    t = (text or "").lower().strip()
    if not t:
        return Intent("chat")
    for rx, scenario, response, critical in _COMPILED:
        if rx.search(t):
            return Intent("scenario", scenario, response, critical)
    if _QUERY_RE.search(t):
        return Intent("query")
    return Intent("chat")


def is_affirmative(text: str) -> bool:
    """Onay sözcüğü var mı? Ret sözcüğü varsa her zaman False."""
    t = (text or "").lower()
    if any(w in t for w in NO_WORDS):
        return False
    return any(w in t for w in YES_WORDS)
