#!/usr/bin/env python3
"""
maximize_stim_electrodes.py — Select up to 32 stimulation-compatible electrodes
from an activity scan, maximising stim-unit coverage and activity-based quality.

The MaxOne / MaxTwo chip has exactly 32 stimulation buffer units. Two electrodes
that share a stim unit cannot be wired simultaneously. This script finds the
best electrode for each available stim unit, targeting a full set of 32 in a
single pass.

Algorithm:
  1. Load scan_results.npz (spike rates + amplitudes for scanned electrodes).
  2. Score active electrodes: score = sqrt(norm_amplitude × log1p(norm_rate)).
     Geometric mean rewards electrodes with both strong signals and regular activity.
  3. Greedy spatial spread filter (default 100 µm): build a candidate pool of
     ~pool_factor × max_stim electrodes, starting from highest-scored.  Large
     pool ensures each of the 32 stim units is likely represented multiple times
     so we can choose the best candidate per unit.
  4. Probe stim-unit assignments by routing the entire candidate pool and querying
     connect_electrode_to_stimulation / query_stimulation_at_electrode (same
     mechanism as check_stim_compatibility.py).
  5. Greedy stim-unit selection: one electrode per stim unit, chosen by score.
     Rank stim units by their best candidate's score; take top-max_stim units.
  6. Retrospective spacing check: for any pair of selected electrodes closer than
     min_spacing_um, try to swap the lower-scored one for an alternative candidate
     in the same stim unit that satisfies the spacing constraint.  If no viable
     swap exists, keep both (stim-unit coverage takes priority over strict spacing
     at low electrode counts, and with only 32 electrodes across the full array
     violations are rare).
  7. Write stim_pool.json and update manifest.json.

Output (stim_pool.json):
  {
    "well": 0,
    "n_selected": 32,
    "stim_units_covered": [1, 2, ..., 32],
    "electrodes": [
      {"electrode_id": 5280, "stim_unit": 5, "score": 0.91,
       "row": 24, "col": 0, "x_um": 0.0, "y_um": 420.0},
      ...
    ]
  }

Typical usage (followed by 02_select_electrodes.py to create the .cfg):

    /home/sharf-lab/MaxLab/python/bin/python3 \\
        /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/maximize_stim_electrodes.py \\
        --scan-results orchestrator/<plan_id>/scan/scan_results.npz \\
        --well 0 \\
        --output-dir orchestrator/<plan_id>/recordings/<date>/<HHMMSS_maxstim_w0>

    # Then extract electrode IDs from stim_pool.json and pass to 02_select_electrodes.py:
    /home/sharf-lab/MaxLab/python/bin/python3 \\
        /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/02_select_electrodes.py \\
        --by amplitude --regions 40 --wells 0 \\
        --stim-electrodes <comma-separated IDs from stim_pool.json> \\
        --kind stim \\
        --output-dir orchestrator/<plan_id>/recordings/<date>/<HHMMSS_route_w0>

Requires: maxlab (only available via /home/sharf-lab/MaxLab/python/bin/python3).
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import maxlab as mx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manifest as manifest_mod

# ── Grid constants (MaxOne / MaxTwo — same array geometry) ─────────────
NUM_ROWS        = 120
NUM_COLS        = 220
TOTAL_ELECTRODES = NUM_ROWS * NUM_COLS
PITCH_UM        = 17.5        # µm per electrode pitch (row and column directions)
MAX_STIM_UNITS  = 32          # hardware cap: both MaxOne and MaxTwo have 32 stim buffers
DETECTION_THRESHOLD = 5.0


# ── Spatial helpers ────────────────────────────────────────────────────

def electrode_position(electrode_id):
    """Return (row, col) from a flat electrode index."""
    return divmod(int(electrode_id), NUM_COLS)


def distance_um(elec_a, elec_b):
    """Euclidean distance in µm between two electrode IDs."""
    ra, ca = electrode_position(elec_a)
    rb, cb = electrode_position(elec_b)
    return float(np.sqrt(((ra - rb) * PITCH_UM) ** 2 + ((ca - cb) * PITCH_UM) ** 2))


# ── Scoring ────────────────────────────────────────────────────────────

def compute_scores(electrodes, spike_rates, mean_amplitudes):
    """
    Composite activity score for each electrode.
    Returns a float array aligned with `electrodes`.

    Formula: sqrt(amp_norm × log1p_rate_norm)
      - amp_norm:        mean_amplitudes / max(active amplitudes)
      - log1p_rate_norm: log1p(spike_rates) / max(log1p(active rates))

    Geometric mean: both amplitude and firing regularity must be non-trivial
    for a high score.  Inactive electrodes (rate == 0) score 0.
    """
    active = spike_rates > 0
    if not np.any(active):
        return np.zeros(len(electrodes), dtype=np.float64)

    amp = mean_amplitudes.astype(np.float64)
    rate = spike_rates.astype(np.float64)

    max_amp = float(np.max(amp[active]))
    amp_norm = amp / max_amp if max_amp > 0 else np.zeros_like(amp)

    rate_log = np.log1p(rate)
    max_rate_log = float(np.max(rate_log[active]))
    rate_norm = rate_log / max_rate_log if max_rate_log > 0 else np.zeros_like(rate_log)

    return np.sqrt(amp_norm * rate_norm)


# ── Greedy spatial spread filter ───────────────────────────────────────

def greedy_spread(electrodes, scores, min_spacing_um, target_count):
    """
    Select up to `target_count` electrodes in score-descending order,
    skipping any electrode within `min_spacing_um` of an already-selected one.

    Returns a list of electrode IDs (in selection order, highest-scored first).
    """
    order = np.argsort(-scores)
    selected = []
    sel_pos_um = []  # (y_um, x_um) of selected electrodes for fast distance check

    for idx in order:
        elec = int(electrodes[idx])
        if scores[idx] <= 0.0:
            break  # remaining electrodes are inactive
        r, c = electrode_position(elec)
        y_um = r * PITCH_UM
        x_um = c * PITCH_UM
        too_close = any(
            np.sqrt((y_um - sy) ** 2 + (x_um - sx) ** 2) < min_spacing_um
            for sy, sx in sel_pos_um
        )
        if not too_close:
            selected.append(elec)
            sel_pos_um.append((y_um, x_um))
        if len(selected) >= target_count:
            break

    return selected


# ── Stim-unit probe ────────────────────────────────────────────────────

def probe_stim_units(candidates, well):
    """
    Route `candidates` on `well` and query each electrode's stim-unit assignment.

    With more than 32 candidates the hardware assigns multiple electrodes to the
    same stim unit — this is expected behaviour and how we discover which unit
    each electrode maps to.  (Same mechanism as check_stim_compatibility.py.)

    Returns:
        assignments: dict {electrode_id (int): stim_unit_id (int)}
            stim_unit_id ≤ 0 means the hardware could not assign a stim unit
            to that electrode (routing failed for that specific electrode).
    """
    n = len(candidates)
    print(f"  Probing stim-unit assignments: well={well}, n_candidates={n}")

    mx.activate([well])
    mx.initialize([well])
    time.sleep(mx.Timing.waitInit)
    mx.send(mx.Core().enable_stimulation_power(True))
    mx.send_raw(f"stream_set_event_threshold {DETECTION_THRESHOLD}")

    arr = mx.Array("online")
    arr.reset()
    arr.select_electrodes(list(candidates))
    arr.select_stimulation_electrodes(list(candidates))
    arr.route()

    assignments = {}
    failed = []
    for elec in candidates:
        arr.connect_electrode_to_stimulation(elec)
        raw = arr.query_stimulation_at_electrode(elec)
        try:
            unit_id = int(str(raw).strip())
        except (TypeError, ValueError):
            unit_id = 0
        assignments[elec] = unit_id
        if unit_id <= 0:
            failed.append(elec)

    n_valid   = sum(1 for u in assignments.values() if u > 0)
    n_units   = len({u for u in assignments.values() if u > 0})
    n_invalid = len(failed)
    print(f"  Probe complete: {n_valid}/{n} valid assignments, "
          f"{n_units} unique stim units found"
          + (f", {n_invalid} electrodes returned unit_id ≤ 0 (skipped)" if n_invalid else ""))
    return assignments


# ── Best-per-unit selection ────────────────────────────────────────────

def select_best_per_unit(assignments, scores_map, max_stim):
    """
    Given stim_unit_assignments and a score per electrode, pick the
    highest-scored electrode per stim unit, then take the top-max_stim
    units ranked by their best-candidate score.

    Returns: list of (electrode_id, stim_unit_id, score) sorted by score desc.
    """
    by_unit = {}  # unit_id → [(score, electrode_id), ...]
    for elec, unit in assignments.items():
        if unit <= 0:
            continue
        s = scores_map.get(int(elec), 0.0)
        by_unit.setdefault(unit, []).append((s, int(elec)))

    # Sort each unit's candidates by score desc
    for unit in by_unit:
        by_unit[unit].sort(reverse=True)

    # Best candidate per unit → ranked list of (best_score, electrode_id, unit_id)
    ranked = sorted(
        [(members[0][0], members[0][1], unit) for unit, members in by_unit.items()],
        reverse=True,
    )

    top = ranked[:max_stim]
    return [(elec, unit, sc) for sc, elec, unit in top]


# ── Retrospective spacing check ────────────────────────────────────────

def fix_spacing(selected, unit_candidates, scores_map, min_spacing_um):
    """
    For pairs of selected electrodes closer than min_spacing_um, attempt to swap
    the lower-scored one for an alternative in the same stim unit that satisfies
    the spacing constraint against all other currently-selected electrodes.

    `unit_candidates`: {unit_id: [(score, electrode_id), ...]} — all probe
    candidates per unit, sorted by score desc.

    Returns updated selected list (same format).
    Runs at most a few passes to resolve cascading swaps.
    """
    sel = {unit: (elec, sc) for elec, unit, sc in selected}

    for _pass in range(5):
        pairs = list(sel.items())
        changed = False
        for i in range(len(pairs)):
            unit_i, (elec_i, sc_i) = pairs[i]
            for j in range(i + 1, len(pairs)):
                unit_j, (elec_j, sc_j) = pairs[j]
                if distance_um(elec_i, elec_j) >= min_spacing_um:
                    continue
                # Swap the lower-scored electrode's unit to a different candidate
                target_unit = unit_j if sc_i >= sc_j else unit_i
                keep_elec   = elec_i if sc_i >= sc_j else elec_j
                other_elecs = {e for u, (e, s) in sel.items() if u != target_unit}

                found = None
                for alt_sc, alt_elec in unit_candidates.get(target_unit, []):
                    if alt_elec == sel[target_unit][0]:
                        continue  # current electrode — already failed
                    if all(distance_um(alt_elec, oe) >= min_spacing_um for oe in other_elecs) \
                            and distance_um(alt_elec, keep_elec) >= min_spacing_um:
                        found = (alt_elec, alt_sc)
                        break

                if found:
                    old_elec = sel[target_unit][0]
                    sel[target_unit] = found
                    print(f"  Spacing swap: unit {target_unit}: "
                          f"elec {old_elec} → {found[0]} "
                          f"({distance_um(elec_i, elec_j):.0f} µm conflict resolved)")
                    changed = True
                    break
            if changed:
                break
        if not changed:
            break

    result = [(elec, unit, sc) for unit, (elec, sc) in sel.items()]
    result.sort(key=lambda x: x[2], reverse=True)
    return result


# ── Print summary ─────────────────────────────────────────────────────

def print_summary(selected, min_spacing_um, amp_map, rate_map):
    sel_ids = [e for e, u, s in selected]
    violations = [
        (sel_ids[i], sel_ids[j], distance_um(sel_ids[i], sel_ids[j]))
        for i in range(len(sel_ids))
        for j in range(i + 1, len(sel_ids))
        if distance_um(sel_ids[i], sel_ids[j]) < min_spacing_um
    ]

    print(f"\n{'=' * 60}")
    print(f"  MAXIMIZE STIM ELECTRODES — SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Stim electrodes selected:  {len(selected)} / {MAX_STIM_UNITS} max")
    print(f"  Stim units covered:        {len(set(u for _, u, _ in selected))} / {MAX_STIM_UNITS}")
    print(f"  Spacing violations (<{min_spacing_um:.0f} µm): {len(violations)}")
    if violations:
        for a, b, d in violations[:3]:
            print(f"    elec {a} ↔ elec {b}: {d:.1f} µm")
        if len(violations) > 3:
            print(f"    … and {len(violations) - 3} more")
    print()
    print("  Top 10 selected electrodes (by score):")
    for i, (elec, unit, sc) in enumerate(selected[:10]):
        r, c = electrode_position(elec)
        amp  = amp_map.get(elec, 0.0)
        rate = rate_map.get(elec, 0.0)
        print(f"    #{i+1:2d}: elec {elec:5d}  row={r:3d} col={c:3d}  "
              f"stim_unit={unit:2d}  score={sc:.3f}  "
              f"amp={amp:.1f}µV  rate={rate:.3f}Hz")
    if len(selected) > 10:
        print(f"    … and {len(selected) - 10} more (see stim_pool.json)")
    print()


# ── Entry point ────────────────────────────────────────────────────────

def _parse_well(s):
    w = int(s)
    if not 0 <= w <= 5:
        raise argparse.ArgumentTypeError(f"well {w} out of range 0–5")
    return w


def main():
    parser = argparse.ArgumentParser(
        description="Maximise stim electrode count from an activity scan (≤ 32 stim units).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--scan-results", required=True,
                        help="Path to scan_results.npz from 01_activity_scan.py.")
    parser.add_argument("--well", type=_parse_well, required=True,
                        help="Well number for the stim-unit routing probe (0–5).")
    parser.add_argument("--output-dir", required=True,
                        help="Directory for stim_pool.json and manifest.json.")
    parser.add_argument("--max-stim", type=int, default=MAX_STIM_UNITS,
                        help=f"Maximum stim electrodes to select (default: {MAX_STIM_UNITS}).")
    parser.add_argument("--min-spacing-um", type=float, default=100.0,
                        help="Minimum µm between selected stim electrodes (default: 100).")
    parser.add_argument("--pool-factor", type=int, default=3,
                        help="Candidate pool = pool_factor × max_stim (default: 3 → 96 candidates). "
                             "Larger values improve stim-unit coverage at the cost of a slower probe.")
    parser.add_argument("--kind", default=None, choices=manifest_mod.KIND_CHOICES,
                        help="Experiment kind for manifest.json.")
    args = parser.parse_args()

    step_started_at = manifest_mod.now_iso()
    step_t0 = time.perf_counter()

    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    scan_path  = os.path.abspath(args.scan_results)

    if not os.path.exists(scan_path):
        print(f"ERROR: scan results not found: {scan_path}", file=sys.stderr)
        sys.exit(1)

    # ── 1. Load scan ───────────────────────────────────────────────────
    print(f"Loading scan results from {scan_path}")
    data          = np.load(scan_path)
    electrodes    = data["electrodes"]          # shape (N,) int
    spike_rates   = data["spike_rates"]         # shape (N,) float Hz
    mean_amps     = data["mean_amplitudes"]     # shape (N,) float µV
    wells_in_scan = data["wells"].tolist() if "wells" in data.files else [args.well]

    n_scanned = len(electrodes)
    n_active  = int(np.sum(spike_rates > 0))
    print(f"  Electrodes scanned: {n_scanned},  active (rate > 0): {n_active}")

    if n_active == 0:
        err = RuntimeError("no active electrodes — cannot build stim pool")
        manifest_mod.record_step_failure(
            output_dir, "maximize_stim", err, kind=args.kind,
            wells_requested=[args.well],
            extra={"started_at": step_started_at, "n_scanned": n_scanned},
        )
        raise err

    # Build per-electrode lookup dictionaries for fast access
    amp_map  = {int(electrodes[i]): float(mean_amps[i])  for i in range(n_scanned)}
    rate_map = {int(electrodes[i]): float(spike_rates[i]) for i in range(n_scanned)}

    # ── 2. Score electrodes ────────────────────────────────────────────
    print("Scoring active electrodes...")
    scores     = compute_scores(electrodes, spike_rates, mean_amps)
    scores_map = {int(electrodes[i]): float(scores[i]) for i in range(n_scanned)}

    # ── 3. Greedy spatial spread → candidate pool ──────────────────────
    pool_target = args.pool_factor * args.max_stim
    print(f"Building candidate pool (target {pool_target} electrodes, "
          f"min spacing {args.min_spacing_um} µm)...")
    pool = greedy_spread(electrodes, scores, args.min_spacing_um, pool_target)
    n_pool = len(pool)
    print(f"  Candidate pool: {n_pool} electrodes")

    if n_pool < args.max_stim:
        print(f"  WARNING: pool ({n_pool}) < max_stim ({args.max_stim}). "
              f"Consider reducing --min-spacing-um or using a denser scan mode "
              f"(checkerboard or full).")

    # ── 4. Probe stim-unit assignments ─────────────────────────────────
    try:
        assignments = probe_stim_units(pool, args.well)
    except Exception as e:
        manifest_mod.record_step_failure(
            output_dir, "maximize_stim", e, kind=args.kind,
            wells_requested=[args.well],
            extra={"started_at": step_started_at,
                   "duration_s": round(time.perf_counter() - step_t0, 2),
                   "n_pool": n_pool},
        )
        raise

    # Build per-unit candidate groups for retrospective swap step
    unit_candidates = {}   # {unit_id: [(score, elec), ...] sorted desc}
    for elec in pool:
        unit = assignments.get(elec, 0)
        if unit <= 0:
            continue
        sc = scores_map.get(elec, 0.0)
        unit_candidates.setdefault(unit, []).append((sc, elec))
    for uid in unit_candidates:
        unit_candidates[uid].sort(reverse=True)

    n_units_available = len(unit_candidates)

    # ── 5. Best electrode per stim unit ───────────────────────────────
    print(f"Selecting up to {args.max_stim} stim electrodes "
          f"({n_units_available} stim units available in pool)...")
    selected = select_best_per_unit(assignments, scores_map, args.max_stim)
    print(f"  After unit selection: {len(selected)} electrodes across "
          f"{len(set(u for _, u, _ in selected))} stim units")

    # ── 6. Retrospective spacing check ────────────────────────────────
    print("Running retrospective spacing check...")
    selected = fix_spacing(selected, unit_candidates, scores_map, args.min_spacing_um)

    # ── 7. Report & write outputs ──────────────────────────────────────
    print_summary(selected, args.min_spacing_um, amp_map, rate_map)

    n_selected = len(selected)
    covered_units = sorted({u for _, u, _ in selected})
    sel_ids = [e for e, u, s in selected]

    spacing_violations = [
        (sel_ids[i], sel_ids[j], round(distance_um(sel_ids[i], sel_ids[j]), 1))
        for i in range(len(sel_ids))
        for j in range(i + 1, len(sel_ids))
        if distance_um(sel_ids[i], sel_ids[j]) < args.min_spacing_um
    ]

    duration_s = round(time.perf_counter() - step_t0, 2)

    # stim_pool.json
    stim_pool = {
        "well":                  args.well,
        "max_stim_requested":    args.max_stim,
        "min_spacing_um":        args.min_spacing_um,
        "pool_size":             n_pool,
        "stim_units_available":  n_units_available,
        "n_selected":            n_selected,
        "stim_units_covered":    covered_units,
        "n_spacing_violations":  len(spacing_violations),
        "spacing_violations":    [
            {"elec_a": a, "elec_b": b, "dist_um": d}
            for a, b, d in spacing_violations
        ],
        "electrodes": [
            {
                "electrode_id": elec,
                "stim_unit":    unit,
                "score":        round(sc, 4),
                "amplitude_uv": round(amp_map.get(elec, 0.0), 2),
                "rate_hz":      round(rate_map.get(elec, 0.0), 4),
                "row":          electrode_position(elec)[0],
                "col":          electrode_position(elec)[1],
                "x_um":         round(electrode_position(elec)[1] * PITCH_UM, 1),
                "y_um":         round(electrode_position(elec)[0] * PITCH_UM, 1),
            }
            for elec, unit, sc in selected
        ],
    }

    pool_path = os.path.join(output_dir, "stim_pool.json")
    with open(pool_path, "w") as f:
        json.dump(stim_pool, f, indent=2)
    print(f"  stim_pool.json → {pool_path}")

    # manifest.json
    m = manifest_mod.load_or_init(output_dir, kind=args.kind, wells_requested=wells_in_scan)
    manifest_mod.set_step(m, "maximize_stim", {
        "status":               "ok",
        "started_at":           step_started_at,
        "duration_s":           duration_s,
        "well":                 args.well,
        "scan_path":            scan_path,
        "n_active_electrodes":  n_active,
        "pool_size":            n_pool,
        "stim_units_available": n_units_available,
        "n_selected":           n_selected,
        "stim_units_covered":   covered_units,
        "n_spacing_violations": len(spacing_violations),
        "output_file":          "stim_pool.json",
        "output_bytes":         os.path.getsize(pool_path),
    })
    manifest_mod.write_atomic(m, output_dir)
    print(f"  manifest.json  → {manifest_mod.manifest_path(output_dir)}")
    print("Done.")


if __name__ == "__main__":
    main()
