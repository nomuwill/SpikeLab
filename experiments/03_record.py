#!/usr/bin/env python3
"""
Step 3: Record from the selected electrode configuration.

Loads the .cfg file produced by 02_select_electrodes.py, initialises
the hardware, and records data from all wells for the configured
duration.

Usage:
    python 03_record.py [--duration 300] [--name recording]
"""

import argparse
import os
import time

import maxlab as mx

import manifest as manifest_mod
from config import (
    WELLS,
    DETECTION_THRESHOLD,
    RECORDING_DURATION_SEC,
    OUTPUT_DIR,
    GENERATED_CONFIG_FILE,
    RECORDING_PREFIX,
)


def find_unique_name(directory, prefix):
    """Find a filename that doesn't collide with existing recordings."""
    name = prefix
    counter = 0
    while os.path.exists(os.path.join(directory, f"{name}.raw.h5")):
        counter += 1
        name = f"{prefix}_{counter}"
    return name


def _parse_wells(s):
    wells = [int(x) for x in s.split(",") if x.strip()]
    for w in wells:
        if not 0 <= w <= 5:
            raise argparse.ArgumentTypeError(f"well {w} out of range 0-5")
    if not wells:
        raise argparse.ArgumentTypeError("must specify at least one well")
    return wells


def main():
    parser = argparse.ArgumentParser(description="Record from selected electrodes")
    parser.add_argument("--duration", type=float, default=RECORDING_DURATION_SEC,
                        help=f"Recording duration in seconds (default: {RECORDING_DURATION_SEC})")
    parser.add_argument("--name", type=str, default=RECORDING_PREFIX,
                        help=f"Recording filename prefix (default: {RECORDING_PREFIX})")
    parser.add_argument("--wells", type=_parse_wells, default=None,
                        help="Comma-separated wells (0-5), e.g. '0,2,4'. "
                             "Use a subset for single-well experiments. "
                             "Defaults to WELLS in config.py (all 6).")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Directory to read selected_electrodes.cfg from and "
                             "write <name>.raw.h5 to. Defaults to OUTPUT_DIR in config.py.")
    parser.add_argument("--kind", type=str, default=None,
                        choices=manifest_mod.KIND_CHOICES,
                        help="Experiment kind. Recorded in manifest.json if not already set.")
    args = parser.parse_args()

    step_started_at = manifest_mod.now_iso()

    wells = args.wells if args.wells is not None else list(WELLS)

    output_dir = os.path.abspath(args.output_dir) if args.output_dir else os.path.abspath(OUTPUT_DIR)
    os.makedirs(output_dir, exist_ok=True)
    rec_name = None
    total = None

    try:
        cfg_path = os.path.join(output_dir, GENERATED_CONFIG_FILE)
        if not os.path.exists(cfg_path):
            raise FileNotFoundError(
                f"configuration not found at {cfg_path}. Run 02_select_electrodes.py first."
            )

        rec_name = find_unique_name(output_dir, args.name)

        print(f"Recording configuration")
        print(f"  Config:   {cfg_path}")
        print(f"  Wells:    {wells}")
        print(f"  Duration: {args.duration}s")
        print(f"  Output:   {output_dir}/{rec_name}.raw.h5")
        print()

        # ── Hardware init ──────────────────────────────────────────────────
        print("Initialising hardware...")
        mx.activate(wells)
        mx.initialize(wells)
        time.sleep(mx.Timing.waitInit)

        mx.send(mx.Core().enable_stimulation_power(True))
        mx.send_raw(f"stream_set_event_threshold {DETECTION_THRESHOLD}")

        # ── Load electrode configuration ───────────────────────────────────
        print(f"Loading configuration from {cfg_path}...")
        arr = mx.Array("online")
        arr.load_config(cfg_path)
        arr.download(wells)
        time.sleep(mx.Timing.waitAfterDownload)
        mx.offset()

        # ── Start recording ────────────────────────────────────────────────
        saver = mx.Saving()
        saver.open_directory(output_dir)
        saver.set_legacy_format(False)

        # Define recording groups per well (all channels)
        saver.group_delete_all()
        for w in wells:
            saver.group_define(w, "routed", list(range(1024)))

        saver.start_file(rec_name)
        saver.start_recording(wells)

        print(f"\nRecording started: {rec_name}")
        print(f"  Duration: {args.duration}s")
        print(f"  Wells: {wells}")

        # ── Wait with progress updates ─────────────────────────────────────
        t_start = time.perf_counter()
        interval = max(10.0, args.duration / 20)  # ~20 progress updates
        next_report = interval

        while True:
            elapsed = time.perf_counter() - t_start
            if elapsed >= args.duration:
                break
            if elapsed >= next_report:
                pct = 100 * elapsed / args.duration
                print(f"  {elapsed:.0f}s / {args.duration:.0f}s ({pct:.0f}%)")
                next_report += interval
            time.sleep(min(1.0, args.duration - elapsed))

        # ── Stop recording ─────────────────────────────────────────────────
        saver.stop_recording()
        time.sleep(mx.Timing.waitAfterRecording)
        saver.stop_file()
        saver.group_delete_all()

        total = time.perf_counter() - t_start
        print(f"\nRecording complete: {total:.1f}s")
    except Exception as e:
        manifest_mod.record_step_failure(
            output_dir, "record", e, kind=args.kind, wells_requested=wells,
            extra={"started_at": step_started_at, "name": rec_name,
                   "duration_requested_s": args.duration,
                   "duration_actual_s": round(total, 2) if total is not None else None},
        )
        raise

    h5_path = os.path.join(output_dir, f"{rec_name}.raw.h5")
    if os.path.exists(h5_path):
        size_mb = os.path.getsize(h5_path) / (1024 * 1024)
        print(f"  File: {h5_path} ({size_mb:.1f} MB)")

        # Update manifest.json (append this recording)
        m = manifest_mod.load_or_init(output_dir, kind=args.kind, wells_requested=wells)
        try:
            wells_info = manifest_mod.extract_wells_from_h5(h5_path)
            mxw_version = manifest_mod.read_mxw_version(h5_path)
        except Exception as e:
            wells_info = []
            mxw_version = None
            m["errors"].append(f"manifest: could not read per-well info from {rec_name}.raw.h5: {e}")
        if mxw_version and not m["environment"].get("mxw_version"):
            m["environment"]["mxw_version"] = mxw_version
        manifest_mod.append_recording(m, {
            "status": "ok",
            "started_at": step_started_at,
            "name": rec_name,
            "output_file": f"{rec_name}.raw.h5",
            "output_bytes": os.path.getsize(h5_path),
            "duration_requested_s": args.duration,
            "duration_actual_s": round(total, 2),
            "wells": wells_info,
            "events": [],
        })
        manifest_mod.write_atomic(m, output_dir)
        print(f"  Manifest updated: {manifest_mod.manifest_path(output_dir)}")
    else:
        print(f"  Warning: expected file {h5_path} not found")

    print("Done.")


if __name__ == "__main__":
    main()
