#!/usr/bin/env python3
"""
AKGYS Pi Sunucusu — Flask + SocketIO
Hyperloop güvenlik simülasyonu; operatör/yolcu/robot arayüzlerini ve
Mac sesli asistanını gerçek zamanlı besler.

Öne çıkanlar:
  - Thread-güvenli paylaşılan durum (RLock)
  - Kalıcı (latching) ACİL DURDURMA — sim döngüsü tarafından ezilmez
  - Ortam değişkeni tabanlı yapılandırma (hardcoded sır yok)
  - Token korumalı /screenshot uç noktası
  - Otoritatif /api/state ve /healthz uç noktaları
  - Denetim (audit) günlüğü
"""

import os
import time
import random
import logging
import secrets
import threading
import subprocess
from datetime import datetime, timezone

from flask import Flask, render_template, jsonify, request, abort
from flask_socketio import SocketIO


# ── YAPILANDIRMA (ortam değişkenleri) ─────────────────────────────
def _env(name, default=None):
    return os.getenv(name, default)


HOST              = _env("AKGYS_HOST", "0.0.0.0")
PORT              = int(_env("AKGYS_PORT", "5001"))
SECRET_KEY        = _env("AKGYS_SECRET_KEY") or secrets.token_hex(32)
CORS_ORIGINS      = _env("AKGYS_CORS", "*")
SCREENSHOT_TOKEN  = _env("AKGYS_SCREENSHOT_TOKEN", "")          # boş => uç nokta kapalı
SCREENSHOT_DIR    = os.path.expanduser(_env("AKGYS_SCREENSHOT_DIR", "~/akgys/screenshots"))
ROUTE_KM          = float(_env("AKGYS_ROUTE_KM", "1900"))       # toplam güzergah (km)
ROUTE_MIN         = int(_env("AKGYS_ROUTE_MIN", "83"))          # toplam süre (dk)
DEMO_MULT         = float(_env("AKGYS_DEMO_MULT", "35"))        # demo hızlandırma çarpanı
OVERRIDE_HOLD_S   = float(_env("AKGYS_OVERRIDE_HOLD", "18"))    # senaryo enjeksiyon süresi (sn)
LOG_DIR           = os.path.expanduser(_env("AKGYS_LOG_DIR", "~/akgys/logs"))


# ── GÜNLÜKLEME ─────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("akgys")

# Denetim günlüğü ayrı bir dosyaya (güvenlik gereksinimi: kim, ne, ne zaman)
audit = logging.getLogger("akgys.audit")
audit.setLevel(logging.INFO)
_audit_fh = logging.FileHandler(os.path.join(LOG_DIR, "audit.log"))
_audit_fh.setFormatter(logging.Formatter("%(asctime)s  %(message)s"))
audit.addHandler(_audit_fh)
audit.propagate = False


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── UYGULAMA ───────────────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins=CORS_ORIGINS, async_mode="threading")

if CORS_ORIGINS == "*":
    log.warning("CORS tüm origin'lere açık (AKGYS_CORS ile kısıtlayın).")
if not SCREENSHOT_TOKEN:
    log.warning("AKGYS_SCREENSHOT_TOKEN tanımlı değil — /screenshot uç noktası KAPALI.")


STATIONS = [
    {"name": "Istanbul", "x": 17.2, "y": 32.5},
    {"name": "Ankara",   "x": 46.0, "y": 38.0},
    {"name": "Sivas",    "x": 61.0, "y": 33.0},
    {"name": "Erzurum",  "x": 74.5, "y": 26.0},
    {"name": "Kars",     "x": 83.5, "y": 23.5},
]

# ── PAYLAŞILAN DURUM (kilit korumalı) ─────────────────────────────
_state_lock = threading.RLock()
state = {
    "risk": 8,
    "risk_skoru": 8,
    "durum": "NORMAL",
    "hiz": 1200,
    "konum": 0.0,
    "konum_km": 0.0,
    "bms_temp": 32.0,
    "imu_sap": 0.18,
    "basinc": 101.2,
    "gaz": 85,
    "varis": "14:55",
    "kalan_dk": ROUTE_MIN,
    "mesaj": "",
}


def state_snapshot():
    """Durumun kilit altında alınmış kopyası (yayın/HTTP için güvenli)."""
    with _state_lock:
        return dict(state)


PHASE_SCRIPT = [
    (20, 8,  "NORMAL",   1200, ""),
    (10, 35, "UYARI",    950,  ""),
    (8,  68, "KRITIK",   600,  ""),
    (6,  89, "ACIL",     0,    ""),
    (10, 5,  "KURTARMA", 0,    ""),
    (15, 8,  "NORMAL",   1200, ""),
]

# Senaryo başına enjekte edilen hedef değerler
SCENARIO_STATES = {
    "normal":     {"risk": 8,  "durum": "NORMAL",   "hiz": 1200, "bms_temp": 32.0, "imu_sap": 0.18, "basinc": 101.2, "gaz": 85},
    "bms":        {"risk": 72, "durum": "KRITIK",   "hiz": 600,  "bms_temp": 62.0, "imu_sap": 0.22, "basinc": 100.8, "gaz": 92},
    "levitasyon": {"risk": 74, "durum": "KRITIK",   "hiz": 420,  "bms_temp": 34.0, "imu_sap": 4.20, "basinc": 100.9, "gaz": 88},
    "navigasyon": {"risk": 52, "durum": "UYARI",    "hiz": 820,  "bms_temp": 33.5, "imu_sap": 0.20, "basinc": 101.0, "gaz": 87},
    "z1":         {"risk": 91, "durum": "ACIL",     "hiz": 0,    "bms_temp": 74.0, "imu_sap": 5.80, "basinc": 91.0,  "gaz": 580},
    "basinc":     {"risk": 48, "durum": "UYARI",    "hiz": 900,  "bms_temp": 33.0, "imu_sap": 0.19, "basinc": 78.5,  "gaz": 88},
    "oksijen":    {"risk": 65, "durum": "KRITIK",   "hiz": 720,  "bms_temp": 33.5, "imu_sap": 0.20, "basinc": 71.0,  "gaz": 135},
    "acildurak":  {"risk": 95, "durum": "ACIL",     "hiz": 0,    "bms_temp": 38.0, "imu_sap": 0.45, "basinc": 99.0,  "gaz": 210},
    "sarsinti":   {"risk": 56, "durum": "UYARI",    "hiz": 960,  "bms_temp": 33.5, "imu_sap": 3.60, "basinc": 100.7, "gaz": 88},
    "tahliye":    {"risk": 88, "durum": "KURTARMA", "hiz": 0,    "bms_temp": 37.0, "imu_sap": 0.28, "basinc": 97.5,  "gaz": 195},
}
VALID_SCENARIOS = set(SCENARIO_STATES.keys())

# Simülasyon kontrol bayrakları (hepsi _state_lock altında değiştirilir)
_sim = {
    "override": {},          # aktif senaryo enjeksiyonu
    "override_until": 0.0,    # bu zamandan sonra sona erer
    "emergency": False,       # KALICI acil durdurma kilidi — sadece 'normal' sıfırlar
}


def _apply_values(target, noisy=True):
    """Verilen hedef değerleri (gürültüyle) paylaşılan duruma yazar. Kilit çağıran tarafta."""
    def n(mu, sd):
        return random.gauss(0, sd) if noisy else 0.0
    state["risk"]       = round(max(0.0, min(100.0, target["risk"] + n(0, 1.5))), 1)
    state["risk_skoru"] = state["risk"]
    state["durum"]      = target["durum"]
    state["hiz"]        = target["hiz"]
    state["bms_temp"]   = round(target["bms_temp"] + n(0, 0.4), 1)
    state["imu_sap"]    = round(abs(target["imu_sap"] + n(0, 0.08)), 2)
    state["basinc"]     = round(target["basinc"] + n(0, 0.25), 1)
    state["gaz"]        = max(0, int(target["gaz"] + n(0, 8)))


def sim_loop():
    t = 0.0
    phase_idx = 0
    phase_start = 0.0
    pos = 0.0

    while True:
        dt = 0.5
        time.sleep(dt)
        t += dt
        now = time.time()

        with _state_lock:
            if _sim["emergency"]:
                # KALICI acil durdurma: sim script'i tarafından ASLA ezilmez.
                _apply_values(SCENARIO_STATES["acildurak"])

            elif _sim["override"] and now < _sim["override_until"]:
                _apply_values(_sim["override"])

            else:
                if _sim["override"] and now >= _sim["override_until"]:
                    _sim["override"] = {}
                    _sim["override_until"] = 0.0

                # Normal faz senaryosu
                phase = PHASE_SCRIPT[phase_idx]
                if t - phase_start >= phase[0]:
                    phase_idx = (phase_idx + 1) % len(PHASE_SCRIPT)
                    phase_start = t
                    phase = PHASE_SCRIPT[phase_idx]

                state["risk"]       = round(state["risk"] * 0.85 + phase[1] * 0.15, 1)
                state["risk_skoru"] = state["risk"]
                state["durum"]      = phase[2]
                state["hiz"]        = phase[3]
                state["mesaj"]      = phase[4]

                r = state["risk"] / 100.0
                state["bms_temp"] = round(32 + r * 28 + random.gauss(0, 0.3), 1)
                state["imu_sap"]  = round(abs(0.18 + r * 3.8 + random.gauss(0, 0.05)), 2)
                state["basinc"]   = round(101.2 - r * 21 + random.gauss(0, 0.2), 1)
                state["gaz"]      = int(85 + r * 915 + random.gauss(0, 5))

            # Konum güncelle
            if state["hiz"] > 0:
                pos += (state["hiz"] / 3600.0) * dt / ROUTE_KM * DEMO_MULT
                if pos >= 1.0:
                    pos = 0.0
            state["konum"]    = round(pos, 5)
            state["konum_km"] = round(pos * ROUTE_KM, 1)
            state["kalan_dk"] = max(0, int(ROUTE_MIN - pos * ROUTE_MIN))
            snapshot = dict(state)

        socketio.emit("update", snapshot)


# ── HTTP ROTALARI ──────────────────────────────────────────────────
@app.route("/")
def index():
    return '<a href="/yolcu">Yolcu</a> | <a href="/operator">Operator</a> | <a href="/robot">Robot Yüzü</a>'


@app.route("/robot")
def robot():
    return render_template("robot.html")


@app.route("/yolcu")
def yolcu():
    return render_template("yolcu.html", stations=STATIONS)


@app.route("/operator")
def operator():
    return render_template("operator.html")


@app.route("/api/state")
def api_state():
    """Otoritatif durum kaynağı (asistan/izleme için)."""
    return jsonify(state_snapshot())


@app.route("/healthz")
def healthz():
    with _state_lock:
        emergency = _sim["emergency"]
    return jsonify({"ok": True, "emergency": emergency, "time": _now_iso()})


# ── SOCKETIO OLAYLARI ─────────────────────────────────────────────
@socketio.on("assistant_state")
def handle_assistant_state(data):
    """Mac asistanından gelen görsel durumu (listening/thinking/speaking) yayınla."""
    if not isinstance(data, dict):
        return
    state_val = str(data.get("state", "idle"))[:20]
    text_val = str(data.get("text", ""))[:400]
    socketio.emit("assistant_state", {"state": state_val, "text": text_val})


@socketio.on("acil_durdur")
def handle_acil(data=None):
    """KALICI acil durdurma — 'normal' komutu gelene dek sürer."""
    with _state_lock:
        _sim["emergency"] = True
        _sim["override"] = {}
        _sim["override_until"] = 0.0
        _apply_values(SCENARIO_STATES["acildurak"], noisy=False)
        snapshot = dict(state)
    audit.info("ACIL_DURDUR  src=%s", request.remote_addr)
    log.warning("ACİL DURDURMA etkin (kaynak=%s)", request.remote_addr)
    socketio.emit("update", snapshot)
    socketio.emit("show_alert", {"scenario": "acildurak"})


@socketio.on("sim_trigger")
def handle_sim_trigger(data):
    scenario = (data or {}).get("scenario", "normal")
    if scenario not in VALID_SCENARIOS:
        log.warning("Geçersiz senaryo yok sayıldı: %r (src=%s)", scenario, request.remote_addr)
        return

    with _state_lock:
        if scenario == "normal":
            _sim["emergency"] = False
            _sim["override"] = {}
            _sim["override_until"] = 0.0
        elif scenario == "acildurak":
            _sim["emergency"] = True
            _sim["override"] = {}
            _sim["override_until"] = 0.0
            _apply_values(SCENARIO_STATES["acildurak"], noisy=False)
        else:
            _sim["override"] = SCENARIO_STATES[scenario].copy()
            _sim["override_until"] = time.time() + OVERRIDE_HOLD_S
        snapshot = dict(state)

    audit.info("SIM_TRIGGER  scenario=%s src=%s", scenario, request.remote_addr)
    log.info("Senaryo: %s (src=%s)", scenario, request.remote_addr)
    socketio.emit("update", snapshot)
    socketio.emit("show_alert", {"scenario": scenario})


# ── EKRAN GÖRÜNTÜSÜ (token korumalı) ──────────────────────────────
def _check_screenshot_auth():
    if not SCREENSHOT_TOKEN:
        abort(404)  # uç nokta devre dışı
    token = request.headers.get("X-Auth-Token") or request.args.get("token", "")
    if not secrets.compare_digest(token, SCREENSHOT_TOKEN):
        audit.info("SCREENSHOT_DENIED  src=%s", request.remote_addr)
        abort(403)


@app.route("/screenshot", methods=["POST"])
def take_screenshot():
    _check_screenshot_auth()
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"akgys_{ts}.png"
    filepath = os.path.join(SCREENSHOT_DIR, filename)
    try:
        env = {**os.environ, "DISPLAY": os.getenv("DISPLAY", ":0")}
        result = subprocess.run(["scrot", "-d", "1", filepath],
                                env=env, timeout=6, capture_output=True)
        if result.returncode == 0:
            audit.info("SCREENSHOT  file=%s src=%s", filename, request.remote_addr)
            return jsonify({"ok": True, "file": filename, "path": filepath})
        return jsonify({"ok": False, "error": result.stderr.decode(errors="replace")}), 500
    except Exception as e:
        log.exception("Ekran görüntüsü hatası")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/screenshots", methods=["GET"])
def list_screenshots():
    _check_screenshot_auth()
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    files = sorted(os.listdir(SCREENSHOT_DIR), reverse=True)[:20]
    return jsonify({"files": files})


if __name__ == "__main__":
    log.info("AKGYS Pi sunucusu başlıyor — http://%s:%s", HOST, PORT)
    threading.Thread(target=sim_loop, daemon=True).start()
    # Üretim için: gunicorn/eventlet önerilir. Demo için werkzeug yeterli.
    socketio.run(app, host=HOST, port=PORT, debug=False, allow_unsafe_werkzeug=True)
