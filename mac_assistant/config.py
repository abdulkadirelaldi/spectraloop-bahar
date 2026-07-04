"""AKGYS Mac asistanı yapılandırması — tüm ayarlar ortam değişkeninden okunur."""

import os
from dataclasses import dataclass, field
from typing import Optional, Union


def _int(name, default):
    return int(os.getenv(name, str(default)))


def _float(name, default):
    return float(os.getenv(name, str(default)))


def _mic_device():
    v = os.getenv("AKGYS_MIC_DEVICE", "").strip()
    if v == "":
        return None            # sistem varsayılanı
    try:
        return int(v)          # cihaz index'i
    except ValueError:
        return v               # cihaz adı


@dataclass
class Config:
    # Ağ
    pi_url: str        = field(default_factory=lambda: os.getenv("AKGYS_PI_URL", "http://10.126.14.177:5001"))
    ollama_url: str    = field(default_factory=lambda: os.getenv("AKGYS_OLLAMA_URL", "http://localhost:11434"))
    ollama_model: str  = field(default_factory=lambda: os.getenv("AKGYS_OLLAMA_MODEL", "qwen2.5:3b"))

    # STT (konuşma-metin)
    whisper_model: str = field(default_factory=lambda: os.getenv("AKGYS_WHISPER_MODEL", "small"))
    sample_rate: int   = field(default_factory=lambda: _int("AKGYS_SAMPLE_RATE", 16000))
    mic_device: Optional[Union[int, str]] = field(default_factory=_mic_device)

    # VAD (konuşma tespiti)
    vad_factor: float     = field(default_factory=lambda: _float("AKGYS_VAD_FACTOR", 3.5))    # gürültü tabanı x kaç
    vad_silence_ms: int   = field(default_factory=lambda: _int("AKGYS_VAD_SILENCE_MS", 800))  # bu kadar sessizlikte bitir
    utter_max_s: float    = field(default_factory=lambda: _float("AKGYS_UTTER_MAX_S", 12.0))  # tek söylem üst sınırı
    start_timeout_s: float= field(default_factory=lambda: _float("AKGYS_START_TIMEOUT_S", 8.0))  # konuşma başlamazsa vazgeç
    min_utter_s: float    = field(default_factory=lambda: _float("AKGYS_MIN_UTTER_S", 0.35))  # bundan kısa söylem yok sayılır

    # Etkileşim
    active_window_s: float= field(default_factory=lambda: _float("AKGYS_ACTIVE_WINDOW_S", 12.0))  # uyandırma sonrası wake-word'süz süre
    confirm_timeout_s: float = field(default_factory=lambda: _float("AKGYS_CONFIRM_TIMEOUT_S", 6.0))

    # TTS
    tts_voice: str     = field(default_factory=lambda: os.getenv("AKGYS_TTS_VOICE", "Yelda"))
    tts_rate: int      = field(default_factory=lambda: _int("AKGYS_TTS_RATE", 180))
    tts_device: str    = field(default_factory=lambda: os.getenv("AKGYS_TTS_DEVICE", ""))  # ses çıkış cihazı adı

    # LLM
    llm_timeout: float    = field(default_factory=lambda: _float("AKGYS_LLM_TIMEOUT", 20.0))
    llm_num_predict: int  = field(default_factory=lambda: _int("AKGYS_LLM_NUM_PREDICT", 200))
    llm_temperature: float= field(default_factory=lambda: _float("AKGYS_LLM_TEMPERATURE", 0.6))
    memory_turns: int     = field(default_factory=lambda: _int("AKGYS_MEMORY_TURNS", 4))     # tutulacak diyalog turu

    def summary(self):
        return (f"Pi={self.pi_url}  Model={self.ollama_model}  "
                f"Whisper={self.whisper_model}  SR={self.sample_rate}")
