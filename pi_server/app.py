import threading
import time
import random
import os
import subprocess
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'akgys2026'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

STATIONS = [
    {"name": "Istanbul",  "x": 17.2, "y": 32.5},
    {"name": "Ankara",    "x": 46.0, "y": 38.0},
    {"name": "Sivas",     "x": 61.0, "y": 33.0},
    {"name": "Erzurum",   "x": 74.5, "y": 26.0},
    {"name": "Kars",      "x": 83.5, "y": 23.5},
]

state = {
    "risk": 8,
    "risk_skoru": 8,
    "durum": "NORMAL",
    "hiz": 1200,
    "konum": 0.0,
    "bms_temp": 32.0,
    "imu_sap": 0.18,
    "basinc": 101.2,
    "gaz": 85,
    "varis": "14:55",
    "kalan_dk": 83,
    "mesaj": "",
}

PHASE_SCRIPT = [
    (20,  8,  "NORMAL",    1200, ""),
    (10,  35, "UYARI",     950,  ""),
    (8,   68, "KRITIK",    600,  ""),
    (6,   89, "ACIL",      0,    ""),
    (10,  5,  "KURTARMA",  0,    ""),
    (15,  8,  "NORMAL",    1200, ""),
]

# Simulation override state
sim_override = {}
sim_override_until = 0.0
sim_scenario_key = None

# Values to inject per scenario (risk, durum, hiz, bms_temp, imu_sap, basinc, gaz)
SCENARIO_STATES = {
    "normal":     {"risk": 8,  "durum": "NORMAL",   "hiz": 1200, "bms_temp": 32.0, "imu_sap": 0.18, "basinc": 101.2, "gaz": 85},
    "bms":        {"risk": 72, "durum": "KRITIK",   "hiz": 600,  "bms_temp": 62.0, "imu_sap": 0.22, "basinc": 100.8, "gaz": 92},
    "levitasyon": {"risk": 74, "durum": "KRITIK",   "hiz": 420,  "bms_temp": 34.0, "imu_sap": 4.20, "basinc": 100.9, "gaz": 88},
    "navigasyon": {"risk": 52, "durum": "UYARI",    "hiz": 820,  "bms_temp": 33.5, "imu_sap": 0.20, "basinc": 101.0, "gaz": 87},
    "z1":         {"risk": 91, "durum": "ACIL",     "hiz": 0,    "bms_temp": 74.0, "imu_sap": 5.80, "basinc": 91.0,  "gaz": 580},
    "basinc":     {"risk": 48, "durum": "UYARI",    "hiz": 900,  "bms_temp": 33.0, "imu_sap": 0.19, "basinc": 78.5,  "gaz": 88},
    "oksijen":    {"risk": 65, "durum": "KRITIK",   "hiz": 720,  "bms_temp": 33.5, "imu_sap": 0.20, "basinc": 71.0,  "gaz": 135},
    "acildurak":  {"risk": 92, "durum": "ACIL",     "hiz": 0,    "bms_temp": 38.0, "imu_sap": 0.45, "basinc": 99.0,  "gaz": 210},
    "sarsinti":   {"risk": 56, "durum": "UYARI",    "hiz": 960,  "bms_temp": 33.5, "imu_sap": 3.60, "basinc": 100.7, "gaz": 88},
    "tahliye":    {"risk": 88, "durum": "KURTARMA", "hiz": 0,    "bms_temp": 37.0, "imu_sap": 0.28, "basinc": 97.5,  "gaz": 195},
}

DEMO_MULT = 35

def sim_loop():
    global sim_override, sim_override_until

    t = 0.0
    phase_idx = 0
    phase_start = 0.0
    pos = 0.0

    while True:
        dt = 0.5
        time.sleep(dt)
        t += dt

        # Check if sim override is active
        now = time.time()
        if sim_override and now < sim_override_until:
            # Inject override values with small noise
            ov = sim_override
            state["risk"]       = round(ov["risk"] + random.gauss(0, 1.5), 1)
            state["risk_skoru"] = state["risk"]
            state["durum"]      = ov["durum"]
            state["hiz"]        = ov["hiz"]
            state["bms_temp"]   = round(ov["bms_temp"] + random.gauss(0, 0.4), 1)
            state["imu_sap"]    = round(abs(ov["imu_sap"] + random.gauss(0, 0.08)), 2)
            state["basinc"]     = round(ov["basinc"] + random.gauss(0, 0.25), 1)
            state["gaz"]        = int(ov["gaz"] + random.gauss(0, 8))
        else:
            if sim_override and now >= sim_override_until:
                sim_override = {}
                sim_override_until = 0.0

            # Normal phase script
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

        # Position update
        if state["hiz"] > 0:
            pos += (state["hiz"] / 3600.0) * dt / 1900.0 * DEMO_MULT
            if pos >= 1.0:
                pos = 0.0
        state["konum"] = round(pos, 5)
        state["kalan_dk"] = max(0, int(83 - pos * 83))

        socketio.emit('update', state)


@app.route('/')
def index():
    return '<a href="/yolcu">Yolcu</a> | <a href="/operator">Operator</a> | <a href="/robot">Robot Yüzü</a>'

@app.route('/robot')
def robot():
    return render_template('robot.html')

@app.route('/yolcu')
def yolcu():
    return render_template('yolcu.html', stations=STATIONS)

@app.route('/operator')
def operator():
    return render_template('operator.html')

@socketio.on('assistant_state')
def handle_assistant_state(data):
    """Mac'ten gelen asistan durumunu tüm browser'lara ilet"""
    socketio.emit('assistant_state', data)

@socketio.on('acil_durdur')
def handle_acil(data=None):
    state["hiz"]        = 0
    state["durum"]      = "ACIL"
    state["risk_skoru"] = 95
    socketio.emit('update', state)

@socketio.on('sim_trigger')
def handle_sim_trigger(data):
    global sim_override, sim_override_until
    scenario = data.get('scenario', 'normal') if data else 'normal'

    if scenario == 'normal':
        sim_override = {}
        sim_override_until = 0.0
    elif scenario in SCENARIO_STATES:
        sim_override = SCENARIO_STATES[scenario].copy()
        sim_override_until = time.time() + 18  # 18 seconds override

    # Immediately emit updated state to all clients
    socketio.emit('update', state)
    # Tell yolcu screen to show the alert card for this scenario
    socketio.emit('show_alert', {'scenario': scenario})


SCREENSHOT_DIR = os.path.expanduser('~/akgys/screenshots')

@app.route('/screenshot', methods=['POST'])
def take_screenshot():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'akgys_{ts}.png'
    filepath = os.path.join(SCREENSHOT_DIR, filename)
    try:
        env = {**os.environ, 'DISPLAY': ':0'}
        result = subprocess.run(['scrot', '-d', '1', filepath],
                                env=env, timeout=6, capture_output=True)
        if result.returncode == 0:
            return jsonify({'ok': True, 'file': filename, 'path': filepath})
        else:
            return jsonify({'ok': False, 'error': result.stderr.decode()})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})

@app.route('/screenshots', methods=['GET'])
def list_screenshots():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    files = sorted(os.listdir(SCREENSHOT_DIR), reverse=True)[:20]
    return jsonify({'files': files})

if __name__ == '__main__':
    th = threading.Thread(target=sim_loop, daemon=True)
    th.start()
    socketio.run(app, host="0.0.0.0", port=5001, debug=False, allow_unsafe_werkzeug=True)
