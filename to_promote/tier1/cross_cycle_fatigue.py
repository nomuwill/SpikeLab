"""Cross-cycle fatigue / recovery analysis.

For each lead condition, plot response strength against:
  (a) time since the previous cycle that stimmed any of its electrodes (rest)
  (b) total pulses delivered to its electrodes in the previous cycle (recent load)
  (c) cumulative pulses through that point (chronic load)

Times pulled from each cycle's manifest.json (start_time_ms / stop_time_ms,
which are the MaxLab system unix timestamps from the .h5 metadata).
"""
from __future__ import annotations
import json
import glob
import os
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

PLAN = Path(
    "/home/sharf-lab/Desktop/Research_automation/orchestrator/"
    "stim-optimize-maxone-cortical_2026-04-27"
)
OUT = PLAN / "scratch/plots_fatigue"
OUT.mkdir(exist_ok=True, parents=True)


def find_manifest_for_cycle(cycle_num: int) -> Path | None:
    """Locate the cycle_<N> manifest using the recording dir naming."""
    for p in sorted(glob.glob(str(PLAN / "recordings/*/*/manifest.json"))):
        d = json.loads(open(p).read())
        rec = d.get("steps", {}).get("record")
        if isinstance(rec, list) and rec and rec[0].get("name") == f"cycle_{cycle_num}":
            wells = rec[0].get("wells", [])
            if wells and wells[0].get("spike_count", -1) is not None:
                # Skip the 41 KB failed-stim cycle 13 manifest (output_bytes ~ 41k)
                if rec[0].get("output_bytes", 0) < 1_000_000:
                    continue
                return Path(p)
    return None


def cycle_times():
    """Return dict cycle -> (start_unix_ms, stop_unix_ms, manifest_path)."""
    out = {}
    for cyc in range(1, 20):
        mp = find_manifest_for_cycle(cyc)
        if mp is None:
            continue
        d = json.loads(mp.read_text())
        rec = d["steps"]["record"][0]
        w = rec["wells"][0]
        out[cyc] = (int(w["start_time_ms"]), int(w["stop_time_ms"]), mp)
    return out


def conditions_for_cycle(cyc):
    cd = PLAN / f"well_0/cycle_{cyc}/conditions.json"
    if not cd.exists():
        return []
    return json.loads(cd.read_text()).get("conditions", [])


def pulses_in_cycle_for_electrode_set(cyc, electrode_set):
    """Total pulses delivered to ANY electrode in electrode_set during cycle cyc."""
    total = 0
    for c in conditions_for_cycle(cyc):
        es = set(c.get("electrodes", []))
        if es & electrode_set:
            total += int(c.get("n_pulses", 1)) * int(c.get("n_trials", 1))
    return total


def cumulative_pulses_through(cyc, electrode_set):
    """Cumulative pulses through cycle cyc (inclusive)."""
    return sum(pulses_in_cycle_for_electrode_set(c, electrode_set)
               for c in range(1, cyc + 1))


def last_cycle_with_stim(cyc, electrode_set):
    """Most recent cycle BEFORE cyc that stimmed any electrode in the set."""
    for c in range(cyc - 1, 0, -1):
        if pulses_in_cycle_for_electrode_set(c, electrode_set) > 0:
            return c
    return None


def main():
    times = cycle_times()
    print(f"Got times for cycles: {sorted(times.keys())}")
    for cyc in sorted(times.keys()):
        start, stop, _ = times[cyc]
        print(f"  c{cyc}: {start} -> {stop}  ({(stop-start)/60000:.1f} min)")

    # nl rankings
    nl = json.loads(
        (PLAN / "scratch/reanalysis_nonlocal/by_threshold_100um.json").read_text()
    )

    # Lead conditions to track
    leads = [
        ("site_15498_train10_100Hz", {15498}),
        ("pair_15956+16402_train10_100Hz", {15956, 16402}),
        ("site_15498_train5_100Hz_600mV", {15498}),
    ]

    # Build per-condition records
    records = {label: [] for label, _ in leads}
    for label, eset in leads:
        for cyc in sorted(times.keys()):
            cd = nl.get(str(cyc), {})
            if label not in cd:
                continue
            nl_val = cd[label]["strength_nonlocal_top10"]
            start, stop, _ = times[cyc]

            # rest time: from end of last cycle that stimmed the set, to start of this cycle
            prev_cyc = last_cycle_with_stim(cyc, eset)
            if prev_cyc is None:
                rest_min = float("nan")
            else:
                _, prev_stop, _ = times[prev_cyc]
                rest_min = (start - prev_stop) / 60000.0

            recent_load = (pulses_in_cycle_for_electrode_set(prev_cyc, eset)
                           if prev_cyc is not None else 0)
            cum_load = cumulative_pulses_through(cyc - 1, eset)

            records[label].append({
                "cycle": cyc,
                "nl": nl_val,
                "rest_min": rest_min,
                "prev_cycle": prev_cyc,
                "recent_load_pulses": recent_load,
                "cum_load_through_prev": cum_load,
                "start_unix_ms": start,
            })

    # Print table
    for label, eset in leads:
        print(f"\n=== {label} (electrodes {sorted(eset)}) ===")
        print(f"{'cyc':>3}  {'nl':>7}  {'rest_min':>9}  {'prev_cyc':>8}  "
              f"{'recent_pls':>10}  {'cum_pls_prev':>13}")
        for r in records[label]:
            rest = (f"{r['rest_min']:.1f}" if not np.isnan(r['rest_min']) else "—")
            prev = str(r["prev_cycle"]) if r["prev_cycle"] is not None else "—"
            print(f"{r['cycle']:>3}  {r['nl']:>7.2f}  {rest:>9}  {prev:>8}  "
                  f"{r['recent_load_pulses']:>10,}  {r['cum_load_through_prev']:>13,}")

    # Plot timeline + scatter
    fig, axes = plt.subplots(3, 2, figsize=(14, 13))

    # (1) Timeline of cycles + lead responses
    ax = axes[0, 0]
    t0 = min(times[c][0] for c in times) / 1000.0  # seconds
    for cyc in sorted(times.keys()):
        s, e, _ = times[cyc]
        ax.axvspan((s/1000.0 - t0)/3600.0, (e/1000.0 - t0)/3600.0,
                   alpha=0.15, color="gray")
        ax.text((s/1000.0 - t0)/3600.0, 1, f"c{cyc}", fontsize=7,
                rotation=90, va="bottom", ha="left", color="gray")
    colors = {"site_15498_train10_100Hz": "tab:red",
              "pair_15956+16402_train10_100Hz": "tab:blue",
              "site_15498_train5_100Hz_600mV": "tab:orange"}
    for label, _ in leads:
        rs = records[label]
        if not rs:
            continue
        xs = [(r["start_unix_ms"]/1000.0 - t0)/3600.0 for r in rs]
        ys = [r["nl"] for r in rs]
        ax.plot(xs, ys, marker="o", label=label, color=colors[label], linewidth=2)
    ax.set_xlabel("hours since cycle 1 start")
    ax.set_ylabel("nl_top10")
    ax.set_title("Lead condition responses on the real-time timeline\n"
                 "gray bars = recording windows; cycle labels at top")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # (2) nl vs rest_min (per pathway)
    ax = axes[0, 1]
    for label, _ in leads:
        rs = [r for r in records[label] if not np.isnan(r["rest_min"])]
        if not rs:
            continue
        xs = [r["rest_min"] for r in rs]
        ys = [r["nl"] for r in rs]
        labels_inline = [f"c{r['cycle']}" for r in rs]
        ax.scatter(xs, ys, color=colors[label], s=80, label=label)
        for x, y, t in zip(xs, ys, labels_inline):
            ax.annotate(t, (x, y), xytext=(4, 4), textcoords="offset points",
                        fontsize=8, color=colors[label])
    ax.set_xscale("symlog", linthresh=1)
    ax.set_xlabel("rest_min since previous cycle stimming this pathway (log scale)")
    ax.set_ylabel("nl_top10")
    ax.set_title("Response vs rest time")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")

    # (3) nl vs recent_load
    ax = axes[1, 0]
    for label, _ in leads:
        rs = records[label]
        if not rs:
            continue
        xs = [r["recent_load_pulses"] for r in rs]
        ys = [r["nl"] for r in rs]
        labels_inline = [f"c{r['cycle']}" for r in rs]
        ax.scatter(xs, ys, color=colors[label], s=80, label=label)
        for x, y, t in zip(xs, ys, labels_inline):
            ax.annotate(t, (x, y), xytext=(4, 4), textcoords="offset points",
                        fontsize=8, color=colors[label])
    ax.set_xlabel("pulses delivered to pathway in immediately previous cycle")
    ax.set_ylabel("nl_top10")
    ax.set_title("Response vs recent stim load (previous cycle only)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # (4) nl vs cumulative load
    ax = axes[1, 1]
    for label, _ in leads:
        rs = records[label]
        if not rs:
            continue
        xs = [r["cum_load_through_prev"] for r in rs]
        ys = [r["nl"] for r in rs]
        labels_inline = [f"c{r['cycle']}" for r in rs]
        ax.scatter(xs, ys, color=colors[label], s=80, label=label)
        for x, y, t in zip(xs, ys, labels_inline):
            ax.annotate(t, (x, y), xytext=(4, 4), textcoords="offset points",
                        fontsize=8, color=colors[label])
    ax.set_xlabel("cumulative pulses on pathway through previous cycle")
    ax.set_ylabel("nl_top10")
    ax.set_title("Response vs chronic stim load")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    # (5) Combined: rest vs nl with marker SIZE = recent load
    ax = axes[2, 0]
    for label, _ in leads:
        rs = [r for r in records[label] if not np.isnan(r["rest_min"])]
        if not rs:
            continue
        xs = [r["rest_min"] for r in rs]
        ys = [r["nl"] for r in rs]
        sizes = [max(30, r["recent_load_pulses"] / 30.0) for r in rs]
        ax.scatter(xs, ys, color=colors[label], s=sizes, alpha=0.6,
                   edgecolors="black", linewidths=0.5, label=label)
        for r, x, y in zip(rs, xs, ys):
            ax.annotate(f"c{r['cycle']}", (x, y), xytext=(6, 6),
                        textcoords="offset points", fontsize=7,
                        color=colors[label])
    ax.set_xscale("symlog", linthresh=1)
    ax.set_xlabel("rest_min since previous cycle stimming this pathway")
    ax.set_ylabel("nl_top10")
    ax.set_title("Response vs rest, marker size ∝ pulses delivered in previous cycle")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, which="both")

    # (6) Per-condition cycle history
    ax = axes[2, 1]
    for label, _ in leads:
        rs = records[label]
        if not rs:
            continue
        xs = [r["cycle"] for r in rs]
        ys = [r["nl"] for r in rs]
        ax.plot(xs, ys, marker="o", color=colors[label], label=label, linewidth=2)
    ax.set_xlabel("cycle number")
    ax.set_ylabel("nl_top10")
    ax.set_title("Response vs cycle index")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(OUT / "cross_cycle_fatigue.png", dpi=110, bbox_inches="tight")
    plt.close()
    print(f"\nWrote {OUT}/cross_cycle_fatigue.png")


if __name__ == "__main__":
    main()
