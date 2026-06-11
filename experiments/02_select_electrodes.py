#!/usr/bin/env python3
"""
Step 2: Select electrodes from scan results and create a .cfg file.

Loads the .npz file produced by 01_activity_scan.py, places circular
regions around the most active electrodes, routes them through the
MaxWell SDK, and saves the resulting .cfg configuration.

The selection algorithm places a configurable number of regions around
the highest-ranked electrodes.  Each region includes the nearest N
electrodes from the full 26,400-electrode grid (not just scanned ones).
When regions overlap, the overlapping electrodes free up budget for
additional regions to be placed.

Usage:
    python 02_select_electrodes.py [--by spike_rate|amplitude] [--regions 40]
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
    MIN_REGIONS,
    MAX_SELECTED_ELECTRODES,
    OUTPUT_DIR,
    SCAN_RESULTS_FILE,
    GENERATED_CONFIG_FILE,
)

# ── Grid constants ─────────────────────────────────────────────────────

NUM_ROWS = 120
NUM_COLS = 220
TOTAL_ELECTRODES = NUM_ROWS * NUM_COLS


# ── Spatial helpers ────────────────────────────────────────────────────


def electrode_position(electrode_id):
    """Convert flat electrode index to (row, col)."""
    return divmod(electrode_id, NUM_COLS)


def find_nearest(center_id, count):
    """Return the *count* nearest electrode IDs to *center_id* on the grid."""
    cr, cc = electrode_position(center_id)
    all_ids = np.arange(TOTAL_ELECTRODES)
    rows = all_ids // NUM_COLS
    cols = all_ids % NUM_COLS
    dists = (rows - cr) ** 2 + (cols - cc) ** 2
    if count >= len(dists):
        order = np.argsort(dists)
    else:
        candidates = np.argpartition(dists, count)[:count]
        order = candidates[np.argsort(dists[candidates])]
    return order.tolist()


# ── Region placement ───────────────────────────────────────────────────


def place_regions(ranked_electrodes, min_regions, max_total, electrodes_per_region):
    """Place circular regions around ranked electrodes.

    Phase 1: place min_regions regions.  Overlapping electrodes are
    free (already selected), effectively increasing the budget.

    Phase 2: spend any remaining budget on additional regions.

    Returns:
        selected:  set of electrode IDs
        regions:   list of (center, electrode_list, n_new, n_overlap) tuples
    """
    selected = set()
    regions = []
    used_centers = set()

    def _add_region(center, phase_label):
        nearest = find_nearest(center, electrodes_per_region)
        n_overlap = sum(1 for e in nearest if e in selected)
        n_new = len(nearest) - n_overlap
        selected.update(nearest)
        regions.append((center, nearest, n_new, n_overlap, phase_label))

    # Phase 1: guaranteed regions
    placed = 0
    for el in ranked_electrodes:
        if el in used_centers:
            continue
        used_centers.add(el)
        _add_region(el, 1)
        placed += 1
        if placed >= min_regions:
            break

    # Phase 2: bonus regions from freed budget
    budget_left = max_total - len(selected)
    for el in ranked_electrodes:
        if budget_left < electrodes_per_region:
            break
        if el in used_centers:
            continue
        used_centers.add(el)

        # Check how many new electrodes this region would cost
        preview = find_nearest(el, electrodes_per_region)
        cost = sum(1 for e in preview if e not in selected)
        if cost > budget_left:
            continue

        _add_region(el, 2)
        budget_left = max_total - len(selected)

    return selected, regions


# ── Routing and saving ─────────────────────────────────────────────────


def route_and_save(electrodes, wells, threshold, output_path,
                   stim_electrodes=None):
    """Route electrodes through the MaxWell SDK and save the .cfg file.

    If *stim_electrodes* is provided, those electrodes are additionally
    wired to stim buffers via select_stimulation_electrodes().  The
    function returns a dict mapping ``{electrode: unit_id}`` for the
    wired stim electrodes, so the caller can persist it alongside the
    .cfg for later use by experiment scripts.
    """
    print(f"  Initialising hardware...")
    mx.activate(wells)
    mx.initialize(wells)
    time.sleep(mx.Timing.waitInit)

    mx.send(mx.Core().enable_stimulation_power(True))
    mx.send_raw(f"stream_set_event_threshold {threshold}")

    print(f"  Routing {len(electrodes)} electrodes...")
    arr = mx.Array("online")
    arr.reset()
    arr.select_electrodes(list(electrodes))
    if stim_electrodes:
        arr.select_stimulation_electrodes(list(stim_electrodes))
    arr.route()

    # Validate stim electrode routing BEFORE download
    stim_unit_map = {}
    if stim_electrodes:
        used_units = set()
        for elec in stim_electrodes:
            arr.connect_electrode_to_stimulation(elec)
            raw = arr.query_stimulation_at_electrode(elec)
            try:
                unit_id = int(str(raw).strip())
            except (TypeError, ValueError):
                unit_id = 0
            if unit_id <= 0:
                raise RuntimeError(
                    f"Electrode {elec} cannot be stimulated "
                    f"(query returned {raw!r})."
                )
            if unit_id in used_units:
                prev = next(e for e, u in stim_unit_map.items() if u == unit_id)
                raise RuntimeError(
                    f"Stim electrodes {prev} and {elec} both resolve to stim "
                    f"unit {unit_id}.  Choose a different electrode."
                )
            used_units.add(unit_id)
            stim_unit_map[elec] = unit_id
        print(f"  Stim electrodes wired: {stim_unit_map}")

    arr.download(wells)
    time.sleep(mx.Timing.waitAfterDownload)
    mx.offset()

    arr.save_config(output_path)
    print(f"  Configuration saved to: {output_path}")

    return stim_unit_map


def _parse_stim_electrodes(s):
    elecs = [int(x) for x in s.split(",") if x.strip()]
    if not elecs:
        raise argparse.ArgumentTypeError(
            "--stim-electrodes must contain at least one electrode"
        )
    if len(set(elecs)) != len(elecs):
        raise argparse.ArgumentTypeError(
            "duplicate electrodes in --stim-electrodes"
        )
    if len(elecs) > 32:
        raise argparse.ArgumentTypeError(
            f"--stim-electrodes has {len(elecs)} entries; hardware limit is 32"
        )
    for e in elecs:
        if not 0 <= e < 26400:
            raise argparse.ArgumentTypeError(
                f"electrode {e} out of range 0-26399"
            )
    return elecs


# ── Print summary ─────────────────────────────────────────────────────


def print_region_summary(regions, total_selected, max_total):
    phase1 = [r for r in regions if r[4] == 1]
    phase2 = [r for r in regions if r[4] == 2]
    total_overlap = sum(r[3] for r in regions)
    avg_radius = 0.0
    if regions:
        radii = []
        for center, elecs, _, _, _ in regions:
            cr, cc = electrode_position(center)
            if elecs:
                fr, fc = electrode_position(elecs[-1])
                radii.append(np.sqrt((cr - fr) ** 2 + (cc - fc) ** 2))
        avg_radius = np.mean(radii) if radii else 0.0

    print(f"\n{'=' * 60}")
    print(f"  ELECTRODE SELECTION SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Total electrodes:      {total_selected} / {max_total}")
    print(f"  Phase 1 regions:       {len(phase1)}")
    print(f"  Phase 2 regions:       {len(phase2)}")
    print(f"  Total regions:         {len(regions)}")
    print(f"  Total overlaps:        {total_overlap}")
    print(f"  Avg region radius:     {avg_radius:.1f} grid units (~{avg_radius * 17.5:.0f} µm)")
    print()

    print("  Top 5 regions:")
    for i, (center, elecs, n_new, n_overlap, phase) in enumerate(regions[:5]):
        r, c = electrode_position(center)
        print(f"    #{i+1}: electrode {center} (row={r}, col={c}), "
              f"new={n_new}, overlap={n_overlap}, phase={phase}")
    if len(regions) > 5:
        print(f"    ... and {len(regions) - 5} more")
    print()


# ── Entry point ────────────────────────────────────────────────────────


def _parse_wells(s):
    wells = [int(x) for x in s.split(",") if x.strip()]
    for w in wells:
        if not 0 <= w <= 5:
            raise argparse.ArgumentTypeError(f"well {w} out of range 0-5")
    if not wells:
        raise argparse.ArgumentTypeError("must specify at least one well")
    return wells


def main():
    parser = argparse.ArgumentParser(description="Select electrodes from scan results")
    parser.add_argument("--by", choices=["spike_rate", "amplitude"], default="spike_rate",
                        help="Metric to rank electrodes by")
    parser.add_argument("--regions", type=int, default=MIN_REGIONS,
                        help=f"Minimum number of regions (default: {MIN_REGIONS})")
    parser.add_argument("--wells", type=_parse_wells, default=None,
                        help="Comma-separated wells (0-5). Overrides config.py "
                             "and the wells recorded in scan_results.npz.")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Directory to read scan_results.npz from (when --scan-results "
                             "is not given) and write selected_electrodes.cfg to. "
                             "Defaults to OUTPUT_DIR in config.py.")
    parser.add_argument("--scan-results", type=str, default=None,
                        help="Explicit path to a scan_results.npz file. If provided, this "
                             "file is read directly instead of {output-dir}/scan_results.npz, "
                             "avoiding the need to copy scan results into every per-config "
                             "output directory.")
    parser.add_argument("--kind", type=str, default=None,
                        choices=manifest_mod.KIND_CHOICES,
                        help="Experiment kind. Recorded in manifest.json if not already set.")
    parser.add_argument("--stim-electrodes", type=_parse_stim_electrodes, default=None,
                        help="Comma-separated electrode IDs (e.g. '5280,12464') to wire "
                             "for stimulation in the produced .cfg.  Max 32.  Requires a "
                             "single-well --wells (stim is single-well in this minimal "
                             "implementation).  Each electrode is validated to map to a "
                             "unique stim buffer; failures raise.  When set, the routing "
                             "is also written to stim_routing.json next to the .cfg.")
    args = parser.parse_args()

    step_started_at = manifest_mod.now_iso()
    step_t0 = time.perf_counter()

    # Resolve to absolute paths
    output_dir = os.path.abspath(args.output_dir) if args.output_dir else os.path.abspath(OUTPUT_DIR)
    wells = None  # may remain None if we fail before loading scan results

    try:
        # Load scan results: --scan-results wins over --output-dir default.
        if args.scan_results:
            scan_path = os.path.abspath(args.scan_results)
        else:
            scan_path = os.path.join(output_dir, SCAN_RESULTS_FILE)
        if not os.path.exists(scan_path):
            raise FileNotFoundError(
                f"scan results not found at {scan_path}. Run 01_activity_scan.py first."
            )

        data = np.load(scan_path)
        electrodes = data["electrodes"]
        if args.by == "spike_rate":
            values = data["spike_rates"]
        else:
            values = data["mean_amplitudes"]

        # Wells: CLI > scan_results.npz > config.py
        if args.wells is not None:
            wells = args.wells
        elif "wells" in data.files:
            wells = data["wells"].tolist()
        else:
            wells = list(WELLS)

        print(f"Loaded scan results: {len(electrodes)} electrodes")
        print(f"Ranking by: {args.by}")
        print(f"Minimum regions: {args.regions}")
        print(f"Wells: {wells}")

        # Rank electrodes by chosen metric (descending)
        order = np.argsort(-values)
        ranked = electrodes[order].tolist()

        # Compute budget per region
        per_region = MAX_SELECTED_ELECTRODES // args.regions
        print(f"Budget per region: {per_region} electrodes")

        # Place regions
        selected, regions = place_regions(ranked, args.regions, MAX_SELECTED_ELECTRODES, per_region)
        print_region_summary(regions, len(selected), MAX_SELECTED_ELECTRODES)

        # Stim electrodes (single-well only in this minimal implementation)
        stim_unit_map = {}
        if args.stim_electrodes:
            if len(wells) != 1:
                raise ValueError(
                    f"--stim-electrodes requires exactly one --wells entry; "
                    f"got {wells}.  Pass `--wells N` explicitly (where N is "
                    f"the single well to stimulate).  Stim is single-well "
                    f"in this minimal implementation."
                )
            print(f"Stim electrodes: {args.stim_electrodes}")

        # Route and save
        cfg_path = os.path.join(output_dir, GENERATED_CONFIG_FILE)
        stim_unit_map = route_and_save(
            sorted(selected), wells, DETECTION_THRESHOLD, cfg_path,
            stim_electrodes=args.stim_electrodes,
        )

        # Persist stim routing alongside the .cfg, for experiment scripts
        if stim_unit_map:
            import json
            stim_routing_path = os.path.join(output_dir, "stim_routing.json")
            with open(stim_routing_path, "w") as f:
                json.dump({
                    "wells": wells,
                    "stim_electrodes": list(args.stim_electrodes),
                    "routing": {str(e): u for e, u in stim_unit_map.items()},
                }, f, indent=2)
            print(f"  Stim routing saved to: {stim_routing_path}")
    except Exception as e:
        manifest_mod.record_step_failure(
            output_dir, "select", e, kind=args.kind, wells_requested=wells,
            extra={"started_at": step_started_at,
                   "duration_s": round(time.perf_counter() - step_t0, 2),
                   "by": args.by},
        )
        raise

    # Update manifest.json
    phase1 = sum(1 for r in regions if r[4] == 1)
    phase2 = sum(1 for r in regions if r[4] == 2)
    m = manifest_mod.load_or_init(output_dir, kind=args.kind, wells_requested=wells)
    select_entry = {
        "status": "ok",
        "started_at": step_started_at,
        "duration_s": round(time.perf_counter() - step_t0, 2),
        "by": args.by,
        "regions_phase1": phase1,
        "regions_phase2": phase2,
        "total_regions": len(regions),
        "electrodes_selected": len(selected),
        "output_file": GENERATED_CONFIG_FILE,
        "output_bytes": os.path.getsize(cfg_path),
    }
    if stim_unit_map:
        select_entry["stim_electrodes"] = list(args.stim_electrodes)
        select_entry["stim_routing"] = {str(e): u for e, u in stim_unit_map.items()}
    manifest_mod.set_step(m, "select", select_entry)
    manifest_mod.write_atomic(m, output_dir)
    print(f"  Manifest updated: {manifest_mod.manifest_path(output_dir)}")

    print("Done.")


if __name__ == "__main__":
    main()
