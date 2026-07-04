"""
AKGYS niyet eşleştirme birim testleri.

Çalıştırma:  cd mac_assistant && python3 -m pytest -q
(ses/model bağımlılıkları GEREKMEZ — sadece intent.py test edilir.)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from intent import (
    match_intent, has_wake_word, strip_wake_words, is_affirmative,
)


# ── Senaryo yönlendirmesi ─────────────────────────────────────────
def test_emergency_is_critical():
    it = match_intent("acil durdur")
    assert it.kind == "scenario"
    assert it.scenario == "acildurak"
    assert it.critical is True


def test_bare_durdur_routes_to_emergency_but_critical():
    it = match_intent("hemen durdur")
    assert it.scenario == "acildurak"
    assert it.critical is True  # onay istenecek → yanlış tetik güvenli


def test_bms_scenario():
    assert match_intent("f1 bms ısınmasını başlat").scenario == "bms"
    assert match_intent("batarya termal test").scenario == "bms"


def test_levitasyon_scenario():
    assert match_intent("levitasyon sapması").scenario == "levitasyon"


def test_normal_reset():
    it = match_intent("sistemi normale al sıfırla")
    assert it.scenario == "normal"
    assert it.critical is False


def test_tahliye_critical():
    it = match_intent("yolcuları tahliye et")
    assert it.scenario == "tahliye"
    assert it.critical is True


def test_z1_multi_fault_critical():
    assert match_intent("z1 çoklu arıza").critical is True


# ── Durum sorgusu vs. sohbet ──────────────────────────────────────
def test_query_routing():
    assert match_intent("şu anki risk durumu ne").kind == "query"
    assert match_intent("hız kaç").kind == "query"
    assert match_intent("varışa ne kadar kaldı").kind == "query"


def test_general_chat_not_query():
    # 'nasıl' artık query'e kaymamalı
    assert match_intent("bugün hava nasıl").kind == "chat"
    assert match_intent("bana bir fıkra anlat").kind == "chat"


# ── Uyandırma sözcüğü ─────────────────────────────────────────────
def test_wake_word_detection():
    assert has_wake_word("spectraloop acil durdur")
    assert has_wake_word("asistan durum ne")
    assert not has_wake_word("hava durumu nasıl")


def test_strip_wake_words():
    assert strip_wake_words("spectraloop acil durdur") == "acil durdur"
    assert strip_wake_words("asistan, risk durumu ne") == "risk durumu ne"
    assert strip_wake_words("spectraloop") == ""


# ── Bulanık (fuzzy) eşleşme — yanlış/eksik duyulan sözcükler ──────
def test_fren_maps_to_emergency():
    it = match_intent("fren yap")
    assert it.kind == "scenario"
    assert it.scenario == "acildurak"


def test_misheard_levitasyon():
    # Listede olmayan bir yanlış duyum → bulanık eşleşme, teyit istenir
    it = match_intent("levitesyon baslat")
    assert it.scenario == "levitasyon"
    assert it.confidence == "medium"


def test_misheard_navigasyon():
    assert match_intent("navigasion arizasi").scenario == "navigasyon"


def test_misheard_sarsinti():
    assert match_intent("titresim var").scenario == "sarsinti"


def test_query_not_confused_with_durdur():
    # 'durum' bulanık şekilde 'durdur'a kaymamalı
    assert match_intent("risk durumu ne").kind == "query"


def test_unrelated_is_chat():
    assert match_intent("bana yemek tarifi ver").kind == "chat"


def test_exact_is_high_confidence():
    assert match_intent("acil durdur").confidence == "high"


# ── Onay mantığı ──────────────────────────────────────────────────
def test_affirmative():
    assert is_affirmative("evet onaylıyorum")
    assert is_affirmative("tamam başlat")
    assert not is_affirmative("hayır iptal")
    assert not is_affirmative("vazgeç")
    assert not is_affirmative("")


if __name__ == "__main__":
    # pytest yoksa düz çalıştırma
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print(f"  ✓ {fn.__name__}")
        except AssertionError:
            print(f"  ✗ {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} test geçti")
    sys.exit(0 if passed == len(fns) else 1)
