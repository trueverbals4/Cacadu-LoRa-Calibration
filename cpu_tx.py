"""
cpu_tx.py — Cacadu Layer 1 pipeline simulation TX side
Runs on CPU. Sends a test frame to WROVER #1 (COM7) in Cacadu frame format.
Frame: SOF(FF FE) + LIC + LEN_HI + LEN_LO + DATA + EOF(FF FD)
"""

import serial
import time
import sys

COM_PORT = "COM7"
BAUD     = 115200
LIC      = 1       # Latency Importance Class (1-9); sent as LIC*100 = 100

# Build a synthetic 96x96 grayscale test image (9216 bytes, ramp pattern)
WIDTH, HEIGHT = 96, 96
image_data = bytes([(i * 256 // (WIDTH * HEIGHT)) & 0xFF for i in range(WIDTH * HEIGHT)])

def build_frame(data, lic=LIC):
    length = len(data) + 1   # LEN includes one EOF byte per protocol
    hi = (length >> 8) & 0xFF
    lo = length & 0xFF
    frame = bytes([0xFF, 0xFE, lic * 100 & 0xFF, hi, lo]) + data + bytes([0xFF, 0xFD])
    return frame

def main():
    frame = build_frame(image_data)
    print(f"[CPU TX] Frame built: {len(image_data)} image bytes, {len(frame)} total bytes")
    print(f"[CPU TX] LIC={LIC}  LEN={len(image_data)+1}  SOF=FFFE  EOF=FFFD")

    try:
        ser = serial.Serial(COM_PORT, BAUD, timeout=2)
    except Exception as e:
        print(f"[CPU TX] ERROR opening {COM_PORT}: {e}")
        sys.exit(1)

    time.sleep(1.5)
    print(f"[CPU TX] Connected to {COM_PORT}. Sending frame in 2s...")
    time.sleep(2)

    ser.write(frame)
    ser.flush()
    print(f"[CPU TX] Frame sent ({len(frame)} bytes). Waiting for WROVER ACK...")

    # Read any echo/status back from WROVER #1
    deadline = time.time() + 10
    while time.time() < deadline:
        if ser.in_waiting:
            line = ser.readline().decode(errors='replace').strip()
            if line:
                print(f"[WROVER1] {line}")
                if "FRAME_DONE" in line or "TX_COMPLETE" in line:
                    break

    ser.close()
    print("[CPU TX] Done.")

if __name__ == "__main__":
    main()
