#!/usr/bin/env python3
"""
lora_rate_test.py — LoRa drop-rate sweep test
Sweeps AT+CRFOP (TX power) and inter-chunk delay.
Runs 2 passes per configuration, collects drop data, saves to CSV.

Usage:
  python lora_rate_test.py                    # full sweep (~2.5 hrs)
  python lora_rate_test.py --quick            # power sweep only, 1024-byte frame
  python lora_rate_test.py --power-only       # power sweep, full 9216-byte frame
  python lora_rate_test.py --delay-only       # delay sweep, full frame
  python lora_rate_test.py --tx COM7 --rx COM3 --quick
  python lora_rate_test.py --quick --powers 5,22   # focused test at two power levels
  python lora_rate_test.py --quick --powers 22 --recover  # ARQ recovery mode

ARQ recovery (--recover flag, requires wrover1_tx_v7.ino or later):
  After each frame, always retry chunk 1 (assumed timing-dropped).
  Also retry one additional chunk if it dropped.  Max 2 retries per frame.
  Reports pre-ARQ and post-ARQ loss separately.

COM ports default to COM7 (TX/WROVER1) and COM3 (RX/WROVER2).
"""

import serial, time, re, csv, os, sys, argparse, random, struct
from datetime import datetime

# ─── Config ──────────────────────────────────────────────────────────────────
TX_PORT   = "COM7"
RX_PORT   = "COM3"
BAUD      = 115200

# SF9/BW125 airtime per 113-byte chunk ≈ 633ms.
# AT+SEND timeout = 3000ms. Effective per-chunk time = 3000 + delay_ms.
# Absolute minimum delay must be > 633ms to avoid overlap.

POWER_SWEEP   = [5, 8, 11, 14, 17, 22]   # AT+CRFOP dBm values
DELAY_SWEEP   = [700, 800, 915, 1100, 1400]  # inter-chunk delay ms

FULL_FRAME_BYTES  = 9216
QUICK_FRAME_BYTES = 1024
RUNS_PER_CONFIG   = 2

# ARQ settings (used when --recover flag is set)
ARQ_MAX_RETRIES       = 2      # max total retry chunks per frame
ARQ_PRE_RETRY_DELAY_S = 1.0   # wait after FRAME_DONE before first retry
                               # RX RYLR998 needs time to finish binary output
                               # before it can cleanly receive the retry packet

# RX timeout: must cover full transmission.
# At delay=700ms: (3000+700)*84/1000 = ~311s → use 420s
# At delay=1400ms: (3000+1400)*84/1000 = ~370s → use 480s
# We calculate per-run below.

OUTPUT_CSV = "lora_sweep_results.csv"

# ─── Helpers ─────────────────────────────────────────────────────────────────
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")

def make_frame(n_bytes: int) -> bytes:
    """Deterministic test frame — same every run for reproducibility."""
    random.seed(0xCACAD0)
    return bytes([random.randint(0, 255) for _ in range(n_bytes)])

def frame_to_hex(data: bytes) -> str:
    return data.hex().upper()

def compute_rx_timeout(delay_ms: int, n_chunks: int) -> int:
    """Conservatively: (3000 + delay_ms) * n_chunks / 1000 * 1.3 + 60 seconds."""
    per_chunk_s = (3000 + delay_ms) / 1000.0
    return int(per_chunk_s * n_chunks * 1.3) + 60

def chunk_count(frame_bytes: int, chunk_size: int = 110) -> int:
    return (frame_bytes + chunk_size - 1) // chunk_size

# ─── Serial helpers ───────────────────────────────────────────────────────────
def open_ports(tx_port, rx_port):
    tx = serial.Serial(tx_port, BAUD, timeout=0.1)
    rx = serial.Serial(rx_port, BAUD, timeout=0.1)
    time.sleep(0.5)
    tx.reset_input_buffer(); tx.reset_output_buffer()
    rx.reset_input_buffer(); rx.reset_output_buffer()
    return tx, rx

def send_line(ser, line: str):
    ser.write((line + "\n").encode())
    ser.flush()

def read_line(ser, timeout_s=0.1) -> str:
    ser.timeout = timeout_s
    raw = ser.readline()
    if raw:
        return raw.decode(errors="replace").strip()
    return ""

def drain(ser, seconds=0.5):
    """Read and discard all available bytes for `seconds`."""
    t = time.time()
    while time.time() - t < seconds:
        ser.read(ser.in_waiting or 1)

# ─── Send config commands to WROVER1 ─────────────────────────────────────────
def configure_tx(tx_ser, power_dBm: int, delay_ms: int):
    log(f"  Configuring TX: POWER={power_dBm} dBm  DELAY={delay_ms} ms")
    send_line(tx_ser, f"CMD:POWER={power_dBm}")
    # Wait for configure echo (up to 15s for AT commands to complete)
    deadline = time.time() + 15
    while time.time() < deadline:
        line = read_line(tx_ser, 0.2)
        if line:
            log(f"  TX>> {line}")
        if "[TX] Configured" in line:
            break
    send_line(tx_ser, f"CMD:DELAY={delay_ms}")
    time.sleep(0.2)
    line = read_line(tx_ser, 0.5)
    if line: log(f"  TX>> {line}")
    send_line(tx_ser, "CMD:START")
    time.sleep(0.2)

# ─── Send frame to WROVER1 ────────────────────────────────────────────────────
def send_frame(tx_ser, frame: bytes):
    hex_str = frame_to_hex(frame)
    send_line(tx_ser, str(len(frame)))          # frame byte count
    tx_ser.write(hex_str.encode()); tx_ser.flush()
    log(f"  Frame sent: {len(frame)} bytes → {len(hex_str)} hex chars")

# ─── ARQ: retry a single chunk and wait for RX confirmation ─────────────────
def arq_retry_chunk(tx_ser, rx_ser, chunk_n: int, timeout_s: int = 15) -> bool:
    """
    Send CMD:RETRY=N to TX, monitor both ports until RX confirms receipt
    or RETRY_DONE appears on TX side.  Returns True if RX logged the chunk.
    chunk_n is 1-based (matches firmware CMD:RETRY=N convention).
    """
    log(f"  [ARQ] >>> Retrying chunk {chunk_n}")
    send_line(tx_ser, f"CMD:RETRY={chunk_n}")

    rx_got_it = False
    deadline  = time.time() + timeout_s

    while time.time() < deadline:
        # TX side
        tx_line = read_line(tx_ser, 0.05)
        if tx_line:
            log(f"  TX: {tx_line}")
            if "RETRY_DONE" in tx_line:
                time.sleep(0.6)   # let last RX event arrive
                break

        # RX side
        rx_line = read_line(rx_ser, 0.05)
        if not rx_line:
            continue
        log(f"  RX: {rx_line}")

        # Did RX log this chunk?
        m = CHUNK_RE.search(rx_line)
        if m and int(m.group(1)) == chunk_n:
            rx_got_it = True
            continue

        # Also accept raw +RCV with matching first byte
        m = RCV_RE.search(rx_line)
        if m:
            payload_hex = m.group(1)
            if len(payload_hex) >= 2:
                chunk_idx = int(payload_hex[0:2], 16)
                if chunk_idx + 1 == chunk_n:
                    rx_got_it = True

    if rx_got_it:
        log(f"  [ARQ] <<< Chunk {chunk_n} RECOVERED ✓")
    else:
        log(f"  [ARQ] <<< Chunk {chunk_n} still DROPPED")
    return rx_got_it

# ─── Monitor WROVER2 RX ───────────────────────────────────────────────────────
RCV_RE  = re.compile(r'\+RCV=\d+,\d+,([0-9A-Fa-f]+),-?(\d+),(-?\d+)')
ERR_RE  = re.compile(r'\+ERR=(\d+)')
# Matches WROVER2 output like: "Chunk 5/84 data=110B RSSI=-27 SNR=8"
CHUNK_RE = re.compile(r'Chunk\s+(\d+)/(\d+).*RSSI=(-?\d+).*SNR=(-?\d+)')
# Matches firmware "FRAME_COMPLETE chunks=10 data_bytes=1024"
FRAME_COMPLETE_RE = re.compile(r'FRAME_COMPLETE\s+chunks=(\d+)\s+data_bytes=(\d+)')

# Directory where received images are saved (local, not OneDrive)
IMAGE_SAVE_DIR = r"C:\Users\Hoodt\Documents\CacaduImages"

def _save_rx_image(rx_ser, data_bytes: int) -> str | None:
    """
    Read exactly data_bytes raw binary bytes from rx_ser and save as .jpg.
    Returns the saved file path, or None if the read came up short.
    At 115200 baud, 1024 bytes takes ~90 ms; allow 5 s for larger frames.
    """
    import os
    timeout_s = max(5.0, data_bytes / 10000)  # ~10 kB/s safe lower bound
    old_timeout = rx_ser.timeout
    rx_ser.timeout = timeout_s
    raw = rx_ser.read(data_bytes)
    rx_ser.timeout = old_timeout

    if len(raw) != data_bytes:
        log(f"  [IMG] WARN: expected {data_bytes} B, got {len(raw)} B — skipping save")
        return None

    os.makedirs(IMAGE_SAVE_DIR, exist_ok=True)
    fname = f"rx_frame_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
    path  = os.path.join(IMAGE_SAVE_DIR, fname)
    with open(path, 'wb') as f:
        f.write(raw)
    log(f"  [IMG] Saved {data_bytes} bytes → {path}")
    return path

def run_rx_monitor(tx_ser, rx_ser, total_chunks: int, timeout_s: int):
    """
    Monitor both ports until FRAME_DONE or timeout.
    Returns dict: {chunk_num: {'rssi': int, 'snr': int}} for received,
                  and set of dropped chunk numbers.
    """
    received   = {}   # chunk_num -> {'rssi', 'snr'}
    dropped    = set()
    frame_done = False
    deadline   = time.time() + timeout_s

    while time.time() < deadline:
        # Read TX side
        tx_line = read_line(tx_ser, 0.05)
        if tx_line:
            log(f"  TX: {tx_line}")
            if "FRAME_DONE" in tx_line:
                frame_done = True
                time.sleep(1.0)   # let last RX event arrive
                break

        # Read RX side
        rx_line = read_line(rx_ser, 0.05)
        if not rx_line:
            continue
        log(f"  RX: {rx_line}")

        # Detect full frame completion — capture binary image immediately
        m = FRAME_COMPLETE_RE.search(rx_line)
        if m:
            n_chunks_done = int(m.group(1))
            n_data_bytes  = int(m.group(2))
            if n_chunks_done > 0 and n_data_bytes > 0:
                _save_rx_image(rx_ser, n_data_bytes)
            continue  # skip other parsers for this line

        # Parse chunk-received event from WROVER2 firmware
        m = CHUNK_RE.search(rx_line)
        if m:
            chunk_n = int(m.group(1))
            rssi    = int(m.group(3))
            snr     = int(m.group(4))
            received[chunk_n] = {'rssi': rssi, 'snr': snr}
            continue

        # Parse raw +RCV from RYLR998 (if WROVER2 echoes it)
        m = RCV_RE.search(rx_line)
        if m:
            # Extract chunk number from first 2 hex chars of payload
            payload_hex = m.group(1)
            if len(payload_hex) >= 4:
                chunk_idx  = int(payload_hex[0:2], 16)
                chunk_n    = chunk_idx + 1
                rssi       = -int(m.group(2))
                snr        = int(m.group(3))
                received[chunk_n] = {'rssi': rssi, 'snr': snr}
            continue

        # Parse +ERR=12 (CRC error = dropped chunk)
        m = ERR_RE.search(rx_line)
        if m and m.group(1) == "12":
            # We can't know exact chunk number from ERR alone.
            # Mark a synthetic "unknown" drop — we'll reconcile later.
            dropped.add(f"err_{len(dropped)}")

    # Reconcile: any chunk in 1..total_chunks NOT in received is dropped
    confirmed_drops = set(range(1, total_chunks + 1)) - set(received.keys())

    return received, confirmed_drops, frame_done

# ─── Single test run ──────────────────────────────────────────────────────────
def single_run(tx_ser, rx_ser, power_dBm: int, delay_ms: int,
               frame: bytes, run_idx: int, recover: bool = False):
    """
    Run one frame transmission.
    If recover=True, applies ARQ after the initial pass:
      - Retries chunk 1 only if it actually dropped (timing-drop likely).
      - Fills remaining slots with other dropped chunks (lowest index first).
      - Max ARQ_MAX_RETRIES total retries.
    """
    n_chunks  = chunk_count(len(frame))
    rx_timeout = compute_rx_timeout(delay_ms, n_chunks)

    log(f"── Run {run_idx}  POWER={power_dBm}dBm  DELAY={delay_ms}ms  "
        f"frame={len(frame)}B  {n_chunks} chunks  timeout={rx_timeout}s"
        f"{'  [ARQ ON]' if recover else ''}")

    # Drain buffers
    drain(tx_ser, 0.3); drain(rx_ser, 0.3)

    # Configure TX with new params
    configure_tx(tx_ser, power_dBm, delay_ms)

    # Send frame
    t_start = time.time()
    send_frame(tx_ser, frame)

    # Monitor initial pass
    received, dropped, frame_done = run_rx_monitor(
        tx_ser, rx_ser, n_chunks, rx_timeout)

    n_drop_pre  = len(dropped)
    loss_pct_pre = n_drop_pre / n_chunks * 100.0
    log(f"  PRE-ARQ:  {len(received)}/{n_chunks} received  "
        f"{n_drop_pre} dropped  loss={loss_pct_pre:.1f}%  "
        f"{'FRAME_DONE' if frame_done else 'TIMEOUT'}")

    # ── ARQ recovery ─────────────────────────────────────────────────────────
    arq_recovered  = []   # chunk numbers successfully retried
    arq_failed     = []   # chunk numbers that still didn't arrive
    retries_used   = 0

    if recover:
        # SLOT 1: retry chunk 1 only if it actually dropped.
        # If chunk 1 arrived cleanly, skip slot 1 — don't waste a retry
        # or trigger +ERR=12 on a frame RX already completed.
        # Pre-retry delay: let RX flush FRAME_COMPLETE binary output first.
        time.sleep(ARQ_PRE_RETRY_DELAY_S)
        if 1 in dropped:
            ok = arq_retry_chunk(tx_ser, rx_ser, 1)
            retries_used += 1
            if ok:
                arq_recovered.append(1)
                received[1] = {'rssi': None, 'snr': None, 'retry': True}
                dropped.discard(1)
            else:
                arq_failed.append(1)

        # Fill remaining retry slots with other dropped chunks (lowest first).
        other_drops = sorted(dropped - {1})
        while other_drops and retries_used < ARQ_MAX_RETRIES:
            c = other_drops.pop(0)
            ok = arq_retry_chunk(tx_ser, rx_ser, c)
            retries_used += 1
            if ok:
                arq_recovered.append(c)
                received[c] = {'rssi': None, 'snr': None, 'retry': True}
                dropped.discard(c)
            else:
                arq_failed.append(c)

    # ── Final stats ───────────────────────────────────────────────────────────
    elapsed  = time.time() - t_start
    n_rx     = len(received)
    n_drop   = len(dropped)
    loss_pct = n_drop / n_chunks * 100.0

    rssi_vals = [v['rssi'] for v in received.values()
                 if v.get('rssi') is not None]
    rssi_min  = min(rssi_vals) if rssi_vals else None
    rssi_max  = max(rssi_vals) if rssi_vals else None
    rssi_med  = sorted(rssi_vals)[len(rssi_vals)//2] if rssi_vals else None

    result = {
        "power_dBm":       power_dBm,
        "delay_ms":        delay_ms,
        "run_idx":         run_idx,
        "frame_bytes":     len(frame),
        "n_chunks":        n_chunks,
        "n_received":      n_rx,
        "n_dropped":       n_drop,
        "loss_pct":        round(loss_pct, 2),
        "loss_pct_pre_arq": round(loss_pct_pre, 2),
        "arq_retries":     retries_used,
        "arq_recovered":   ",".join(str(c) for c in arq_recovered),
        "arq_failed":      ",".join(str(c) for c in arq_failed),
        "rssi_min":        rssi_min,
        "rssi_max":        rssi_max,
        "rssi_median":     rssi_med,
        "frame_done":      frame_done,
        "elapsed_s":       round(elapsed, 1),
        "chunks_detail":   {c: received[c] for c in sorted(received)},
    }

    tag = "✓ PASS" if loss_pct == 0.0 else (
          "~ MARGINAL" if loss_pct <= 10.0 else "✗ FAIL")
    log(f"  FINAL:    {n_rx}/{n_chunks} received  {n_drop} dropped  "
        f"loss={loss_pct:.1f}%  rssi={rssi_min}..{rssi_max}dBm  "
        f"retries={retries_used}  recovered={arq_recovered}  {tag}")

    return result

# ─── Main sweep ───────────────────────────────────────────────────────────────
# ─── Human-in-the-loop helpers ───────────────────────────────────────────────
def print_config_summary(config_results):
    """Print a clean 2-run summary for one configuration."""
    if not config_results:
        return
    r = config_results[0]
    losses = [x["loss_pct"] for x in config_results]
    avg_loss = sum(losses) / len(losses)
    n_chunks = r["n_chunks"]
    tag = ("✓ PASS" if avg_loss <= 5.0
           else "~ MARGINAL (cap fix)" if avg_loss <= 7.0
           else "✗ FAIL")
    print()
    print("┌─────────────────────────────────────────────────────────┐")
    print(f"│  CONFIG:  POWER={r['power_dBm']} dBm   DELAY={r['delay_ms']} ms")
    for i, res in enumerate(config_results):
        print(f"│  Run {res['run_idx']}: {res['n_received']}/{n_chunks} received  "
              f"{res['n_dropped']} dropped  {res['loss_pct']:.1f}%  "
              f"{'DONE' if res['frame_done'] else 'TIMEOUT'}")
    print(f"│  ─────────────────────────────────────────────────────")
    print(f"│  AVG LOSS: {avg_loss:.1f}%   {tag}")
    print("└─────────────────────────────────────────────────────────┘")
    print()

def human_pause(config_results, csv_path, all_results, interactive):
    """
    Save results so far, then optionally wait for human go-ahead.
    Returns 'continue', 'skip', or 'quit'.
    """
    save_csv(all_results, csv_path)   # incremental save after every config
    if not interactive:
        return "continue"
    print("  Press ENTER to run next config,  'q' + ENTER to stop sweep,")
    print(f"  's' + ENTER to skip current sweep type.")
    try:
        choice = input("  > ").strip().lower()
    except EOFError:
        return "continue"
    if choice == "q":
        log("Sweep aborted by operator.")
        return "quit"
    if choice == "s":
        log("Skipping to next sweep type.")
        return "skip"
    return "continue"

def run_sweep(tx_port, rx_port, frame: bytes,
              do_power: bool, do_delay: bool, runs_per: int,
              interactive: bool = True, csv_path: str = OUTPUT_CSV,
              recover: bool = False):
    """
    Human-in-the-loop sweep.
    After every 2-run config block:
      - prints averaged results
      - saves CSV (incremental)
      - if interactive=True: pauses for human go-ahead before next config
    """
    all_results = []

    tx_ser, rx_ser = open_ports(tx_port, rx_port)
    log(f"Ports open: TX={tx_port}  RX={rx_port}")
    log(f"Frame: {len(frame)} bytes  chunks: {chunk_count(len(frame))}")
    if interactive:
        log("INTERACTIVE MODE — pauses after each config block for your review.")
    else:
        log("AUTO MODE — runs all configs without pausing.")
    if recover:
        log(f"ARQ MODE — chunk 1 retried if dropped; up to {ARQ_MAX_RETRIES} retries per frame.")

    try:
        # ── Power sweep ────────────────────────────────────────────────────
        if do_power:
            log("═══ POWER SWEEP ═══")
            fixed_delay = 915   # keep delay at baseline
            for pwr in POWER_SWEEP:
                config_batch = []
                for r in range(1, runs_per + 1):
                    res = single_run(tx_ser, rx_ser, pwr, fixed_delay,
                                     frame, r, recover=recover)
                    res["sweep_type"] = "power"
                    all_results.append(res)
                    config_batch.append(res)
                    if r < runs_per:
                        time.sleep(5)   # brief cooldown between the 2 runs

                print_config_summary(config_batch)
                action = human_pause(config_batch, csv_path,
                                     all_results, interactive)
                if action == "quit":
                    return all_results
                if action == "skip":
                    break
                time.sleep(3)   # module settle before next config

        # ── Delay sweep ────────────────────────────────────────────────────
        if do_delay:
            log("═══ DELAY SWEEP ═══")
            fixed_power = 5     # low power to isolate timing effect
            for dly in DELAY_SWEEP:
                config_batch = []
                for r in range(1, runs_per + 1):
                    res = single_run(tx_ser, rx_ser, fixed_power, dly,
                                     frame, r, recover=recover)
                    res["sweep_type"] = "delay"
                    all_results.append(res)
                    config_batch.append(res)
                    if r < runs_per:
                        time.sleep(5)

                print_config_summary(config_batch)
                action = human_pause(config_batch, csv_path,
                                     all_results, interactive)
                if action == "quit":
                    return all_results
                if action == "skip":
                    break
                time.sleep(3)

    finally:
        tx_ser.close(); rx_ser.close()

    return all_results

# ─── Save CSV ─────────────────────────────────────────────────────────────────
def save_csv(results, path):
    if not results: return
    fields = ["sweep_type","power_dBm","delay_ms","run_idx","frame_bytes",
              "n_chunks","n_received","n_dropped","loss_pct","loss_pct_pre_arq",
              "arq_retries","arq_recovered","arq_failed",
              "rssi_min","rssi_max","rssi_median","frame_done","elapsed_s"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)
    log(f"CSV saved: {path}")

# ─── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tx",          default=TX_PORT)
    p.add_argument("--rx",          default=RX_PORT)
    p.add_argument("--quick",       action="store_true",
                   help="Power sweep only, 1024-byte frame")
    p.add_argument("--power-only",  action="store_true",
                   help="Power sweep, full 9216-byte frame")
    p.add_argument("--delay-only",  action="store_true",
                   help="Delay sweep, full frame")
    p.add_argument("--auto",        action="store_true",
                   help="Run all configs without pausing (no human-in-the-loop)")
    p.add_argument("--powers",      default=None,
                   help="Comma-separated power levels to test, e.g. --powers 5,22")
    p.add_argument("--recover",     action="store_true",
                   help="Enable ARQ recovery: auto-retry chunk 1 + 1 other drop "
                        "(max 2 retries). Requires wrover1_tx_v7.ino or later.")
    p.add_argument("--out",         default=OUTPUT_CSV)
    args = p.parse_args()

    # Override POWER_SWEEP if --powers specified
    if args.powers:
        POWER_SWEEP[:] = [int(x.strip()) for x in args.powers.split(",")]
        log(f"POWER_SWEEP overridden to: {POWER_SWEEP}")

    interactive = not args.auto    # default = human-in-the-loop

    if args.quick:
        frame = make_frame(QUICK_FRAME_BYTES)
        do_power, do_delay = True, False
        log("QUICK MODE: power sweep, 1024-byte frame")
    elif args.power_only:
        frame = make_frame(FULL_FRAME_BYTES)
        do_power, do_delay = True, False
    elif args.delay_only:
        frame = make_frame(FULL_FRAME_BYTES)
        do_power, do_delay = False, True
    else:
        frame = make_frame(FULL_FRAME_BYTES)
        do_power, do_delay = True, True
        log("FULL SWEEP: power + delay, 9216-byte frame")

    log(f"Mode: {'AUTO (no pauses)' if args.auto else 'INTERACTIVE (pauses after each 2-run block)'}")

    results = run_sweep(args.tx, args.rx, frame, do_power, do_delay,
                        RUNS_PER_CONFIG, interactive=interactive,
                        csv_path=args.out, recover=args.recover)
    save_csv(results, args.out)
    log(f"Done. {len(results)} test runs completed.")
    log(f"Run: python lora_rate_graph.py {args.out}")
