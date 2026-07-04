"""
AKGYS niyet (intent) eşleştirme — saf mantık, ağır bağımlılık yok.

İki katmanlı eşleştirme:
  1) TAM eşleşme (kelime sınırlı regex)  -> confidence = "high"  (doğrudan uygula)
  2) BULANIK eşleşme (difflib benzerliği) -> confidence = "medium" (önce "bunu mu
     demek istediniz?" diye sor). Yanlış/eksik duyulan sözcükleri en yakın komuta
     eşler ("levitason" -> levitasyon, "fren yap" -> acil durdurma).

Yalnızca standart kütüphane (`re`, `difflib`) kullanır; böylece ses/model
bağımlılıkları olmadan birim testi yazılabilir (bkz. test_intent.py).
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

# Uyandırma sözcükleri (Whisper yazımı sapabildiği için varyantlar dahil)
WAKE_WORDS = ["spectraloop", "spektraloop", "spectra loop", "spectra", "asistan", "sistem"]

# Onay / ret sözcükleri
YES_WORDS = ["evet", "onayla", "onaylıyorum", "onaylıyor", "tamam", "olur", "başlat", "devam et", "devam", "doğru", "aynen"]
NO_WORDS  = ["hayır", "iptal", "vazgeç", "yapma", "olmaz", "yanlış", "değil"]

# Bulanık eşleşme eşiği: bunun üstü "bunu mu demek istediniz?" ile teyit edilir.
# 0.78 gerçek yanlış duyumları (~0.82-1.0) yakalar, alakasız cümleleri (~<=0.73) eler.
FUZZY_CONFIRM = 0.78
_FUZZY_MIN_KW_LEN = 4   # bu uzunluktan kısa anahtar sözcüklerde bulanık eşleşme yapılmaz


@dataclass
class Intent:
    kind: str               # "scenario" | "query" | "chat"
    scenario: str = None
    response: str = None
    critical: bool = False
    label: str = None       # "bunu mu demek istediniz: <label>?" için kısa ad
    confidence: str = "high"  # "high" (tam) | "medium" (bulanık)
    score: float = 1.0


# Her niyet: anahtar sözcükler (eş anlamlılar + olası yanlış duyumlar dahil).
# Sıra = TAM eşleşmede öncelik.
INTENT_DEFS = [
    {
        "key": "acildurak", "label": "Acil durdurma", "critical": True,
        "response": "Acil durdurma başlatılıyor!",
        "keywords": ["acil dur", "acil durdur", "acilen dur", "hemen dur", "derhal dur",
                     "durdur", "dur", "fren", "frenle", "freni bas", "fren yap",
                     "yavaşla dur", "stop", "tren dursun", "aracı durdur"],
    },
    {
        "key": "tahliye", "label": "Tahliye", "critical": True,
        "response": "Tahliye simülasyonu başlatılıyor.",
        "keywords": ["tahliye", "boşalt", "yolcuları çıkar", "yolcuları tahliye",
                     "kapıları aç", "tahliye et"],
    },
    {
        "key": "z1", "label": "Z1 çoklu arıza", "critical": True,
        "response": "Z1 çoklu arıza simülasyonu başlatılıyor.",
        "keywords": ["z1", "çoklu arıza", "tüm arıza", "kritik arıza", "hepsi",
                     "toplu arıza", "genel arıza"],
    },
    {
        "key": "bms", "label": "F1 BMS ısınma", "critical": False,
        "response": "F1 BMS ısınma simülasyonu başlatılıyor.",
        "keywords": ["f1", "bms", "ısınma", "isinma", "termal", "batarya", "akü",
                     "pil ısınma", "batarya ısınma", "aşırı ısınma"],
    },
    {
        "key": "levitasyon", "label": "F2 levitasyon sapması", "critical": False,
        "response": "F2 levitasyon sapması simülasyonu başlatılıyor.",
        "keywords": ["f2", "levitasyon", "levitason", "levitasion", "kaldırma",
                     "sapma", "manyetik", "havada kalma", "kaldırma kuvveti"],
    },
    {
        "key": "navigasyon", "label": "F3 navigasyon arızası", "critical": False,
        "response": "F3 navigasyon arızası simülasyonu başlatılıyor.",
        "keywords": ["f3", "navigasyon", "navigasion", "enkoder", "konumlama",
                     "gps", "yön", "rota arıza", "konum arıza"],
    },
    {
        "key": "basinc", "label": "Basınç düşüşü", "critical": False,
        "response": "Basınç düşüşü simülasyonu başlatılıyor.",
        "keywords": ["basınç", "basinc", "kabin basınc", "basınç düşüş",
                     "hava basıncı", "vakum"],
    },
    {
        "key": "oksijen", "label": "Oksijen maskesi", "critical": False,
        "response": "Oksijen maskesi simülasyonu başlatılıyor.",
        "keywords": ["oksijen", "maske", "nefes", "hava kalites", "havasız",
                     "oksijen düşüş"],
    },
    {
        "key": "sarsinti", "label": "Sarsıntı", "critical": False,
        "response": "Sarsıntı simülasyonu başlatılıyor.",
        "keywords": ["sarsıntı", "sarsinti", "titreşim", "titresim", "deprem",
                     "sallanma", "vibrasyon"],
    },
    {
        "key": "normal", "label": "Normal mod", "critical": False,
        "response": "Normal moda geçiliyor.",
        "keywords": ["normal", "normale al", "normale dön", "sıfırla", "sifirla",
                     "başa dön", "resetle", "eski hal", "iptal et hepsini",
                     "her şeyi durdur normal"],
    },
]

# Durum sorgusu — belirsiz sözcükler ("nasıl", "söyle") bilerek dışarıda.
_QUERY_RE = re.compile(
    r"durum|risk|\bhız\b|\bhiz\b|hangi\s*istasyon|nerede(yiz)?|kaçıncı|"
    r"bilgi\s*ver|rapor|ne\s*kadar\s*kaldı|varış|sıcaklık|basınç\s*ne|kaç\s*derece",
    re.IGNORECASE,
)

_WAKE_RE = re.compile(
    "|".join(re.escape(w) for w in sorted(WAKE_WORDS, key=len, reverse=True)),
    re.IGNORECASE,
)


# ── Yardımcılar ────────────────────────────────────────────────────
def _norm(text: str) -> str:
    """Küçük harf + noktalama temizliği + boşluk sadeleştirme."""
    t = (text or "").lower()
    t = re.sub(r"[^\wçğıöşü\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _has_phrase(text: str, kw: str) -> bool:
    return re.search(r"\b" + re.escape(kw) + r"\b", text) is not None


def _ngrams(tokens, n):
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def _fuzzy_score(text: str, keywords) -> tuple:
    """Metin ile anahtar sözcükler arasındaki en iyi bulanık benzerlik (0..1)."""
    tokens = text.split()
    candidates = tokens + _ngrams(tokens, 2) + _ngrams(tokens, 3)
    best, best_kw = 0.0, None
    for kw in keywords:
        if len(kw) < _FUZZY_MIN_KW_LEN:
            continue  # çok kısa sözcüklerde bulanık eşleşme güvensiz
        for cand in candidates:
            r = SequenceMatcher(None, cand, kw).ratio()
            if r > best:
                best, best_kw = r, kw
    return best, best_kw


# ── Genel API ─────────────────────────────────────────────────────
def has_wake_word(text: str) -> bool:
    return bool(_WAKE_RE.search(text or ""))


def strip_wake_words(text: str) -> str:
    """Uyandırma sözcüklerini temizler ('Spectraloop acil dur' -> 'acil dur')."""
    cleaned = _WAKE_RE.sub(" ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" ,.!?;:").strip()


def match_intent(text: str) -> Intent:
    """
    Metni bir Intent'e eşler.
      1) Tam eşleşme  -> confidence "high"
      2) Durum sorgusu (bulanıklıktan önce, "durum"/"durdur" karışmasın diye)
      3) Bulanık eşleşme -> confidence "medium"
      4) Aksi halde sohbet
    """
    t = _norm(text)
    if not t:
        return Intent("chat")

    # 1) Tam (kelime sınırlı) eşleşme
    for d in INTENT_DEFS:
        for kw in d["keywords"]:
            if _has_phrase(t, kw):
                return Intent("scenario", d["key"], d["response"], d["critical"],
                              d["label"], "high", 1.0)

    # 2) Durum sorgusu (bulanık taramadan ÖNCE)
    if _QUERY_RE.search(t):
        return Intent("query", confidence="high")

    # 3) Bulanık eşleşme — en yakın komut
    best_score, best_def = 0.0, None
    for d in INTENT_DEFS:
        score, _ = _fuzzy_score(t, d["keywords"])
        if score > best_score:
            best_score, best_def = score, d
    if best_def and best_score >= FUZZY_CONFIRM:
        return Intent("scenario", best_def["key"], best_def["response"],
                      best_def["critical"], best_def["label"], "medium",
                      round(best_score, 2))

    # 4) Serbest sohbet
    return Intent("chat")


def is_affirmative(text: str) -> bool:
    """Onay sözcüğü var mı? Ret sözcüğü varsa her zaman False."""
    t = _norm(text)
    if any(w in t for w in NO_WORDS):
        return False
    return any(w in t for w in YES_WORDS)
