#!/usr/bin/env python3
"""Build the stim candidate pool from an activity scan.

Reads `scan_results.npz` (from 01_activity_scan.py) and emits a
`candidate_pool.json` with two pools:

  - strict_pool: top-10 by FR ∪ top-10 by amp, interleaved + dedupe,
    greedy spread filter (target 100 µm, relax to 50 µm if pool < 12),
    capped at 20. The "obvious" candidates.

  - overprovisioning_pool: up to 32 electrodes (fills all MaxOne stim-unit
    slots). Draws from top-60 by FR ∪ amp union (120 unique candidates)
    with a fixed 100 µm greedy spread filter, capped at 32. The wider
    topk (vs the old 30) gives the spread filter enough spatially
    distributed candidates to reliably fill all 32 stim-unit slots without
    relaxing the 100 µm minimum distance. Submitted to
    02_select_electrodes.py --stim-electrodes.

Geometry: MaxOne, 120 rows × 220 cols, 17.5 µm pitch.

Usage:
    python build_candidate_pool.py \
        --scan scan/scan_results.npz \
        --out scan/candidate_pool.json \
        [--strict-pool-size 12] [--overprov-size 32] [--topk 60]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

# MaxOne grid
N_ROWS = 120
N_COLS = 220
PITCH_UM = 17.5
TOTAL_ELECTRODES = N_ROWS * N_COLS


def elec_xy_um(eid: int) -> tuple[float, float]:
    return ((eid % N_COLS) * PITCH_UM, (eid // N_COLS) * PITCH_UM)


def elec_distance_um(e1: int, e2: int) -> float:
    x1, y1 = elec_xy_um(int(e1))
    x2, y2 = elec_xy_um(int(e2))
    return float(((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5)


def interleave_unique(a: list[int], b: list[int]) -> list[int]:
    """Interleave two ranked lists, returning unique elements in order."""
    out: list[int] = []
    seen: set[int] = set()
    for x, y in zip(a, b):
        for v in (x, y):
            if v not in seen:
                out.append(int(v))
                seen.add(v)
    for tail in (a, b):
        for v in tail[len(out):]:
            if v not in seen:
                out.append(int(v))
                seen.add(v)
    return out


def greedy_spread_filter(ranked: list[int], spread_um: float, cap: int) -> list[int]:
    """Walk ranked list; keep each candidate only if it's ≥ spread_um from
    every already-kept electrode. Stops early at cap."""
    kept: list[int] = []
    for e in ranked:
        if len(kept) >= cap:
            break
        if all(elec_distance_um(e, k) >= spread_um for k in kept):
            kept.append(int(e))
    return kept


def greedy_spread_nocap(ranked: list[int], spread_um: float) -> list[int]:
    """Greedy spread filter with no size cap — collect all passing candidates.
    Cap is applied afterward by the caller."""
    kept: list[int] = []
    for e in ranked:
        if all(elec_distance_um(e, k) >= spread_um for k in kept):
            kept.append(int(e))
    return kept


def build_strict_pool(
    top_rate: list[int],
    top_amp: list[int],
    min_size: int = 12,
    cap: int = 20,
    spread_um_primary: float = 100.0,
    spread_um_fallback: float = 50.0,
) -> tuple[list[int], float]:
    """Strict pool: top-10 by each metric ∪, interleave, spread filter."""
    union = interleave_unique(top_rate, top_amp)
    pool = greedy_spread_filter(union, spread_um_primary, cap)
    spread_used = spread_um_primary
    if len(pool) < min_size:
        pool = greedy_spread_filter(union, spread_um_fallback, cap)
        spread_used = spread_um_fallback
    return pool, spread_used


def build_overprovisioning_pool(
    fr_ranked: list[int],
    amp_ranked: list[int],
    topk: int = 60,
    pool_cap: int = 32,
    spread_um: float = 100.0,
) -> tuple[list[int], float]:
    """Over-provisioning pool targeting pool_cap electrodes (hardware max).

    Fixed 100 µm spread — no relaxation. topk=60 gives the greedy filter
    a wide enough candidate set to fill all 32 stim-unit slots even on
    cultures with clustered activity.
    """
    union = interleave_unique(fr_ranked[:topk], amp_ranked[:topk])
    pool = greedy_spread_nocap(union, spread_um)
    return pool[:pool_cap], spread_um


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build candidate stim pool from activity scan")
    ap.add_argument("--scan", required=True,
                    help="Path to scan_results.npz produced by 01_activity_scan.py")
    ap.add_argument("--out", required=True,
                    help="Output path for candidate_pool.json")
    ap.add_argument("--strict-pool-size", type=int, default=12,
                    help="Target minimum size of the strict pool (default 12)")
    ap.add_argument("--strict-pool-cap", type=int, default=20,
                    help="Maximum strict pool size (default 20)")
    ap.add_argument("--overprov-size", type=int, default=32,
                    help="Over-provisioning pool cap (default 32 = hardware max)")
    ap.add_argument("--topk", type=int, default=60,
                    help="Top-K per metric for the over-provisioning union (default 60)")
    ap.add_argument("--top-per-metric", type=int, default=10,
                    help="Top-K per metric for the strict pool union (default 10)")
    args = ap.parse_args()

    scan_path = Path(args.scan).resolve()
    if not scan_path.exists():
        raise FileNotFoundError(f"scan results not found at {scan_path}")

    data = np.load(scan_path)
    electrodes = data["electrodes"].astype(np.int64)
    spike_rates = data["spike_rates"].astype(np.float64)
    mean_amplitudes = data["mean_amplitudes"].astype(np.float64)

    if not (len(electrodes) == len(spike_rates) == len(mean_amplitudes)):
        raise ValueError(
            f"Length mismatch in scan results: electrodes={len(electrodes)}, "
            f"spike_rates={len(spike_rates)}, mean_amplitudes={len(mean_amplitudes)}"
        )

    n_active = int(np.sum(spike_rates > 0))

    rate_order = np.argsort(-spike_rates)
    amp_order = np.argsort(-mean_amplitudes)
    fr_ranked_full = [int(e) for e in electrodes[rate_order].tolist()]
    amp_ranked_full = [int(e) for e in electrodes[amp_order].tolist()]

    # Strict pool: top-N per metric
    top_rate = fr_ranked_full[: args.top_per_metric]
    top_amp = amp_ranked_full[: args.top_per_metric]
    strict_pool, strict_spread_used = build_strict_pool(
        top_rate, top_amp,
        min_size=args.strict_pool_size,
        cap=args.strict_pool_cap,
    )

    # Over-provisioning pool: fixed 100 µm spread, topk=60, cap=32
    overprov_pool, overprov_spread_used = build_overprovisioning_pool(
        fr_ranked_full, amp_ranked_full,
        topk=args.topk,
        pool_cap=args.overprov_size,
    )

    # Per-electrode metrics for everyone in either pool
    elec_to_idx = {int(e): i for i, e in enumerate(electrodes)}
    pooled_set = set(strict_pool) | set(overprov_pool)
    per_elec_metrics = {}
    for e in pooled_set:
        i = elec_to_idx[int(e)]
        x, y = elec_xy_um(int(e))
        per_elec_metrics[str(int(e))] = {
            "rate_hz": float(spike_rates[i]),
            "amp_uV": float(mean_amplitudes[i]),
            "x_um": x,
            "y_um": y,
        }

    out = {
        "n_active": n_active,
        "n_scanned": int(len(electrodes)),
        "top_rate_ids": top_rate,
        "top_rate_values_hz": [float(spike_rates[elec_to_idx[e]]) for e in top_rate],
        "top_amp_ids": top_amp,
        "top_amp_values_uV": [float(mean_amplitudes[elec_to_idx[e]]) for e in top_amp],
        "strict_pool": strict_pool,
        "strict_pool_size": len(strict_pool),
        "strict_spread_um_used": strict_spread_used,
        "overprovisioning_pool": overprov_pool,
        "overprovisioning_pool_size": len(overprov_pool),
        "overprov_spread_um_used": overprov_spread_used,
        "overprov_topk_per_metric": args.topk,
        "rationale": (
            "strict_pool: top-{tp} by spike_rate ∪ top-{tp} by mean_amplitude "
            "(interleaved, deduped); greedy >= {sp_use} µm spread filter "
            "(relaxed from 100 µm if needed); capped at {cap}. "
            "overprovisioning_pool: >= 100 µm spread (fixed), topk={topk}, cap {ov_cap}."
        ).format(
            tp=args.top_per_metric, sp_use=strict_spread_used, cap=args.strict_pool_cap,
            topk=args.topk, ov_cap=args.overprov_size,
        ),
        "per_electrode_metrics": per_elec_metrics,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))

    print(f"Active electrodes: {n_active}/{len(electrodes)}")
    print(f"Strict pool ({len(strict_pool)} @ {strict_spread_used} µm): {strict_pool}")
    print(f"Over-provisioning pool ({len(overprov_pool)} @ {overprov_spread_used} µm): "
          f"{overprov_pool}")
    print(f"Wrote {out_path}")

    if n_active < 8:
        print(f"WARNING: only {n_active} active electrodes (< 8). "
              f"Phase A insufficient_active_electrodes — halt.")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
