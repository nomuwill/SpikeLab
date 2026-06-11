#!/usr/bin/env python
"""Multi-unit / population features and detailed waveform shape comparison.

Builds on compare_intrinsic_features.py with two new feature blocks:

  MULTI-UNIT / POPULATION:
    - pop_coupling_zerolag — Pearson r between unit's binned rate and
      population trace (sum over all OTHER units, self-excluded), at lag 0.
    - pop_coupling_max — max |Pearson r| over ±200 ms lag grid.
    - pop_lag_at_max_ms — lag (ms) where max |r| occurs. Negative = leads
      population (driver), positive = follows (follower).
    - driver_score — |pop_coupling_max| × sigmoid(-lag_ms/50). High when
      coupling is strong AND unit leads. Parent's driver-ranking metric.
    - mean_pairwise_abs_corr — average |Pearson r| with all OTHER units
      (100 ms bins, self-excluded).
    - n_strong_partners — count of OTHER units with |r| > 0.3.

  WAVEFORM SHAPE:
    - spike_width_ms (re-confirmed): trough→peak interval.
    - half_width_ms: width at half-min depth.
    - peak_trough_ratio: positive peak amp / |negative trough| (Rs/FS marker).
    - repolarization_slope_uVms: slope (µV/ms) from trough to recovery.
    - asymmetry: (post-trough peak amp) / (pre-trough peak amp).

Compares two splits: top-5 and top-10 vs rest (using reliability rank).

Outputs:
  - results/unit_features_full.csv          per-unit table (all features)
  - results/multiunit_waveform_top5.png     comparison at TOP_K=5
  - results/multiunit_waveform_top10.png    comparison at TOP_K=10
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

# Population coupling params
POP_BIN_MS = 100.0
POP_LAG_MAX_MS = 200.0
POP_LAG_STEP_MS = 10.0
STRONG_PARTNER_THRESH = 0.3


def elec_xy_um(eid):
    return ((eid % N_COLS) * PITCH_UM, (eid // N_COLS) * PITCH_UM)


def elec_distance_um(e1, e2):
    x1, y1 = elec_xy_um(e1)
    x2, y2 = elec_xy_um(e2)
    return float(((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


# ──────────────────────────────────────────────────────────────────────
# Waveform features
# ──────────────────────────────────────────────────────────────────────

def waveform_features(template_1d: np.ndarray, fs_hz: float) -> dict:
    """Extract shape features from a 1D primary-channel template."""
    out = dict(
        spike_width_ms=float("nan"),
        half_width_ms=float("nan"),
        peak_trough_ratio=float("nan"),
        repolarization_slope_uVms=float("nan"),
        asymmetry=float("nan"),
    )
    if template_1d.size < 5:
        return out
    n = template_1d.size
    samples_per_ms = fs_hz / 1000.0
    trough_idx = int(np.argmin(template_1d))
    trough_val = float(template_1d[trough_idx])
    if trough_val >= 0:
        return out  # no real spike

    # Peak after trough
    after = template_1d[trough_idx:]
    peak_offset = int(np.argmax(after))
    peak_idx_global = trough_idx + peak_offset
    peak_val = float(template_1d[peak_idx_global])

    out["spike_width_ms"] = float(peak_offset / samples_per_ms)

    # Half-width: width at half-min depth, around trough
    half_level = 0.5 * trough_val
    # Left side
    left_idx = trough_idx
    while left_idx > 0 and template_1d[left_idx] < half_level:
        left_idx -= 1
    # Right side
    right_idx = trough_idx
    while right_idx < n - 1 and template_1d[right_idx] < half_level:
        right_idx += 1
    out["half_width_ms"] = float((right_idx - left_idx) / samples_per_ms)

    # Peak/trough ratio
    if abs(trough_val) > 1e-9:
        out["peak_trough_ratio"] = peak_val / abs(trough_val)

    # Repolarization slope: trough → peak, average µV/ms
    dt_ms = (peak_idx_global - trough_idx) / samples_per_ms
    if dt_ms > 0:
        out["repolarization_slope_uVms"] = (peak_val - trough_val) / dt_ms

    # Asymmetry: post-trough rise vs pre-trough fall (steeper rise = more asymmetric)
    pre_window = template_1d[max(0, trough_idx - 20):trough_idx]
    post_window = template_1d[trough_idx:min(n, trough_idx + 20)]
    if pre_window.size > 0 and post_window.size > 0:
        pre_max = float(pre_window.max() - trough_val)
        post_max = float(post_window.max() - trough_val)
        if pre_max > 1e-9:
            out["asymmetry"] = post_max / pre_max
    return out


# ──────────────────────────────────────────────────────────────────────
# Population features
# ──────────────────────────────────────────────────────────────────────

def population_features(
    spike_times_list: list[np.ndarray],
    length_ms: float,
    bin_ms: float = POP_BIN_MS,
    lag_max_ms: float = POP_LAG_MAX_MS,
    lag_step_ms: float = POP_LAG_STEP_MS,
) -> tuple[np.ndarray, dict]:
    """Compute population coupling + lag features for each unit.

    Returns (binned_rates [N x B], per_unit_features dict).
    """
    n_units = len(spike_times_list)
    n_bins = int(length_ms / bin_ms)
    rates = np.zeros((n_units, n_bins), dtype=np.float64)
    for i, st_ms in enumerate(spike_times_list):
        if len(st_ms) == 0:
            continue
        bin_idx = (np.asarray(st_ms, dtype=np.float64) / bin_ms).astype(np.int64)
        bin_idx = bin_idx[(bin_idx >= 0) & (bin_idx < n_bins)]
        if bin_idx.size:
            np.add.at(rates[i], bin_idx, 1)

    # z-score each unit's rate
    means = rates.mean(axis=1, keepdims=True)
    stds = rates.std(axis=1, keepdims=True)
    stds_safe = np.where(stds > 0, stds, 1.0)
    rates_z = (rates - means) / stds_safe

    # Population trace = sum over OTHER units (self-excluded), z-scored
    total_z = rates_z.sum(axis=0)  # for "all minus self" later

    # Lag grid in bins
    lag_bins = np.arange(
        int(-lag_max_ms / bin_ms),
        int(lag_max_ms / bin_ms) + 1,
        max(1, int(lag_step_ms / bin_ms)),
        dtype=np.int64,
    )

    per_unit = {}
    for i in range(n_units):
        # Population trace excluding unit i, z-scored across time
        pop_others = total_z - rates_z[i]
        pop_mean = pop_others.mean()
        pop_std = pop_others.std()
        if pop_std == 0:
            zerolag = float("nan")
            max_abs = float("nan")
            lag_ms_at_max = float("nan")
        else:
            pop_others_z = (pop_others - pop_mean) / pop_std
            # Zero-lag Pearson
            zerolag = float(np.dot(rates_z[i], pop_others_z) / n_bins)
            # Lag scan
            best_abs = 0.0
            best_signed = 0.0
            best_lag = 0
            for lb in lag_bins:
                if lb < 0:
                    a = rates_z[i, -lb:]
                    b = pop_others_z[:n_bins + lb]
                elif lb > 0:
                    a = rates_z[i, :n_bins - lb]
                    b = pop_others_z[lb:]
                else:
                    a = rates_z[i]
                    b = pop_others_z
                if a.size == 0 or a.size != b.size:
                    continue
                r = float(np.dot(a, b) / a.size)
                if abs(r) > best_abs:
                    best_abs = abs(r)
                    best_signed = r
                    best_lag = lb
            max_abs = best_abs
            lag_ms_at_max = float(best_lag * bin_ms)

        # Pairwise correlations with all OTHER units
        if pop_std == 0:
            mean_pairwise_abs = float("nan")
            n_strong = 0
        else:
            corrs = []
            for j in range(n_units):
                if j == i:
                    continue
                if stds[j, 0] == 0:
                    continue
                r = float(np.dot(rates_z[i], rates_z[j]) / n_bins)
                corrs.append(r)
            if corrs:
                arr = np.asarray(corrs)
                mean_pairwise_abs = float(np.mean(np.abs(arr)))
                n_strong = int(np.sum(np.abs(arr) > STRONG_PARTNER_THRESH))
            else:
                mean_pairwise_abs = 0.0
                n_strong = 0

        # Driver score: |coupling| × sigmoid(-lag/50) — high when strong and leading
        if np.isfinite(max_abs) and np.isfinite(lag_ms_at_max):
            driver_score = float(max_abs * sigmoid(-lag_ms_at_max / 50.0))
        else:
            driver_score = float("nan")

        per_unit[i] = dict(
            pop_coupling_zerolag=zerolag,
            pop_coupling_max=max_abs,
            pop_coupling_max_signed=best_signed if pop_std > 0 else float("nan"),
            pop_lag_at_max_ms=lag_ms_at_max,
            driver_score=driver_score,
            mean_pairwise_abs_corr=mean_pairwise_abs,
            n_strong_partners=n_strong,
        )
    return rates, per_unit


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def make_comparison_figure(rows, features, top_k, out_png, title):
    top_rows = [r for r in rows if r["reliability_rank"] <= top_k]
    rest_rows = [r for r in rows if r["reliability_rank"] > top_k]

    stats = {}
    for fkey, _ in features:
        t = np.array([r[fkey] for r in top_rows if np.isfinite(r[fkey])])
        b = np.array([r[fkey] for r in rest_rows if np.isfinite(r[fkey])])
        if len(t) < 2 or len(b) < 2:
            p = float("nan"); u = float("nan")
        else:
            try:
                res = mannwhitneyu(t, b, alternative="two-sided")
                p = res.pvalue; u = res.statistic
            except Exception:
                p = float("nan"); u = float("nan")
        stats[fkey] = dict(
            top_mean=float(np.mean(t)) if len(t) else float("nan"),
            rest_mean=float(np.mean(b)) if len(b) else float("nan"),
            top_median=float(np.median(t)) if len(t) else float("nan"),
            rest_median=float(np.median(b)) if len(b) else float("nan"),
            p=p, u=u,
            n_top=len(t), n_rest=len(b),
        )

    n_feat = len(features)
    ncols = 4
    nrows = (n_feat + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.3 * ncols, 2.6 * nrows), squeeze=False)

    for fi, (fkey, ftitle) in enumerate(features):
        ax = axes[fi // ncols, fi % ncols]
        t = np.array([r[fkey] for r in top_rows if np.isfinite(r[fkey])])
        b = np.array([r[fkey] for r in rest_rows if np.isfinite(r[fkey])])
        all_v = np.concatenate([t, b]) if len(t) and len(b) else (t if len(t) else b)
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
                linewidth=0.5, label=f"top-{top_k} (n={len(t)})")
        # Mark top units
        for r in top_rows:
            v = r[fkey]
            if np.isfinite(v):
                idx = r["reliability_rank"] - 1
                color = plt.cm.tab10(idx % 10) if idx < 10 else 'magenta'
                ax.axvline(v, color=color, linewidth=1.1, alpha=0.8)
        p = stats[fkey]["p"]
        sig = ""
        if np.isfinite(p):
            if p < 0.01: sig = " **"
            elif p < 0.05: sig = " *"
            elif p < 0.10: sig = " ~"
        ax.set_title(f"{ftitle}\nMW p = {p:.3g}{sig}", fontsize=8)
        ax.tick_params(axis='both', labelsize=7)
        if fi == 0:
            ax.legend(fontsize=6, loc='upper right')

    for fi in range(n_feat, nrows * ncols):
        axes[fi // ncols, fi % ncols].axis('off')

    fig.suptitle(title, fontsize=10, y=0.995)
    plt.tight_layout(rect=(0, 0, 1, 0.95))
    plt.savefig(out_png, dpi=130, bbox_inches='tight')
    plt.close(fig)
    return stats


def main() -> None:
    with open(SD_PKL, "rb") as f:
        sd = pickle.load(f)
    bi = json.load(open(BASELINE_INFO))
    routing = json.loads(ROUTING_JSON.read_text())
    stim_electrodes = [int(e) for e in routing["stim_electrodes"]]

    with open(UNITS_CSV) as f:
        ranked = list(csv.DictReader(f))
    rank_by_uid = {int(r["unit_id"]): rank for rank, r in enumerate(ranked, 1)}
    reliability_by_uid = {int(r["unit_id"]): float(r["reliability_score"]) for r in ranked}

    bi_by_uid = {int(u["unit_id"]): u for u in bi["unit_features"]}

    # ─ Population features (all 41 units' spike trains)
    spike_trains_ms = [np.asarray(sd.train[i], dtype=np.float64) for i in range(sd.N)]
    length_ms = float(sd.length)
    rates, pop_feats = population_features(spike_trains_ms, length_ms)

    # ─ Per-unit feature rows
    rows = []
    for sd_idx, attrs in enumerate(sd.neuron_attributes):
        uid = int(attrs["unit_id"])
        elec_arr = attrs["electrode"]
        primary = int(elec_arr) if hasattr(elec_arr, "shape") and elec_arr.shape == () else int(elec_arr.flat[0])
        template = np.asarray(attrs["template"], dtype=np.float64)
        wf = waveform_features(template, FS_HZ)
        pf = pop_feats.get(sd_idx, {})
        bi_row = bi_by_uid.get(uid, {})
        dist_to_nearest_stim = min(elec_distance_um(primary, se) for se in stim_electrodes)

        rows.append(dict(
            unit_id=uid,
            reliability_rank=rank_by_uid.get(uid, -1),
            reliability_score=reliability_by_uid.get(uid, 0.0),
            primary_electrode=primary,
            snr=float(attrs["snr"]),
            amplitude_uV=float(attrs["amplitude"]),
            baseline_rate_hz=float(bi_row.get("baseline_rate_hz", 0.0)),
            mean_correlation_top10=float(bi_row.get("mean_correlation_top10", 0.0)),
            isi_cv=float(bi_row.get("isi_cv", 0.0)),
            dist_to_nearest_stim_um=dist_to_nearest_stim,
            # Population
            pop_coupling_zerolag=pf.get("pop_coupling_zerolag", float("nan")),
            pop_coupling_max=pf.get("pop_coupling_max", float("nan")),
            pop_coupling_max_signed=pf.get("pop_coupling_max_signed", float("nan")),
            pop_lag_at_max_ms=pf.get("pop_lag_at_max_ms", float("nan")),
            driver_score=pf.get("driver_score", float("nan")),
            mean_pairwise_abs_corr=pf.get("mean_pairwise_abs_corr", float("nan")),
            n_strong_partners=pf.get("n_strong_partners", 0),
            # Waveform
            spike_width_ms=wf["spike_width_ms"],
            half_width_ms=wf["half_width_ms"],
            peak_trough_ratio=wf["peak_trough_ratio"],
            repolarization_slope_uVms=wf["repolarization_slope_uVms"],
            asymmetry=wf["asymmetry"],
        ))

    rows.sort(key=lambda r: r["reliability_rank"])

    # CSV
    fieldnames = list(rows[0].keys())
    csv_path = RESULTS_DIR / "unit_features_full.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: (f"{v:.4f}" if isinstance(v, float) else v) for k, v in r.items()})
    print(f"Wrote {csv_path}")

    # Features to compare (new ones primarily; keep dist_to_stim as control)
    features = [
        ("pop_coupling_zerolag", "Pop coupling, lag 0 (Pearson r)"),
        ("pop_coupling_max", "Pop coupling, max |r| ±200 ms"),
        ("pop_lag_at_max_ms", "Lag (ms) at max coupling"),
        ("driver_score", "Driver score (|r| × sigmoid(-lag/50))"),
        ("mean_pairwise_abs_corr", "Mean |pairwise r| (100 ms bins)"),
        ("n_strong_partners", "# partners with |r| > 0.3"),
        ("spike_width_ms", "Spike width (trough→peak, ms)"),
        ("half_width_ms", "Half-width at min depth (ms)"),
        ("peak_trough_ratio", "Peak/trough ratio"),
        ("repolarization_slope_uVms", "Repolarization slope (µV/ms)"),
        ("asymmetry", "Waveform asymmetry (post/pre)"),
        ("dist_to_nearest_stim_um", "Dist to nearest stim (µm) [control]"),
    ]

    # Two comparison figures: top-5 and top-10
    for top_k in (5, 10):
        out_png = RESULTS_DIR / f"multiunit_waveform_top{top_k}.png"
        title = (
            f"Multi-unit + waveform features — top-{top_k} responders vs rest "
            f"({len(rows) - top_k} units)\n"
            f"Mann-Whitney two-sided p-values. ~ = p<0.10, * = p<0.05, ** = p<0.01"
        )
        print()
        print(f"=== TOP-{top_k} vs rest ===")
        stats = make_comparison_figure(rows, features, top_k, out_png, title)
        print(f"{'feature':<35s}  {'top mean':>10s}  {'rest mean':>10s}  {'p':>8s}")
        for fkey, _ in features:
            s = stats[fkey]
            sig = ""
            if np.isfinite(s["p"]):
                if s["p"] < 0.01: sig = " **"
                elif s["p"] < 0.05: sig = " *"
                elif s["p"] < 0.10: sig = " ~"
            print(f"{fkey:<35s}  {s['top_mean']:>10.3f}  {s['rest_mean']:>10.3f}  {s['p']:>8.3g}{sig}")
        print(f"Wrote {out_png}")


if __name__ == "__main__":
    main()
