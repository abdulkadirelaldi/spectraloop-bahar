# Spectraloop Bahar — AKGYS Sesli Asistan & Arayüz

Hyperloop güvenlik simülasyon sistemi. Raspberry Pi üzerinde çalışan Flask arayüzü + Mac üzerinde çalışan offline sesli asistan.

## Klasör Yapısı

```
mac_assistant/
  assistant.py       → Mac'te çalışan sesli asistan (Whisper + Ollama + say)

pi_server/
  app.py             → Raspberry Pi Flask + SocketIO sunucusu
  templates/
    operator.html    → Operatör kontrol paneli
    yolcu.html       → Yolcu ekranı
    robot.html       → Spectra AI robot yüzü
  static/
    spectra.svg      → Spectra karakter görseli
```

## Pi Kurulum

```bash
pip3 install flask flask-socketio
cd ~/akgys && python3 app.py
```

## Mac Kurulum

```bash
pip3 install openai-whisper sounddevice numpy requests "python-socketio[client]"
brew install ollama && ollama pull qwen2.5:3b
python3 mac_assistant/assistant.py
```
