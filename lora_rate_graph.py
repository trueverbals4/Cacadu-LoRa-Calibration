#!/usr/bin/env python3
"""
lora_rate_graph.py — Correlation graphs from lora_rate_test.py CSV output.

Usage:
  python lora_rate_graph.py lora_sweep_results.csv
  python lora_rate_graph.py lora_sweep_results.csv --out sweep_analysis.png

Produces:
  - Panel 1: TX Power (dBm) vs Drop Rate % — both runs + average line
  - Panel 2: Inter-chunk Delay (ms) vs Drop Rate %
  - Panel 3: TX Power vs RSSI (median, min, max band)
  - Panel 4: Summary table — all configs with PASS/MARGINAL/FAIL tags
"""

import csv, sys, argparse, os
import statistics

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.lines import Line2D
    HAVE_MPL = True
except ImportError:
    HAVE_MPL = False

# ─── Thresholds ──────────────────────────────────────────────────────────────
TARGET_PCT   = 5.0    # hard target
MARGINAL_PCT = 7.0    # fixable with bypass caps

# ─── Load CSV ─────────────────────────────────────────────────────────────────
def load(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows.append({
                "sweep_type": r["sweep_type"],
                "power_dBm":  int(r["power_dBm"]),
                "delay_ms":   int(r["delay_ms"]),
                "run_idx":    int(r["run_idx"]),
                "n_chunks":   int(r["n_chunks"]),
                "n_received": int(r["n_received"]),
                "n_dropped":  int(r["n_dropped"]),
                "loss_pct":   float(r["loss_pct"]),
                "rssi_min":   int(r["rssi_min"]) if r["rssi_min"] not in ("","None") else None,
                "rssi_max":   int(r["rssi_max"]) if r["rssi_max"] not in ("","None") else None,
                "rssi_median":int(r["rssi_median"]) if r["rssi_median"] not in ("","None") else None,
                "frame_done": r["frame_done"] == "True",
            })
    return rows

def group_by(rows, key_fields, value_field):
    """Group rows by (f1,f2,...) tuple, collect value_field as list."""
    groups = {}
    for r in rows:
        k = tuple(r[f] for f in key_fields)
        groups.setdefault(k, []).append(r[value_field])
    return groups

def avg(vals):
    return sum(vals) / len(vals) if vals else None

def status_tag(loss_pct):
    if loss_pct is None: return "—"
    if loss_pct <= TARGET_PCT:  return "✓ PASS"
    if loss_pct <= MARGINAL_PCT: return "~ MARGINAL"
    return "✗ FAIL"

def status_color(loss_pct):
    if loss_pct is None: return "gray"
    if loss_pct <= TARGET_PCT:   return "#2ca02c"   # green
    if loss_pct <= MARGINAL_PCT: return "#ff7f0e"   # orange
    return "#d62728"                                 # red

# ─── Graph ────────────────────────────────────────────────────────────────────
def make_graph(rows, out_path):
    power_rows = [r for r in rows if r["sweep_type"] == "power"]
    delay_rows = [r for r in rows if r["sweep_type"] == "delay"]

    fig = plt.figure(figsize=(16, 14), facecolor="#1a1a2e")
    fig.suptitle(
        "Cacadu LoRa Layer 1 — TX Rate Sweep Analysis\n"
        "SF9 · BW125 · CR4/5 · 915 MHz · NETWORKID=18",
        color="white", fontsize=15, fontweight="bold", y=0.97
    )

    grid = fig.add_gridspec(3, 2, hspace=0.42, wspace=0.32,
                            left=0.07, right=0.97, top=0.92, bottom=0.04)

    ax1 = fig.add_subplot(grid[0, 0])   # Power vs Drop Rate
    ax2 = fig.add_subplot(grid[0, 1])   # Delay vs Drop Rate
    ax3 = fig.add_subplot(grid[1, 0])   # Power vs RSSI band
    ax4 = fig.add_subplot(grid[1, 1])   # Delay vs RSSI median
    ax5 = fig.add_subplot(grid[2, :])   # Summary table

    BG   = "#16213e"
    GRID = "#2a2a4a"
    TEXT = "#e0e0e0"
    RUN_COLORS = ["#74b9ff", "#a29bfe"]  # run 1, run 2

    def style_ax(ax, title, xlabel, ylabel):
        ax.set_facecolor(BG)
        ax.set_title(title, color=TEXT, fontsize=11, pad=8)
        ax.set_xlabel(xlabel, color=TEXT, fontsize=9)
        ax.set_ylabel(ylabel, color=TEXT, fontsize=9)
        ax.tick_params(colors=TEXT)
        ax.spines[:].set_color(GRID)
        ax.grid(True, color=GRID, linewidth=0.6, linestyle="--", alpha=0.7)
        for s in ax.spines.values(): s.set_color(GRID)

    def draw_thresholds(ax):
        ax.axhline(TARGET_PCT,   color="#2ca02c", lw=1.4, ls="--",
                   label=f"Target ≤{TARGET_PCT:.0f}%")
        ax.axhline(MARGINAL_PCT, color="#ff7f0e", lw=1.2, ls=":",
                   label=f"Marginal ≤{MARGINAL_PCT:.0f}% (cap fix)")

    # ── Panel 1: Power vs Drop Rate ──────────────────────────────────────────
    if power_rows:
        style_ax(ax1, "TX Power vs Drop Rate", "TX Power (AT+CRFOP, dBm)", "Drop Rate (%)")
        draw_thresholds(ax1)

        powers = sorted(set(r["power_dBm"] for r in power_rows))
        run_ids = sorted(set(r["run_idx"] for r in power_rows))

        avg_loss = []
        for pwr in powers:
            for i, rid in enumerate(run_ids):
                pts = [r["loss_pct"] for r in power_rows
                       if r["power_dBm"] == pwr and r["run_idx"] == rid]
                if pts:
                    ax1.scatter([pwr], pts, color=RUN_COLORS[i % len(RUN_COLORS)],
                                s=60, zorder=4, label=f"Run {rid}" if pwr == powers[0] else "")
            all_pts = [r["loss_pct"] for r in power_rows if r["power_dBm"] == pwr]
            avg_loss.append(avg(all_pts))

        ax1.plot(powers, avg_loss, color="#fdcb6e", lw=2, marker="D",
                 markersize=7, label="Average", zorder=5)

        # Color-code background zones
        ax1.fill_between([min(powers)-0.5, max(powers)+0.5], 0, TARGET_PCT,
                         color="#2ca02c", alpha=0.07)
        ax1.fill_between([min(powers)-0.5, max(powers)+0.5], TARGET_PCT, MARGINAL_PCT,
                         color="#ff7f0e", alpha=0.07)

        ax1.set_xlim(min(powers)-1, max(powers)+1)
        ax1.set_ylim(-1, max(max(r["loss_pct"] for r in power_rows), MARGINAL_PCT) + 5)
        ax1.set_xticks(powers)
        ax1.legend(fontsize=8, facecolor=BG, labelcolor=TEXT, framealpha=0.8)
    else:
        ax1.text(0.5, 0.5, "No power sweep data", ha="center", va="center",
                 color=TEXT, transform=ax1.transAxes)
        ax1.set_facecolor(BG)

    # ── Panel 2: Delay vs Drop Rate ──────────────────────────────────────────
    if delay_rows:
        style_ax(ax2, "Inter-chunk Delay vs Drop Rate", "Delay (ms)", "Drop Rate (%)")
        draw_thresholds(ax2)

        delays  = sorted(set(r["delay_ms"] for r in delay_rows))
        run_ids = sorted(set(r["run_idx"] for r in delay_rows))

        avg_loss = []
        for dly in delays:
            for i, rid in enumerate(run_ids):
                pts = [r["loss_pct"] for r in delay_rows
                       if r["delay_ms"] == dly and r["run_idx"] == rid]
                if pts:
                    ax2.scatter([dly], pts, color=RUN_COLORS[i % len(RUN_COLORS)],
                                s=60, zorder=4, label=f"Run {rid}" if dly == delays[0] else "")
            all_pts = [r["loss_pct"] for r in delay_rows if r["delay_ms"] == dly]
            avg_loss.append(avg(all_pts))

        ax2.plot(delays, avg_loss, color="#fdcb6e", lw=2, marker="D",
                 markersize=7, label="Average", zorder=5)
        ax2.axvline(633, color="#fd79a8", lw=1.2, ls=":", label="SF9 airtime 633ms")
        ax2.axvline(915, color="#b2bec3", lw=1.0, ls="--", alpha=0.6, label="Baseline 915ms")

        ax2.fill_between([min(delays)-10, max(delays)+10], 0, TARGET_PCT,
                         color="#2ca02c", alpha=0.07)
        ax2.fill_between([min(delays)-10, max(delays)+10], TARGET_PCT, MARGINAL_PCT,
                         color="#ff7f0e", alpha=0.07)

        ax2.set_xlim(min(delays)-50, max(delays)+50)
        ax2.set_ylim(-1, max(max(r["loss_pct"] for r in delay_rows), MARGINAL_PCT) + 5)
        ax2.legend(fontsize=8, facecolor=BG, labelcolor=TEXT, framealpha=0.8)
    else:
        ax2.text(0.5, 0.5, "No delay sweep data", ha="center", va="center",
                 color=TEXT, transform=ax2.transAxes)
        ax2.set_facecolor(BG)

    # ── Panel 3: Power vs RSSI band ──────────────────────────────────────────
    if power_rows:
        style_ax(ax3, "TX Power vs Received RSSI", "TX Power (dBm)", "RSSI (dBm)")
        ax3.axhline(-20, color="#d63031", lw=1.2, ls="--", label="Saturation zone > −20 dBm")
        ax3.axhline(-50, color="#2ca02c", lw=1.2, ls="--", label="Safe floor −50 dBm")
        ax3.axhspan(-50, -20, alpha=0.08, color="#2ca02c", label="Ideal −50 to −20")

        powers = sorted(set(r["power_dBm"] for r in power_rows))
        med_vals, min_vals, max_vals = [], [], []
        for pwr in powers:
            pts = [r for r in power_rows if r["power_dBm"] == pwr]
            meds = [r["rssi_median"] for r in pts if r["rssi_median"] is not None]
            mins = [r["rssi_min"]    for r in pts if r["rssi_min"]    is not None]
            maxs = [r["rssi_max"]    for r in pts if r["rssi_max"]    is not None]
            med_vals.append(avg(meds))
            min_vals.append(avg(mins))
            max_vals.append(avg(maxs))

        ax3.plot(powers, med_vals, color="#74b9ff", lw=2, marker="o",
                 markersize=7, label="RSSI median", zorder=5)
        ax3.fill_between(powers, min_vals, max_vals, alpha=0.25,
                         color="#74b9ff", label="RSSI min–max band")

        ax3.set_xlim(min(powers)-1, max(powers)+1)
        ax3.set_xticks(powers)
        ax3.invert_xaxis()   # high power left → low power right
        ax3.set_xlabel("TX Power dBm (high → low)", color=TEXT, fontsize=9)
        ax3.legend(fontsize=8, facecolor=BG, labelcolor=TEXT, framealpha=0.8)
    else:
        ax3.text(0.5, 0.5, "No power sweep data", ha="center", va="center",
                 color=TEXT, transform=ax3.transAxes)
        ax3.set_facecolor(BG)

    # ── Panel 4: Delay vs RSSI median ────────────────────────────────────────
    if delay_rows:
        style_ax(ax4, "Delay vs Received RSSI (Median)", "Delay (ms)", "RSSI (dBm)")
        ax4.axhline(-20, color="#d63031", lw=1.2, ls="--", label="Saturation > −20 dBm")
        ax4.axhline(-50, color="#2ca02c", lw=1.2, ls="--", label="Safe floor −50 dBm")

        delays = sorted(set(r["delay_ms"] for r in delay_rows))
        meds   = []
        for dly in delays:
            pts = [r["rssi_median"] for r in delay_rows
                   if r["delay_ms"] == dly and r["rssi_median"] is not None]
            meds.append(avg(pts))

        ax4.plot(delays, meds, color="#a29bfe", lw=2, marker="o",
                 markersize=7, label="RSSI median", zorder=5)
        ax4.set_xlim(min(delays)-50, max(delays)+50)
        ax4.legend(fontsize=8, facecolor=BG, labelcolor=TEXT, framealpha=0.8)
    else:
        ax4.text(0.5, 0.5, "No delay sweep data", ha="center", va="center",
                 color=TEXT, transform=ax4.transAxes)
        ax4.set_facecolor(BG)

    # ── Panel 5: Summary table ────────────────────────────────────────────────
    ax5.set_facecolor(BG)
    ax5.set_title("All Configurations — Average Drop Rate", color=TEXT,
                  fontsize=11, pad=8)
    ax5.axis("off")

    # Build rows: power sweep entries + delay sweep entries
    table_data = []
    headers = ["Sweep", "Power (dBm)", "Delay (ms)", "Chunks",
               "Avg RX", "Avg Drops", "Avg Loss %", "RSSI Med", "Status"]

    def build_table_rows(sweep_rows, sweep_type, fixed_key, varying_key, varying_vals):
        for val in varying_vals:
            pts = [r for r in sweep_rows if r[varying_key] == val]
            if not pts: continue
            fixed_val = pts[0][fixed_key]
            avg_rx    = avg([r["n_received"] for r in pts])
            avg_drop  = avg([r["n_dropped"]  for r in pts])
            avg_loss  = avg([r["loss_pct"]   for r in pts])
            n_chunks  = pts[0]["n_chunks"]
            rmed      = avg([r["rssi_median"] for r in pts if r["rssi_median"] is not None])
            table_data.append([
                sweep_type,
                str(val) if sweep_type == "Power" else str(fixed_val),
                str(fixed_val) if sweep_type == "Power" else str(val),
                str(n_chunks),
                f"{avg_rx:.0f}",
                f"{avg_drop:.1f}",
                f"{avg_loss:.1f}%",
                f"{rmed:.0f}" if rmed is not None else "—",
                status_tag(avg_loss),
            ])

    if power_rows:
        build_table_rows(power_rows, "Power", "delay_ms", "power_dBm",
                         sorted(set(r["power_dBm"] for r in power_rows)))
    if delay_rows:
        build_table_rows(delay_rows, "Delay", "power_dBm", "delay_ms",
                         sorted(set(r["delay_ms"] for r in delay_rows)))

    if table_data:
        col_widths = [0.07, 0.10, 0.10, 0.07, 0.08, 0.10, 0.10, 0.10, 0.12]
        x_positions = [0.01]
        for w in col_widths[:-1]:
            x_positions.append(x_positions[-1] + w)

        # Header
        for j, h in enumerate(headers):
            ax5.text(x_positions[j], 0.95, h, transform=ax5.transAxes,
                     color="#fdcb6e", fontsize=8.5, fontweight="bold",
                     va="top", ha="left")

        ax5.axhline(0.90, xmin=0.01, xmax=0.99, color=GRID, lw=0.8,
                    transform=ax5.transAxes)

        # Data rows
        row_h = 0.075
        for i, row in enumerate(table_data):
            y = 0.88 - i * row_h
            if y < 0.02: break
            loss_val = float(row[6].replace("%","")) if row[6] != "—" else None
            row_color = status_color(loss_val)
            # Status cell background
            ax5.add_patch(mpatches.FancyBboxPatch(
                (x_positions[-1] - 0.005, y - 0.03), col_widths[-1] + 0.005, 0.05,
                boxstyle="round,pad=0.002",
                facecolor=row_color, alpha=0.25,
                transform=ax5.transAxes, clip_on=False
            ))
            for j, val in enumerate(row):
                color = row_color if j == len(row)-1 else TEXT
                ax5.text(x_positions[j], y, val, transform=ax5.transAxes,
                         color=color, fontsize=8, va="top", ha="left")

        # Legend
        legend_y = 0.02
        for tag, color in [("✓ PASS (≤5%)", "#2ca02c"),
                            ("~ MARGINAL (≤7%, cap fix)", "#ff7f0e"),
                            ("✗ FAIL (>7%)", "#d62728")]:
            ax5.text(x_positions[0], legend_y, tag, transform=ax5.transAxes,
                     color=color, fontsize=8, va="bottom", ha="left")
            x_positions[0] += 0.22
    else:
        ax5.text(0.5, 0.5, "No results to display", ha="center", va="center",
                 color=TEXT, transform=ax5.transAxes)

    # ── Save ─────────────────────────────────────────────────────────────────
    plt.savefig(out_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    print(f"Graph saved: {out_path}")
    plt.close()

# ─── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("csv", help="CSV from lora_rate_test.py")
    p.add_argument("--out", default="lora_sweep_analysis.png")
    args = p.parse_args()

    if not HAVE_MPL:
        print("ERROR: matplotlib not found. pip install matplotlib --break-system-packages")
        sys.exit(1)

    rows = load(args.csv)
    print(f"Loaded {len(rows)} rows from {args.csv}")
    make_graph(rows, args.out)
