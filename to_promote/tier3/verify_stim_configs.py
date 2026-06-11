#!/usr/bin/env python3
"""Build Config A and Config B stim configs using iterative conflict resolution.

Mirrors the approach used in stim-optimize-maxone-cortical:
  1. Route all quality-ranked candidates through the hardware in one pass.
  2. Read unit assignments in bulk.
  3. Resolve conflicts offline: for each unit claimed by >1 electrode, keep
     the highest-quality one and drop the rest.
  4. Re-route the surviving set. Repeat until no conflicts remain.

Config A uses the top-quality candidates; Config B uses the next tier with
Config A electrodes excluded.

Results are saved as stim_routing_configA.json and stim_routing_configB.json.

Run under MaxLab Python:
    /home/sharf-lab/MaxLab/python/bin/python3 verify_stim_configs.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import h5py
import numpy as np
from scipy.ndimage import gaussian_filter, maximum_filter, label
from scipy.spatial import cKDTree

EPHYS = "/home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts"
sys.path.insert(0, EPHYS)

# ── Paths ──────────────────────────────────────────────────────────────────
TASK_DIR = Path(__file__).parent
SCAN_DIR = Path("/home/sharf-lab/Data/New_Project/260512/02260/ActivityScan")
CFG_PATH = TASK_DIR / "recordings/2026-05-12/165720_screen/selected_electrodes.cfg"
OUT_DIR  = TASK_DIR / "recordings/2026-05-12/165720_screen"

# ── Array geometry ─────────────────────────────────────────────────────────
N_COLS, N_ROWS, PITCH_UM = 220, 120, 17.5

# ── Selection parameters (must match plot_organoid_boundary.py) ────────────
HEATMAP_BIN_UM = 17.5
SIGMA_UM       = 100.0
DENSITY_THRESH = 0.08
RADIUS_PCT     = 98
STIM_BUFFER_UM = 100.0
STIM_SPREAD_UM = 100.0  # min separation between ALL stim candidates — maintained throughout
NBHD_UM        = 100.0  # recording neighbourhood radius around each stim candidate
FILL_RADIUS_UM = 300.0  # max distance from a stim candidate for fill electrodes
MAX_ELECS      = 1024

TARGET_UNITS   = 32   # stim units to fill per config
CANDIDATE_N    = 32   # candidates offered to hardware per config
MAX_PASSES     = 10   # safety cap on conflict-resolution iterations

WELLS = [0]


# ── Geometry ───────────────────────────────────────────────────────────────
def _xy(e):
    return (e % N_COLS) * PITCH_UM, (e // N_COLS) * PITCH_UM

def _dist_pt(e, cx, cy):
    x, y = _xy(e)
    return ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5

def _dist(a, b):
    ax, ay = _xy(a); bx, by = _xy(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


# ── Data loaders ───────────────────────────────────────────────────────────
def load_scan(scan_dir):
    spike_counts: dict[int, int] = {}
    amp_sums:    dict[int, float] = {}
    for h5p in sorted(scan_dir.glob("*/data.raw.h5")):
        with h5py.File(str(h5p), "r") as h5:
            if "data_store" not in h5:
                continue
            for blk in sorted(k for k in h5["data_store"] if k.startswith("data")):
                mk = f"data_store/{blk}/settings/mapping"
                sk = f"data_store/{blk}/spikes"
                if mk not in h5 or sk not in h5:
                    continue
                ch2e = {int(r["channel"]): int(r["electrode"])
                        for r in np.array(h5[mk])}
                for row in np.array(h5[sk]):
                    e = ch2e.get(int(row["channel"]))
                    if e is not None:
                        spike_counts[e] = spike_counts.get(e, 0) + 1
                        amp_sums[e] = amp_sums.get(e, 0.0) + abs(float(row["amplitude"]))
    return spike_counts, amp_sums


def parse_cfg(path):
    first = path.read_text().splitlines()[0]
    return {int(m.group(1)) for entry in first.split(";")
            if (m := re.match(r"\d+\((\d+)\)", entry))}


def fit_circle(spike_counts):
    xdim = N_COLS * PITCH_UM; ydim = N_ROWS * PITCH_UM
    x_edges = np.arange(0, xdim + HEATMAP_BIN_UM, HEATMAP_BIN_UM)
    y_edges = np.arange(0, ydim + HEATMAP_BIN_UM, HEATMAP_BIN_UM)
    xs = np.array([(e % N_COLS) * PITCH_UM for e in spike_counts], dtype=float)
    ys = np.array([(e // N_COLS) * PITCH_UM for e in spike_counts], dtype=float)
    ws = np.array(list(spike_counts.values()), dtype=float)
    counts, _, _ = np.histogram2d(xs, ys, bins=[x_edges, y_edges], weights=ws)
    blurred = gaussian_filter(counts, sigma=SIGMA_UM / HEATMAP_BIN_UM)
    if blurred.max() > 0:
        blurred /= blurred.max()
    mask = blurred >= DENSITY_THRESH
    xi, yi = np.where(mask)
    bx = (x_edges[xi] + x_edges[xi + 1]) / 2
    by = (y_edges[yi] + y_edges[yi + 1]) / 2
    cx = float((bx.min() + bx.max()) / 2)
    cy = float((by.min() + by.max()) / 2)
    return cx, cy, float(np.percentile(np.sqrt((bx - cx)**2 + (by - cy)**2), RADIUS_PCT))



# ── Recording set builder ──────────────────────────────────────────────────
def build_recording_set(spike_counts, mean_amp, cfg_inside, stim_sites, cx, cy, outer_r):
    """Build ≤1024-electrode recording set optimised around stim_sites.

    Starts from boundary-filtered cfg electrodes, adds active-electrode
    neighbourhoods around each stim site (spacing-constrained), then fills
    remaining slots with any electrode inside the boundary near a stim site.
    """
    cfg_pts = np.array([_xy(e) for e in cfg_inside]) if cfg_inside else np.empty((0, 2))
    spacing = PITCH_UM * 2
    if len(cfg_pts) > 1:
        tree = cKDTree(cfg_pts)
        nn, _ = tree.query(cfg_pts, k=2)
        spacing = float(np.median(nn[:, 1]))

    recording = set(cfg_inside)
    pts = [list(p) for p in cfg_pts]
    placed: cKDTree | None = cKDTree(pts) if pts else None

    def fits(e):
        if placed is None:
            return True
        d, _ = placed.query([_xy(e)], k=1)
        return float(d[0]) >= spacing

    def add(e):
        nonlocal placed
        if len(recording) >= MAX_ELECS:
            return False
        recording.add(e)
        pts.append(list(_xy(e)))
        placed = cKDTree(pts)
        return True

    for s in stim_sites:
        for n in sorted(
            [e for e in spike_counts
             if e not in recording
             and _dist(e, s) <= NBHD_UM
             and _dist_pt(e, cx, cy) <= outer_r],
            key=lambda e: -mean_amp[e],
        ):
            if fits(n):
                if not add(n):
                    break

    if len(recording) < MAX_ELECS:
        fill = sorted(
            [e for e in range(N_COLS * N_ROWS)
             if e not in recording
             and any(_dist(e, s) <= FILL_RADIUS_UM for s in stim_sites)],
            key=lambda e: min(_dist(e, s) for s in stim_sites),
        )
        for e in fill:
            if fits(e):
                if not add(e):
                    break

    return recording


# ── Single-pass route + unit assignment read ───────────────────────────────
def route_and_read_units(arr, recording_elecs, candidates):
    """Route candidates, read all unit assignments in one pass.

    Returns {electrode: unit_id} for every candidate that got a valid unit.
    Does NOT connect any electrodes — caller decides which to keep.
    """
    arr.reset()
    arr.select_electrodes(list(recording_elecs))
    arr.select_stimulation_electrodes(list(candidates))
    arr.route()

    assignments: dict[int, int] = {}
    for e in candidates:
        arr.connect_electrode_to_stimulation(e)
        raw = arr.query_stimulation_at_electrode(e)
        arr.disconnect_electrode_from_stimulation(e)
        try:
            unit_id = int(str(raw).strip())
        except (TypeError, ValueError):
            unit_id = 0
        if unit_id > 0:
            assignments[e] = unit_id
    return assignments


# ── Iterative conflict resolution ──────────────────────────────────────────
def resolve_conflicts(assignments, quality_rank):
    """For each unit claimed by >1 electrode, keep the highest-quality one.

    quality_rank: {electrode: rank_index} — lower index = better quality.
    Returns (survivors, drops_log).
    """
    unit_to_elecs: dict[int, list[int]] = {}
    for e, u in assignments.items():
        unit_to_elecs.setdefault(u, []).append(e)

    survivors = set(assignments.keys())
    drops_log = []
    for unit, elecs in unit_to_elecs.items():
        if len(elecs) == 1:
            continue
        # Keep best-ranked, drop the rest
        best = min(elecs, key=lambda e: quality_rank.get(e, 9999))
        for e in elecs:
            if e != best:
                survivors.discard(e)
                drops_log.append({"unit": unit, "kept": best, "dropped": e})
    return survivors, drops_log


# ── Amplitude blob detection ───────────────────────────────────────────────
def _detect_amp_peaks(mean_amp, org_cx, org_cy, org_r):
    """Find local-maximum peaks in the Gaussian-blurred amplitude heatmap.

    Returns list of (x_um, y_um, normalised_value) inside the organoid boundary,
    sorted by value descending.  Only electrodes from the activity scan (mean_amp)
    contribute to the heatmap, so the peaks reflect genuine high-amplitude regions.
    """
    xdim = N_COLS * PITCH_UM; ydim = N_ROWS * PITCH_UM
    x_edges = np.arange(0, xdim + HEATMAP_BIN_UM, HEATMAP_BIN_UM)
    y_edges = np.arange(0, ydim + HEATMAP_BIN_UM, HEATMAP_BIN_UM)
    xs = np.array([(e % N_COLS) * PITCH_UM for e in mean_amp], dtype=float)
    ys = np.array([(e // N_COLS) * PITCH_UM for e in mean_amp], dtype=float)
    ws = np.array(list(mean_amp.values()), dtype=float)
    sigma_blob_um   = 87.5
    peak_neigh_bins = 6
    peak_thresh     = 0.15

    heatmap, _, _ = np.histogram2d(xs, ys, bins=[x_edges, y_edges], weights=ws)
    blurred = gaussian_filter(heatmap, sigma=sigma_blob_um / HEATMAP_BIN_UM)
    normed  = blurred / blurred.max() if blurred.max() > 0 else blurred.copy()

    local_max = maximum_filter(normed, size=2 * peak_neigh_bins + 1)
    is_peak   = (normed == local_max) & (normed >= peak_thresh)
    labeled, n = label(is_peak)

    x_centers = (x_edges[:-1] + x_edges[1:]) / 2
    y_centers  = (y_edges[:-1] + y_edges[1:]) / 2

    peaks = []
    for i in range(1, n + 1):
        xi, yi = np.where(labeled == i)
        vals   = normed[xi, yi]
        best   = int(np.argmax(vals))
        bin_px = float(x_centers[xi[best]])
        bin_py = float(y_centers[yi[best]])
        if ((bin_px - org_cx) ** 2 + (bin_py - org_cy) ** 2) ** 0.5 > org_r:
            continue
        # Snap to highest-amplitude electrode within one electrode pitch
        snap_sq = PITCH_UM ** 2
        nearby = [(e, mean_amp[e]) for e in mean_amp
                  if (_xy(e)[0] - bin_px) ** 2 + (_xy(e)[1] - bin_py) ** 2 <= snap_sq]
        if nearby:
            best_e = max(nearby, key=lambda t: t[1])[0]
            px, py = _xy(best_e)
        else:
            px, py = bin_px, bin_py
        peaks.append((px, py, float(vals[best])))

    peaks.sort(key=lambda p: -p[2])
    return peaks


# ── Main config builder ────────────────────────────────────────────────────
def build_config(label, arr, candidates, quality_rank, recording_elecs):
    """Iteratively route + resolve until conflict-free or pass limit reached."""
    print(f"\n{'='*60}")
    print(f"  Config {label}: {len(candidates)} candidates, target {TARGET_UNITS} units")
    print(f"{'='*60}")

    proposal = list(candidates)
    history = []
    all_drops: list[dict] = []

    for pass_n in range(1, MAX_PASSES + 1):
        print(f"  Pass {pass_n}: routing {len(proposal)} candidates...", flush=True)
        assignments = route_and_read_units(arr, recording_elecs, proposal)

        n_units = len(set(assignments.values()))
        not_stimmable = [e for e in proposal if e not in assignments]
        conflicts_by_unit = {u: [e for e, uu in assignments.items() if uu == u]
                             for u in set(assignments.values())
                             if sum(1 for uu in assignments.values() if uu == u) > 1}
        n_conflicts = len(conflicts_by_unit)

        print(f"         → {len(assignments)} assigned, {n_units} unique units, "
              f"{len(not_stimmable)} not stimmable, {n_conflicts} unit conflicts")

        history.append({
            "pass": pass_n,
            "n_proposed": len(proposal),
            "n_assigned": len(assignments),
            "n_unique_units": n_units,
            "n_conflicts": n_conflicts,
            "compatible": n_conflicts == 0,
            "stim_unit_assignments": {str(e): u for e, u in assignments.items()},
        })

        if n_conflicts == 0:
            print(f"  Compatible — no conflicts.")
            break

        survivors, drops = resolve_conflicts(assignments, quality_rank)
        all_drops.extend([{**d, "pass": pass_n} for d in drops])
        for d in drops:
            print(f"    unit {d['unit']:>2}: kept {d['kept']}, dropped {d['dropped']}")

        # Drop non-stimmable electrodes too
        for e in not_stimmable:
            survivors.discard(e)

        proposal = [e for e in proposal if e in survivors]

        if not proposal:
            print(f"  All candidates exhausted after conflict resolution.")
            break
    else:
        print(f"  Warning: reached pass limit ({MAX_PASSES}) — returning best result")

    # Final routing with survivors — connect keepers
    final_assignments = route_and_read_units(arr, recording_elecs, proposal)
    # Sanity check: should be conflict-free now
    final_routing: dict[int, int] = {}
    unit_seen: set[int] = set()
    for e in sorted(final_assignments, key=lambda e: quality_rank.get(e, 9999)):
        u = final_assignments[e]
        if u not in unit_seen:
            final_routing[e] = u
            unit_seen.add(u)

    for e in final_routing:
        arr.connect_electrode_to_stimulation(e)

    print(f"\n  Config {label}: {len(final_routing)} stim units wired")
    for e, u in sorted(final_routing.items(), key=lambda kv: kv[1]):
        x, y = _xy(e)
        print(f"    unit {u:>2}  elec {e:>6}  ({x:.0f}, {y:.0f}) µm")

    return final_routing, {"passes": pass_n, "history": history, "drops_log": all_drops}


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    import maxlab as mx
    from experiment_lib import prepare_hardware

    print("Loading scan data...")
    spike_counts, amp_sums = load_scan(SCAN_DIR)
    mean_amp = {e: amp_sums[e] / max(spike_counts[e], 1) for e in spike_counts}
    print(f"  Active electrodes: {len(spike_counts)}")

    cx, cy, outer_r = fit_circle(spike_counts)
    buffer_r = outer_r - STIM_BUFFER_UM
    print(f"  Boundary: cx={cx:.0f} cy={cy:.0f} r={outer_r:.0f} µm")

    # Load blacklist of previously confirmed not-stimmable electrodes
    blacklist_path = OUT_DIR / "not_stimmable.json"
    blacklist: set[int] = set()
    if blacklist_path.exists():
        blacklist = set(json.loads(blacklist_path.read_text()))
        print(f"  Blacklist: {len(blacklist)} previously not-stimmable electrodes excluded")

    # Amplitude-ranked greedy selection with 100 µm spread throughout.
    all_inside_sorted = sorted(
        [e for e in spike_counts if _dist_pt(e, cx, cy) <= buffer_r],
        key=lambda e: -mean_amp[e],
    )
    full_pool: list[int] = []
    for e in all_inside_sorted:
        if e not in blacklist and all(_dist(e, f) >= STIM_SPREAD_UM for f in full_pool):
            full_pool.append(e)
    quality_rank = {e: i for i, e in enumerate(full_pool)}
    print(f"  Candidate pool ({STIM_SPREAD_UM:.0f} µm spread): {len(full_pool)} electrodes")

    # Build a boundary-only recording set.  Start from cfg electrodes inside the
    # boundary; use the union of (current verified stim sites + full spread pool)
    # as fill centres so routing paths for both existing and new candidates are
    # well-supported.
    cfg_elecs   = parse_cfg(CFG_PATH)
    cfg_inside  = {e for e in cfg_elecs if _dist_pt(e, cx, cy) <= outer_r}
    cfg_outside = cfg_elecs - cfg_inside
    print(f"  Cfg inside boundary: {len(cfg_inside)}  outside (to replace): {len(cfg_outside)}")

    # Fill centres = amplitude-ranked spread pool only.
    # Stale stim JSONs from a different ranking run would pull the recording set
    # toward the wrong positions, so we ignore them here.
    fill_centres: list[int] = list(full_pool)
    print(f"  Fill centres: {len(fill_centres)} amplitude-ranked candidates")
    print(f"  Recording fill radius: {FILL_RADIUS_UM:.0f} µm circle around each site")

    recording_elecs = build_recording_set(spike_counts, mean_amp, cfg_inside,
                                          fill_centres, cx, cy, outer_r)
    print(f"  New recording set: {len(recording_elecs)} / {MAX_ELECS}  "
          f"(stim neighbourhood inside boundary; fill extends outside)")

    # Hardware init
    prepare_hardware(WELLS)
    mx.send(mx.Core().enable_stimulation_power(True))
    arr = mx.Array("online")

    if cfg_outside or (recording_elecs != set(cfg_inside)):
        print("\nDownloading new recording config...")
        arr.reset()
        arr.select_electrodes(list(recording_elecs))
        arr.select_stimulation_electrodes(list(full_pool))
        arr.route()
        arr.download(WELLS)
        time.sleep(mx.Timing.waitAfterDownload)
        mx.offset()
        arr.save_config(str(CFG_PATH))
        print(f"  Saved: {CFG_PATH.name}")
    else:
        print("  Recording config unchanged — skipping download.")

    # ── Global probe: route all candidates at once, resolve conflicts by amplitude
    # This surfaces every achievable high-amp site before splitting into A/B,
    # so we never waste Config A slots on candidates that can't route.
    print("\nProbing all candidates with current recording config...")
    all_assignments = route_and_read_units(arr, recording_elecs, full_pool)
    not_stimmable = [e for e in full_pool if e not in all_assignments]
    print(f"  {len(all_assignments)} stimmable, {len(not_stimmable)} not stimmable")
    if not_stimmable:
        print(f"  Not stimmable: {not_stimmable}")

    # Accumulate blacklist and persist so future runs skip these electrodes
    blacklist |= set(not_stimmable)
    blacklist_path.write_text(json.dumps(sorted(blacklist)))
    print(f"  Blacklist updated: {len(blacklist)} total not-stimmable electrodes saved")

    all_survivors, global_drops = resolve_conflicts(all_assignments, quality_rank)
    for d in global_drops:
        kept_amp    = mean_amp.get(d["kept"], 0)
        dropped_amp = mean_amp.get(d["dropped"], 0)
        print(f"    unit {d['unit']:>2}: kept {d['kept']} (amp={kept_amp:.1f}), "
              f"dropped {d['dropped']} (amp={dropped_amp:.1f})")
    print(f"  Conflict-free achievable set: {len(all_survivors)} sites")

    # Config A: top TARGET_UNITS of the conflict-free set, by amplitude
    achievable_by_amp = sorted(all_survivors, key=lambda e: -mean_amp[e])
    pool_a = achievable_by_amp[:TARGET_UNITS]

    # Config B: dropped candidates (lost a unit conflict to a Config A site).
    # In a fresh routing call they may land on different units.
    dropped_elecs = {d["dropped"] for d in global_drops}
    pool_b = sorted(dropped_elecs, key=lambda e: -mean_amp[e])

    print(f"  Config A pool: {len(pool_a)} (top amplitude, conflict-free)")
    print(f"  Config B pool: {len(pool_b)} (dropped candidates — may resolve differently)")

    # ── Config A ─────────────────────────────────────────────────────────────
    routing_a, meta_a = build_config("A", arr, pool_a, quality_rank, recording_elecs)

    # ── Config B ─────────────────────────────────────────────────────────────
    routing_b, meta_b = build_config("B", arr, pool_b, quality_rank, recording_elecs)

    # ── Save ────────────────────────────────────────────────────────────────
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for label, routing, meta in [("A", routing_a, meta_a), ("B", routing_b, meta_b)]:
        routing_out = OUT_DIR / f"stim_routing_config{label}.json"
        routing_out.write_text(json.dumps({
            "config": label,
            "wells": WELLS,
            "stim_electrodes": sorted(routing.keys()),
            "routing": {str(e): int(u) for e, u in routing.items()},
        }, indent=2))
        compat_out = OUT_DIR / f"stim_compat_config{label}.json"
        compat_out.write_text(json.dumps({
            "config": label, "compatible": True,
            "n_unique_stim_units": len(routing),
            **meta,
        }, indent=2))
        print(f"  Saved: {routing_out.name}  {compat_out.name}")

    print(f"\n{'='*60}")
    print(f"  DONE  Config A: {len(routing_a)} units  Config B: {len(routing_b)} units")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
