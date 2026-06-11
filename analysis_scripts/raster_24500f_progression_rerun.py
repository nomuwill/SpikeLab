"""
24500f H9SynGFP MO progression — D37 baseline, D37 haloperidol, D38 halo24hr baseline.
UUID: 2025-12-20-e-H9SynGFP_Midbrain_control_baseline_haloperidol_rotenone
Output: /home/sharf-lab/Desktop/Greg/24500f_progression_5min.png
"""

import os, sys, shutil, subprocess, tempfile, zipfile, warnings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from spikelab.data_loaders import load_spikedata_from_kilosort, load_spikedata_from_pickle
from spikelab.spikedata.plot_utils import plot_recording

UUID      = "2025-12-20-e-H9SynGFP_Midbrain_control_baseline_haloperidol_rotenone"
S3_KS2    = f"s3://braingeneers/ephys/{UUID}/derived/kilosort2"
S3_PKL    = f"s3://braingeneers/ephys/{UUID}/derived/ks2SpikeLab"
ENDPOINT  = "https://s3.braingeneers.gi.ucsc.edu"
FS_HZ     = 20000.0
WINDOW_MS = 300_000
OUT_DIR   = "/home/sharf-lab/Desktop/Greg"
os.makedirs(OUT_DIR, exist_ok=True)

# (label, stem, source)  source="pkl" loads from ks2SpikeLab pkl; "phy" from kilosort2 zip
RECORDINGS = [
    ("D37 baseline",          "24500f_MO_H9SynGFP_D37_12192025",                    "pkl"),
    ("D37 haloperidol",       "24500f_MO_H9SynGFP_D37_haloperidol_12192025",        "phy"),
    ("D38 halo24hr baseline", "24500f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025", "phy"),
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


def load_recording(stem, source):
    return load_from_pkl(stem) if source == "pkl" else load_from_phy(stem)


sds = []
burst_data = []

for label, stem, source in RECORDINGS:
    print(f"\n{'─'*55}")
    print(f"  [{label}]  {stem}")
    try:
        print("  loading ...")
        sd = load_recording(stem, source)
        print(f"  units: {sd.N},  duration: {sd.length/1000:.1f} s")
        tburst, edges, _ = sd.get_bursts(
            1.5, 200, 0.3, square_width=100, gauss_sigma=100,
        )
        n5 = int((tburst < WINDOW_MS).sum())
        print(f"  bursts in 5 min: {n5} / {len(tburst)}")
        sds.append(sd)
        burst_data.append((tburst, edges))
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        sds.append(None)
        burst_data.append((None, None))

n = len(RECORDINGS)
fig_height = max(8, n * 5.5)
fig = plt.figure(figsize=(18, fig_height))
outer_gs = gridspec.GridSpec(n, 1, figure=fig, hspace=0.40)

axes_per_rec = []
for i in range(n):
    inner = gridspec.GridSpecFromSubplotSpec(
        2, 2, subplot_spec=outer_gs[i],
        height_ratios=[3, 1], width_ratios=[1, 0.015],
        hspace=0.04, wspace=0.02,
    )
    r_ax  = fig.add_subplot(inner[0, 0])
    r_cax = fig.add_subplot(inner[0, 1])
    p_ax  = fig.add_subplot(inner[1, 0], sharex=r_ax)
    p_cax = fig.add_subplot(inner[1, 1])
    r_cax.axis("off")
    p_cax.axis("off")
    axes_per_rec.append([(r_ax, r_cax), (p_ax, p_cax)])

for i, (label, stem, source) in enumerate(RECORDINGS):
    sd = sds[i]
    tburst, edges = burst_data[i]
    r_ax, _ = axes_per_rec[i][0]
    p_ax, _ = axes_per_rec[i][1]

    if sd is None:
        r_ax.text(0.5, 0.5, f"{label}: load failed",
                  transform=r_ax.transAxes,
                  ha="center", va="center", color="red", fontsize=11)
        p_ax.axis("off")
    else:
        plot_recording(
            sd,
            show_raster=True,
            show_pop_rate=True,
            burst_times=tburst,
            burst_edges=edges,
            time_range=(0, WINDOW_MS),
            raster_bin_size_ms=1.0,
            pop_rate_params={"square_width": 100, "gauss_sigma": 100},
            axes=axes_per_rec[i],
            font_size=10,
            show=False,
            save_path=None,
        )
        n5 = int((tburst < WINDOW_MS).sum()) if tburst is not None else 0
        r_ax.set_title(
            f"{label}  —  {sd.N} units  —  {n5} bursts / 5 min",
            loc="left", fontsize=11, fontweight="bold", pad=4,
        )

    if i < n - 1:
        plt.setp(p_ax.get_xticklabels(), visible=False)
        p_ax.set_xlabel("")

fig.suptitle(
    "MEA 24500f  —  H9SynGFP Midbrain  —  first 5 min  —  100 ms burst detection",
    fontsize=13, fontweight="bold", y=1.005,
)

save_path = os.path.join(OUT_DIR, "24500f_progression_5min.png")
fig.savefig(save_path, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"\nSaved → {save_path}")
