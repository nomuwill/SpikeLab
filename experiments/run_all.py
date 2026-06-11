#!/usr/bin/env python3
"""
Run the full experiment pipeline: scan → select → record.

Convenience script that executes all three steps in sequence.
Each step can also be run independently.

Usage:
    python run_all.py [--mode full|sparse_7x|checkerboard]
                      [--by spike_rate|amplitude]
                      [--regions 40] [--duration 300] [--name recording]
"""

import argparse
import os
import subprocess
import sys
import time

import manifest as manifest_mod

# Resolve script directory so subprocesses find the right files
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def run_step(name, cmd):
    """Run a step and check for errors."""
    print(f"\n{'#' * 60}")
    print(f"# {name}")
    print(f"{'#' * 60}\n")

    # Resolve the script path relative to this file's directory
    cmd[0] = os.path.join(SCRIPT_DIR, cmd[0])
    result = subprocess.run([sys.executable] + cmd, cwd=SCRIPT_DIR)
    if result.returncode != 0:
        print(f"\nError: {name} failed (exit code {result.returncode})")
        print("Aborting pipeline.")
        sys.exit(result.returncode)

    print(f"\n{name} completed successfully.\n")
    return result


def main():
    parser = argparse.ArgumentParser(description="Run full experiment pipeline")
    parser.add_argument("--mode", choices=["full", "sparse_7x", "checkerboard"],
                        default="sparse_7x",
                        help="Activity scan mode (default: sparse_7x).")
    parser.add_argument("--scan-seconds", type=float, default=None,
                        help="Recording seconds per scan block. Forwarded to "
                             "01_activity_scan.py. Defaults to config.py.")
    parser.add_argument("--by", choices=["spike_rate", "amplitude"],
                        default="spike_rate",
                        help="Metric for electrode ranking (default: spike_rate)")
    parser.add_argument("--regions", type=int, default=40,
                        help="Minimum number of electrode regions (default: 40)")
    parser.add_argument("--duration", type=float, default=300.0,
                        help="Recording duration in seconds (default: 300)")
    parser.add_argument("--name", type=str, default="recording",
                        help="Recording filename prefix (default: recording)")
    parser.add_argument("--wells", type=str, default=None,
                        help="Comma-separated wells (0-5) for ALL steps, e.g. "
                             "'0,2,4'. Defaults to WELLS in config.py.")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Directory to use for ALL steps (scan_results.npz, "
                             "selected_electrodes.cfg, <name>.raw.h5). "
                             "Defaults to OUTPUT_DIR in config.py.")
    parser.add_argument("--kind", type=str, default=None,
                        choices=manifest_mod.KIND_CHOICES,
                        help="Experiment kind. Forwarded to all steps and "
                             "recorded in manifest.json.")
    parser.add_argument("--stim-electrodes", type=str, default=None,
                        help="Comma-separated electrode IDs to wire for "
                             "stimulation in step 2 (forwarded to "
                             "02_select_electrodes.py).  Requires a single "
                             "--wells entry.  Use --duration 0 to skip "
                             "step 3 and run a custom stim experiment "
                             "script afterwards.")
    parser.add_argument("--skip-record", action="store_true",
                        help="Skip step 3 (recording) entirely.  Useful "
                             "when preparing a stim experiment that will "
                             "be driven by a custom script using "
                             "experiment_lib.py.  Equivalent to --duration 0.")
    args = parser.parse_args()

    t_start = time.perf_counter()

    wells_args = ["--wells", args.wells] if args.wells else []
    output_args = ["--output-dir", args.output_dir] if args.output_dir else []
    kind_args = ["--kind", args.kind] if args.kind else []
    common_args = wells_args + output_args + kind_args

    skip_record = args.skip_record or args.duration <= 0

    # Pre-touch manifest so steps see run_all_invoked=true from the start.
    # Resolve output dir the same way each step does.
    from config import OUTPUT_DIR as _DEFAULT_OUTPUT_DIR
    od = os.path.abspath(args.output_dir) if args.output_dir else os.path.abspath(_DEFAULT_OUTPUT_DIR)
    os.makedirs(od, exist_ok=True)
    m = manifest_mod.load_or_init(od, kind=args.kind, run_all_invoked=True)
    manifest_mod.write_atomic(m, od)

    # Step 1: Activity scan
    scan_cmd = ["01_activity_scan.py", "--mode", args.mode]
    if args.scan_seconds is not None:
        scan_cmd += ["--scan-seconds", str(args.scan_seconds)]
    scan_cmd += common_args
    run_step("Step 1: Activity Scan", scan_cmd)

    # Step 2: Electrode selection
    select_cmd = ["02_select_electrodes.py", "--by", args.by,
                  "--regions", str(args.regions)] + common_args
    if args.stim_electrodes:
        select_cmd += ["--stim-electrodes", args.stim_electrodes]
    run_step("Step 2: Electrode Selection", select_cmd)

    # Step 3: Recording (skipped when --skip-record or --duration <= 0)
    if skip_record:
        print(f"\n{'#' * 60}")
        print(f"# Step 3: Recording — SKIPPED "
              f"({'--skip-record' if args.skip_record else '--duration 0'})")
        print(f"{'#' * 60}\n")
    else:
        record_cmd = ["03_record.py", "--duration", str(args.duration),
                      "--name", args.name] + common_args
        run_step("Step 3: Recording", record_cmd)

    total = time.perf_counter() - t_start
    print(f"{'=' * 60}")
    print(f"  Pipeline complete in {total:.1f}s ({total / 60:.1f} min)")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
