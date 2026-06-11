#!/usr/bin/env python3
"""
Step 0: Electrode coupling scan — maps organoid footprint via RMS noise.

Sweeps the electrode array in blocks, recording brief raw voltage per block.
Per-electrode RMS (µV) is computed: electrodes under tissue show higher RMS
than electrodes in bare media, revealing the organoid footprint without
requiring any spontaneous spiking activity.

NOTE: This measures RMS voltage noise, not calibrated electrical impedance (Ω).
For publication-grade impedance values, use MaxLab Live's built-in impedance
tool. For organoid footprint mapping (the purpose here), RMS noise is a
reliable and practical proxy — tissue contact causes measurable increases in
local noise floor.

Outputs (written to --output-dir):
  impedance_results.npz  — per-electrode RMS values, electrode positions
  stim_sites.json        — proposed stim site electrode IDs (max-spread within footprint)
                           confirmed=false until user edits and sets confirmed=true
  manifest.json          — step record (key "impedance_scan")

Run plot_impedance_map.py (automation conda env) after this script to generate
the spatial heatmap and stim site overlay.

Usage:
    /home/sharf-lab/MaxLab/python/bin/python3 \\
      /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/00_impedance_scan.py \\
      [--wells 0] [--scan-seconds 5] [--n-sites 2] \\
      [--footprint-percentile 70] [--output-dir PATH]
"""

import argparse
import json
import os
import time

import h5py
import numpy as np
import maxlab as mx

import manifest as manifest_mod
from config import (
    WELLS,
    DETECTION_THRESHOLD,
    MAX_ELECTRODES_PER_BLOCK,
    OUTPUT_DIR,
)

# ── Grid constants (same as 01_activity_scan.py) ──────────────────────

NUM_ROWS = 120
NUM_COLS = 220
TOTAL_ELECTRODES = NUM_ROWS * NUM_COLS

RESULTS_FILE = "impedance_results.npz"
STIM_SITES_FILE = "stim_sites.json"


# ── Block partitioning ─────────────────────────────────────────────────


def partition_sparse_7x(max_per_block, row_step=2, col_step=2):
    """Every other row and column — same subset as 01_activity_scan sparse mode."""
    ids = []
    for r in range(0, NUM_ROWS, row_step):
        for c in range(0, NUM_COLS, col_step):
            ids.append(r * NUM_COLS + c)
    return [ids[i:i + max_per_block] for i in range(0, len(ids), max_per_block)]


# ── Spatial helpers ────────────────────────────────────────────────────


def electrode_rc(electrode_id):
    """Return (row, col) for a flat electrode index."""
    return divmod(electrode_id, NUM_COLS)


# ── Hardware setup ─────────────────────────────────────────────────────


def setup_hardware(wells):
    """Initialise hardware for raw recording — stimulation power OFF."""
    print(f"  Activating wells: {wells}")
    mx.activate(wells)
    mx.initialize(wells)
    time.sleep(mx.Timing.waitInit)
    mx.send(mx.Core().enable_stimulation_power(False))
    mx.send_raw(f"stream_set_event_threshold {DETECTION_THRESHOLD}")
    print("  Hardware initialised")


def route_block(electrodes, wells):
    arr = mx.Array("online")
    arr.reset()
    arr.select_electrodes(electrodes)
    arr.route()
    arr.download(wells)
    time.sleep(mx.Timing.waitAfterDownload)
    mx.offset()


def record_block(wells, duration, save_dir, block_idx):
    """Record raw voltage for one block. Returns the HDF5 filepath."""
    save_dir = os.path.abspath(save_dir)
    fname = f"_imp_block_{block_idx:04d}"
    fpath = os.path.join(save_dir, f"{fname}.raw.h5")

    saver = mx.Saving()
    saver.open_directory(save_dir)
    saver.set_legacy_format(False)
    saver.group_delete_all()
    for w in wells:
        saver.group_define(w, "routed", list(range(1024)))
    saver.start_file(fname)
    saver.start_recording(wells)

    time.sleep(duration)

    saver.stop_recording()
    time.sleep(mx.Timing.waitAfterRecording)
    saver.stop_file()
    saver.group_delete_all()
    return fpath


# ── RMS extraction ─────────────────────────────────────────────────────


def extract_rms(filepath):
    """Read raw voltage from HDF5 and return {electrode_id: rms_uv}."""
    result = {}
    with h5py.File(filepath, "r") as h5:
        # Find data group (data0000 for single-well MaxOne)
        ds_node = h5.get("data_store", {})
        dgroups = sorted(k for k in ds_node.keys() if k.startswith("data"))
        if not dgroups:
            return result
        dg = dgroups[0]
        base = f"data_store/{dg}"

        # Channel → electrode mapping
        ch_to_el = {}
        mapping_key = f"{base}/settings/mapping"
        if mapping_key in h5:
            for row in np.array(h5[mapping_key]):
                ch_to_el[int(row["channel"])] = int(row["electrode"])

        raw_key = f"{base}/groups/routed/raw"
        if raw_key not in h5:
            print(f"    WARNING: raw data not found in {filepath} — skipping block")
            return result

        raw = np.array(h5[raw_key], dtype=np.float32)  # (n_channels, n_frames)

        # LSB: mV per ADC count
        lsb_key = f"{base}/settings/lsb"
        lsb_uv = 1.0  # fallback: values will be in ADC counts
        if lsb_key in h5:
            lsb_mv = float(np.array(h5[lsb_key]).flat[0])
            lsb_uv = lsb_mv * 1000.0

        # Robust DC removal: subtract per-channel median
        raw -= np.median(raw, axis=1, keepdims=True)
        raw *= lsb_uv  # convert to µV

        # RMS per channel
        rms = np.sqrt(np.mean(raw ** 2, axis=1))

        for ch_idx, rms_val in enumerate(rms):
            el = ch_to_el.get(ch_idx, -1)
            if el >= 0:
                result[el] = float(rms_val)

    return result


# ── Main scan ──────────────────────────────────────────────────────────


def run_scan(blocks, wells, scan_seconds, save_dir):
    """Scan all blocks and return {electrode_id: rms_uv}."""
    rms_map = {}
    n_blocks = len(blocks)
    t_start = time.perf_counter()

    for i, block in enumerate(blocks):
        elapsed = time.perf_counter() - t_start
        print(f"\n  Block {i + 1}/{n_blocks} ({len(block)} electrodes, "
              f"elapsed {elapsed:.0f}s)")

        route_block(block, wells)
        fpath = record_block(wells, scan_seconds, save_dir, i)
        block_rms = extract_rms(fpath)

        vals = list(block_rms.values())
        if vals:
            print(f"    {len(block_rms)} electrodes, RMS [{min(vals):.1f}, {max(vals):.1f}] µV")
        else:
            print(f"    No RMS data extracted — raw group missing")

        rms_map.update(block_rms)

        try:
            os.remove(fpath)
        except OSError:
            pass

    total = time.perf_counter() - t_start
    print(f"\n  Scan complete: {n_blocks} blocks in {total:.1f}s, "
          f"{len(rms_map)} electrodes measured")
    return rms_map


# ── Footprint + stim site selection ───────────────────────────────────


def find_footprint(rms_map, percentile):
    """Return (footprint_electrodes, threshold_uv).

    footprint = electrodes in top (100-percentile)% by RMS.
    E.g. percentile=70 → threshold at 70th percentile → top 30% define footprint.
    """
    ids = np.array(list(rms_map.keys()), dtype=np.int32)
    vals = np.array([rms_map[e] for e in ids], dtype=np.float64)
    threshold = float(np.percentile(vals, percentile))
    footprint = ids[vals >= threshold].tolist()
    return footprint, threshold


def farthest_point_sites(footprint, n_sites):
    """Greedily select n_sites electrodes from footprint maximising mutual distance.

    Starts from the electrode farthest from the array centroid, then at each step
    adds the electrode that maximises the minimum distance to already-selected sites.
    """
    if n_sites <= 0:
        return []
    if n_sites >= len(footprint):
        return list(footprint[:n_sites])

    fp = np.array(footprint, dtype=np.int32)
    rows = (fp // NUM_COLS).astype(np.float32)
    cols = (fp % NUM_COLS).astype(np.float32)

    # Seed: electrode farthest from array center
    cr, cc = NUM_ROWS / 2.0, NUM_COLS / 2.0
    dists_center = (rows - cr) ** 2 + (cols - cc) ** 2
    selected_idx = [int(np.argmax(dists_center))]

    while len(selected_idx) < n_sites:
        min_dists = np.full(len(fp), np.inf)
        for si in selected_idx:
            d = (rows - rows[si]) ** 2 + (cols - cols[si]) ** 2
            np.minimum(min_dists, d, out=min_dists)
        next_idx = int(np.argmax(min_dists))
        selected_idx.append(next_idx)

    return [int(fp[i]) for i in selected_idx]


# ── Output ─────────────────────────────────────────────────────────────


def save_results(rms_map, wells, scan_seconds, out_path):
    electrodes = np.array(sorted(rms_map.keys()), dtype=np.int32)
    rms_arr = np.array([rms_map[int(e)] for e in electrodes], dtype=np.float64)
    rows_arr = electrodes // NUM_COLS
    cols_arr = electrodes % NUM_COLS

    np.savez(
        out_path,
        electrodes=electrodes,
        rms_uv=rms_arr,
        rows=rows_arr,
        cols=cols_arr,
        wells=np.array(wells, dtype=np.int32),
        scan_seconds=float(scan_seconds),
        num_rows=NUM_ROWS,
        num_cols=NUM_COLS,
    )
    print(f"  Results saved to {out_path}")


def save_stim_sites(sites, footprint, footprint_threshold, n_sites,
                    footprint_percentile, rms_map, out_path):
    """Write stim_sites.json.  confirmed=false until user reviews and edits."""
    site_records = []
    for site_id, elec in enumerate(sites):
        r, c = electrode_rc(elec)
        site_records.append({
            "id": site_id,
            "electrode": elec,
            "row": r,
            "col": c,
            "rms_uv": round(rms_map.get(elec, 0.0), 2),
        })

    payload = {
        "confirmed": False,
        "n_sites": n_sites,
        "sites": site_records,
        "footprint_n_electrodes": len(footprint),
        "footprint_percentile": footprint_percentile,
        "footprint_rms_threshold_uv": round(footprint_threshold, 2),
        "note": (
            "Review impedance_map.png, then adjust 'sites' if needed and "
            "set 'confirmed': true before the orchestrator proceeds."
        ),
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"  Proposed stim sites saved to {out_path}")
    for s in site_records:
        print(f"    Site {s['id']}: electrode {s['electrode']} "
              f"(row={s['row']}, col={s['col']}, RMS={s['rms_uv']:.1f} µV)")


# ── CLI ────────────────────────────────────────────────────────────────


def _parse_wells(s):
    wells = [int(x) for x in s.split(",") if x.strip()]
    for w in wells:
        if not 0 <= w <= 5:
            raise argparse.ArgumentTypeError(f"well {w} out of range 0-5")
    return wells


def main():
    parser = argparse.ArgumentParser(
        description="Electrode coupling scan (RMS noise — maps organoid footprint)"
    )
    parser.add_argument("--wells", type=_parse_wells, default=None,
                        help="Comma-separated wells, e.g. '0'. Default: config.py WELLS.")
    parser.add_argument("--scan-seconds", type=float, default=5.0,
                        help="Recording seconds per electrode block (default: 5)")
    parser.add_argument("--n-sites", type=int, default=2,
                        help="Number of stim site candidates to propose (default: 2)")
    parser.add_argument("--footprint-percentile", type=float, default=70.0,
                        help="Percentile threshold for organoid footprint: electrodes "
                             "above this RMS percentile are considered footprint. "
                             "70 = top 30%% highest RMS (default).")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory. Default: config.py OUTPUT_DIR.")
    parser.add_argument("--kind", type=str, default=None,
                        choices=manifest_mod.KIND_CHOICES)
    args = parser.parse_args()

    wells = args.wells if args.wells is not None else list(WELLS)
    output_dir = (os.path.abspath(args.output_dir) if args.output_dir
                  else os.path.abspath(OUTPUT_DIR))
    os.makedirs(output_dir, exist_ok=True)

    blocks = partition_sparse_7x(MAX_ELECTRODES_PER_BLOCK)
    est_min = len(blocks) * (args.scan_seconds + 20) / 60.0
    print(f"Electrode coupling scan")
    print(f"  Wells: {wells}")
    print(f"  Blocks: {len(blocks)} × {args.scan_seconds}s ≈ {est_min:.1f} min")
    print(f"  Footprint: top {100 - args.footprint_percentile:.0f}% by RMS")
    print(f"  Output dir: {output_dir}")

    step_started_at = manifest_mod.now_iso()
    step_t0 = time.perf_counter()

    try:
        setup_hardware(wells)
        rms_map = run_scan(blocks, wells, args.scan_seconds, output_dir)

        # Stats
        rms_vals = list(rms_map.values())
        print(f"\n  RMS summary: median={np.median(rms_vals):.1f} µV, "
              f"mean={np.mean(rms_vals):.1f} µV, "
              f"max={np.max(rms_vals):.1f} µV")

        results_path = os.path.join(output_dir, RESULTS_FILE)
        save_results(rms_map, wells, args.scan_seconds, results_path)

        footprint, threshold = find_footprint(rms_map, args.footprint_percentile)
        print(f"\n  Footprint: {len(footprint)} electrodes above "
              f"{threshold:.1f} µV RMS")

        sites = farthest_point_sites(footprint, args.n_sites)
        stim_path = os.path.join(output_dir, STIM_SITES_FILE)
        save_stim_sites(sites, footprint, threshold, args.n_sites,
                        args.footprint_percentile, rms_map, stim_path)

    except Exception as e:
        manifest_mod.record_step_failure(
            output_dir, "impedance_scan", e, kind=args.kind, wells_requested=wells,
            extra={
                "started_at": step_started_at,
                "duration_s": round(time.perf_counter() - step_t0, 2),
            },
        )
        raise

    # Update manifest
    m = manifest_mod.load_or_init(output_dir, kind=args.kind, wells_requested=wells)
    manifest_mod.set_step(m, "impedance_scan", {
        "status": "ok",
        "started_at": step_started_at,
        "duration_s": round(time.perf_counter() - step_t0, 2),
        "scan_seconds_per_block": args.scan_seconds,
        "blocks": len(blocks),
        "electrodes_measured": len(rms_map),
        "footprint_electrodes": len(footprint),
        "footprint_percentile": args.footprint_percentile,
        "footprint_rms_threshold_uv": round(threshold, 2),
        "proposed_stim_sites": sites,
        "output_file": RESULTS_FILE,
        "stim_sites_file": STIM_SITES_FILE,
    })
    manifest_mod.write_atomic(m, output_dir)
    print(f"  Manifest updated")
    print(f"\nNext steps:")
    print(f"  1. conda run -n automation python "
          f"/home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/"
          f"plot_impedance_map.py --output-dir {output_dir}")
    print(f"  2. Review impedance_map.png")
    print(f"  3. Edit {stim_path} — adjust 'sites' if needed, set 'confirmed': true")


if __name__ == "__main__":
    main()
