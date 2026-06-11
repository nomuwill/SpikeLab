"""Burst-aligned analysis across all diazepam conditions.

Detects bursts, aligns to burst peaks, computes:
  1. Within-burst unit-to-unit correlations
  2. Rank-order consistency
  3. PCA on per-burst correlation features
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import os

from spikelab.workspace.workspace import AnalysisWorkspace
from spikelab.spikedata.plot_utils import (
    plot_distribution,
    plot_manifold,
    plot_recording,
)

SCRIPT_DIR = os.path.dirname(__file__)
WS_PATH = os.path.join(SCRIPT_DIR, "results", "workspace")
FIG_DIR = os.path.join(SCRIPT_DIR, "figures")
os.makedirs(FIG_DIR, exist_ok=True)

ws = AnalysisWorkspace.load(WS_PATH)

CONDITIONS = ["D0", "D3", "D10", "D30", "D50"]
LABELS = {"D0": "0 µM", "D3": "3 µM", "D10": "10 µM", "D30": "30 µM", "D50": "50 µM"}

# Burst detection parameters (from manuscript example notebook)
THR_BURST = 2.5
MIN_BURST_DIFF = 1000
BURST_EDGE_MULT = 0.2
PRE_MS = 500.0
POST_MS = 500.0

# ============================================================
# Step 1: Detect bursts and create aligned RateSliceStacks
# ============================================================
burst_info = {}
rate_stacks = {}

for cond in CONDITIONS:
    sd = ws.get(cond, "spikedata")

    pop_rate = sd.get_pop_rate(square_width=20, gauss_sigma=100, raster_bin_size_ms=1.0)
    pop_rate_acc = sd.get_pop_rate(square_width=8, gauss_sigma=8, raster_bin_size_ms=1.0)

    tburst, edges, peak_amp = sd.get_bursts(
        thr_burst=THR_BURST,
        min_burst_diff=MIN_BURST_DIFF,
        burst_edge_mult_thresh=BURST_EDGE_MULT,
        pop_rate=pop_rate,
        pop_rate_acc=pop_rate_acc,
    )

    burst_info[cond] = {
        "n_bursts": len(tburst),
        "tburst_ms": tburst,
        "peak_amp": peak_amp,
    }
    print(f"{cond}: {len(tburst)} bursts detected")

    if len(tburst) >= 2:
        rss = sd.align_to_events(
            tburst, pre_ms=PRE_MS, post_ms=POST_MS, kind="rate",
            bin_size_ms=1.0, sigma_ms=10,
        )
        rate_stacks[cond] = rss
    else:
        print(f"  Skipping alignment — too few bursts")

# ============================================================
# Step 2: Plot burst counts and mean amplitudes
# ============================================================
fig, axes = plt.subplots(1, 2, figsize=(10, 4))

n_bursts = [burst_info[c]["n_bursts"] for c in CONDITIONS]
mean_amps = [
    burst_info[c]["peak_amp"].mean() if burst_info[c]["n_bursts"] > 0 else 0
    for c in CONDITIONS
]

ax = axes[0]
ax.bar(range(len(CONDITIONS)), n_bursts, color="steelblue", edgecolor="black", linewidth=0.5)
ax.set_xticks(range(len(CONDITIONS)))
ax.set_xticklabels([LABELS[c] for c in CONDITIONS], fontsize=10)
ax.set_ylabel("Number of bursts", fontsize=11)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.tick_params(direction="out")

ax = axes[1]
ax.bar(range(len(CONDITIONS)), mean_amps, color="coral", edgecolor="black", linewidth=0.5)
ax.set_xticks(range(len(CONDITIONS)))
ax.set_xticklabels([LABELS[c] for c in CONDITIONS], fontsize=10)
ax.set_ylabel("Mean burst amplitude (spikes/bin)", fontsize=11)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.tick_params(direction="out")

fig.tight_layout()
fig.savefig(os.path.join(FIG_DIR, "burst_counts_amplitudes.png"), dpi=150, bbox_inches="tight")
plt.close(fig)
print("Saved burst_counts_amplitudes.png")

# ============================================================
# Step 3: Within-burst unit-to-unit correlations
# ============================================================
av_corrs = {}  # per-burst average correlation

for cond in CONDITIONS:
    if cond not in rate_stacks:
        continue
    rss = rate_stacks[cond]
    u2u_corr, u2u_lag, av_corr, av_lag = rss.unit_to_unit_correlation(max_lag=10)
    av_corrs[cond] = av_corr
    print(f"{cond}: mean within-burst u2u corr = {np.nanmean(av_corr):.3f}")

# Plot distribution of per-burst average correlations
conds_with_data = [c for c in CONDITIONS if c in av_corrs]
fig, ax = plt.subplots(figsize=(7, 4))
plot_distribution(
    ax=ax,
    metric_data=[av_corrs[c] for c in conds_with_data],
    labels=[LABELS[c] for c in conds_with_data],
    ylabel="Mean within-burst correlation",
    style="violin",
)
fig.savefig(os.path.join(FIG_DIR, "burst_u2u_correlation.png"), dpi=150, bbox_inches="tight")
plt.close(fig)
print("Saved burst_u2u_correlation.png")

# ============================================================
# Step 4: Rank-order analysis
# ============================================================
rank_results = {}

for cond in CONDITIONS:
    if cond not in rate_stacks:
        continue
    rss = rate_stacks[cond]
    corr_mat, av_corr_ro, overlap_mat = rss.rank_order_correlation(
        MIN_RATE_THRESHOLD=0.1, min_overlap=3, n_shuffles=100, seed=1,
    )
    rank_results[cond] = {
        "corr_matrix": corr_mat,
        "av_corr": av_corr_ro,
    }
    print(f"{cond}: mean rank-order correlation = {av_corr_ro:.3f}")

# Plot rank-order correlation matrices
conds_ro = [c for c in CONDITIONS if c in rank_results]
fig, axes = plt.subplots(1, len(conds_ro), figsize=(4 * len(conds_ro), 3.5))
if len(conds_ro) == 1:
    axes = [axes]
for ax, cond in zip(axes, conds_ro):
    mat = rank_results[cond]["corr_matrix"]
    mat.plot(ax=ax, cmap="RdBu_r", vmin=-1, vmax=1,
             colorbar_label="Rank corr." if cond == conds_ro[-1] else "")
    ax.set_xticklabels([])
    ax.set_yticklabels([])
    ax.set_xlabel("Burst", fontsize=11)
    ax.set_ylabel("Burst", fontsize=11)
    ax.text(0.5, 1.05, f"{LABELS[cond]} (r={rank_results[cond]['av_corr']:.2f})",
            transform=ax.transAxes, ha="center", va="bottom", fontsize=11)

fig.tight_layout()
fig.savefig(os.path.join(FIG_DIR, "burst_rank_order.png"), dpi=150, bbox_inches="tight")
plt.close(fig)
print("Saved burst_rank_order.png")

# ============================================================
# Step 5: PCA on per-burst u2u correlation features
# ============================================================
from spikelab.spikedata.utils import PCA_reduction

all_features = []
cond_labels = []

for cond in CONDITIONS:
    if cond not in rate_stacks:
        continue
    rss = rate_stacks[cond]
    u2u_corr, _, _, _ = rss.unit_to_unit_correlation(max_lag=10)
    features = u2u_corr.extract_lower_triangle_features()  # (S, F)
    # Replace NaNs with 0 for PCA
    features = np.nan_to_num(features, nan=0.0)
    all_features.append(features)
    cond_labels.extend([LABELS[cond]] * features.shape[0])

all_features = np.vstack(all_features)
cond_labels = np.array(cond_labels)

embedding, explained_var = PCA_reduction(all_features, n_components=2)
print(f"PCA explained variance: PC1={explained_var[0]:.1f}%, PC2={explained_var[1]:.1f}%")

# Plot PCA
fig, ax = plt.subplots(figsize=(6, 5))
unique_labels = [LABELS[c] for c in CONDITIONS if c in rate_stacks]
colors = plt.cm.viridis(np.linspace(0, 1, len(unique_labels)))
for label, color in zip(unique_labels, colors):
    mask = cond_labels == label
    ax.scatter(embedding[mask, 0], embedding[mask, 1],
               c=[color], label=label, s=30, alpha=0.7, edgecolors="k", linewidth=0.3)
ax.set_xlabel(f"PC1 ({explained_var[0]:.1f}%)", fontsize=11)
ax.set_ylabel(f"PC2 ({explained_var[1]:.1f}%)", fontsize=11)
ax.legend(frameon=False, fontsize=10)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.tick_params(direction="out")
fig.savefig(os.path.join(FIG_DIR, "burst_pca.png"), dpi=150, bbox_inches="tight")
plt.close(fig)
print("Saved burst_pca.png")

# Save results to workspace
for cond in CONDITIONS:
    if cond in burst_info:
        ws.store(cond, "burst_times", burst_info[cond]["tburst_ms"])
        ws.store(cond, "burst_peak_amp", burst_info[cond]["peak_amp"])
ws.save(WS_PATH)
print("Workspace updated.")
