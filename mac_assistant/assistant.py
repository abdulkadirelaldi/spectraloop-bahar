#!/usr/bin/env python3
"""
AKGYS Sesli Asistan (Mac)

Durum makinesi:  IDLE → (uyandırma) → LISTEN (VAD) → THINK → SPEAK → IDLE

Öne çıkanlar:
  - Enerji tabanlı VAD: sabit pencere yok; konuşma bitince otomatik kesilir
  - Uyandırma sözcüğü kapısı + uyandırma sonrası konuşma penceresi
  - faster-whisper varsa onu, yoksa openai-whisper'ı kullanır
  - Konuşma hafızası (çok turlu bağlam)
  - Kritik komutlarda (acil durdurma/tahliye) sesli onay
  - LLM/STT hataları kullanıcıya ham hata olarak okunmaz
  - TTS sırasında mikrofon susturulur (kendini dinlemeyi önler)
  - Otomatik yeniden bağlanan Pi istemcisi + /api/state yedeği
"""

import os
import sys
import time
import logging
import threading
import subprocess
from collections import deque

import numpy as np
import requests
import sounddevice as sd
import socketio

from config import Config
import intent


# ── GÜNLÜKLEME ─────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("akgys.assistant")

SYSTEM_PROMPT = (
    "Sen AKGYS (Akıllı Güvenlik Yönetim Sistemi) sesli asistanısın. "
    "Bir hyperloop güvenlik simülasyonunu izliyorsun. Türkçe, kısa ve net konuş; "
    "en fazla iki cümle. Sistem komutları: simülasyon senaryoları, acil durdurma, "
    "normal mod, durum sorgulama. Sana verilen güncel sistem durumunu kullanarak yanıt ver."
)

# Whisper'ı alan sözcüklerine yönlendiren ipucu — gürültülü ortamda ve teknik
# terimlerde ("levitasyon", "BMS", "fren") doğru transkripsiyon olasılığını artırır.
DOMAIN_PROMPT = (
    "AKGYS hyperloop güvenlik komutları: spectraloop, acil durdur, fren yap, "
    "levitasyon sapması, navigasyon arızası, BMS ısınma, batarya, tahliye, "
    "oksijen maskesi, basınç düşüşü, sarsıntı, çoklu arıza, normale al, "
    "risk durumu, hız, konum."
)


# ── KONUŞMA-METİN (STT) ───────────────────────────────────────────
class STT:
    """faster-whisper tercih edilir; yoksa openai-whisper'a düşer."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.backend = None
        try:
            from faster_whisper import WhisperModel
            log.info("faster-whisper yükleniyor (%s)…", cfg.whisper_model)
            self._model = WhisperModel(cfg.whisper_model, device="cpu", compute_type="int8")
            self.backend = "faster"
        except Exception:
            import whisper
            log.info("openai-whisper yükleniyor (%s)…", cfg.whisper_model)
            self._model = whisper.load_model(cfg.whisper_model)
            self.backend = "openai"
        log.info("STT hazır (backend=%s)", self.backend)

    def transcribe(self, audio: np.ndarray) -> str:
        try:
            if self.backend == "faster":
                # vad_filter: dahili Silero VAD ile gürültü/sessizlik ayıklanır.
                segments, _ = self._model.transcribe(
                    audio, language="tr", beam_size=5, temperature=0.0,
                    initial_prompt=DOMAIN_PROMPT,
                    condition_on_previous_text=False,
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=300),
                )
                return " ".join(s.text for s in segments).strip()
            result = self._model.transcribe(
                audio, language="tr", fp16=False, temperature=0.0,
                initial_prompt=DOMAIN_PROMPT,
                condition_on_previous_text=False,
            )
            return result.get("text", "").strip()
        except Exception:
            log.exception("Transkripsiyon hatası")
            return ""


# ── MİKROFON + ENERJİ TABANLI VAD ─────────────────────────────────
class Microphone:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sr = cfg.sample_rate
        self.frame_ms = 30
        self.frame_len = int(self.sr * self.frame_ms / 1000)
        self.noise_floor = 1e-3
        self.muted = threading.Event()   # TTS sırasında set edilir

    def calibrate(self, seconds: float = 1.0):
        """Ortam gürültü tabanını ölçer (VAD eşiği bunun katıdır)."""
        try:
            rec = sd.rec(int(seconds * self.sr), samplerate=self.sr,
                         channels=1, dtype="float32", device=self.cfg.mic_device)
            sd.wait()
            rms = float(np.sqrt(np.mean(rec.astype(np.float32) ** 2)))
            self.noise_floor = max(rms, 1e-4)
            log.info("Gürültü tabanı: %.5f  (VAD eşiği ≈ %.5f)",
                     self.noise_floor, self.noise_floor * self.cfg.vad_factor)
        except Exception:
            log.exception("Kalibrasyon hatası; varsayılan eşik kullanılacak")

    def listen(self, max_s=None, silence_ms=None, start_timeout_s=None):
        """
        Bir söylemi VAD ile yakalar. Konuşma başlayınca kaydeder, `silence_ms`
        kadar sessizlikte durur. Konuşma hiç başlamazsa None döner.
        """
        cfg = self.cfg
        max_s = max_s or cfg.utter_max_s
        silence_ms = silence_ms or cfg.vad_silence_ms
        start_timeout_s = start_timeout_s or cfg.start_timeout_s

        threshold = self.noise_floor * cfg.vad_factor
        silence_limit = int(silence_ms / self.frame_ms)
        start_limit = int(start_timeout_s * 1000 / self.frame_ms)
        max_frames = int(max_s * 1000 / self.frame_ms)

        frames, triggered, silent, count = [], False, 0, 0
        try:
            with sd.InputStream(samplerate=self.sr, channels=1, dtype="float32",
                                blocksize=self.frame_len, device=cfg.mic_device) as stream:
                while True:
                    if self.muted.is_set():
                        return None
                    block, _ = stream.read(self.frame_len)
                    samples = block[:, 0]
                    rms = float(np.sqrt(np.mean(samples ** 2) + 1e-12))
                    count += 1

                    if not triggered:
                        if rms > threshold:
                            triggered = True
                            frames.append(samples)
                        elif count > start_limit:
                            return None
                    else:
                        frames.append(samples)
                        if rms < threshold:
                            silent += 1
                            if silent > silence_limit:
                                break
                        else:
                            silent = 0
                        if len(frames) > max_frames:
                            break
        except Exception:
            log.exception("Mikrofon okuma hatası")
            return None

        if not frames:
            return None
        audio = np.concatenate(frames).astype(np.float32)
        if len(audio) < self.sr * cfg.min_utter_s:
            return None
        return audio


# ── METİN-KONUŞMA (TTS) ───────────────────────────────────────────
class TTS:
    def __init__(self, cfg: Config, mic: Microphone, pi):
        self.cfg = cfg
        self.mic = mic
        self.pi = pi

    def say(self, text: str):
        if not text:
            return
        self.pi.emit_face("speaking", text)
        log.info("🔊 %s", text)
        self.mic.muted.set()                 # kendini dinlememek için
        try:
            cmd = ["say", "-v", self.cfg.tts_voice, "-r", str(self.cfg.tts_rate)]
            if self.cfg.tts_device:
                cmd += ["-a", self.cfg.tts_device]
            cmd.append(text)
            subprocess.run(cmd, check=True)
        except Exception:
            try:  # ses adı bulunamazsa varsayılan sesle
                subprocess.run(["say", "-r", str(self.cfg.tts_rate), text], check=False)
            except Exception:
                log.exception("TTS hatası")
        finally:
            time.sleep(0.15)                 # hoparlör kuyruğu boşalsın
            self.mic.muted.clear()
        self.pi.emit_face("idle")


# ── LLM (Ollama) — konuşma hafızalı ───────────────────────────────
class LLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.history = deque(maxlen=cfg.memory_turns * 2)

    def ask(self, user_text: str, state_ctx: str = ""):
        """Yanıt metnini döndürür; hata olursa None (çağıran nazik mesaj verir)."""
        system = SYSTEM_PROMPT
        if state_ctx:
            system += f"\n\nGüncel sistem durumu: {state_ctx}"
        messages = [{"role": "system", "content": system}]
        messages += list(self.history)
        messages.append({"role": "user", "content": user_text})

        payload = {
            "model": self.cfg.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": self.cfg.llm_num_predict,
                        "temperature": self.cfg.llm_temperature},
        }
        try:
            r = requests.post(f"{self.cfg.ollama_url}/api/chat",
                              json=payload, timeout=self.cfg.llm_timeout)
            r.raise_for_status()
            content = r.json()["message"]["content"].strip()
            if not content:
                return None
            self.history.append({"role": "user", "content": user_text})
            self.history.append({"role": "assistant", "content": content})
            return content
        except Exception as e:
            log.error("Ollama hatası: %s", e)
            return None


# ── PI SOCKETIO İSTEMCİSİ ─────────────────────────────────────────
class PiClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.sio = socketio.Client(reconnection=True, reconnection_attempts=0,
                                   reconnection_delay=2, reconnection_delay_max=8)
        self.state = {}
        self.connected = False

        @self.sio.event
        def connect():
            self.connected = True
            log.info("Pi'ye bağlanıldı: %s", cfg.pi_url)

        @self.sio.event
        def disconnect():
            self.connected = False
            log.warning("Pi bağlantısı koptu")

        @self.sio.on("update")
        def on_update(data):
            if isinstance(data, dict):
                self.state = data

    def start(self):
        def _run():
            while True:
                try:
                    self.sio.connect(self.cfg.pi_url, wait_timeout=5)
                    self.sio.wait()
                except Exception as e:
                    log.warning("Pi bağlantısı bekleniyor… (%s)", e)
                    time.sleep(3)
        threading.Thread(target=_run, daemon=True).start()

    def _emit(self, event, data):
        try:
            if self.connected:
                self.sio.emit(event, data)
                return True
        except Exception:
            log.debug("emit başarısız: %s", event)
        return False

    def emit_face(self, state, text=""):
        self._emit("assistant_state", {"state": state, "text": text})

    def trigger(self, scenario):
        ok = self._emit("sim_trigger", {"scenario": scenario})
        log.info("→ Senaryo: %s %s", scenario, "" if ok else "(BAĞLANTI YOK)")

    def emergency(self):
        ok = self._emit("acil_durdur", {})
        log.warning("→ ACİL DURDURMA %s", "" if ok else "(BAĞLANTI YOK)")

    def get_state(self):
        """Önce canlı socket durumu; yoksa /api/state HTTP yedeği."""
        if self.state:
            return self.state
        try:
            r = requests.get(f"{self.cfg.pi_url}/api/state", timeout=3)
            r.raise_for_status()
            return r.json()
        except Exception:
            return {}


def state_summary(s: dict) -> str:
    if not s:
        return ""
    return (f"Durum: {s.get('durum','?')}, "
            f"Risk: {s.get('risk_skoru','?')}/100, "
            f"Hız: {s.get('hiz','?')} km/s, "
            f"BMS sıcaklık: {s.get('bms_temp','?')}°C, "
            f"Basınç: {s.get('basinc','?')} kPa, "
            f"Konum: {s.get('konum_km','?')} km, "
            f"Varışa: {s.get('kalan_dk','?')} dk")


# ── ASİSTAN (durum makinesi) ──────────────────────────────────────
class Assistant:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.pi = PiClient(cfg)
        self.mic = Microphone(cfg)
        self.stt = STT(cfg)
        self.tts = TTS(cfg, self.mic, self.pi)
        self.llm = LLM(cfg)
        self.active_until = 0.0    # bu zamana dek wake-word gerekmez

    # --- kritik komut onayı ---
    def _confirm(self, prompt: str) -> bool:
        self.tts.say(prompt)
        self.pi.emit_face("listening")
        audio = self.mic.listen(max_s=self.cfg.confirm_timeout_s,
                                start_timeout_s=self.cfg.confirm_timeout_s)
        if audio is None:
            return False
        reply = self.stt.transcribe(audio)
        log.info("Onay yanıtı: %r", reply)
        return intent.is_affirmative(reply)

    def _execute(self, it):
        if it.scenario == "acildurak":
            self.pi.emergency()
        else:
            self.pi.trigger(it.scenario)
        self.tts.say(it.response)

    def _handle(self, text: str):
        it = intent.match_intent(text)

        if it.kind == "scenario":
            if it.confidence == "medium":
                # Emin değiliz (yanlış/eksik duyulmuş olabilir) → önce teyit et.
                log.info("Bulanık eşleşme: %s (skor=%.2f)", it.label, it.score)
                if self._confirm(f"{it.label} komutunu mu demek istediniz?"):
                    self._execute(it)      # açık teyit; kritik olsa da tek onay yeter
                else:
                    self.tts.say("Anlaşılmadı, lütfen komutu tekrar söyleyin.")
                return

            # Yüksek güven (tam eşleşme)
            if it.critical:
                if not self._confirm(f"{it.response} Onaylıyor musunuz?"):
                    self.tts.say("İşlem iptal edildi.")
                    return
            self._execute(it)

        elif it.kind == "query":
            self.pi.emit_face("thinking", text)
            answer = self.llm.ask(text, state_summary(self.pi.get_state()))
            self.tts.say(answer or "Şu anda durum bilgisini alamıyorum.")

        else:  # chat
            self.pi.emit_face("thinking", text)
            answer = self.llm.ask(text)
            self.tts.say(answer or "Şu anda yanıt veremiyorum, lütfen tekrar deneyin.")

    def run(self):
        self.pi.start()
        time.sleep(1.0)
        self.mic.calibrate(1.0)
        self.tts.say("Merhaba, ben Spectraloop sesli asistanı. Beni uyandırmak için "
                     "Spectraloop deyin.")

        while True:
            try:
                active = time.time() < self.active_until
                self.pi.emit_face("listening" if active else "idle")

                audio = self.mic.listen()
                if audio is None:
                    continue

                text = self.stt.transcribe(audio)
                if not text or len(text) < 2:
                    continue
                log.info("📝 %s", text)

                wake = intent.has_wake_word(text)
                if not active and not wake:
                    continue  # uyandırma bekleniyor

                command = intent.strip_wake_words(text) if wake else text
                if wake and not command:
                    # sadece uyandırma sözcüğü → onayla, pencereyi aç
                    self.tts.say("Buyurun, sizi dinliyorum.")
                    self.active_until = time.time() + self.cfg.active_window_s
                    continue

                self.pi.emit_face("thinking", command)
                self._handle(command)
                self.active_until = time.time() + self.cfg.active_window_s

            except Exception:
                log.exception("Döngü hatası (devam ediliyor)")
                time.sleep(0.3)


def main():
    cfg = Config()
    print("\n" + "=" * 56)
    print("  AKGYS Sesli Asistan")
    print("  " + cfg.summary())
    print("  Uyandırma:", ", ".join(intent.WAKE_WORDS[:3]))
    print("=" * 56 + "\n")
    assistant = Assistant(cfg)
    try:
        assistant.run()
    except KeyboardInterrupt:
        log.info("Asistan durduruldu.")
        try:
            assistant.pi.emit_face("idle")
        except Exception:
            pass
        sys.exit(0)


if __name__ == "__main__":
    main()
