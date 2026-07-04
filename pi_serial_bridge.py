#!/usr/bin/env python3
"""
Spectraloop - Raspberry Pi Seri Kopru
--------------------------------------
MacBook'tan TCP ile komut alir, Arduino'ya USB seri ile iletir.

Kurulum:   pip3 install pyserial
Calistir:  python3 pi_serial_bridge.py

Komut eslemesi (TCP metin -> Arduino harfi):
    ALL     -> 'A'   (tum frenler)
    FRONT   -> 'F'   (on frenler)
    REAR    -> 'R'   (arka frenler)
    RELEASE -> 'X'   (serbest birak)
"""
import socket
import time
import serial

# --- Ayarlar ---
SERIAL_PORT = "/dev/ttyACM0"   # Arduino Uno genelde ttyACM0. USB-seri cevirici ise ttyUSB0
BAUD = 115200
TCP_HOST = "0.0.0.0"           # Tum arayuzlerde dinle
TCP_PORT = 5005

CMD_MAP = {
    "ALL": b"A",
    "FRONT": b"F",
    "REAR": b"R",
    "RELEASE": b"X",
}


def main():
    # Arduino'ya baglan
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
    time.sleep(2)  # Arduino reset sonrasi bekle
    print(f"[Pi] Arduino baglandi: {SERIAL_PORT}")

    # TCP sunucu
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((TCP_HOST, TCP_PORT))
    srv.listen(1)
    print(f"[Pi] TCP dinleniyor: 0.0.0.0:{TCP_PORT}")

    while True:
        conn, addr = srv.accept()
        print(f"[Pi] Baglanti: {addr}")
        with conn:
            buffer = ""
            while True:
                data = conn.recv(1024)
                if not data:
                    break
                buffer += data.decode("utf-8", errors="ignore")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    cmd = line.strip().upper()
                    if not cmd:
                        continue
                    byte = CMD_MAP.get(cmd)
                    if byte is None:
                        print(f"[Pi] Bilinmeyen komut: {cmd}")
                        conn.sendall(b"ERR\n")
                        continue
                    ser.write(byte)
                    print(f"[Pi] -> Arduino: {cmd} ({byte!r})")
                    resp = ser.readline().decode(errors="ignore").strip()
                    conn.sendall((resp + "\n").encode())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[Pi] Kapatiliyor.")
