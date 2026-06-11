"""
ConstraintExp4 — all 5 MEAs, chronological progression (May 19 → May 22 2026).
First 10 minutes per recording, population burst outlines (100 ms bins),
burst width / IBI / CV in panel titles.

Output: analysis/ConstraintExp4/ConstraintExp4_rasters_10min.png
"""

import os
import shutil
import subprocess
import tempfile
import warnings

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from spikelab.data_loaders import load_spikedata_from_pickle
from spikelab.spikedata.plot_utils import plot_recording

# ── Config ─────────────────────────────────────────────────────────────────────
UUID      = "2026-05-22-e-ConstraintExp4"
S3_PKL    = f"s3://braingeneers/ephys/{UUID}/derived/spikelabKS2"
ENDPOINT  = "https://s3.braingeneers.gi.ucsc.edu"
WINDOW_MS = 600_000   # 10 minutes

OUT_DIR = "analysis/ConstraintExp4"
os.makedirs(OUT_DIR, exist_ok=True)

# Chronological order; chip 23131 recorded on both dates
RECORDINGS = [
    ("23131  —  May 19 (05/19/2026)", "23131_19May26"),
    ("23187  —  May 19 (05/19/2026)", "23187_19May26"),
    ("23131  —  May 22 (05/22/2026)", "23131_22May26"),
    ("24478  —  May 22 (05/22/2026)", "24478_22May26"),
    ("24487  —  May 22 (05/22/2026)", "24487_22May26"),
]


def load_pkl(stem: str):
    tmp = tempfile.mkdtemp()
    try:
        local = os.path.join(tmp, "sd.pkl")
        r = subprocess.run(
            ["aws", "s3", "cp", f"{S3_PKL}/{stem}/sorted_spikedata_curated.pkl",
             local, "--endpoint-url", ENDPOINT],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return load_spikedata_from_pickle(local)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def burst_stats(tburst, edges, window_ms):
    if tburst is None or len(tburst) == 0:
        return {"n": 0, "width_mean": np.nan, "width_std": np.nan,
                "ibi_mean": np.nan, "ibi_std": np.nan, "width_cv": np.nan}
    mask  = tburst < window_ms
    tb_w  = tburst[mask]
    ed_w  = edges[mask]
    n     = len(tb_w)
    if n == 0:
        return {"n": 0, "width_mean": np.nan, "width_std": np.nan,
                "ibi_mean": np.nan, "ibi_std": np.nan, "width_cv": np.nan}
    widths = ed_w[:, 1] - ed_w[:, 0]                          # ms (bin_size=1)
    ibis   = np.diff(tb_w) / 1000.0 if n > 1 else np.array([np.nan])  # peak-to-peak, s
    wm, ws = float(np.mean(widths)), float(np.std(widths))
    return {
        "n":          n,
        "width_mean": wm,
        "width_std":  ws,
        "ibi_mean":   float(np.nanmean(ibis)),
        "ibi_std":    float(np.nanstd(ibis)),
        "width_cv":   ws / wm if wm > 0 else np.nan,
    }


# ── 1. Load + detect bursts ────────────────────────────────────────────────────
print("Loading recordings and detecting bursts ...")
sds, burst_data, stats_list = [], [], []

for label, stem in RECORDINGS:
    print(f"\n  [{label}]")
    try:
        sd = load_pkl(stem)
        print(f"    units: {sd.N},  duration: {sd.length/1000:.1f} s")
        tburst, edges, _ = sd.get_bursts(
            1.5, 200, 0.3, square_width=100, gauss_sigma=100,
        )
        st   = burst_stats(tburst, edges, WINDOW_MS)
        n10  = int((tburst < WINDOW_MS).sum())
        print(f"    bursts in 10 min: {n10} / {len(tburst)}")
        sds.append(sd)
        burst_data.append((tburst, edges))
        stats_list.append(st)
    except Exception as exc:
        print(f"    ERROR: {exc}")
        sds.append(None)
        burst_data.append((None, None))
        stats_list.append(None)


# ── 2. Build figure ────────────────────────────────────────────────────────────
n        = len(RECORDINGS)
fig_h    = n * 3.5
fig      = plt.figure(figsize=(20, fig_h))
outer_gs = gridspec.GridSpec(n, 1, figure=fig, hspace=0.45)

axes_per_rec = []
for i in range(n):
    inner = gridspec.GridSpecFromSubplotSpec(
        2, 2, subplot_spec=outer_gs[i],
        height_ratios=[3, 1], width_ratios=[1, 0.012],
        hspace=0.05, wspace=0.02,
    )
    r_ax  = fig.add_subplot(inner[0, 0])
    r_cax = fig.add_subplot(inner[0, 1])
    p_ax  = fig.add_subplot(inner[1, 0], sharex=r_ax)
    p_cax = fig.add_subplot(inner[1, 1])
    r_cax.axis("off"); p_cax.axis("off")
    axes_per_rec.append([(r_ax, r_cax), (p_ax, p_cax)])

for i, (label, stem) in enumerate(RECORDINGS):
    sd            = sds[i]
    tburst, edges = burst_data[i]
    st            = stats_list[i]
    r_ax, _       = axes_per_rec[i][0]
    p_ax, _       = axes_per_rec[i][1]

    if sd is None:
        r_ax.set_facecolor("#f5f5f5")
        r_ax.text(0.5, 0.5, "Load failed", transform=r_ax.transAxes,
                  ha="center", va="center", color="#888888",
                  fontsize=11, style="italic")
        r_ax.set_title(label, loc="left", fontsize=9,
                       fontweight="bold", pad=3, color="#666666")
        p_ax.axis("off")
        continue

    t_end = min(WINDOW_MS, sd.length)

    plot_recording(
        sd,
        show_raster=True,
        show_pop_rate=True,
        burst_times=tburst,
        burst_edges=edges,
        time_range=(0, t_end),
        raster_bin_size_ms=1.0,
        pop_rate_params={"square_width": 100, "gauss_sigma": 100},
        axes=axes_per_rec[i],
        font_size=9,
        show=False,
        save_path=None,
    )

    if st and st["n"] > 0:
        w_str   = (f"{st['width_mean']:.0f} ± {st['width_std']:.0f} ms"
                   if not np.isnan(st["width_mean"]) else "—")
        ibi_str = (f"{st['ibi_mean']:.2f} ± {st['ibi_std']:.2f} s"
                   if not np.isnan(st["ibi_mean"]) else "—")
        cv_str  = f"{st['width_cv']:.2f}" if not np.isnan(st["width_cv"]) else "—"
        stats_str = (f"{st['n']} bursts  |  width: {w_str}  |  "
                     f"IBI: {ibi_str}  |  width CV: {cv_str}")
    else:
        stats_str = "0 bursts in 10 min"

    r_ax.set_title(
        f"{label}  —  {sd.N} units  —  {stats_str}",
        loc="left", fontsize=9, fontweight="bold", pad=3,
    )
    if i < n - 1:
        plt.setp(p_ax.get_xticklabels(), visible=False)
        p_ax.set_xlabel("")

fig.suptitle(
    "ConstraintExp4  —  H9SynGFP MO  —  Progression  —  First 10 min  —  100 ms burst detection",
    fontsize=13, fontweight="bold", y=1.002,
)

save_path = os.path.join(OUT_DIR, "ConstraintExp4_rasters_10min.png")
fig.savefig(save_path, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"\nSaved → {save_path}")
