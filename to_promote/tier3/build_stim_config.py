#!/usr/bin/env python3
"""Build a stim-optimised recording config from an activity scan.

Workflow:
  1. Load scan data (all data000X blocks from --scan-dir).
  2. Select up to 32 stim sites: amplitude-ranked, greedy >= STIM_SPREAD_UM spread.
  3. For each stim site add the nearest NEIGHBORHOOD_N scan-active electrodes
     within NEIGHBORHOOD_UM → local recording neighbourhood.
  4. Load the reference config (--cfg) as the starting electrode set.
  5. Budget:
       a. Remove perimeter config electrodes (outer PERIMETER_ROWS rows/cols)
          sorted by amplitude × rate ascending — lowest quality first.
       b. If still short, remove the lowest-quality non-perimeter config
          electrodes (never touching neighbourhood or stim sites).
       c. Add all neighbourhood electrodes not yet in the set.
       d. Fill any remaining capacity up to MAX_ELECTRODES with the
          highest-amplitude non-perimeter scan electrodes not yet included.
  6. Hardware: arr.reset() → select_electrodes → select_stimulation_electrodes
     → route() → download → offset → probe stim units → save_config.
  7. Write selected_electrodes.cfg + stim_routing.json + candidate_summary.json.

Run under MaxLab Python:
  /home/sharf-lab/MaxLab/python/bin/python3 build_stim_config.py \\
      --cfg /path/to/gui_config.cfg \\
      --scan-dir /path/to/ActivityScan/ \\
      --output-dir recordings/YYYY-MM-DD/HHMMSS_screen
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import h5py
import numpy as np

EPHYS = "/home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts"
sys.path.insert(0, EPHYS)

# ── Tunable constants ──────────────────────────────────────────────────
STIM_SPREAD_UM    = 150.0   # min distance between stim sites (ensures distinct stim units)
NEIGHBORHOOD_UM   = 100.0   # radius around each stim site to add recording electrodes
NEIGHBORHOOD_N    = 8       # max scan-active neighbours per stim site
PERIMETER_ROWS    = 3       # outer rows/cols of the array treated as perimeter
MAX_STIM          = 32      # hardware stim-unit ceiling
MAX_ELECTRODES    = 1024    # hardware channel ceiling

# MaxOne grid
N_COLS  = 220
N_ROWS  = 120
PITCH   = 17.5


# ── Geometry helpers ───────────────────────────────────────────────────

def _xy(e: int) -> tuple[float, float]:
    return (e % N_COLS) * PITCH, (e // N_COLS) * PITCH


def _dist(a: int, b: int) -> float:
    x1, y1 = _xy(a)
    x2, y2 = _xy(b)
    return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5


def _is_perimeter(e: int) -> bool:
    r, c = e // N_COLS, e % N_COLS
    return (r < PERIMETER_ROWS or r >= N_ROWS - PERIMETER_ROWS
            or c < PERIMETER_ROWS or c >= N_COLS - PERIMETER_ROWS)


# ── Scan data loading ──────────────────────────────────────────────────

def _load_scan(scan_dir: Path) -> tuple[dict[int, int], dict[int, float]]:
    """Aggregate spike counts and amplitude sums from all h5 blocks."""
    h5_files = sorted(scan_dir.glob("*/data.raw.h5"))
    if not h5_files:
        raise RuntimeError(f"No data.raw.h5 files found under {scan_dir}")
    spike_counts: dict[int, int] = {}
    amp_sums: dict[int, float] = {}
    for h5p in h5_files:
        with h5py.File(str(h5p), "r") as h5:
            if "data_store" not in h5:
                continue
            for blk in sorted(k for k in h5["data_store"].keys()
                               if k.startswith("data")):
                mk = f"data_store/{blk}/settings/mapping"
                sk = f"data_store/{blk}/spikes"
                if mk not in h5 or sk not in h5:
                    continue
                ch2e = {int(r["channel"]): int(r["electrode"])
                        for r in np.array(h5[mk])}
                for row in np.array(h5[sk]):
                    e = ch2e.get(int(row["channel"]))
                    if e is None:
                        continue
                    spike_counts[e] = spike_counts.get(e, 0) + 1
                    amp_sums[e] = amp_sums.get(e, 0.0) + abs(float(row["amplitude"]))
    return spike_counts, amp_sums


# ── Config parsing ─────────────────────────────────────────────────────

def _parse_cfg(cfg_path: str) -> set[int]:
    """Return electrode IDs from a MaxWell GUI .cfg file (first line)."""
    first = open(cfg_path).readline().strip()
    return {int(m.group(1))
            for entry in first.split(";")
            if (m := re.match(r"\d+\((\d+)\)", entry))}


# ── Stim site selection ────────────────────────────────────────────────

def _select_stim_sites(amp_sorted: list[int]) -> list[int]:
    """Greedy amplitude-ranked spread filter → up to MAX_STIM stim sites."""
    sites: list[int] = []
    for e in amp_sorted:
        if len(sites) >= MAX_STIM:
            break
        if all(_dist(e, s) >= STIM_SPREAD_UM for s in sites):
            sites.append(e)
    return sites


# ── Hardware routing ───────────────────────────────────────────────────

def _route_and_probe(arr, wells, recording_elecs, stim_candidates,
                     mean_amp: dict[int, float]) -> dict[int, int]:
    """Reset, select recording + stim electrodes, route, probe stim units.

    Returns {electrode_id: stim_unit_id} for conflict-free stim electrodes.
    Conflict resolution: keep the highest mean-amplitude electrode per unit.
    """
    arr.reset()
    arr.select_electrodes(list(recording_elecs))
    arr.select_stimulation_electrodes(list(stim_candidates))
    arr.route()

    unit_to_elec: dict[int, int] = {}
    routing: dict[int, int] = {}

    for elec in stim_candidates:
        arr.connect_electrode_to_stimulation(elec)
        raw = arr.query_stimulation_at_electrode(elec)
        try:
            unit_id = int(str(raw).strip())
        except (TypeError, ValueError):
            unit_id = 0
        arr.disconnect_electrode_from_stimulation(elec)

        if unit_id <= 0:
            print(f"  [stim] {elec}: not stimulable (returned {raw!r}) — skip")
            continue
        if unit_id in unit_to_elec:
            incumbent = unit_to_elec[unit_id]
            if mean_amp.get(elec, 0) > mean_amp.get(incumbent, 0):
                print(f"  [stim] {elec} replaces {incumbent} on unit {unit_id} "
                      f"(higher amplitude)")
                del routing[incumbent]
                unit_to_elec[unit_id] = elec
                routing[elec] = unit_id
            else:
                print(f"  [stim] {elec} conflicts with {incumbent} on unit {unit_id} "
                      f"— keeping {incumbent}")
            continue

        unit_to_elec[unit_id] = elec
        routing[elec] = unit_id

    for elec in routing:
        arr.connect_electrode_to_stimulation(elec)

    return routing


# ── Main ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build stim-optimised config: amplitude-ranked stim sites + "
                    "scan-active neighbourhoods + remove perimeter low-quality elecs."
    )
    ap.add_argument("--cfg", required=True,
                    help="Reference GUI .cfg (defines starting electrode set)")
    ap.add_argument("--scan-dir", required=True,
                    help="Activity scan directory (<scan-dir>/<NNN>/data.raw.h5)")
    ap.add_argument("--output-dir", required=True,
                    help="Output directory for .cfg, stim_routing.json, summary")
    ap.add_argument("--wells", type=int, nargs="+", default=[0])
    ap.add_argument("--stim-spread-um", type=float, default=STIM_SPREAD_UM)
    ap.add_argument("--neighborhood-um", type=float, default=NEIGHBORHOOD_UM)
    ap.add_argument("--neighborhood-n", type=int, default=NEIGHBORHOOD_N)
    ap.add_argument("--perimeter-rows", type=int, default=PERIMETER_ROWS)
    args = ap.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    scan_dir = Path(args.scan_dir).resolve()
    gui_cfg = str(Path(args.cfg).resolve())

    print(f"[build_stim_config] cfg:           {gui_cfg}")
    print(f"[build_stim_config] scan-dir:       {scan_dir}")
    print(f"[build_stim_config] output-dir:     {output_dir}")
    print(f"[build_stim_config] stim spread:    {args.stim_spread_um:.0f} µm")
    print(f"[build_stim_config] neighbourhood:  {args.neighborhood_n} elecs "
          f"within {args.neighborhood_um:.0f} µm")
    print(f"[build_stim_config] perimeter rows: {args.perimeter_rows}")

    # ── 1. Load scan ───────────────────────────────────────────────────
    print("\n[1/5] Loading scan data...")
    spike_counts, amp_sums = _load_scan(scan_dir)
    mean_amp = {e: amp_sums[e] / max(spike_counts[e], 1) for e in spike_counts}
    all_active = sorted(spike_counts, key=lambda e: -mean_amp[e])
    print(f"  Active electrodes in scan: {len(all_active)}")

    # ── 2. Select stim sites ───────────────────────────────────────────
    print(f"\n[2/5] Selecting stim sites "
          f"(amp-ranked, >= {args.stim_spread_um:.0f} µm spread)...")
    stim_sites = _select_stim_sites(all_active)
    print(f"  Stim sites: {len(stim_sites)}")
    for i, e in enumerate(stim_sites[:5]):
        x, y = _xy(e)
        print(f"    {i+1}. elec {e:>6}  amp={mean_amp[e]:.1f}  "
              f"rate={spike_counts[e]}  ({x:.0f},{y:.0f}) µm")
    if len(stim_sites) > 5:
        print(f"    ... ({len(stim_sites)-5} more)")

    # ── 3. Build neighbourhood sets ────────────────────────────────────
    print(f"\n[3/5] Building recording neighbourhoods "
          f"({args.neighborhood_n} nearest within {args.neighborhood_um:.0f} µm)...")
    nbhd_set: set[int] = set(stim_sites)
    for s in stim_sites:
        neighbours = sorted(
            [e for e in all_active if e != s
             and _dist(s, e) <= args.neighborhood_um],
            key=lambda e: -mean_amp[e]
        )[:args.neighborhood_n]
        nbhd_set.update(neighbours)
    print(f"  Stim sites + neighbourhoods: {len(nbhd_set)} unique electrodes")

    # ── 4. Budget: starting from reference config ──────────────────────
    print(f"\n[4/5] Building final electrode set (target ≤ {MAX_ELECTRODES})...")
    cfg_set = _parse_cfg(gui_cfg)
    print(f"  Reference config: {len(cfg_set)} electrodes")

    # Score each config electrode by quality (amplitude × rate)
    def quality(e: int) -> float:
        return mean_amp.get(e, 0.0) * spike_counts.get(e, 0)

    working = set(cfg_set)

    # a. Count how many neighbourhood electrodes are missing
    missing = nbhd_set - working
    need_to_remove = max(0, len(missing) - (MAX_ELECTRODES - len(working)))
    print(f"  Neighbourhood not in config: {len(missing)}  "
          f"need to remove: {need_to_remove}")

    if need_to_remove > 0:
        # b. Remove perimeter electrodes first (lowest quality first)
        removable_perimeter = sorted(
            [e for e in working
             if _is_perimeter(e) and e not in nbhd_set],
            key=quality   # ascending = worst first
        )
        n_from_perimeter = min(len(removable_perimeter), need_to_remove)
        for e in removable_perimeter[:n_from_perimeter]:
            working.discard(e)
        removed_peri = n_from_perimeter
        print(f"  Removed {removed_peri} perimeter electrodes")

        # c. If still short, remove lowest-quality non-perimeter config elecs
        still_need = max(0, need_to_remove - removed_peri)
        if still_need > 0:
            removable_interior = sorted(
                [e for e in working
                 if not _is_perimeter(e) and e not in nbhd_set],
                key=quality
            )
            for e in removable_interior[:still_need]:
                working.discard(e)
            print(f"  Removed {still_need} low-quality interior electrodes")

    # d. Add all neighbourhood electrodes
    working.update(nbhd_set)
    print(f"  After adding neighbourhoods: {len(working)}")

    # e. Fill remaining capacity with highest-amplitude non-perimeter scan elecs
    remaining = MAX_ELECTRODES - len(working)
    if remaining > 0:
        fill_candidates = [
            e for e in all_active
            if e not in working and not _is_perimeter(e)
        ]
        fill = fill_candidates[:remaining]
        working.update(fill)
        print(f"  Filled {len(fill)} more slots with best non-perimeter scan elecs")

    final_set = sorted(working)
    print(f"  Final electrode count: {len(final_set)} / {MAX_ELECTRODES}")

    # Stim candidates passed to router = all stim sites, amp descending
    stim_candidates = sorted(stim_sites, key=lambda e: -mean_amp[e])

    # ── 5. Hardware ────────────────────────────────────────────────────
    print(f"\n[5/5] Hardware routing...")

    import maxlab as mx
    from experiment_lib import (
        DETECTION_THRESHOLD,
        prepare_hardware,
    )

    prepare_hardware(args.wells)
    mx.send(mx.Core().enable_stimulation_power(True))
    mx.send_raw(f"stream_set_event_threshold {DETECTION_THRESHOLD}")

    arr = mx.Array("online")
    routing = _route_and_probe(arr, args.wells, final_set, stim_candidates, mean_amp)

    arr.download(args.wells)
    time.sleep(mx.Timing.waitAfterDownload)
    mx.offset()

    # ── Save outputs ───────────────────────────────────────────────────
    cfg_out = output_dir / "selected_electrodes.cfg"
    arr.save_config(str(cfg_out))
    print(f"  selected_electrodes.cfg  ({len(routing)} stim electrodes wired)")

    (output_dir / "stim_routing.json").write_text(json.dumps({
        "wells": args.wells,
        "stim_electrodes": sorted(routing.keys()),
        "routing": {str(e): int(u) for e, u in routing.items()},
    }, indent=2))
    print("  stim_routing.json")

    # scan_results.npz for downstream compatibility
    elec_arr = np.array(final_set, dtype=np.int32)
    np.savez(
        output_dir / "scan_results.npz",
        electrodes=elec_arr,
        spike_rates=np.array([float(spike_counts.get(e, 0)) for e in final_set],
                              dtype=np.float64),
        mean_amplitudes=np.array([mean_amp.get(e, 0.0) for e in final_set],
                                  dtype=np.float64),
    )
    print("  scan_results.npz")

    # Summary JSON
    summary = {
        "n_recording_electrodes": len(final_set),
        "n_stim_sites_selected": len(stim_sites),
        "n_stim_sites_wired": len(routing),
        "stim_spread_um": args.stim_spread_um,
        "neighborhood_um": args.neighborhood_um,
        "neighborhood_n": args.neighborhood_n,
        "stim_sites": stim_sites,
        "wired_routing": {str(e): int(u) for e, u in routing.items()},
    }
    (output_dir / "candidate_summary.json").write_text(json.dumps(summary, indent=2))
    print("  candidate_summary.json")

    print(f"\n[build_stim_config] Done.")
    print(f"  Wired stim electrodes ({len(routing)}): {sorted(routing.keys())}")
    print(f"\nNext: bash run_pipeline.sh --skip-phase-a "
          f"--recording-dir {output_dir}")


if __name__ == "__main__":
    main()
