"""
Timeline raster figure for 23137f D33–D38 Control (6 recordings on one page).
UUID: 2025-12-20-e-H9SynGFP_Midbrain_control_baseline_haloperidol_rotenone
Layout: 6 stacked groups (raster + pop rate), first 5 min, 100 ms burst detection.
Output: /home/sharf-lab/Desktop/Greg/
"""

import os, sys, shutil, subprocess, tempfile, zipfile, warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from spikelab.data_loaders import load_spikedata_from_kilosort
from spikelab.spikedata.plot_utils import plot_recording

UUID = "2025-12-20-e-H9SynGFP_Midbrain_control_baseline_haloperidol_rotenone"
S3_BASE = f"s3://braingeneers/ephys/{UUID}/derived/kilosort2"
S3_ENDPOINT = "https://s3.braingeneers.gi.ucsc.edu"

FS_HZ = 20000.0
WINDOW_MS = 300_000       # 5 minutes

OUT_DIR = "/home/sharf-lab/Desktop/Greg"
os.makedirs(OUT_DIR, exist_ok=True)

RECORDINGS = [
    ("D33", "23137f_MO_H9SynGFP_D33_Control_12152025"),
    ("D34", "23137f_MO_H9SynGFP_D34_Control_12162025"),
    ("D35", "23137f_MO_H9SynGFP_D35_Control_12172025"),
    ("D36", "23137f_MO_H9SynGFP_D36_Control_12182025"),
    ("D37", "23137f_MO_H9SynGFP_D37_Control_12192025"),
    ("D38", "23137f_MO_H9SynGFP_D38_Control_12202025"),
]


def download_s3(s3_url, local_path):
    r = subprocess.run(
        ["aws", "s3", "cp", s3_url, local_path, "--endpoint-url", S3_ENDPOINT],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr)


# ── 1. Download, extract, load, detect bursts ────────────────────────────────
sds = []
burst_data = []

for day, rec in RECORDINGS:
    print(f"\n{'─'*55}")
    print(f"{day}: {rec}")
    tmp = tempfile.mkdtemp()
    try:
        zip_path = os.path.join(tmp, "phy.zip")
        print("  downloading phy.zip ...")
        download_s3(f"{S3_BASE}/{rec}_phy.zip", zip_path)

        phy_dir = os.path.join(tmp, "phy")
        os.makedirs(phy_dir)
        print("  extracting ...")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(phy_dir)

        print("  loading ...")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sd = load_spikedata_from_kilosort(
                phy_dir,
                fs_Hz=FS_HZ,
                cluster_info_tsv="cluster_group.tsv",
                include_noise=False,
            )
        print(f"  units: {sd.N},  duration: {sd.length / 1000:.1f} s")

        print("  detecting bursts ...")
        tburst, edges, _ = sd.get_bursts(
            1.5, 200, 0.3,
            square_width=100, gauss_sigma=100,
        )
        n5 = int((tburst < WINDOW_MS).sum())
        print(f"  bursts in 5 min: {n5} / {len(tburst)} total")

        sds.append(sd)
        burst_data.append((tburst, edges))

    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sds.append(None)
        burst_data.append((None, None))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 2. Build composite figure ─────────────────────────────────────────────────
n_recs = len(RECORDINGS)

# Outer GridSpec: one row per recording, with visible separation between days
fig = plt.figure(figsize=(18, 32))
outer_gs = gridspec.GridSpec(n_recs, 1, figure=fig, hspace=0.40)

# Inner GridSpec per recording: raster (height 3) + pop_rate (height 1), plus cbar column
axes_per_rec = []
for i in range(n_recs):
    inner = gridspec.GridSpecFromSubplotSpec(
        2, 2,
        subplot_spec=outer_gs[i],
        height_ratios=[3, 1],
        width_ratios=[1, 0.015],
        hspace=0.04,
        wspace=0.02,
    )
    r_ax  = fig.add_subplot(inner[0, 0])
    r_cax = fig.add_subplot(inner[0, 1])
    p_ax  = fig.add_subplot(inner[1, 0], sharex=r_ax)
    p_cax = fig.add_subplot(inner[1, 1])
    r_cax.axis("off")
    p_cax.axis("off")
    axes_per_rec.append([(r_ax, r_cax), (p_ax, p_cax)])


# ── 3. Plot each recording into its pre-allocated axes ────────────────────────
for i, (day, rec) in enumerate(RECORDINGS):
    sd = sds[i]
    tburst, edges = burst_data[i]

    if sd is None:
        r_ax, _ = axes_per_rec[i][0]
        r_ax.text(0.5, 0.5, f"{day}: load failed", transform=r_ax.transAxes,
                  ha="center", va="center", color="red")
        continue

    axes_arg = axes_per_rec[i]

    plot_recording(
        sd,
        show_raster=True,
        show_pop_rate=True,
        burst_times=tburst,
        burst_edges=edges,
        time_range=(0, WINDOW_MS),
        raster_bin_size_ms=1.0,
        pop_rate_params={"square_width": 100, "gauss_sigma": 100},
        axes=axes_arg,
        font_size=10,
        show=False,
        save_path=None,
    )

    r_ax, _ = axes_arg[0]
    p_ax, _ = axes_arg[1]

    # Day label on the raster panel
    r_ax.set_title(
        f"{day}  —  {sd.N} units",
        loc="left", fontsize=12, fontweight="bold", pad=4,
    )

    # Hide x-axis labels/ticks on all but the last recording
    if i < n_recs - 1:
        plt.setp(p_ax.get_xticklabels(), visible=False)
        p_ax.set_xlabel("")


# ── 4. Overall title and save ─────────────────────────────────────────────────
fig.suptitle(
    "23137f  H9SynGFP  —  Control baseline  —  D33–D38  (first 5 min, 100 ms burst detection)",
    fontsize=13, fontweight="bold", y=1.005,
)

save_path = os.path.join(OUT_DIR, "23137f_Control_D33-D38_timeline_5min.png")
fig.savefig(save_path, dpi=150, bbox_inches="tight")
plt.close(fig)

print(f"\nSaved → {save_path}")
