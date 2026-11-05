"""
pipeline_run.py — Cacadu Layer 1 pipeline simulation
Hoodtronics LLC — Cacadu/LoRa-ImageGenAI Codec Pipeline

Runs TX and RX sides in parallel.
  TX: CPU → WROVER #1 (COM7) → RYLR998 → LoRa
  RX: LoRa → RYLR998 → WROVER #2 (COM3) → CPU

Usage:
    python pipeline_run.py
"""

import serial
import threading
import time
import sys

# ── Configuration ─────────────────────────────────────────────────────────────
TX_PORT   = "COM7"
RX_PORT   = "COM3"
BAUD      = 115200
LIC       = 1          # Latency Importance Class (1–9)
RX_TIMEOUT = 360       # seconds to wait for full frame on RX side

# ── Test image: 96×96 grayscale ramp ─────────────────────────────────────────
WIDTH, HEIGHT  = 96, 96
EXPECTED_DATA  = bytes([(i * 256 // (WIDTH * HEIGHT)) & 0xFF
                         for i in range(WIDTH * HEIGHT)])

# ── Shared state ──────────────────────────────────────────────────────────────
rx_result = {"data": None, "lic": None, "done": False, "error": None}

# ── Frame helpers ─────────────────────────────────────────────────────────────
def build_frame(data, lic=LIC):
    length = len(data) + 1
    return (bytes([0xFF, 0xFE, (lic * 100) & 0xFF,
                   (length >> 8) & 0xFF, length & 0xFF])
            + data + bytes([0xFF, 0xFD]))

def parse_frame(buf):
    b = bytes(buf)
    sof = b.find(b'\xff\xfe')
    if sof < 0 or len(b) < sof + 7:
        return None, None
    lic_byte  = b[sof + 2]
    len_field = (b[sof + 3] << 8) | b[sof + 4]
    data_len  = len_field - 1
    end       = sof + 5 + data_len + 2
    if len(b) < end:
        return None, None
    if b[sof + 5 + data_len] != 0xFF or b[end - 1] != 0xFD:
        return None, None
    return bytes(b[sof + 5: sof + 5 + data_len]), lic_byte

# ── RX thread ─────────────────────────────────────────────────────────────────
def rx_worker():
    try:
        ser = serial.Serial(RX_PORT, BAUD, timeout=0.1)
    except Exception as e:
        rx_result["error"] = str(e)
        rx_result["done"]  = True
        return

    raw   = bytearray()
    start = time.time()
    print(f"[RX] Listening on {RX_PORT}...")

    while time.time() - start < RX_TIMEOUT:
        chunk = ser.read(512)
        if chunk:
            raw.extend(chunk)

        # Echo text lines
        while b'\n' in raw:
            idx  = raw.index(b'\n')
            line = raw[:idx].decode(errors='replace').strip()
            raw  = raw[idx + 1:]
            if line:
                print(f"  [WROVER2] {line}")

        # Scan for binary frame
        rb    = bytes(raw)
        data, lic = parse_frame(rb)
        if data is not None:
            rx_result["data"] = data
            rx_result["lic"]  = lic
            rx_result["done"] = True
            ser.close()
            return

    rx_result["error"] = f"Timeout after {RX_TIMEOUT}s — no frame received"
    rx_result["done"]  = True
    ser.close()

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  Cacadu Layer 1 Pipeline — Hoodtronics LLC")
    print(f"  TX: {TX_PORT}  RX: {RX_PORT}  LIC={LIC}")
    print(f"  Test image: {WIDTH}×{HEIGHT} grayscale ({len(EXPECTED_DATA)} bytes)")
    print("=" * 60)

    # Start RX listener
    t = threading.Thread(target=rx_worker, daemon=True)
    t.start()
    time.sleep(1.0)   # let RX open port first

    # Open TX port
    try:
        tx_ser = serial.Serial(TX_PORT, BAUD, timeout=2)
    except Exception as e:
        print(f"[TX] ERROR opening {TX_PORT}: {e}")
        sys.exit(1)

    time.sleep(1.5)
    frame = build_frame(EXPECTED_DATA, LIC)
    print(f"\n[TX] Sending frame: {len(EXPECTED_DATA)} image bytes, "
          f"{len(frame)} total bytes")

    tx_ser.write(frame)
    tx_ser.flush()
    print(f"[TX] Frame sent. Waiting for WROVER #1 to chunk and transmit...\n")

    # Echo WROVER #1 status
    deadline = time.time() + 300
    while time.time() < deadline and not rx_result["done"]:
        if tx_ser.in_waiting:
            line = tx_ser.readline().decode(errors='replace').strip()
            if line:
                print(f"  [WROVER1] {line}")
        time.sleep(0.01)

    tx_ser.close()
    t.join(timeout=120)

    # Result
    print("\n" + "=" * 60)
    if rx_result["error"]:
        print(f"[RESULT] FAIL — {rx_result['error']}")
        sys.exit(1)

    data = rx_result["data"]
    lic  = rx_result["lic"]

    if data is None:
        print("[RESULT] FAIL — RX thread returned no data (no frame received)")
        sys.exit(1)

    print(f"[RESULT] Frame received — {len(data)} bytes  LIC={lic}")

    if data == EXPECTED_DATA:
        print("[RESULT] ✓  MATCH — frame is identical to source image")
        print("[RESULT]    Layer 1 pipeline CONFIRMED")
    else:
        mismatches = sum(1 for a, b in zip(data, EXPECTED_DATA) if a != b)
        extra = abs(len(data) - len(EXPECTED_DATA))
        print(f"[RESULT] ✗  MISMATCH — {mismatches} byte(s) differ, "
              f"{extra} length delta")
    print("=" * 60)

if __name__ == "__main__":
    main()
