# Cacadu LoRa Calibration System

**Hoodtronics LLC — Indianapolis, IN**
`NSF SBIR Phase I Research Tool`

---

## Overview

The Cacadu LoRa Calibration System optimizes LoRa radio transmission signaling for embedded imaging pipelines.
It was purpose-built to characterize, tune, and validate LoRa link performance under real hardware conditions.
The system sweeps TX power levels and inter-packet delay intervals systematically.
It measures packet loss, ARQ recovery efficiency, and RSSI across all tested configurations.
Results are logged to CSV for analysis and reproducibility.

This tool directly supports the **Cacadu IncrementalPCA codec pipeline** — a compression system for UAS imagery.
The codec transmits 96×96-pixel PCA vectors as single LoRa packets over RYLR998 modules.
Calibration ensures the radio link is stable before any codec deployment.

> **This is an active research instrument.**
> Architecture, measurement parameters, and run configurations are continuously improved with each field campaign.

---

## Primary Purpose

**Optimize LoRa transmission signaling** for low-bandwidth, long-range embedded systems.

The calibration system answers three core questions:

1. What TX power level minimizes energy while maintaining acceptable packet loss?
2. What inter-packet delay prevents radio buffer collisions at each power setting?
3. How effectively does ARQ retransmission recover dropped chunks per frame?

---

## Use Cases

| Domain | Application |
|---|---|
| **UAS / Drone R&D** | Validate LoRa link budget before airframe integration |
| **IoT Sensor Networks** | Tune RYLR998 modules for field deployment |
| **Precision Agriculture** | Optimize long-range telemetry in GPS-denied zones |
| **Search & Rescue** | Characterize LoRa mesh links under interference |
| **Academic Research** | Reproducible RF link sweep data for publication |
| **SBIR / Federal R&D** | Documented calibration evidence for proposal support |
| **Any UART LoRa System** | Generalizes to AT-command LoRa modules beyond RYLR998 |

---

## Hardware

| Node | Role | MCU |
|---|---|---|
| **WROVER1** | TX — receives frame from CPU, chunks and transmits via LoRa | ESP32-WROVER |
| **WROVER2** | RX — receives LoRa chunks, reassembles frame, forwards to CPU | ESP32-WROVER |
| **LoRa Radio** | RYLR998 (AT-command UART) | — |
| **Host PC** | Runs calibration sweep scripts | Windows / Linux |

**Default radio settings:** SF9 · BW125 · CR4/5 · ~633 ms airtime per 113-byte chunk

---

## Software Components

```
CacaduLoraonly/
├── lora_rate_test.py    # Full power + delay sweep with ARQ
├── pipeline_run.py      # Layer 1 end-to-end pipeline verification
├── cpu_rx.py            # RX-side frame listener and validator
└── lora_sweep_results.csv  # Calibration output log
```

### `lora_rate_test.py` — Main Calibration Tool

Sweeps TX power and delay, logs all metrics to CSV.

**Power sweep:** `[5, 8, 11, 14, 17, 22]` dBm
**Delay sweep:** `[700, 800, 915, 1100, 1400]` ms
**Runs per config:** 2 (configurable)
**Chunk size:** 110 bytes (RYLR998 payload limit)
**ARQ:** retries chunk 1 + 1 additional dropped chunk; max 2 retries per frame

```bash
# Full sweep (requires wrover1_tx_v7 firmware for ARQ)
python lora_rate_test.py --recover

# Quick single-frame test
python lora_rate_test.py --quick
```

### `pipeline_run.py` — Layer 1 Pipeline Verification

Sends a 96×96 grayscale ramp image (9216 bytes) end-to-end.
Verifies byte-perfect reconstruction at the CPU RX side.

```bash
python pipeline_run.py
```

### Frame Protocol

```
[0xFF, 0xFE, LIC_byte, length_hi, length_lo, ...data..., 0xFF, 0xFD]
```

| Field | Description |
|---|---|
| `0xFF 0xFE` | Start-of-frame marker |
| `LIC_byte` | Latency Importance Class × 100 |
| `length_hi/lo` | Payload length (big-endian) |
| `0xFF 0xFD` | End-of-frame marker |

---

## Pass Criteria

| Loss (post-ARQ) | Result |
|---|---|
| ≤ 5% | ✅ PASS |
| 5% – 7% | ⚠️ MARGINAL |
| > 7% | ❌ FAIL |

---

## Staff Dashboard

A browser-based calibration dashboard is available for field staff.
It visualizes sweep results without requiring Python or data science tools.

**Features:**
- Drag-and-drop CSV import
- PASS / MARGINAL / FAIL badges per run
- Bar chart: packet loss vs. TX power
- Line chart: RSSI vs. TX power
- ARQ recovery comparison chart
- Auto-loads the latest lab results on open

> Dashboard hosted at: `cacadu_cal_ui.html` (see `/dashboard` folder)

---

## Configuration

Edit constants at the top of `lora_rate_test.py`:

```python
TX_PORT          = "COM7"      # WROVER1 serial port
RX_PORT          = "COM3"      # WROVER2 serial port
BAUD             = 115200
POWER_SWEEP      = [5, 8, 11, 14, 17, 22]   # dBm
DELAY_SWEEP      = [700, 800, 915, 1100, 1400]  # ms
RUNS_PER_CONFIG  = 2
CHUNK_SIZE       = 110         # bytes
QUICK_FRAME_BYTES = 1024
FULL_FRAME_BYTES  = 9216       # 96×96 grayscale
```

---

## Roadmap — Continuous Improvement

This system evolves with each research campaign. Planned enhancements include:

- [ ] SF7–SF12 spreading factor sweep
- [ ] BW125 / BW250 / BW500 bandwidth comparison
- [ ] SNR and noise floor logging per chunk
- [ ] Multi-node relay chain calibration (3+ hops)
- [ ] Automated optimal-config selection and report generation
- [ ] Integration with IncrementalPCA codec compression metrics
- [ ] Real-time dashboard streaming (WebSocket)
- [ ] Environmental variable logging (temperature, humidity, range)

> Architecture, measurement parameters, and run configurations are updated continuously.
> All changes are versioned and documented in the commit history.

---

## Related Research

This tool supports the following publication:

> **"IncrementalPCA-Based Image Compression for LoRa-Constrained UAS Telemetry"**
> Hoodtronics LLC — Indianapolis, IN
> *NSF SBIR Phase I — UEI: Z3TLHYQ1QMT3*

Key result: **122.9:1 compression ratio at 35.6 dB PSNR** on 96×96 images.
Single LoRa packet transmission at SF9 verified end-to-end.

---

## License

Released under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.
See `LICENSE` for full terms.

---

## Attribution

**Hoodtronics LLC — Indianapolis, IN**
Research & Development | Embedded AI Systems

> *"Built to measure. Improved to perform."*

---

*NSF SBIR Phase I Research Instrument — Active Development*
