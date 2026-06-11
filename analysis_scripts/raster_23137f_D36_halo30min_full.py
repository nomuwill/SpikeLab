"""
23137f D36 haloperidol 30min — full recording in 6 × 5-min windows.
UUID: 2025-12-18-e-MO_H9SynGFP_D36_control_baseline_haloperidol
Output: /home/sharf-lab/Desktop/Greg/23137f_D36_halo30min_full_6x5min.png
"""

import os, shutil, subprocess, tempfile, zipfile, warnings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from spikelab.data_loaders import load_spikedata_from_kilosort
from spikelab.spikedata.plot_utils import plot_recording

UUID     = "2025-12-18-e-MO_H9SynGFP_D36_control_baseline_haloperidol"
REC      = "23137f_MO_H9SynGFP_D36_haloperidol_30min_12182025"
S3_URL   = f"s3://braingeneers/ephys/{UUID}/derived/kilosort2/{REC}_phy.zip"
ENDPOINT = "https://s3.braingeneers.gi.ucsc.edu"
OUT      = f"/home/sharf-lab/Desktop/Greg/23137f_D40_halo30min_full_6x5min.png"
FS_HZ    = 20000.0
WIN_MS   = 300_000   # 5 min in ms

WINDOWS = [
    (i * WIN_MS, (i + 1) * WIN_MS, f"{i*5}–{(i+1)*5} min")
    for i in range(6)
]

# ── 1. Download, extract, load ────────────────────────────────────────────────
tmp = tempfile.mkdtemp()
try:
    zip_path = os.path.join(tmp, "phy.zip")
    print("Downloading ...")
    subprocess.run(
        ["aws", "s3", "cp", S3_URL, zip_path, "--endpoint-url", ENDPOINT],
        check=True,
    )
    phy_dir = os.path.join(tmp, "phy")
    os.makedirs(phy_dir)
    print("Extracting ...")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(phy_dir)

    print("Loading ...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sd = load_spikedata_from_kilosort(
            phy_dir, fs_Hz=FS_HZ,
            cluster_info_tsv="cluster_group.tsv",
            include_noise=False,
        )
    print(f"Units: {sd.N},  Duration: {sd.length / 1000:.1f} s")

    print("Detecting bursts (full recording) ...")
    tburst, edges, _ = sd.get_bursts(1.5, 200, 0.3, square_width=100, gauss_sigma=100)
    print(f"Total bursts: {len(tburst)}")
    for t0, t1, label in WINDOWS:
        n = int(((tburst >= t0) & (tburst < t1)).sum())
        print(f"  {label}: {n} bursts")

    # ── 2. Build figure ───────────────────────────────────────────────────────
    n = len(WINDOWS)
    fig = plt.figure(figsize=(18, n * 5.5))
    outer_gs = gridspec.GridSpec(n, 1, figure=fig, hspace=0.40)

    axes_per_win = []
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
        axes_per_win.append([(r_ax, r_cax), (p_ax, p_cax)])

    for i, (t0, t1, label) in enumerate(WINDOWS):
        r_ax, _ = axes_per_win[i][0]
        p_ax, _ = axes_per_win[i][1]

        n_bursts = int(((tburst >= t0) & (tburst < t1)).sum())

        plot_recording(
            sd,
            show_raster=True,
            show_pop_rate=True,
            burst_times=tburst,
            burst_edges=edges,
            time_range=(t0, t1),
            raster_bin_size_ms=1.0,
            pop_rate_params={"square_width": 100, "gauss_sigma": 100},
            axes=axes_per_win[i],
            font_size=10,
            show=False,
            save_path=None,
        )

        r_ax.set_title(
            f"{label}  —  {n_bursts} bursts",
            loc="left", fontsize=11, fontweight="bold", pad=4,
        )

        if i < n - 1:
            plt.setp(p_ax.get_xticklabels(), visible=False)
            p_ax.set_xlabel("")

    fig.suptitle(
        "23137f  H9SynGFP  —  D40 haloperidol 30 min  —  full recording (6 × 5 min, 100 ms burst detection)",
        fontsize=13, fontweight="bold", y=1.005,
    )

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fig.savefig(OUT, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\nSaved -> {OUT}")

finally:
    shutil.rmtree(tmp, ignore_errors=True)
