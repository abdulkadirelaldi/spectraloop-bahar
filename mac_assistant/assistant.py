#!/usr/bin/env python3
"""
AKGYS Sesli Asistan
Mac'te çalışır — Pi'deki robot yüzü SocketIO ile kontrol eder
"""

import sounddevice as sd
import numpy as np
import whisper
import requests
import subprocess
import socketio
import threading
import time
import json
import re
import sys
import os

# ── AYARLAR ──────────────────────────────────────────────────────
PI_URL          = "http://10.126.14.177:5001"    # Pi'nin Flask sunucusu
OLLAMA_URL      = "http://localhost:11434"
OLLAMA_MODEL    = "qwen2.5:3b"
WHISPER_MODEL   = "small"                         # tiny/base/small
SAMPLE_RATE     = 16000
RECORD_SECONDS  = 5                               # Her dinleme süresi (sn)
WAKE_WORDS      = ["spectraloop", "asistan", "sistem"]
USB_SPEAKER     = None   # None = sistem varsayılanı, veya cihaz adı

# Mikrofon cihazı — None = sistem varsayılanı, int = cihaz index
MIC_DEVICE = None  # MacBook mic için None bırak (sistem ayarından alır)

SYSTEM_PROMPT = """Sen AKGYS (Akıllı Güvenlik Yönetim Sistemi) sesli asistanısın.
Hyperloop güvenlik sistemini kontrol ediyorsun. Türkçe konuş, kısa ve net cevap ver.
Sistem komutları: simülasyon başlat, acil durdur, normal mod, durum sorgula.
Genel sorulara da kısa cevap verebilirsin."""

# ── PI SOCKETİO BAĞLANTISI ────────────────────────────────────────
sio = socketio.Client()
pi_connected = False

def connect_pi():
    global pi_connected
    while True:
        if not pi_connected:
            try:
                sio.connect(PI_URL)
                pi_connected = True
                print(f"[✓] Pi'ye bağlandı: {PI_URL}")
            except Exception as e:
                print(f"[!] Pi bağlantısı bekliyor... ({e})")
                time.sleep(3)
        time.sleep(5)

@sio.event
def connect():
    global pi_connected
    pi_connected = True

@sio.event
def disconnect():
    global pi_connected
    pi_connected = False

def emit_face(state, text=""):
    """Pi'deki robot yüzüne durum gönder"""
    try:
        sio.emit("assistant_state", {"state": state, "text": text})
    except:
        pass

# ── KOMUT TANINMA ─────────────────────────────────────────────────
COMMANDS = {
    r"(normal|sıfırla|başa dön)":           ("normal",     "Normal moda geçiliyor."),
    r"(f1|bms|ısınma|termal)":              ("bms",        "F1 BMS ısınma simülasyonu başlatılıyor."),
    r"(f2|levitasyon|kaldırma|sapma)":      ("levitasyon", "F2 levitasyon sapması simülasyonu başlatılıyor."),
    r"(f3|navigasyon|konum|enkoder)":       ("navigasyon", "F3 navigasyon arızası simülasyonu başlatılıyor."),
    r"(z1|çoklu|kritik|tüm arız)":         ("z1",         "Z1 çoklu arıza simülasyonu başlatılıyor."),
    r"(basınç|kabin basınç)":              ("basinc",     "Basınç düşüşü simülasyonu başlatılıyor."),
    r"(oksijen|maske|nefes)":              ("oksijen",    "Oksijen maskesi simülasyonu başlatılıyor."),
    r"(acil dur|acil durdur|durdur)":      ("acildurak",  "Acil durdurma başlatılıyor!"),
    r"(sarsıntı|titreşim|deprem)":         ("sarsinti",   "Sarsıntı simülasyonu başlatılıyor."),
    r"(tahliye|çıkış|boşalt)":            ("tahliye",    "Tahliye simülasyonu başlatılıyor."),
}

STATE_QUERY = r"(durum|risk|hız|nerede|kaçıncı|bilgi|söyle|nasıl)"

def check_command(text):
    """Metinde sistem komutu var mı kontrol et"""
    text_lower = text.lower()
    for pattern, (scenario, response) in COMMANDS.items():
        if re.search(pattern, text_lower):
            return scenario, response
    if re.search(STATE_QUERY, text_lower):
        return "query", None
    return None, None

def trigger_scenario(scenario):
    """Pi'ye simülasyon komutu gönder"""
    try:
        sio.emit("sim_trigger", {"scenario": scenario})
        print(f"[→] Senaryo gönderildi: {scenario}")
    except Exception as e:
        print(f"[!] Senaryo gönderilemedi: {e}")

def get_system_state():
    """Pi'den güncel durum al"""
    try:
        # SocketIO state'i bekle (basit yaklaşım: HTTP endpoint yok, son gelen state'i kullan)
        return current_state
    except:
        return None

# ── OLLAMA LLM ────────────────────────────────────────────────────
def ask_ollama(user_text, state_context=""):
    context = f"\nGüncel sistem durumu: {state_context}" if state_context else ""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT + context},
            {"role": "user",   "content": user_text}
        ],
        "stream": False,
        "options": {"num_predict": 150, "temperature": 0.7}
    }
    try:
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=30)
        return r.json()["message"]["content"].strip()
    except Exception as e:
        return f"LLM hatası: {e}"

# ── TTS (macOS say) ───────────────────────────────────────────────
def speak(text, device=None):
    """macOS say komutu ile Türkçe seslendir"""
    emit_face("speaking", text)
    print(f"[🔊] {text}")
    try:
        # Türkçe ses: Yelda veya Damayanti
        cmd = ["say", "-v", "Yelda", "-r", "180", text]
        # USB hoparlör seçimi
        if device:
            cmd += ["-a", device]
        subprocess.run(cmd, check=True)
    except Exception:
        try:
            subprocess.run(["say", "-r", "180", text])
        except:
            pass
    emit_face("idle")

# ── WHISPER STT ───────────────────────────────────────────────────
print("[...] Whisper modeli yükleniyor...")
whisper_model = whisper.load_model(WHISPER_MODEL)
print("[✓] Whisper hazır")

def record_audio(seconds=RECORD_SECONDS):
    """Mikrofon kaydı al"""
    audio = sd.rec(int(seconds * SAMPLE_RATE),
                   samplerate=SAMPLE_RATE, channels=1, dtype='float32',
                   device=MIC_DEVICE)
    sd.wait()
    return audio.flatten()

def transcribe(audio):
    """Ses → metin"""
    result = whisper_model.transcribe(audio, language="tr", fp16=False)
    return result["text"].strip()

# ── DURUM TAKİBİ ─────────────────────────────────────────────────
current_state = {}

@sio.on("update")
def on_update(data):
    global current_state
    current_state = data

# ── ANA DÖNGÜ ─────────────────────────────────────────────────────
def main():
    print("\n" + "="*50)
    print("  AKGYS Sesli Asistan Başlatıldı")
    print("="*50)
    print(f"  Pi: {PI_URL}")
    print(f"  Model: {OLLAMA_MODEL}")
    print(f"  Uyandırma: {WAKE_WORDS}")
    print("="*50 + "\n")

    # Pi bağlantısını arka planda başlat
    threading.Thread(target=connect_pi, daemon=True).start()
    time.sleep(2)

    speak("Merhaba! Ben Spectraloop sesli asistanıyım. Nasıl yardımcı olabilirim?")

    while True:
        print("\n[👂] Dinleniyor...")
        emit_face("listening")

        try:
            audio = record_audio(RECORD_SECONDS)
            text = transcribe(audio)
            if not text or len(text) < 3:
                continue
            print(f"[📝] Algılanan: {text}")
        except Exception as e:
            print(f"[!] Kayıt hatası: {e}")
            continue

        emit_face("thinking", text)

        # Sistem komutu mu?
        scenario, response = check_command(text)

        if scenario == "acildurak":
            try:
                sio.emit("acil_durdur", {})
            except:
                pass
            speak(response)

        elif scenario and scenario != "query":
            trigger_scenario(scenario)
            speak(response)

        elif scenario == "query" and current_state:
            # Durum bilgisi oluştur
            s = current_state
            state_str = (f"Durum: {s.get('durum','?')}, "
                        f"Risk: {s.get('risk_skoru','?')}/100, "
                        f"Hız: {s.get('hiz','?')} km/h, "
                        f"BMS: {s.get('bms_temp','?')}°C, "
                        f"Konum: {round(s.get('konum',0)*1900)} km")
            answer = ask_ollama(text, state_str)
            speak(answer)

        else:
            # Genel soru → LLM
            answer = ask_ollama(text)
            speak(answer)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[■] Asistan durduruldu.")
        emit_face("idle")
