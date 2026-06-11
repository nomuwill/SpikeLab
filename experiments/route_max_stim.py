#!/usr/bin/env python3
"""
route_max_stim.py — Read stim_pool.json and call 02_select_electrodes.py to
produce the final selected_electrodes.cfg + stim_routing.json.

This is a thin wrapper that extracts the electrode_id list from stim_pool.json
and forwards it to 02_select_electrodes.py --stim-electrodes, so you get a
single command that bridges the maximize_stim_electrodes → route step.

Usage:
    /home/sharf-lab/MaxLab/python/bin/python3 \\
        /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/route_max_stim.py \\
        --stim-pool  path/to/stim_pool.json \\
        --scan-results path/to/scan_results.npz \\
        --wells 0 \\
        --output-dir path/to/route_output_dir \\
        [--by amplitude] [--regions 40] [--kind stim]

All arguments except --stim-pool are forwarded verbatim to 02_select_electrodes.py.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Route the stim pool from maximize_stim_electrodes.py.",
    )
    parser.add_argument("--stim-pool", required=True,
                        help="Path to stim_pool.json from maximize_stim_electrodes.py.")
    parser.add_argument("--scan-results", default=None,
                        help="Path to scan_results.npz (passed as --scan-results to 02_select_electrodes.py).")
    parser.add_argument("--wells", required=True,
                        help="Comma-separated well list, e.g. '0'.")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory for selected_electrodes.cfg.")
    parser.add_argument("--by", default="amplitude", choices=["amplitude", "spike_rate"],
                        help="Ranking metric for recording electrode selection (default: amplitude).")
    parser.add_argument("--regions", type=int, default=40,
                        help="Minimum recording regions (default: 40).")
    parser.add_argument("--kind", default=None,
                        help="Experiment kind for manifest (e.g. stim).")
    args = parser.parse_args()

    # Load stim pool
    pool_path = os.path.abspath(args.stim_pool)
    if not os.path.exists(pool_path):
        print(f"ERROR: stim_pool.json not found: {pool_path}", file=sys.stderr)
        sys.exit(1)

    with open(pool_path) as f:
        pool = json.load(f)

    electrode_ids = [str(e["electrode_id"]) for e in pool["electrodes"]]
    if not electrode_ids:
        print("ERROR: stim_pool.json contains no electrodes.", file=sys.stderr)
        sys.exit(1)

    n = len(electrode_ids)
    print(f"Routing {n} stim electrodes from {pool_path}")
    print(f"  Electrode IDs: {', '.join(electrode_ids[:8])}{'...' if n > 8 else ''}")

    # Build 02_select_electrodes.py command
    script = str(Path(__file__).resolve().parent / "02_select_electrodes.py")
    python = "/home/sharf-lab/MaxLab/python/bin/python3"

    cmd = [
        python, script,
        "--by", args.by,
        "--regions", str(args.regions),
        "--wells", args.wells,
        "--stim-electrodes", ",".join(electrode_ids),
        "--output-dir", os.path.abspath(args.output_dir),
    ]
    if args.scan_results:
        cmd += ["--scan-results", os.path.abspath(args.scan_results)]
    if args.kind:
        cmd += ["--kind", args.kind]

    print(f"\nRunning: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
