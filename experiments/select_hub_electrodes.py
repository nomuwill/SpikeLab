#!/usr/bin/env python3
"""
select_hub_electrodes.py — Greedy switch-matrix-aware hub electrode selection.

Places two hub centres randomly on opposite sides of the organoid footprint
(via PCA split), then greedily expands each hub within a 100 µm radius by
querying the MaxWell switch matrix after each candidate addition.  Interleaves
additions between hubs so both grow evenly.  Produces the definitive
selected_electrodes.cfg + stim_routing.json via a single final call to
02_select_electrodes.py.

Must run under MaxLab Python (hardware routing queries require maxlab):

    /home/sharf-lab/MaxLab/python/bin/python3 \\
      /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/select_hub_electrodes.py \\
      --impedance-dir  orchestrator/hub-induction_2026-05-18/recordings/setup \\
      --output-dir     orchestrator/hub-induction_2026-05-18/config \\
      [--well 0] [--hub-radius-um 100] [--n-per-hub 8]
      [--footprint-percentile 70] [--seed SEED]

Outputs (in --output-dir):
  hub_config.json             hub electrode lists, routing, centres
  selected_electrodes.cfg     MaxWell routing config (from 02_select_electrodes.py)
  stim_routing.json           stim unit assignments (from 02_select_electrodes.py)
  manifest.json               step record

After this script, run:
  conda run -n automation python plot_impedance_map.py \\
      --impedance-dir <impedance-dir> --output-dir <output-dir>
to generate a hub overlay plot for visual verification.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import maxlab as mx

import manifest as manifest_mod
from config import DETECTION_THRESHOLD

# ── Constants ──────────────────────────────────────────────────────────

SCRIPTS_DIR = str(Path(__file__).resolve().parent)
MAXLAB_PYTHON = "/home/sharf-lab/MaxLab/python/bin/python3"
NUM_ROWS = 120
NUM_COLS = 220
PITCH_UM = 17.5          # µm per electrode grid unit (MaxWell HDMEA)
HUB_CONFIG_FILE = "hub_config.json"
IMPEDANCE_FILE = "impedance_results.npz"


# ── Geometry helpers ────────────────────────────────────────────────────

def electrode_rc(elec):
    return divmod(int(elec), NUM_COLS)


def electrodes_within_radius(center_elec, radius_grid):
    """All valid grid electrodes within radius_grid units of center, sorted by distance."""
    cr, cc = electrode_rc(center_elec)
    r_lo = max(0,          int(cr - radius_grid) - 1)
    r_hi = min(NUM_ROWS-1, int(cr + radius_grid) + 1)
    c_lo = max(0,          int(cc - radius_grid) - 1)
    c_hi = min(NUM_COLS-1, int(cc + radius_grid) + 1)

    candidates = []
    for r in range(r_lo, r_hi + 1):
        for c in range(c_lo, c_hi + 1):
            dist = (r - cr)**2 + (c - cc)**2  # squared — sort cheaply
            if dist <= radius_grid**2:
                candidates.append((dist, r * NUM_COLS + c))
    candidates.sort()
    return [elec for _, elec in candidates]  # centre is first (dist=0)


# ── Hub centre selection ────────────────────────────────────────────────

def select_hub_centres(imp_data, footprint_percentile, rng):
    """Return (centre_a, centre_b) from opposite PCA-split footprint pools."""
    electrodes = imp_data["electrodes"]
    rms        = imp_data["rms_uv"]
    rows       = imp_data["rows"].astype(float)
    cols       = imp_data["cols"].astype(float)

    threshold  = np.percentile(rms, footprint_percentile)
    mask       = rms >= threshold
    fp_elecs   = electrodes[mask]
    fp_rows    = rows[mask]
    fp_cols    = cols[mask]

    if len(fp_elecs) < 4:
        raise RuntimeError(
            f"Only {len(fp_elecs)} footprint electrodes above the {footprint_percentile}th "
            "percentile — the impedance scan may not have captured tissue contact.  "
            "Try re-running 00_impedance_scan.py or lowering --footprint-percentile."
        )

    # PCA: project footprint positions onto principal axis
    positions = np.stack([fp_rows, fp_cols], axis=1)
    centroid  = positions.mean(axis=0)
    X         = positions - centroid
    _, _, Vt  = np.linalg.svd(X, full_matrices=False)
    pc1       = Vt[0]                      # major axis of organoid footprint
    proj      = X @ pc1                    # scalar projection per electrode

    # Opposite-side pools: bottom and top quartiles along principal axis
    p25 = np.percentile(proj, 25)
    p75 = np.percentile(proj, 75)
    pool_a = fp_elecs[proj <= p25]         # "one end"
    pool_b = fp_elecs[proj >= p75]         # "other end"

    if len(pool_a) == 0 or len(pool_b) == 0:
        raise RuntimeError(
            "PCA split produced an empty pool — footprint may be too small.  "
            "Lower --footprint-percentile."
        )

    centre_a = int(rng.choice(pool_a))
    centre_b = int(rng.choice(pool_b))
    print(f"  Hub A centre: electrode {centre_a} "
          f"(row={centre_a // NUM_COLS}, col={centre_a % NUM_COLS})")
    print(f"  Hub B centre: electrode {centre_b} "
          f"(row={centre_b // NUM_COLS}, col={centre_b % NUM_COLS})")

    # Sanity: separation
    ra, ca = electrode_rc(centre_a)
    rb, cb = electrode_rc(centre_b)
    sep_um = PITCH_UM * ((ra - rb)**2 + (ca - cb)**2)**0.5
    print(f"  Centre separation: {sep_um:.0f} µm")
    if sep_um < 200:
        print("  WARNING: centres are < 200 µm apart — hubs may overlap.  "
              "Consider a different seed or a smaller footprint-percentile.")

    return centre_a, centre_b


# ── Switch matrix compatibility check ──────────────────────────────────

def check_compatible(electrode_list, well):
    """Route electrode_list and return {elec: unit_id} if all unique, else None.

    Mirrors the logic in check_stim_compatibility.py but returns the full
    assignment dict rather than a payload, and returns None (not raises) on any
    conflict so the greedy loop can just try the next candidate.
    """
    try:
        arr = mx.Array("online")
        arr.reset()
        arr.select_electrodes(list(electrode_list))
        arr.select_stimulation_electrodes(list(electrode_list))
        arr.route()

        assignments = {}
        used_units  = set()
        for elec in electrode_list:
            arr.connect_electrode_to_stimulation(elec)
            raw = arr.query_stimulation_at_electrode(elec)
            try:
                uid = int(str(raw).strip())
            except (TypeError, ValueError):
                return None
            if uid <= 0 or uid in used_units:
                return None
            used_units.add(uid)
            assignments[elec] = uid

        return assignments
    except Exception:
        return None


# ── Greedy interleaved hub expansion ───────────────────────────────────

def greedy_expand(centre_a, centre_b, cands_a, cands_b, well, n_min, n_max):
    """Interleaved greedy search: add one electrode per hub per round.

    cands_a / cands_b: electrode lists sorted by distance from hub centre
                       (centre is first element — already selected).
    Checks the combined (hub_a ∪ hub_b) set against the switch matrix after
    every addition so cross-hub conflicts are caught early.

    Returns (hub_a, hub_b, routing_dict).
    """
    hub_a = [centre_a]
    hub_b = [centre_b]

    # Skip centres from expansion pools
    expand_a = [e for e in cands_a if e != centre_a]
    expand_b = [e for e in cands_b if e != centre_b]
    ia, ib   = 0, 0

    # Verify centres are mutually compatible before expanding
    routing = check_compatible(hub_a + hub_b, well)
    if routing is None:
        raise RuntimeError(
            f"Hub centres {centre_a} and {centre_b} share a stim unit — "
            "re-run with a different --seed."
        )
    print(f"  Centres compatible ✓ — expanding hubs...")

    # Interleaved expansion
    while True:
        progress = False

        # ── Try to grow hub A ──────────────────────────────────────────
        if len(hub_a) < n_max:
            while ia < len(expand_a):
                cand = expand_a[ia]; ia += 1
                if cand in hub_b:
                    continue  # electrode cannot serve both hubs
                result = check_compatible(hub_a + [cand] + hub_b, well)
                if result is not None:
                    hub_a.append(cand)
                    routing  = result
                    progress = True
                    r, c     = electrode_rc(cand)
                    print(f"    Hub A [{len(hub_a)}/{n_max}]: +electrode {cand} "
                          f"(row={r}, col={c})")
                    break

        # ── Try to grow hub B ──────────────────────────────────────────
        if len(hub_b) < n_max:
            while ib < len(expand_b):
                cand = expand_b[ib]; ib += 1
                if cand in hub_a:
                    continue
                result = check_compatible(hub_a + hub_b + [cand], well)
                if result is not None:
                    hub_b.append(cand)
                    routing  = result
                    progress = True
                    r, c     = electrode_rc(cand)
                    print(f"    Hub B [{len(hub_b)}/{n_max}]: +electrode {cand} "
                          f"(row={r}, col={c})")
                    break

        if not progress:
            break  # exhausted all candidates in both pools
        if len(hub_a) >= n_max and len(hub_b) >= n_max:
            break

    if len(hub_a) < n_min:
        raise RuntimeError(
            f"Hub A: only {len(hub_a)} compatible electrodes found within radius "
            f"(need {n_min}).  Try increasing --hub-radius-um or lowering --n-per-hub."
        )
    if len(hub_b) < n_min:
        raise RuntimeError(
            f"Hub B: only {len(hub_b)} compatible electrodes found within radius "
            f"(need {n_min}).  Try increasing --hub-radius-um or lowering --n-per-hub."
        )

    print(f"\n  Hub A: {len(hub_a)} electrodes — {hub_a}")
    print(f"  Hub B: {len(hub_b)} electrodes — {hub_b}")
    return hub_a, hub_b, routing


# ── Electrode selection (recording config) ─────────────────────────────

def run_electrode_selection(all_stim_electrodes, well, impedance_dir, output_dir):
    """Call 02_select_electrodes.py with all hub electrodes as --stim-electrodes.

    Uses the impedance scan results (with rms_uv as the ranking metric) to
    determine which 1024 recording electrodes to include.  Produces
    selected_electrodes.cfg and stim_routing.json in output_dir.
    """
    # Build scan_results.npz compatible with 02_select_electrodes.py
    # by renaming rms_uv → spike_rates (used as the ranking metric)
    imp_path = os.path.join(impedance_dir, IMPEDANCE_FILE)
    imp      = np.load(imp_path)
    compat_path = os.path.join(output_dir, "_impedance_scan_compat.npz")
    np.savez(
        compat_path,
        electrodes       = imp["electrodes"],
        spike_rates      = imp["rms_uv"],    # RMS as recording-site quality proxy
        mean_amplitudes  = imp["rms_uv"],
        scan_mode        = "impedance_rms_proxy",
        detection_threshold = 0.0,
        seconds_per_block   = float(imp["scan_seconds"]),
        wells               = imp["wells"],
    )

    stim_arg = ",".join(str(e) for e in all_stim_electrodes)
    cmd = [
        MAXLAB_PYTHON,
        os.path.join(SCRIPTS_DIR, "02_select_electrodes.py"),
        "--wells",          str(well),
        "--output-dir",     output_dir,
        "--scan-results",   compat_path,
        "--stim-electrodes", stim_arg,
        "--kind",           "stim",
    ]
    print(f"\nRunning electrode selection...")
    print(f"  {' '.join(cmd)}")
    result = subprocess.run(cmd, check=True, text=True)

    # Clean up temp compat file
    try:
        os.remove(compat_path)
    except OSError:
        pass


# ── Save hub config ────────────────────────────────────────────────────

def save_hub_config(hub_a, hub_b, routing, centre_a, centre_b,
                    hub_radius_um, n_per_hub, seed, output_dir):
    def electrode_info(elec):
        r, c = electrode_rc(elec)
        return {"electrode": int(elec), "row": int(r), "col": int(c)}

    payload = {
        "hub_a": {
            "centre":     electrode_info(centre_a),
            "electrodes": [electrode_info(e) for e in hub_a],
            "n":          len(hub_a),
        },
        "hub_b": {
            "centre":     electrode_info(centre_b),
            "electrodes": [electrode_info(e) for e in hub_b],
            "n":          len(hub_b),
        },
        "all_stim_electrodes": [int(e) for e in hub_a + hub_b],
        "stim_routing":        {str(e): int(u) for e, u in routing.items()},
        "hub_radius_um":       hub_radius_um,
        "n_per_hub_target":    n_per_hub,
        "seed":                int(seed) if seed is not None else None,
        "pitch_um":            PITCH_UM,
        "note": (
            "Hub electrodes are locked for the experiment lifetime.  "
            "Verify hub_overlay.png before starting daily sessions."
        ),
    }
    path = os.path.join(output_dir, HUB_CONFIG_FILE)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\n  Hub config saved to {path}")
    return path


# ── Entry point ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Greedy switch-matrix-aware hub electrode selection"
    )
    parser.add_argument("--impedance-dir", required=True,
                        help="Directory containing impedance_results.npz "
                             "(output of 00_impedance_scan.py)")
    parser.add_argument("--output-dir", required=True,
                        help="Destination for hub_config.json, "
                             "selected_electrodes.cfg, stim_routing.json")
    parser.add_argument("--well", type=int, default=0,
                        help="Well index (default: 0 — MaxOne)")
    parser.add_argument("--hub-radius-um", type=float, default=100.0,
                        help="Radius around each hub centre in µm (default: 100)")
    parser.add_argument("--n-per-hub", type=int, default=8,
                        help="Target electrodes per hub (default: 8; min accepted: 4)")
    parser.add_argument("--footprint-percentile", type=float, default=70.0,
                        help="RMS percentile threshold for organoid footprint "
                             "(default: 70 — top 30%% define footprint)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for hub centre placement (default: random)")
    parser.add_argument("--kind", type=str, default="stim",
                        choices=manifest_mod.KIND_CHOICES)
    args = parser.parse_args()

    well         = args.well
    radius_grid  = args.hub_radius_um / PITCH_UM
    n_min        = max(4, args.n_per_hub - 2)  # accept down to n_per_hub - 2
    n_max        = args.n_per_hub
    seed         = args.seed if args.seed is not None else int(time.time()) % 10_000
    rng          = np.random.default_rng(seed)

    impedance_dir = os.path.abspath(args.impedance_dir)
    output_dir    = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    imp_path = os.path.join(impedance_dir, IMPEDANCE_FILE)
    if not os.path.exists(imp_path):
        sys.exit(f"ERROR: {imp_path} not found — run 00_impedance_scan.py first.")

    print(f"Hub electrode selection")
    print(f"  Well: {well}")
    print(f"  Radius: {args.hub_radius_um} µm ({radius_grid:.1f} grid units)")
    print(f"  Target per hub: {n_max}  (min accepted: {n_min})")
    print(f"  Seed: {seed}")

    step_started = manifest_mod.now_iso()
    step_t0      = time.perf_counter()

    try:
        # ── 1. Load impedance data ──────────────────────────────────────
        imp_data = np.load(imp_path)
        print(f"\nLoaded {len(imp_data['electrodes'])} electrode measurements")

        # ── 2. Select hub centres (PCA-split, random) ───────────────────
        print(f"\nSelecting hub centres (footprint percentile={args.footprint_percentile})...")
        centre_a, centre_b = select_hub_centres(
            imp_data, args.footprint_percentile, rng
        )

        # ── 3. Candidate lists (all grid electrodes within radius) ───────
        cands_a = electrodes_within_radius(centre_a, radius_grid)
        cands_b = electrodes_within_radius(centre_b, radius_grid)
        print(f"\n  Candidates: hub A = {len(cands_a)}, hub B = {len(cands_b)} "
              f"(within {args.hub_radius_um} µm)")

        # ── 4. Hardware init ────────────────────────────────────────────
        print(f"\nInitialising hardware (well {well})...")
        mx.activate([well])
        mx.initialize([well])
        time.sleep(mx.Timing.waitInit)
        mx.send(mx.Core().enable_stimulation_power(True))
        mx.send_raw(f"stream_set_event_threshold {DETECTION_THRESHOLD}")
        print("  Hardware ready")

        # ── 5. Greedy expansion ─────────────────────────────────────────
        print(f"\nGreedy stim-compatible expansion...")
        hub_a, hub_b, routing = greedy_expand(
            centre_a, centre_b, cands_a, cands_b,
            well, n_min, n_max
        )

        # ── 6. Definitive electrode selection (cfg + stim routing) ──────
        all_stim = hub_a + hub_b
        run_electrode_selection(all_stim, well, impedance_dir, output_dir)

        # ── 7. Save hub config ──────────────────────────────────────────
        hub_config_path = save_hub_config(
            hub_a, hub_b, routing, centre_a, centre_b,
            args.hub_radius_um, n_max, seed, output_dir
        )

    except Exception as e:
        manifest_mod.record_step_failure(
            output_dir, "hub_selection", e, kind=args.kind,
            wells_requested=[well],
            extra={"started_at": step_started,
                   "duration_s": round(time.perf_counter() - step_t0, 2)},
        )
        raise

    duration = round(time.perf_counter() - step_t0, 2)

    # ── Manifest ────────────────────────────────────────────────────────
    m = manifest_mod.load_or_init(output_dir, kind=args.kind,
                                   wells_requested=[well])
    manifest_mod.set_step(m, "hub_selection", {
        "status":            "ok",
        "started_at":        step_started,
        "duration_s":        duration,
        "seed":              seed,
        "hub_radius_um":     args.hub_radius_um,
        "n_per_hub_target":  n_max,
        "hub_a_centre":      centre_a,
        "hub_a_electrodes":  hub_a,
        "hub_b_centre":      centre_b,
        "hub_b_electrodes":  hub_b,
        "n_hub_a":           len(hub_a),
        "n_hub_b":           len(hub_b),
        "hub_config_file":   HUB_CONFIG_FILE,
    })
    manifest_mod.write_atomic(m, output_dir)

    print(f"\nDone in {duration:.0f}s")
    print(f"\nNext steps:")
    print(f"  1. conda run -n automation python \\")
    print(f"       {SCRIPTS_DIR}/plot_impedance_map.py \\")
    print(f"       --impedance-dir {impedance_dir} --output-dir {output_dir}")
    print(f"  2. Open hub_overlay.png — verify hub placement looks correct")
    print(f"  3. If good, proceed to daily stim sessions")
    print(f"     (hub sites are now locked in {hub_config_path})")


if __name__ == "__main__":
    main()
