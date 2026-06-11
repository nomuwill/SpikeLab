#!/usr/bin/env python
"""Fine-bin lag analysis + multivariate LOO logistic-regression classifier.

PART 1 — Lag analysis at 10 ms bins (vs 100 ms previously):
  - Same population-coupling pipeline but at finer time resolution to reveal
    millisecond-scale driver/follower structure if any exists.
  - Search range: ±200 ms in 10 ms steps (41 lags).
  - Outputs new pop_coupling_max_10ms, pop_lag_at_max_10ms, driver_score_10ms
    features.

PART 2 — Multivariate analysis:
  - Combine ALL features (intrinsic, multi-unit, waveform, fine-bin lag).
  - L1-regularized logistic regression with leave-one-out CV.
  - Report LOO AUC for top-5 and top-10 splits.
  - Permutation test: shuffle labels 1000× and compute null AUC distribution
    to get a real significance for the observed AUC.
  - Feature importance: standardized coefficients (full-data fit).

Outputs:
  - results/unit_features_with_fine_lag.csv
  - results/fine_lag_top5.png        12-panel comparison at top-5 (with 10ms-bin features)
  - results/multivar_logistic.png    ROC + feature-importance bars + null
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
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneOut
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, roc_curve

TASK_DIR = Path(__file__).resolve().parent
SD_PKL = TASK_DIR / "recordings/2026-05-10/191521_screen/sorted_rt_sort/sorted_spikedata_curated_plan.pkl"
BASELINE_INFO = TASK_DIR / "well_0/baseline_info.json"
UNITS_CSV = TASK_DIR / "recordings/2026-05-10/191521_screen/results/units_responding.csv"
ROUTING_JSON = TASK_DIR / "recordings/2026-05-10/191521_screen/stim_routing.json"
FEATURES_CSV = TASK_DIR / "recordings/2026-05-10/191521_screen/results/unit_features_full.csv"
RESULTS_DIR = TASK_DIR / "recordings/2026-05-10/191521_screen/results"

FS_HZ = 20000.0
N_COLS = 220
PITCH_UM = 17.5

# Fine-bin lag params
FINE_BIN_MS = 10.0
FINE_LAG_MAX_MS = 200.0
FINE_LAG_STEP_MS = 10.0  # one step per bin
STRONG_PARTNER_THRESH = 0.3


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def population_features_finebin(
    spike_times_list: list[np.ndarray],
    length_ms: float,
    bin_ms: float = FINE_BIN_MS,
    lag_max_ms: float = FINE_LAG_MAX_MS,
    lag_step_ms: float = FINE_LAG_STEP_MS,
) -> dict:
    """Per-unit pop coupling at finer bin resolution.

    Identical to the 100ms version but with a tighter bin → tighter lag
    grid. Returns dict[idx] -> {pop_coupling_max_10ms, pop_lag_at_max_10ms,
    driver_score_10ms, mean_pairwise_abs_corr_10ms}.
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

    means = rates.mean(axis=1, keepdims=True)
    stds = rates.std(axis=1, keepdims=True)
    stds_safe = np.where(stds > 0, stds, 1.0)
    rates_z = (rates - means) / stds_safe
    total_z = rates_z.sum(axis=0)

    lag_bins = np.arange(
        int(-lag_max_ms / bin_ms),
        int(lag_max_ms / bin_ms) + 1,
        max(1, int(lag_step_ms / bin_ms)),
        dtype=np.int64,
    )

    per = {}
    for i in range(n_units):
        pop_others = total_z - rates_z[i]
        po_std = pop_others.std()
        if po_std == 0:
            per[i] = dict(pop_coupling_max_10ms=float("nan"),
                          pop_lag_at_max_10ms=float("nan"),
                          driver_score_10ms=float("nan"),
                          mean_pairwise_abs_corr_10ms=float("nan"))
            continue
        po_z = (pop_others - pop_others.mean()) / po_std

        best_abs = 0.0
        best_lag = 0
        best_signed = 0.0
        for lb in lag_bins:
            if lb < 0:
                a = rates_z[i, -lb:]; b = po_z[:n_bins + lb]
            elif lb > 0:
                a = rates_z[i, :n_bins - lb]; b = po_z[lb:]
            else:
                a = rates_z[i]; b = po_z
            if a.size == 0 or a.size != b.size:
                continue
            r = float(np.dot(a, b) / a.size)
            if abs(r) > best_abs:
                best_abs = abs(r); best_lag = lb; best_signed = r

        max_abs = best_abs
        lag_ms_at_max = float(best_lag * bin_ms)

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
            mean_pairwise = float(np.mean(np.abs(arr)))
        else:
            mean_pairwise = 0.0

        if np.isfinite(max_abs) and np.isfinite(lag_ms_at_max):
            driver = float(max_abs * sigmoid(-lag_ms_at_max / 50.0))
        else:
            driver = float("nan")

        per[i] = dict(
            pop_coupling_max_10ms=max_abs,
            pop_lag_at_max_10ms=lag_ms_at_max,
            pop_coupling_max_signed_10ms=best_signed,
            driver_score_10ms=driver,
            mean_pairwise_abs_corr_10ms=mean_pairwise,
        )
    return per


def make_comparison_figure(rows, features, top_k, out_png, title):
    top_rows = [r for r in rows if r["reliability_rank"] <= top_k]
    rest_rows = [r for r in rows if r["reliability_rank"] > top_k]
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
        stats[fkey] = dict(
            top_mean=float(np.mean(t)) if len(t) else float("nan"),
            rest_mean=float(np.mean(b)) if len(b) else float("nan"),
            p=p, n_top=len(t), n_rest=len(b),
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
        ax.hist(b, bins=bins, alpha=0.45, color='gray', edgecolor='none', label=f"rest")
        ax.hist(t, bins=bins, alpha=0.75, color='steelblue', edgecolor='navy', linewidth=0.5, label=f"top-{top_k}")
        for r in top_rows:
            v = r[fkey]
            if np.isfinite(v):
                idx = r["reliability_rank"] - 1
                color = plt.cm.tab10(idx % 10)
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


def multivar_loo(X, y, C=1.0, penalty="l1"):
    """LOO-CV logistic regression. Returns (loo_scores, full_fit_coefs, full_fit_features)."""
    scaler = StandardScaler()
    X_std = scaler.fit_transform(X)
    loo = LeaveOneOut()
    scores = np.zeros(len(y))
    for tr, te in loo.split(X_std):
        model = LogisticRegression(
            penalty=penalty, C=C, solver="liblinear", max_iter=1000,
        )
        model.fit(X_std[tr], y[tr])
        scores[te] = model.predict_proba(X_std[te])[:, 1][0]
    # Full-data fit for feature-importance (coefs on standardized features)
    full = LogisticRegression(penalty=penalty, C=C, solver="liblinear", max_iter=1000)
    full.fit(X_std, y)
    return scores, full.coef_[0]


def main() -> None:
    # Load
    with open(SD_PKL, "rb") as f:
        sd = pickle.load(f)
    routing = json.loads(ROUTING_JSON.read_text())
    stim_electrodes = [int(e) for e in routing["stim_electrodes"]]

    with open(UNITS_CSV) as f:
        ranked = list(csv.DictReader(f))
    rank_by_uid = {int(r["unit_id"]): rank for rank, r in enumerate(ranked, 1)}

    with open(FEATURES_CSV) as f:
        feat_rows = list(csv.DictReader(f))
    feats_by_uid = {int(r["unit_id"]): r for r in feat_rows}

    # PART 1: fine-bin lag
    spike_trains_ms = [np.asarray(sd.train[i], dtype=np.float64) for i in range(sd.N)]
    fine = population_features_finebin(spike_trains_ms, float(sd.length))

    # Build merged feature rows with both 100ms-bin and 10ms-bin
    merged = []
    for sd_idx, attrs in enumerate(sd.neuron_attributes):
        uid = int(attrs["unit_id"])
        base = feats_by_uid.get(uid, {})
        if not base:
            continue
        f = fine.get(sd_idx, {})
        row = {}
        # Convert string-csv values to floats
        for k, v in base.items():
            if k in ("unit_id", "reliability_rank", "primary_electrode", "n_strong_partners"):
                try:
                    row[k] = int(v)
                except (TypeError, ValueError):
                    row[k] = v
            else:
                try:
                    row[k] = float(v)
                except (TypeError, ValueError):
                    row[k] = v
        row.update(f)
        row["reliability_rank"] = rank_by_uid.get(uid, -1)
        merged.append(row)

    merged.sort(key=lambda r: r["reliability_rank"])

    # Write merged CSV
    fieldnames = list(merged[0].keys())
    csv_path = RESULTS_DIR / "unit_features_with_fine_lag.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in merged:
            out = {}
            for k, v in r.items():
                if isinstance(v, float):
                    out[k] = f"{v:.4f}"
                else:
                    out[k] = v
            w.writerow(out)
    print(f"Wrote {csv_path}")

    # Fine-lag comparison figure (only the new features + control)
    fine_features = [
        ("pop_coupling_max_10ms", "Pop coupling max (10 ms bins)"),
        ("pop_lag_at_max_10ms", "Lag at max (ms, 10 ms bins)"),
        ("driver_score_10ms", "Driver score (10 ms bins)"),
        ("mean_pairwise_abs_corr_10ms", "Mean |pairwise r| (10 ms bins)"),
        # Repeat the 100ms versions for direct visual comparison:
        ("pop_coupling_max", "Pop coupling max (100 ms bins, control)"),
        ("driver_score", "Driver score (100 ms bins, control)"),
        ("mean_pairwise_abs_corr", "Mean |pairwise r| (100 ms bins)"),
        ("half_width_ms", "Half-width (ms) [previous best]"),
    ]
    print()
    print("=== Fine-bin lag features, top-5 vs rest ===")
    stats = make_comparison_figure(
        merged, fine_features, top_k=5,
        out_png=RESULTS_DIR / "fine_lag_top5.png",
        title="Fine-bin (10 ms) lag + population features — top-5 vs rest\n"
              "Mann-Whitney two-sided p. ~ = p<0.10, * = p<0.05, ** = p<0.01",
    )
    for fkey, _ in fine_features:
        s = stats[fkey]
        sig = ""
        if np.isfinite(s["p"]):
            if s["p"] < 0.01: sig = " **"
            elif s["p"] < 0.05: sig = " *"
            elif s["p"] < 0.10: sig = " ~"
        print(f"  {fkey:<40s}  top_mean={s['top_mean']:>7.3f}  "
              f"rest_mean={s['rest_mean']:>7.3f}  p={s['p']:.3g}{sig}")

    # PART 2: multivariate LOO logistic regression
    # Feature columns (numerical, exclude outputs and labels)
    EXCLUDE = {
        "unit_id", "primary_electrode", "reliability_rank", "reliability_score",
        "n_conditions_responding", "peak_strength_u", "best_condition",
        "x_um", "y_um",
        # Redundant copies
        "pop_coupling_zerolag",  # == pop_coupling_max at 100ms (lag=0 always)
        "pop_coupling_max_signed_10ms",
    }
    feature_keys = [
        k for k in merged[0].keys()
        if k not in EXCLUDE
        and isinstance(merged[0][k], (int, float))
        and not isinstance(merged[0][k], bool)
    ]
    print()
    print(f"Multivariate features ({len(feature_keys)}): {feature_keys}")

    # Build matrix; impute NaN with median
    X_raw = np.array([[r.get(k, np.nan) for k in feature_keys] for r in merged], dtype=np.float64)
    medians = np.nanmedian(X_raw, axis=0)
    inds = np.where(np.isnan(X_raw))
    X_raw[inds] = np.take(medians, inds[1])
    n_samples = X_raw.shape[0]

    np.random.seed(42)

    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    auc_results = {}
    for ax_row, top_k in enumerate((5, 10)):
        y = np.array([1 if r["reliability_rank"] <= top_k else 0 for r in merged], dtype=int)
        n_pos = int(y.sum())
        n_neg = int((1 - y).sum())
        # LOO
        scores, coefs = multivar_loo(X_raw, y, C=0.5, penalty="l1")
        try:
            auc = float(roc_auc_score(y, scores))
        except Exception:
            auc = float("nan")
        # Permutation null
        n_perm = 1000
        null_aucs = []
        for _ in range(n_perm):
            y_perm = np.random.permutation(y)
            s_perm, _ = multivar_loo(X_raw, y_perm, C=0.5, penalty="l1")
            try:
                null_aucs.append(float(roc_auc_score(y_perm, s_perm)))
            except Exception:
                pass
        null_aucs = np.asarray(null_aucs)
        p_perm = float(np.mean(null_aucs >= auc))
        auc_results[top_k] = dict(
            auc=auc, p_perm=p_perm, n_pos=n_pos, n_neg=n_neg,
            null_median=float(np.median(null_aucs)),
            null_95=float(np.quantile(null_aucs, 0.95)),
            coefs=coefs,
        )

        # ROC
        ax = axes[ax_row, 0]
        if np.isfinite(auc):
            fpr, tpr, _ = roc_curve(y, scores)
            ax.plot(fpr, tpr, color='steelblue', linewidth=2.0,
                    label=f"LOO AUC = {auc:.3f}")
        ax.plot([0, 1], [0, 1], 'k--', alpha=0.4, linewidth=0.7, label="chance")
        ax.set_xlabel("False positive rate", fontsize=9)
        ax.set_ylabel("True positive rate", fontsize=9)
        ax.set_title(f"ROC, top-{top_k} (n_pos={n_pos}, n_neg={n_neg})\n"
                     f"Permutation null median={auc_results[top_k]['null_median']:.3f}, "
                     f"95th={auc_results[top_k]['null_95']:.3f}, p={p_perm:.3g}",
                     fontsize=9)
        ax.legend(fontsize=8, loc='lower right')
        ax.set_xlim(0, 1); ax.set_ylim(0, 1)
        ax.tick_params(axis='both', labelsize=7)

        # Feature importance (full-data L1 coefs)
        ax = axes[ax_row, 1]
        order = np.argsort(-np.abs(coefs))
        nshow = min(15, len(feature_keys))
        keys_show = [feature_keys[i] for i in order[:nshow]]
        vals_show = coefs[order[:nshow]]
        colors = ['steelblue' if v > 0 else 'firebrick' for v in vals_show]
        y_pos = np.arange(nshow)
        ax.barh(y_pos, vals_show, color=colors, alpha=0.8)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(keys_show, fontsize=7)
        ax.invert_yaxis()
        ax.axvline(0, color='black', linewidth=0.5)
        ax.set_xlabel("L1 coefficient (standardized features)", fontsize=8)
        ax.set_title(f"Top-{top_k}: feature importance (L1, C=0.5)\n"
                     f"+ blue → predicts responder; − red → predicts non-responder",
                     fontsize=9)
        ax.tick_params(axis='both', labelsize=7)

    fig.suptitle(
        f"Multivariate L1 logistic regression — leave-one-out cross-validation\n"
        f"All {len(feature_keys)} numerical features, n={n_samples} units, "
        f"permutation null = {n_perm} label shuffles",
        fontsize=10, y=0.995,
    )
    plt.tight_layout(rect=(0, 0, 1, 0.95))
    out_png = RESULTS_DIR / "multivar_logistic.png"
    plt.savefig(out_png, dpi=130, bbox_inches='tight')
    plt.close(fig)
    print(f"Wrote {out_png}")

    print()
    print("=== Multivariate summary ===")
    for top_k, r in auc_results.items():
        print(f"  top-{top_k}: LOO AUC = {r['auc']:.3f}  "
              f"(null median {r['null_median']:.3f}, 95% {r['null_95']:.3f}, "
              f"permutation p = {r['p_perm']:.3g})")


if __name__ == "__main__":
    main()
