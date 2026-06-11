"""
2310i D40 comparison — full duration raster for both SCH+Halo candidates.
UUID: 2026-04-18-e-KOLF21J_MO_control_sch_halo_dopamine_04272026
Output: ~/Desktop/Greg/Organized Recording Raster Plots/2310i_D40_comparison.png
"""

import os, sys, shutil, subprocess, tempfile, warnings
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from spikelab.data_loaders import load_spikedata_from_pickle
from spikelab.spikedata.plot_utils import plot_recording

UUID     = "2026-04-18-e-KOLF21J_MO_control_sch_halo_dopamine_04272026"
S3_PKL   = f"s3://braingeneers/ephys/{UUID}/derived/ks2SpikeLab"
ENDPOINT = "https://s3.braingeneers.gi.ucsc.edu"

OUT_DIR  = "/home/sharf-lab/Desktop/Greg/Organized Recording Raster Plots"
os.makedirs(OUT_DIR, exist_ok=True)

RECORDINGS = [
    ("D40 — 24hr_control_newconfig  (335 units / 618 s)",
     "2310i_KOLF21J_MO_D40_24hr_control_newconfig_04232026"),
    ("D40 — sch_halo  (198 units / 1803 s)",
     "2310i_KOLF21J_MO_D40_sch_halo_04232026"),
]


def load_from_pkl(stem):
    tmp = tempfile.mkdtemp()
    try:
        local_pkl = os.path.join(tmp, "spikedata.pkl")
        r = subprocess.run(
            ["aws", "s3", "cp",
             f"{S3_PKL}/{stem}/sorted_spikedata_curated.pkl",
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


sds, burst_data = [], []

for label, stem in RECORDINGS:
    print(f"\n{'─'*60}")
    print(f"  [{label}]")
    try:
        sd = load_from_pkl(stem)
        print(f"  units: {sd.N},  duration: {sd.length/1000:.1f} s")
        tburst, edges, _ = sd.get_bursts(
            1.5, 200, 0.3, square_width=100, gauss_sigma=100,
        )
        print(f"  total bursts: {len(tburst)}")
        sds.append(sd)
        burst_data.append((tburst, edges))
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        sds.append(None)
        burst_data.append((None, None))


n        = len(RECORDINGS)
fig      = plt.figure(figsize=(20, n * 4.5))
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
    r_cax.axis("off")
    p_cax.axis("off")
    axes_per_rec.append([(r_ax, r_cax), (p_ax, p_cax)])

for i, (label, stem) in enumerate(RECORDINGS):
    sd            = sds[i]
    tburst, edges = burst_data[i]
    r_ax, _       = axes_per_rec[i][0]
    p_ax, _       = axes_per_rec[i][1]

    if sd is None:
        r_ax.text(0.5, 0.5, f"load failed", transform=r_ax.transAxes,
                  ha="center", va="center", color="red", fontsize=11)
        p_ax.axis("off")
        continue

    # full duration
    plot_recording(
        sd,
        show_raster=True,
        show_pop_rate=True,
        burst_times=tburst,
        burst_edges=edges,
        time_range=(0, sd.length),
        raster_bin_size_ms=1.0,
        pop_rate_params={"square_width": 100, "gauss_sigma": 100},
        axes=axes_per_rec[i],
        font_size=9,
        show=False,
        save_path=None,
    )

    r_ax.set_title(
        f"{label}  —  {sd.N} units  —  {sd.length/1000:.1f} s  —  {len(tburst)} total bursts",
        loc="left", fontsize=10, fontweight="bold", pad=4,
    )

    if i < n - 1:
        plt.setp(p_ax.get_xticklabels(), visible=False)
        p_ax.set_xlabel("")

fig.suptitle(
    "MEA 2310i  —  D40 SCH23390+Halo candidates  —  Full duration comparison",
    fontsize=13, fontweight="bold", y=1.01,
)

save_path = os.path.join(OUT_DIR, "2310i_D40_comparison.png")
fig.savefig(save_path, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"\nSaved → {save_path}")
