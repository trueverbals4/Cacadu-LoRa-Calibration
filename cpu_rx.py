"""
cpu_rx.py — Cacadu Layer 1 pipeline simulation RX side
Runs on CPU. Reads reassembled frame from WROVER #2 (COM3).
Verifies it matches the original test image.
"""

import serial
import time
import sys

COM_PORT = "COM3"
BAUD     = 115200

WIDTH, HEIGHT = 96, 96
EXPECTED_DATA = bytes([(i * 256 // (WIDTH * HEIGHT)) & 0xFF
                        for i in range(WIDTH * HEIGHT)])

def parse_frame(buf):
    """Parse Cacadu frame. Returns (data, lic) or (None, None)."""
    if len(buf) < 6:
        return None, None
    if buf[0] != 0xFF or buf[1] != 0xFE:
        return None, None
    lic       = buf[2]
    len_field = (buf[3] << 8) | buf[4]
    data_len  = len_field - 1
    if data_len < 0 or len(buf) < 5 + data_len + 2:
        return None, None
    data = buf[5 : 5 + data_len]
    if buf[5 + data_len] != 0xFF or buf[5 + data_len + 1] != 0xFD:
        return None, None
    return bytes(data), lic

def main():
    try:
        ser = serial.Serial(COM_PORT, BAUD, timeout=0.1)
    except Exception as e:
        print(f"[CPU RX] ERROR opening {COM_PORT}: {e}")
        sys.exit(1)

    print(f"[CPU RX] Listening on {COM_PORT} — waiting for frame...")
    print(f"[CPU RX] Expecting {len(EXPECTED_DATA)} bytes of test image data")

    raw_buf   = bytearray()
    frame_buf = bytearray()
    in_frame  = False
    deadline  = time.time() + 120   # 2-minute timeout

    while time.time() < deadline:
        chunk = ser.read(256)
        if chunk:
            raw_buf.extend(chunk)

            # Echo any text lines to console
            while b'\n' in raw_buf:
                idx  = raw_buf.index(b'\n')
                line = raw_buf[:idx].decode(errors='replace').strip()
                raw_buf = raw_buf[idx + 1:]
                if line:
                    print(f"[WROVER2] {line}")

        # Also scan raw_buf for binary frame
        rb = bytes(raw_buf)
        sof = rb.find(b'\xff\xfe')
        if sof >= 0 and len(rb) >= sof + 6:
            len_field = (rb[sof + 3] << 8) | rb[sof + 4]
            data_len  = len_field - 1
            frame_end = sof + 5 + data_len + 2
            if len(rb) >= frame_end:
                candidate = rb[sof:frame_end]
                data, lic = parse_frame(bytearray(candidate))
                if data is not None:
                    print(f"\n[CPU RX] FRAME RECEIVED  lic={lic}  data_len={len(data)}")
                    if data == EXPECTED_DATA:
                        print("[CPU RX] ✓ MATCH — frame data is identical to transmitted image")
                    else:
                        mismatches = sum(1 for a, b in zip(data, EXPECTED_DATA) if a != b)
                        print(f"[CPU RX] ✗ MISMATCH — {mismatches} byte(s) differ"
                              f" (got {len(data)} B, expected {len(EXPECTED_DATA)} B)")
                    raw_buf = bytearray(rb[frame_end:])
                    break

    else:
        print("[CPU RX] TIMEOUT — no complete frame received in 120s")

    ser.close()
    print("[CPU RX] Done.")

if __name__ == "__main__":
    main()
