"""
Combined 23215h + 23125h progression — first 10 min per recording.
UUID: 2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH
Output: ~/Desktop/Greg/Organized Recording Raster Plots/23215h_23125h_combined_10min.png
"""

import os, sys, shutil, subprocess, tempfile, zipfile, warnings
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from spikelab.data_loaders import load_spikedata_from_pickle, load_spikedata_from_kilosort
from spikelab.spikedata.plot_utils import plot_recording

ENDPOINT  = "https://s3.braingeneers.gi.ucsc.edu"
UUID      = "2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH"
S3_PKL    = f"s3://braingeneers/ephys/{UUID}/derived/ks2SpikeLab"
S3_KS2    = f"s3://braingeneers/ephys/{UUID}/derived/kilosort2"
FS_HZ     = 20000.0
WINDOW_MS = 600_000  # 10 min

OUT_DIR = "/home/sharf-lab/Desktop/Greg/Organized Recording Raster Plots"
os.makedirs(OUT_DIR, exist_ok=True)

# (label, stem, source)
RECORDINGS = [
    ("23215h — D34 Baseline (02/24/2026)",
     "23215h_MO_H9SynGFP_D34_02242026",                                          "phy"),
    ("23215h — D34 Dopamine (02/24/2026)",
     "23215h_MO_H9SynGFP_D34_Dopamine_02242026",                                 "pkl"),
    ("23125h — D35 Dopamine+Halo 24hr (02/25/2026)",
     "23125h_MO_H9SynGFP_D35_Dopamine_haloperidol_24hr_02252026",                "pkl"),
    ("23125h — D35 Dopamine+Halo 24hr (Connected) (02/25/2026)",
     "23125h_MO_H9SynGFP_D35_Dopamine_haloperidol_24hr_connectedconfig_02252026", "pkl"),
]


def load_from_pkl(stem):
    tmp = tempfile.mkdtemp()
    try:
        local_pkl = os.path.join(tmp, "spikedata.pkl")
        r = subprocess.run(
            ["aws", "s3", "cp", f"{S3_PKL}/{stem}/sorted_spikedata_curated.pkl",
             local_pkl, "--endpoint-url", ENDPOINT],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return load_spikedata_from_pickle(local_pkl)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def load_from_phy(stem):
    tmp = tempfile.mkdtemp()
    try:
        zip_path = os.path.join(tmp, "phy.zip")
        r = subprocess.run(
            ["aws", "s3", "cp", f"{S3_KS2}/{stem}_phy.zip", zip_path,
             "--endpoint-url", ENDPOINT],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        phy_dir = os.path.join(tmp, "phy")
        os.makedirs(phy_dir)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(phy_dir)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return load_spikedata_from_kilosort(
                phy_dir, fs_Hz=FS_HZ,
                cluster_info_tsv="cluster_group.tsv",
                include_noise=False,
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def burst_stats(tburst, edges, window_ms):
    if tburst is None or len(tburst) == 0:
        return {"n": 0, "width_mean": np.nan, "width_std": np.nan,
                "ibi_mean": np.nan, "ibi_std": np.nan, "width_cv": np.nan}
    mask   = tburst < window_ms
    tb_w, ed_w = tburst[mask], edges[mask]
    n = len(tb_w)
    if n == 0:
        return {"n": 0, "width_mean": np.nan, "width_std": np.nan,
                "ibi_mean": np.nan, "ibi_std": np.nan, "width_cv": np.nan}
    widths = ed_w[:, 1] - ed_w[:, 0]
    ibis   = np.diff(tb_w) / 1000.0 if n > 1 else np.array([np.nan])
    wm, ws = float(np.mean(widths)), float(np.std(widths))
    return {"n": n, "width_mean": wm, "width_std": ws,
            "ibi_mean": float(np.nanmean(ibis)), "ibi_std": float(np.nanstd(ibis)),
            "width_cv": ws / wm if wm > 0 else np.nan}


sds, burst_data, stats_list = [], [], []

for label, stem, source in RECORDINGS:
    print(f"\n{'─'*60}")
    print(f"  [{label}]")
    try:
        sd = load_from_pkl(stem) if source == "pkl" else load_from_phy(stem)
        print(f"  units: {sd.N},  duration: {sd.length/1000:.1f} s")
        tburst, edges, _ = sd.get_bursts(1.5, 200, 0.3, square_width=100, gauss_sigma=100)
        st  = burst_stats(tburst, edges, WINDOW_MS)
        n10 = int((tburst < WINDOW_MS).sum())
        print(f"  bursts in 10 min: {n10} / {len(tburst)}")
        sds.append(sd); burst_data.append((tburst, edges)); stats_list.append(st)
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        sds.append(None); burst_data.append((None, None)); stats_list.append(None)


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

for i, (label, stem, source) in enumerate(RECORDINGS):
    sd            = sds[i]
    tburst, edges = burst_data[i]
    st            = stats_list[i]
    r_ax, _       = axes_per_rec[i][0]
    p_ax, _       = axes_per_rec[i][1]

    if sd is None:
        r_ax.text(0.5, 0.5, "Load failed", transform=r_ax.transAxes,
                  ha="center", va="center", color="red", fontsize=10)
        p_ax.axis("off")
        continue

    t_end = min(WINDOW_MS, sd.length)
    plot_recording(sd, show_raster=True, show_pop_rate=True,
                   burst_times=tburst, burst_edges=edges,
                   time_range=(0, t_end), raster_bin_size_ms=1.0,
                   pop_rate_params={"square_width": 100, "gauss_sigma": 100},
                   axes=axes_per_rec[i], font_size=9, show=False, save_path=None)

    if st and st["n"] > 0:
        w_str   = f"{st['width_mean']:.0f} ± {st['width_std']:.0f} ms" if not np.isnan(st['width_mean']) else "—"
        ibi_str = f"{st['ibi_mean']:.2f} ± {st['ibi_std']:.2f} s" if not np.isnan(st['ibi_mean']) else "—"
        cv_str  = f"{st['width_cv']:.2f}" if not np.isnan(st['width_cv']) else "—"
        stats_str = f"{st['n']} bursts  |  width: {w_str}  |  IBI: {ibi_str}  |  width CV: {cv_str}"
    else:
        stats_str = "0 bursts in window"

    r_ax.set_title(f"{label}  —  {sd.N} units  —  {stats_str}",
                   loc="left", fontsize=9, fontweight="bold", pad=3)

    if i < n - 1:
        plt.setp(p_ax.get_xticklabels(), visible=False)
        p_ax.set_xlabel("")

fig.suptitle(
    "MEA 23215h + 23125h  —  H9SynGFP MO  —  First 10 min  —  100 ms burst detection",
    fontsize=13, fontweight="bold", y=1.01,
)

save_path = os.path.join(OUT_DIR, "23215h_23125h_combined_10min.png")
fig.savefig(save_path, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"\nSaved → {save_path}")
