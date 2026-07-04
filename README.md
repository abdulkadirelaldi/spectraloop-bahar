# Spectraloop Bahar — AKGYS Sesli Asistan & Arayüz

Hyperloop güvenlik simülasyon sistemi. Raspberry Pi üzerinde çalışan Flask/SocketIO
arayüzü + Mac üzerinde çalışan offline sesli asistan (Whisper + Ollama + `say`).

## Mimari

```
Mac (assistant.py)                         Raspberry Pi (app.py)
┌───────────────────────────┐   SocketIO   ┌───────────────────────────┐
│ Mikrofon → VAD → Whisper  │─────────────▶│ sim_trigger / acil_durdur │
│ Intent (regex) / Ollama   │◀─────────────│ update (durum yayını)     │
│ macOS say (TTS)           │  assistant_  │ /api/state  /healthz      │
└───────────────────────────┘    state     └──────────┬────────────────┘
                                                       │ render
                                          operator.html / yolcu.html / robot.html
```

Etkileşim durum makinesi: **IDLE → (uyandırma sözcüğü) → LISTEN (VAD) → THINK → SPEAK → IDLE**

## Klasör Yapısı

```
mac_assistant/
  assistant.py      → Sesli asistan (durum makinesi, VAD, TTS)
  intent.py         → Niyet/komut eşleştirme (saf, test edilebilir)
  config.py         → Ortam değişkeni tabanlı yapılandırma
  test_intent.py    → Birim testleri (ses/model bağımlılığı gerektirmez)
  requirements.txt

pi_server/
  app.py            → Flask + SocketIO sunucu (thread-güvenli, güvenli uç noktalar)
  requirements.txt
  templates/        → operator.html · yolcu.html · robot.html
  static/           → spectra.svg (robot yüzü görseli — bkz. Notlar)

.env.example        → Tüm ayarların şablonu
```

## Öne Çıkan Özellikler

- **Gerçek dinleme:** Sabit 5 sn pencere yerine enerji tabanlı **VAD** — konuşma bitince otomatik kesilir. Başlangıçta ortam gürültüsü kalibre edilir.
- **Uyandırma sözcüğü:** "Spectraloop / asistan / sistem". Uyandırma sonrası kısa bir konuşma penceresinde tekrar söylemeye gerek yoktur.
- **Kritik komut güvenliği:** *Acil durdurma*, *tahliye*, *çoklu arıza* yürütülmeden önce **sesli onay** ister.
- **Kalıcı ACİL DURDURMA:** Pi'de latching kilit — simülasyon döngüsü tarafından ezilmez, yalnızca "normal" komutu sıfırlar.
- **Thread-güvenli durum:** Paylaşılan `state` RLock ile korunur.
- **Konuşma hafızası:** Ollama çağrılarında çok turlu bağlam.
- **Hızlı STT:** `faster-whisper` varsa otomatik kullanılır (CPU'da ~3-5x); yoksa `openai-whisper`.
- **Güvenlik:** Env tabanlı sır yönetimi, token korumalı `/screenshot`, girdi doğrulama, denetim (audit) günlüğü.
- **Nazik hata yönetimi:** LLM/STT hataları kullanıcıya ham hata olarak okunmaz.

## Kurulum

Ayarları `.env` üzerinden verin:

```bash
cp .env.example .env
# .env dosyasını düzenleyin (özellikle AKGYS_PI_URL ve AKGYS_SCREENSHOT_TOKEN)
set -a; source .env; set +a
```

### Pi

```bash
cd pi_server
pip3 install -r requirements.txt
python3 app.py            # http://<pi-ip>:5001
```

### Mac

```bash
cd mac_assistant
pip3 install -r requirements.txt
brew install ollama && ollama pull qwen2.5:3b
python3 assistant.py
```

## Test

```bash
cd mac_assistant
python3 -m pytest -q        # veya: python3 test_intent.py
```

## Sesli Komut Örnekleri

| Söyleyin                              | Sonuç                                  |
|---------------------------------------|----------------------------------------|
| "Spectraloop"                         | Asistanı uyandırır                     |
| "…acil durdur"                        | Onay ister → ACİL DURDURMA (kalıcı)    |
| "…f1 BMS ısınması başlat"             | BMS senaryosu                          |
| "…yolcuları tahliye et"               | Onay ister → tahliye senaryosu         |
| "…risk durumu ne / hız kaç"           | Güncel durumu LLM ile özetler          |
| "…normale al"                         | Tüm senaryoları/acili sıfırlar         |

## Notlar / Sonraki Adımlar

- **Offline:** `socket.io.min.js` artık `pi_server/static/`'e gömülü ve tüm
  şablonlar yerel yolu kullanır — arayüz internet olmadan da çalışır.
- **Robot yüzü:** `pi_server/static/spectra.svg` dahildir (durum
  animasyonlarıyla uyumlu). İsterseniz kendi görselinizle değiştirin.
- **Üretim:** `app.py` demo amaçlı Werkzeug sunucusu kullanır; üretimde
  `gunicorn` + `eventlet` önerilir. `AKGYS_CORS`'u tek origin'e kısıtlayın.
- **True barge-in (konuşurken kesme):** macOS'ta hoparlör sesi mikrofona
  sızdığından yankı iptali (AEC) olmadan güvenilir değildir; bu sürüm TTS
  sırasında mikrofonu susturur.
