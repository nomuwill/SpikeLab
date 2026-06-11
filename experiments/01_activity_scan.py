#!/usr/bin/env python3
"""
Step 1: Activity scan across the electrode array.

Sweeps across blocks of electrodes on all wells, records spike events
from each block, and saves per-electrode spike rate and mean amplitude
to an .npz file for subsequent electrode selection.

The MaxTwo has 26,400 electrodes per well but only 1,024 readout
channels, so scanning requires multiple routing passes.  Three modes:

  - full:          all 26,400 electrodes, ~26 blocks
  - sparse_7x:     every other row + column (6,600 electrodes), ~7 blocks
  - checkerboard:  (row+col) even (13,200 electrodes), ~13 blocks

Usage:
    python 01_activity_scan.py [--mode {full,sparse_7x,checkerboard}]
"""

import argparse
import os
import sys
import time

import numpy as np
import maxlab as mx

import manifest as manifest_mod
from config import (
    WELLS,
    DETECTION_THRESHOLD,
    SCAN_SECONDS_PER_BLOCK,
    MAX_ELECTRODES_PER_BLOCK,
    MIN_REGIONS,
    MAX_SELECTED_ELECTRODES,
    OUTPUT_DIR,
    SCAN_RESULTS_FILE,
)

# ── Grid constants ─────────────────────────────────────────────────────

NUM_ROWS = 120
NUM_COLS = 220
TOTAL_ELECTRODES = NUM_ROWS * NUM_COLS
SAMPLING_RATE = 10_000


# ── Block partitioning ─────────────────────────────────────────────────


def partition_full(max_per_block):
    """All electrodes, split into contiguous blocks."""
    all_ids = list(range(TOTAL_ELECTRODES))
    return [all_ids[i:i + max_per_block] for i in range(0, len(all_ids), max_per_block)]


def partition_sparse_7x(max_per_block, row_step=2, col_step=2):
    """Every row_step-th row and col_step-th column (1/4 of the grid)."""
    ids = []
    for r in range(0, NUM_ROWS, row_step):
        for c in range(0, NUM_COLS, col_step):
            ids.append(r * NUM_COLS + c)
    return [ids[i:i + max_per_block] for i in range(0, len(ids), max_per_block)]


def partition_checkerboard(max_per_block):
    """Checkerboard sampling: electrodes where (row + col) is even (1/2 of the grid)."""
    ids = []
    for r in range(NUM_ROWS):
        for c in range(NUM_COLS):
            if (r + c) % 2 == 0:
                ids.append(r * NUM_COLS + c)
    return [ids[i:i + max_per_block] for i in range(0, len(ids), max_per_block)]


# ── Read spikes from recorded HDF5 ────────────────────────────────────


def extract_spikes(filepath):
    """Read spike events and channel mapping from a Maxwell HDF5 file.

    Returns list of (electrode_id, amplitude) tuples.

    Maxwell HDF5 files require PyTables to read. Importing both maxlab and
    tables in the same process causes a libhdf5 symbol collision and a
    segfault, so extraction runs in a clean subprocess that imports only
    tables (no maxlab).
    """
    import subprocess
    import json

    reader = (
        "import warnings, tables, json, sys\n"
        "spikes = []\n"
        "with warnings.catch_warnings():\n"
        "    warnings.simplefilter('ignore')\n"
        "    with tables.open_file(sys.argv[1], 'r') as h:\n"
        "        ch_to_el = {}\n"
        "        try:\n"
        "            for row in h.root.data_store.data0000.settings.mapping[:]:\n"
        "                ch_to_el[int(row['channel'])] = int(row['electrode'])\n"
        "        except Exception:\n"
        "            pass\n"
        "        try:\n"
        "            for row in h.root.data_store.data0000.spikes[:]:\n"
        "                ch = int(row['channel'])\n"
        "                amp = float(row['amplitude'])\n"
        "                el = ch_to_el.get(ch, -1)\n"
        "                if el >= 0:\n"
        "                    spikes.append([el, abs(amp)])\n"
        "        except Exception:\n"
        "            pass\n"
        "print(json.dumps(spikes))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", reader, filepath],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return []
    try:
        return [tuple(row) for row in json.loads(result.stdout)]
    except Exception:
        return []


# ── Hardware setup ─────────────────────────────────────────────────────


def setup_hardware(wells, threshold):
    """One-time hardware initialisation."""
    print(f"  Activating wells: {wells}")
    mx.activate(wells)
    mx.initialize(wells)
    time.sleep(mx.Timing.waitInit)

    mx.send(mx.Core().enable_stimulation_power(True))
    mx.send_raw(f"stream_set_event_threshold {threshold}")
    print("  Hardware initialised")


def route_block(electrodes, wells):
    """Route a block of electrodes and download to all wells."""
    arr = mx.Array("online")
    arr.reset()
    arr.select_electrodes(electrodes)
    arr.route()
    arr.download(wells)
    time.sleep(mx.Timing.waitAfterDownload)
    mx.offset()


def record_block(wells, duration, save_dir, block_idx):
    """Record spikes for one block. Returns the HDF5 filepath."""
    save_dir = os.path.abspath(save_dir)
    fname = f"_scan_block_{block_idx:04d}"
    fpath = os.path.join(save_dir, f"{fname}.raw.h5")

    saver = mx.Saving()
    saver.open_directory(save_dir)
    saver.set_legacy_format(False)
    saver.start_file(fname)
    saver.start_recording(wells)

    time.sleep(duration)

    saver.stop_recording()
    time.sleep(mx.Timing.waitAfterRecording)
    saver.stop_file()

    return fpath


# ── Main scan loop ─────────────────────────────────────────────────────


def run_scan(blocks, wells, duration_per_block, save_dir):
    """Execute the scan and return per-electrode metrics.

    Returns:
        rates:  dict  {electrode_id: spikes_per_second}
        amps:   dict  {electrode_id: mean_absolute_amplitude}
    """
    spike_counts = {}   # electrode -> total spikes
    amp_sums = {}       # electrode -> sum of |amplitude|
    rec_time = {}       # electrode -> total recording seconds

    n_blocks = len(blocks)
    t_start = time.perf_counter()

    for i, block in enumerate(blocks):
        elapsed = time.perf_counter() - t_start
        print(f"\n  Block {i + 1}/{n_blocks} ({len(block)} electrodes, "
              f"elapsed {elapsed:.0f}s)")

        # Route and record
        route_block(block, wells)
        fpath = record_block(wells, duration_per_block, save_dir, i)

        # Extract spikes
        spikes = extract_spikes(fpath)
        print(f"    {len(spikes)} spikes extracted")

        # Accumulate metrics
        for el in block:
            rec_time[el] = rec_time.get(el, 0.0) + duration_per_block

        for el, amp in spikes:
            spike_counts[el] = spike_counts.get(el, 0) + 1
            amp_sums[el] = amp_sums.get(el, 0.0) + abs(amp)

        # Clean up temporary file
        try:
            os.remove(fpath)
        except OSError:
            pass

    # Compute final metrics
    rates = {}
    amplitudes = {}
    for el in rec_time:
        n = spike_counts.get(el, 0)
        t = rec_time[el]
        rates[el] = n / t if t > 0 else 0.0
        amplitudes[el] = (amp_sums.get(el, 0.0) / n) if n > 0 else 0.0

    total_time = time.perf_counter() - t_start
    print(f"\n  Scan complete: {n_blocks} blocks in {total_time:.1f}s")
    print(f"  Electrodes scanned: {len(rates)}")
    print(f"  Electrodes with activity: {sum(1 for r in rates.values() if r > 0)}")

    return rates, amplitudes


# ── Save results ───────────────────────────────────────────────────────


def save_results(rates, amplitudes, mode, filepath, wells, scan_seconds):
    """Save scan metrics to an .npz file."""
    electrodes = sorted(rates.keys())
    rate_arr = np.array([rates[e] for e in electrodes], dtype=np.float64)
    amp_arr = np.array([amplitudes[e] for e in electrodes], dtype=np.float64)
    el_arr = np.array(electrodes, dtype=np.int32)

    np.savez(
        filepath,
        electrodes=el_arr,
        spike_rates=rate_arr,
        mean_amplitudes=amp_arr,
        scan_mode=mode,
        detection_threshold=DETECTION_THRESHOLD,
        seconds_per_block=scan_seconds,
        wells=np.array(wells, dtype=np.int32),
    )
    print(f"  Results saved to: {filepath}")


# ── Print summary ─────────────────────────────────────────────────────


def print_summary(rates, amplitudes):
    rate_vals = np.array(list(rates.values()))
    amp_vals = np.array(list(amplitudes.values()))
    active = rate_vals > 0

    print(f"\n{'=' * 60}")
    print(f"  SCAN SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total electrodes:    {len(rate_vals)}")
    print(f"  Active electrodes:   {np.sum(active)} ({100 * np.mean(active):.1f}%)")
    if np.any(active):
        print(f"  Rate — mean: {np.mean(rate_vals[active]):.3f} Hz, "
              f"max: {np.max(rate_vals):.3f} Hz")
        print(f"  Amplitude — mean: {np.mean(amp_vals[active]):.3f} µV, "
              f"max: {np.max(amp_vals):.3f} µV")
    print()


# ── Entry point ────────────────────────────────────────────────────────


def _parse_wells(s):
    """Parse comma-separated well list, e.g. '0,2,4'. Validates range 0-5."""
    wells = [int(x) for x in s.split(",") if x.strip()]
    for w in wells:
        if not 0 <= w <= 5:
            raise argparse.ArgumentTypeError(f"well {w} out of range 0-5")
    if not wells:
        raise argparse.ArgumentTypeError("must specify at least one well")
    return wells


def main():
    parser = argparse.ArgumentParser(description="Activity scan")
    parser.add_argument("--mode", choices=["full", "sparse_7x", "checkerboard"],
                        default="sparse_7x",
                        help="Scan mode. full=all electrodes (~26 blocks); "
                             "sparse_7x=every other row/col (~7 blocks, default); "
                             "checkerboard=(row+col) even (~13 blocks).")
    parser.add_argument("--scan-seconds", type=float, default=None,
                        help="Recording seconds per electrode block. "
                             "Defaults to SCAN_SECONDS_PER_BLOCK in config.py.")
    parser.add_argument("--wells", type=_parse_wells, default=None,
                        help="Comma-separated wells (0-5), e.g. '0,2,4'. "
                             "Overrides WELLS in config.py.")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Directory for scan_results.npz (and temporary "
                             "per-block files). Defaults to OUTPUT_DIR in config.py.")
    parser.add_argument("--kind", type=str, default=None,
                        choices=manifest_mod.KIND_CHOICES,
                        help="Experiment kind. Recorded in manifest.json. "
                             "Usually set via run_all.py or 03_record.py; pass here "
                             "when running 01 standalone for a named timepoint.")
    args = parser.parse_args()

    wells = args.wells if args.wells is not None else WELLS
    mode = args.mode
    scan_seconds = args.scan_seconds if args.scan_seconds is not None else SCAN_SECONDS_PER_BLOCK
    print(f"Starting {mode} activity scan on wells {wells} ({scan_seconds}s/block)")

    # Resolve to absolute path so the MaxWell server writes to the right place
    output_dir = os.path.abspath(args.output_dir) if args.output_dir else os.path.abspath(OUTPUT_DIR)
    os.makedirs(output_dir, exist_ok=True)

    if mode == "sparse_7x":
        blocks = partition_sparse_7x(MAX_ELECTRODES_PER_BLOCK)
    elif mode == "checkerboard":
        blocks = partition_checkerboard(MAX_ELECTRODES_PER_BLOCK)
    else:
        blocks = partition_full(MAX_ELECTRODES_PER_BLOCK)

    print(f"  {len(blocks)} blocks, {scan_seconds}s per block")

    step_started_at = manifest_mod.now_iso()
    step_t0 = time.perf_counter()

    try:
        setup_hardware(wells, DETECTION_THRESHOLD)
        rates, amplitudes = run_scan(blocks, wells, scan_seconds, output_dir)
        print_summary(rates, amplitudes)

        out_path = os.path.join(output_dir, SCAN_RESULTS_FILE)
        save_results(rates, amplitudes, mode, out_path, wells, scan_seconds)
    except Exception as e:
        manifest_mod.record_step_failure(
            output_dir, "scan", e, kind=args.kind, wells_requested=wells,
            extra={"started_at": step_started_at,
                   "duration_s": round(time.perf_counter() - step_t0, 2),
                   "mode": mode, "scan_seconds_per_block": scan_seconds,
                   "blocks": len(blocks)},
        )
        raise

    # Update manifest.json
    active = sum(1 for r in rates.values() if r > 0)
    cli_args = {
        "mode": mode,
        "scan_seconds": scan_seconds,
        "wells": wells,
        "output_dir": output_dir,
        "kind": args.kind,
    }
    config_snapshot = {
        "DETECTION_THRESHOLD": DETECTION_THRESHOLD,
        "SCAN_SECONDS_PER_BLOCK": SCAN_SECONDS_PER_BLOCK,
        "MIN_REGIONS": MIN_REGIONS,
        "MAX_SELECTED_ELECTRODES": MAX_SELECTED_ELECTRODES,
    }
    m = manifest_mod.load_or_init(
        output_dir, kind=args.kind, wells_requested=wells,
        cli_args=cli_args, config_snapshot=config_snapshot,
    )
    manifest_mod.set_step(m, "scan", {
        "status": "ok",
        "started_at": step_started_at,
        "duration_s": round(time.perf_counter() - step_t0, 2),
        "mode": mode,
        "scan_seconds_per_block": scan_seconds,
        "blocks": len(blocks),
        "electrodes_scanned": len(rates),
        "active_electrodes": active,
        "output_file": SCAN_RESULTS_FILE,
        "output_bytes": os.path.getsize(out_path),
    })
    manifest_mod.write_atomic(m, output_dir)
    print(f"  Manifest updated: {manifest_mod.manifest_path(output_dir)}")


if __name__ == "__main__":
    main()
