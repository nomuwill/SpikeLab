#!/usr/bin/env python
"""Compare intrinsic baseline features of top responders vs the rest.

Computes per-unit intrinsic features from the 180 s pre-stim baseline:
  - baseline_rate_hz, mean_correlation_top10, isi_cv  (from baseline_info)
  - snr, amplitude_uV, primary_electrode, x_um, y_um  (from sd.neuron_attributes)
  - spike_width_ms (peak-to-trough on the primary-channel template)
  - footprint_extent (count of channels whose template peak amp >= 50% of max)
  - refractory_viol_frac (fraction of ISIs < 3 ms)
  - dist_to_nearest_stim_um, dist_to_best_stim_um
  - reliability_score from the screen (separator: top vs rest)

Outputs:
  - unit_features_all.csv         per-unit table
  - intrinsic_top_vs_rest.png     histograms per feature, top-K vs rest, with M-W p-value
"""
from __future__ import annotations

import csv
import json
import pickle
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import mannwhitneyu

TASK_DIR = Path(__file__).resolve().parent
SD_PKL = TASK_DIR / "recordings/2026-05-10/191521_screen/sorted_rt_sort/sorted_spikedata_curated_plan.pkl"
BASELINE_INFO = TASK_DIR / "well_0/baseline_info.json"
UNITS_CSV = TASK_DIR / "recordings/2026-05-10/191521_screen/results/units_responding.csv"
ROUTING_JSON = TASK_DIR / "recordings/2026-05-10/191521_screen/stim_routing.json"
RESULTS_DIR = TASK_DIR / "recordings/2026-05-10/191521_screen/results"

FS_HZ = 20000.0
N_COLS = 220
PITCH_UM = 17.5
TOP_K = 5  # mark top-K as the "responders" group
RESPONDERS_DEFINITION = "rank <= 5"  # for reporting


def elec_xy_um(eid: int) -> tuple[float, float]:
    return ((eid % N_COLS) * PITCH_UM, (eid // N_COLS) * PITCH_UM)


def elec_distance_um(e1: int, e2: int) -> float:
    x1, y1 = elec_xy_um(e1)
    x2, y2 = elec_xy_um(e2)
    return float(((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5)


def spike_width_ms(template_1d: np.ndarray, fs_hz: float) -> float:
    """Peak-to-trough time on the primary-channel template (ms).

    Convention: find the most negative sample (trough), then the most positive
    sample after it (peak). Width = (peak_idx - trough_idx) / fs.
    """
    if template_1d.size == 0:
        return float("nan")
    trough_idx = int(np.argmin(template_1d))
    after = template_1d[trough_idx:]
    if after.size == 0:
        return float("nan")
    peak_offset = int(np.argmax(after))
    return float(peak_offset / fs_hz * 1000.0)


def footprint_extent_real(template_full: np.ndarray, frac: float = 0.5) -> int:
    """Count channels whose template peak |amp| ≥ frac * max(|amp|)."""
    if template_full.size == 0:
        return 0
    peak_amps = np.max(np.abs(template_full), axis=0)
    mx = float(peak_amps.max())
    if mx <= 0:
        return 0
    return int(np.sum(peak_amps >= frac * mx))


def refractory_viol_frac(spike_times_ms: np.ndarray, threshold_ms: float = 3.0) -> float:
    if len(spike_times_ms) < 2:
        return 0.0
    isis = np.diff(np.sort(spike_times_ms))
    return float(np.sum(isis < threshold_ms) / len(isis))


def main() -> None:
    # Load data
    with open(SD_PKL, "rb") as f:
        sd = pickle.load(f)
    bi = json.load(open(BASELINE_INFO))
    routing = json.loads(ROUTING_JSON.read_text())
    stim_electrodes = [int(e) for e in routing["stim_electrodes"]]

    # Reliability rank from the screen
    with open(UNITS_CSV) as f:
        ranked = list(csv.DictReader(f))
    rank_by_uid = {int(r["unit_id"]): rank for rank, r in enumerate(ranked, 1)}
    reliability_by_uid = {int(r["unit_id"]): float(r["reliability_score"]) for r in ranked}
    n_resp_by_uid = {int(r["unit_id"]): int(r["n_conditions_responding"]) for r in ranked}
    peak_strength_by_uid = {int(r["unit_id"]): float(r["peak_strength_u"]) for r in ranked}
    best_cond_by_uid = {int(r["unit_id"]): r["best_condition"] for r in ranked}

    # baseline_info per-unit lookup
    bi_by_uid = {int(u["unit_id"]): u for u in bi["unit_features"]}

    # Build per-unit feature rows
    rows = []
    for sd_idx, attrs in enumerate(sd.neuron_attributes):
        uid = int(attrs["unit_id"])
        elec_arr = attrs["electrode"]
        primary = int(elec_arr) if hasattr(elec_arr, "shape") and elec_arr.shape == () else int(elec_arr.flat[0])
        x = float(attrs["x"])
        y = float(attrs["y"])
        snr = float(attrs["snr"])
        amp_uv = float(attrs["amplitude"])
        template = np.asarray(attrs["template"], dtype=np.float64)
        template_full = np.asarray(attrs["template_full"], dtype=np.float64)
        spike_w_ms = spike_width_ms(template, FS_HZ)
        foot_ext = footprint_extent_real(template_full, frac=0.5)

        spike_times_ms = np.asarray(sd.train[sd_idx], dtype=np.float64)
        refr_frac = refractory_viol_frac(spike_times_ms, threshold_ms=3.0)

        bi_row = bi_by_uid.get(uid, {})
        baseline_rate_hz = float(bi_row.get("baseline_rate_hz", 0.0))
        mean_corr_top10 = float(bi_row.get("mean_correlation_top10", 0.0))
        isi_cv = float(bi_row.get("isi_cv", 0.0))

        dist_to_nearest_stim = min(elec_distance_um(primary, se) for se in stim_electrodes)
        # Best condition's stim site
        best_cond = best_cond_by_uid.get(uid, "")
        if best_cond.startswith("site_"):
            try:
                best_stim_elec = int(best_cond.split("_")[1])
                dist_to_best_stim = elec_distance_um(primary, best_stim_elec)
            except Exception:
                dist_to_best_stim = float("nan")
        else:
            dist_to_best_stim = float("nan")

        rows.append({
            "unit_id": uid,
            "reliability_rank": rank_by_uid.get(uid, -1),
            "reliability_score": reliability_by_uid.get(uid, 0.0),
            "n_conditions_responding": n_resp_by_uid.get(uid, 0),
            "peak_strength_u": peak_strength_by_uid.get(uid, 0.0),
            "best_condition": best_cond,
            "primary_electrode": primary,
            "x_um": x,
            "y_um": y,
            "snr": snr,
            "amplitude_uV": amp_uv,
            "spike_width_ms": spike_w_ms,
            "footprint_extent": foot_ext,
            "baseline_rate_hz": baseline_rate_hz,
            "mean_correlation_top10": mean_corr_top10,
            "isi_cv": isi_cv,
            "refractory_viol_frac": refr_frac,
            "dist_to_nearest_stim_um": dist_to_nearest_stim,
            "dist_to_best_stim_um": dist_to_best_stim,
        })

    rows.sort(key=lambda r: r["reliability_rank"])

    # Write CSV
    fieldnames = list(rows[0].keys())
    csv_path = RESULTS_DIR / "unit_features_all.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in r.items()})
    print(f"Wrote {csv_path}")

    # Group split
    top_rows = [r for r in rows if r["reliability_rank"] <= TOP_K]
    rest_rows = [r for r in rows if r["reliability_rank"] > TOP_K]
    print(f"Top group: ranks 1..{TOP_K} ({len(top_rows)} units)")
    print(f"Rest:      ranks {TOP_K+1}..{len(rows)} ({len(rest_rows)} units)")

    # Features to compare
    features = [
        ("baseline_rate_hz", "Baseline firing rate (Hz)"),
        ("snr", "SNR"),
        ("amplitude_uV", "Spike amplitude (µV)"),
        ("spike_width_ms", "Spike width: trough→peak (ms)"),
        ("footprint_extent", "Footprint extent (# ch ≥50% peak)"),
        ("mean_correlation_top10", "Mean corr w/ top-10 neighbors (100 ms bins)"),
        ("isi_cv", "ISI CV"),
        ("refractory_viol_frac", "Fraction ISIs < 3 ms"),
        ("dist_to_nearest_stim_um", "Distance to nearest stim site (µm)"),
        ("dist_to_best_stim_um", "Distance to BEST stim site (µm)"),
    ]

    # Stats table
    print()
    print(f"{'feature':<40s}  {'top mean':>10s}  {'rest mean':>10s}  "
          f"{'top median':>10s}  {'rest median':>10s}  {'MW p':>8s}")
    stats = {}
    for fkey, _ in features:
        t = np.array([r[fkey] for r in top_rows if np.isfinite(r[fkey])])
        b = np.array([r[fkey] for r in rest_rows if np.isfinite(r[fkey])])
        if len(t) < 2 or len(b) < 2:
            p = float("nan")
        else:
            try:
                p = mannwhitneyu(t, b, alternative="two-sided").pvalue
            except Exception:
                p = float("nan")
        stats[fkey] = {
            "top_mean": float(np.mean(t)) if len(t) else float("nan"),
            "rest_mean": float(np.mean(b)) if len(b) else float("nan"),
            "top_median": float(np.median(t)) if len(t) else float("nan"),
            "rest_median": float(np.median(b)) if len(b) else float("nan"),
            "p": p,
        }
        print(f"{fkey:<40s}  {stats[fkey]['top_mean']:>10.3f}  {stats[fkey]['rest_mean']:>10.3f}  "
              f"{stats[fkey]['top_median']:>10.3f}  {stats[fkey]['rest_median']:>10.3f}  "
              f"{p:>8.3g}")

    # Multi-panel figure
    n_feat = len(features)
    ncols = 5
    nrows = (n_feat + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.3 * ncols, 2.6 * nrows), squeeze=False)

    for fi, (fkey, ftitle) in enumerate(features):
        ax = axes[fi // ncols, fi % ncols]
        t = np.array([r[fkey] for r in top_rows if np.isfinite(r[fkey])])
        b = np.array([r[fkey] for r in rest_rows if np.isfinite(r[fkey])])
        all_v = np.concatenate([t, b])
        if len(all_v) == 0:
            ax.set_title(f"{ftitle}\n(no data)", fontsize=9)
            continue
        lo, hi = float(all_v.min()), float(all_v.max())
        if hi == lo:
            hi = lo + 1.0
        bins = np.linspace(lo, hi, 12)
        ax.hist(b, bins=bins, alpha=0.45, color='gray', edgecolor='none',
                label=f"rest (n={len(b)})")
        ax.hist(t, bins=bins, alpha=0.75, color='steelblue', edgecolor='navy',
                linewidth=0.5, label=f"top-{TOP_K} (n={len(t)})")
        # Top-5 unit values as dots
        for r in top_rows:
            v = r[fkey]
            if np.isfinite(v):
                ax.axvline(v, color=plt.cm.tab10(r["reliability_rank"] - 1),
                           linewidth=1.2, alpha=0.8)
        p = stats[fkey]["p"]
        sig = ""
        if np.isfinite(p):
            if p < 0.01: sig = " **"
            elif p < 0.05: sig = " *"
        ax.set_title(f"{ftitle}\nMW p = {p:.3g}{sig}", fontsize=8)
        ax.tick_params(axis='both', labelsize=7)
        if fi == 0:
            ax.legend(fontsize=6, loc='upper right')

    # Hide unused panels
    for fi in range(n_feat, nrows * ncols):
        axes[fi // ncols, fi % ncols].axis('off')

    fig.suptitle(
        f"Intrinsic baseline features — top-{TOP_K} responders vs rest ({len(rest_rows)} units)\n"
        f"Histograms (gray=rest, blue=top-{TOP_K}); vertical lines show each top-{TOP_K} unit's value "
        f"(colors as in baseline raster).",
        fontsize=10, y=0.995,
    )
    plt.tight_layout(rect=(0, 0, 1, 0.95))

    out_png = RESULTS_DIR / "intrinsic_top_vs_rest.png"
    plt.savefig(out_png, dpi=130, bbox_inches='tight')
    plt.close(fig)
    print(f"Wrote {out_png}")


if __name__ == "__main__":
    main()
