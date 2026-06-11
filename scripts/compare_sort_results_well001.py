"""
Compare Kilosort2 sorting results from GPU 0 and GPU 1 for well001.

Loads sorted_spikedata_curated.pkl from each GPU's results folder and
produces a side-by-side text table plus overlaid distribution figures.

Outputs (written to data/spikesort_test/parallel_gpu_test/comparison/):
  comparison_table.txt       — plain-text summary table
  snr_comparison.png         — overlaid SNR histograms
  fr_comparison.png          — overlaid firing rate histograms
  spike_count_comparison.png — overlaid spike count histograms
  isi_comparison.png         — overlaid ISI violation histograms
"""

import matplotlib
matplotlib.use("Agg")

import pickle
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE = Path(
    "/home/sharf-lab/Desktop/Analysis_shared/data/spikesort_test/parallel_gpu_test"
)
GPU0_PKL = BASE / "gpu0_well001" / "sorted_spikedata_curated.pkl"
GPU1_PKL = BASE / "gpu1_well001" / "sorted_spikedata_curated.pkl"
OUT_DIR = BASE / "comparison"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ── Load ──────────────────────────────────────────────────────────────────────

def load_sd(pkl_path: Path):
    if not pkl_path.exists():
        raise FileNotFoundError(f"Missing: {pkl_path}")
    with open(pkl_path, "rb") as f:
        return pickle.load(f)


print(f"Loading GPU 0 results from {GPU0_PKL}")
sd0 = load_sd(GPU0_PKL)
print(f"Loading GPU 1 results from {GPU1_PKL}")
sd1 = load_sd(GPU1_PKL)


# ── Extract per-unit metrics ───────────────────────────────────────────────────

def extract_metrics(sd) -> dict:
    attrs = sd.neuron_attributes
    snr = np.array([a["snr"] for a in attrs])
    spike_counts = np.array([len(t) for t in sd.train])
    # Firing rate in Hz: spike count / recording duration in seconds
    dur_s = sd.length / 1000.0
    fr_hz = spike_counts / dur_s if dur_s > 0 else spike_counts * 0.0

    # ISI violations — stored per unit if available, else compute from spike trains
    isi_viols = []
    for a in attrs:
        if "isi_violations_ratio" in a:
            isi_viols.append(float(a["isi_violations_ratio"]) * 100)
        elif "isi_viol" in a:
            isi_viols.append(float(a["isi_viol"]) * 100)
        else:
            # Compute from spike train (ms) — threshold 1.5 ms
            train_ms = sd.train[attrs.index(a)] if hasattr(sd.train[0], "__len__") else []
            if len(train_ms) > 1:
                isi = np.diff(np.sort(train_ms))
                isi_viols.append(100.0 * np.sum(isi < 1.5) / len(isi))
            else:
                isi_viols.append(0.0)
    isi_viols = np.array(isi_viols)

    return {
        "N": sd.N,
        "duration_s": dur_s,
        "total_spikes": int(spike_counts.sum()),
        "snr": snr,
        "fr_hz": fr_hz,
        "spike_counts": spike_counts,
        "isi_viols_pct": isi_viols,
    }


m0 = extract_metrics(sd0)
m1 = extract_metrics(sd1)


# ── Text table ────────────────────────────────────────────────────────────────

def _stats(arr: np.ndarray) -> str:
    if len(arr) == 0:
        return "n/a"
    return (
        f"mean={arr.mean():.2f}  median={np.median(arr):.2f}  "
        f"min={arr.min():.2f}  max={arr.max():.2f}"
    )


lines = [
    "=" * 72,
    "Kilosort2 Docker — well001 GPU comparison",
    "=" * 72,
    f"{'Metric':<30} {'GPU 0':>18} {'GPU 1':>18}",
    "-" * 72,
    f"{'Curated units':<30} {m0['N']:>18} {m1['N']:>18}",
    f"{'Total spikes':<30} {m0['total_spikes']:>18,} {m1['total_spikes']:>18,}",
    f"{'Recording duration (s)':<30} {m0['duration_s']:>18.1f} {m1['duration_s']:>18.1f}",
    "",
    "SNR",
    f"  {'mean':<28} {m0['snr'].mean():>18.2f} {m1['snr'].mean():>18.2f}",
    f"  {'median':<28} {np.median(m0['snr']):>18.2f} {np.median(m1['snr']):>18.2f}",
    f"  {'min':<28} {m0['snr'].min():>18.2f} {m1['snr'].min():>18.2f}",
    f"  {'max':<28} {m0['snr'].max():>18.2f} {m1['snr'].max():>18.2f}",
    "",
    "Firing rate (Hz)",
    f"  {'mean':<28} {m0['fr_hz'].mean():>18.3f} {m1['fr_hz'].mean():>18.3f}",
    f"  {'median':<28} {np.median(m0['fr_hz']):>18.3f} {np.median(m1['fr_hz']):>18.3f}",
    f"  {'min':<28} {m0['fr_hz'].min():>18.3f} {m1['fr_hz'].min():>18.3f}",
    f"  {'max':<28} {m0['fr_hz'].max():>18.3f} {m1['fr_hz'].max():>18.3f}",
    "",
    "Spikes per unit",
    f"  {'mean':<28} {m0['spike_counts'].mean():>18.1f} {m1['spike_counts'].mean():>18.1f}",
    f"  {'median':<28} {np.median(m0['spike_counts']):>18.1f} {np.median(m1['spike_counts']):>18.1f}",
    f"  {'min':<28} {m0['spike_counts'].min():>18} {m1['spike_counts'].min():>18}",
    f"  {'max':<28} {m0['spike_counts'].max():>18} {m1['spike_counts'].max():>18}",
    "",
    "ISI violations (%)",
    f"  {'mean':<28} {m0['isi_viols_pct'].mean():>18.3f} {m1['isi_viols_pct'].mean():>18.3f}",
    f"  {'median':<28} {np.median(m0['isi_viols_pct']):>18.3f} {np.median(m1['isi_viols_pct']):>18.3f}",
    f"  {'max':<28} {m0['isi_viols_pct'].max():>18.3f} {m1['isi_viols_pct'].max():>18.3f}",
    "=" * 72,
]

table_text = "\n".join(lines)
print(table_text)

table_path = OUT_DIR / "comparison_table.txt"
table_path.write_text(table_text)
print(f"\nTable saved to {table_path}")


# ── Figures ───────────────────────────────────────────────────────────────────

COLORS = {"GPU 0": "#1f77b4", "GPU 1": "#ff7f0e"}
FIG_KW = dict(dpi=150, bbox_inches="tight")


def _hist_comparison(
    data0: np.ndarray,
    data1: np.ndarray,
    xlabel: str,
    title: str,
    out_path: Path,
    bins: int = 30,
) -> None:
    fig, ax = plt.subplots(figsize=(7, 4))
    all_vals = np.concatenate([data0, data1])
    bin_edges = np.linspace(all_vals.min(), all_vals.max(), bins + 1)
    ax.hist(data0, bins=bin_edges, alpha=0.6, label=f"GPU 0 (n={len(data0)})", color=COLORS["GPU 0"])
    ax.hist(data1, bins=bin_edges, alpha=0.6, label=f"GPU 1 (n={len(data1)})", color=COLORS["GPU 1"])
    ax.set_xlabel(xlabel)
    ax.set_ylabel("Unit count")
    ax.set_title(title)
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.savefig(out_path, **FIG_KW)
    plt.close(fig)
    print(f"Saved {out_path}")


_hist_comparison(
    m0["snr"], m1["snr"],
    xlabel="SNR",
    title="SNR distribution — GPU 0 vs GPU 1 (Kilosort2 Docker, well001)",
    out_path=OUT_DIR / "snr_comparison.png",
)

_hist_comparison(
    m0["fr_hz"], m1["fr_hz"],
    xlabel="Firing rate (Hz)",
    title="Firing rate distribution — GPU 0 vs GPU 1",
    out_path=OUT_DIR / "fr_comparison.png",
)

_hist_comparison(
    m0["spike_counts"].astype(float), m1["spike_counts"].astype(float),
    xlabel="Spikes per unit",
    title="Spike count distribution — GPU 0 vs GPU 1",
    out_path=OUT_DIR / "spike_count_comparison.png",
)

_hist_comparison(
    m0["isi_viols_pct"], m1["isi_viols_pct"],
    xlabel="ISI violations (%)",
    title="ISI violation distribution — GPU 0 vs GPU 1",
    out_path=OUT_DIR / "isi_comparison.png",
)

# ── Unit-count bar ────────────────────────────────────────────────────────────

fig, ax = plt.subplots(figsize=(4, 4))
bars = ax.bar(["GPU 0", "GPU 1"], [m0["N"], m1["N"]], color=[COLORS["GPU 0"], COLORS["GPU 1"]], width=0.5)
for bar, n in zip(bars, [m0["N"], m1["N"]]):
    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5, str(n), ha="center", va="bottom")
ax.set_ylabel("Curated units")
ax.set_title("Curated unit count — GPU 0 vs GPU 1")
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
fig.savefig(OUT_DIR / "unit_count_comparison.png", **FIG_KW)
plt.close(fig)
print(f"Saved {OUT_DIR / 'unit_count_comparison.png'}")

print("\nComparison complete. All outputs in:", OUT_DIR)
