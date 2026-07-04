#!/usr/bin/env python3
"""
Spectraloop - MacBook Sesli Komut Istemcisi (Bas-Konus / Push-to-Talk)
----------------------------------------------------------------------
'S' tusunu basili tut -> konus -> birak.
Ses Turkce olarak yaziya cevrilir, komut ayristirilip Raspberry Pi'ye TCP ile gonderilir.
ESC ile cikis.

Kurulum (Terminal):
    brew install portaudio
    pip3 install sounddevice numpy faster-whisper pynput

macOS IZNI (onemli!):
    Sistem Ayarlari -> Gizlilik ve Guvenlik ->
      * Erisilebilirlik (Accessibility)  -> Terminal'e izin ver
      * Girdi Izleme (Input Monitoring)  -> Terminal'e izin ver
    Yoksa 'S' tusu algilanmaz.

Calistir:
    python3 mac_voice_client.py
(Ilk calistirmada Whisper modeli iner ~460MB, bir kez internet gerekir. Sonrasi offline.)
"""
import socket
import queue
import threading

import numpy as np
import sounddevice as sd
from pynput import keyboard
from faster_whisper import WhisperModel

# --- Ayarlar ---
PI_HOST = "192.168.1.50"   # <-- Raspberry Pi'nin IP adresi (Pi'de `hostname -I` ile ogren)
PI_PORT = 5005
PTT_KEY = "s"              # Bas-konus tusu
SAMPLE_RATE = 16000        # Whisper 16kHz ister
MODEL_SIZE = "small"       # tiny / base / small / medium — Turkce icin "small" iyi denge

# --- Whisper modelini yukle ---
print("[Mac] Whisper modeli yukleniyor...")
model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
print(f"[Mac] Model hazir. '{PTT_KEY.upper()}' tusunu basili tutup konus. (Cikis: ESC)")

# --- Ses kaydi durumu ---
audio_q = queue.Queue()
recording = False


def audio_callback(indata, frame_count, time_info, status):
    if recording:
        audio_q.put(indata.copy())


stream = sd.InputStream(
    samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=audio_callback
)
stream.start()


def send_command(cmd: str):
    try:
        with socket.create_connection((PI_HOST, PI_PORT), timeout=2) as s:
            s.sendall((cmd + "\n").encode())
            resp = s.recv(1024).decode(errors="ignore").strip()
            print(f"[Mac] Pi cevabi: {resp}")
    except Exception as e:
        print(f"[Mac] Gonderim hatasi: {e}")


def parse_command(text: str):
    """Turkce transkripti fren komutuna cevir. Taninmazsa None doner."""
    t = text.lower()

    # Once: serbest birakma
    if any(k in t for k in ["birak", "bırak", "serbest", "gevset", "gevşet"]):
        return "RELEASE"

    # Fren komutu mu? (yanlis tetiklemeyi azaltmak icin sart)
    if "fren" not in t and "sık" not in t and "sik" not in t:
        return None

    if "arka" in t:
        return "REAR"
    if "ön" in t or t.startswith("on ") or " on " in t:
        return "FRONT"
    # spectra / spektra / tum / hepsi / butun -> hepsi
    if any(k in t for k in ["spectra", "spektra", "tüm", "tum", "hepsi", "bütün", "butun"]):
        return "ALL"

    # Ayirt edici kelime yok ama "frenleri sik" dendi -> guvenli varsayim: hepsi
    return "ALL"


def process_audio():
    chunks = []
    while not audio_q.empty():
        chunks.append(audio_q.get())
    if not chunks:
        print("[Mac] Ses alinamadi.")
        return

    audio = np.concatenate(chunks, axis=0).flatten()
    if len(audio) < SAMPLE_RATE * 0.3:  # 0.3 sn'den kisa -> yok say
        print("[Mac] Cok kisa, atlandi.")
        return

    segments, _ = model.transcribe(audio, language="tr", beam_size=1)
    text = " ".join(seg.text for seg in segments).strip()
    print(f'[Mac] Duyulan: "{text}"')

    cmd = parse_command(text)
    if cmd is None:
        print("[Mac] Komut taninmadi.")
        return
    print(f"[Mac] Komut: {cmd}")
    send_command(cmd)


# --- Klavye bas-konus ---
def on_press(key):
    global recording
    try:
        if key.char == PTT_KEY and not recording:
            recording = True
            while not audio_q.empty():   # kuyrugu temizle
                audio_q.get()
            print("[Mac] KAYIT... (konus)")
    except AttributeError:
        pass


def on_release(key):
    global recording
    try:
        if key.char == PTT_KEY and recording:
            recording = False
            print("[Mac] Isleniyor...")
            threading.Thread(target=process_audio, daemon=True).start()
    except AttributeError:
        pass
    if key == keyboard.Key.esc:
        print("[Mac] Cikiliyor.")
        return False


with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    listener.join()
