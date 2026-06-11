"""
24448 H9SynGFP MO Control progression — D42 through D47.
UUID: 2026-01-02-e-Midbrain_Control_Data
Output: /home/sharf-lab/Desktop/Greg/24448_Control_D42-D47_timeline_5min.png
"""

import os, sys, shutil, subprocess, tempfile, zipfile, warnings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from spikelab.data_loaders import load_spikedata_from_kilosort
from spikelab.spikedata.plot_utils import plot_recording

UUID     = "2026-01-02-e-Midbrain_Control_Data"
S3_BASE  = f"s3://braingeneers/ephys/{UUID}/derived/kilosort2"
ENDPOINT = "https://s3.braingeneers.gi.ucsc.edu"
FS_HZ    = 20000.0
WINDOW_MS = 300_000
OUT_DIR  = "/home/sharf-lab/Desktop/Greg"
os.makedirs(OUT_DIR, exist_ok=True)

RECORDINGS = [
    ("D42", "24448_D42MO_H9SynGFP_1142025"),
    ("D43", "24448_D43MO_H9SynGFP_1152025"),
    ("D44", "24448_D44MO_H9SynGFP_1162025"),
    ("D45", "24448_D45MO_H9SynGFP_1172025"),
    ("D46", "24448_D46MO_H9SynGFP_1182025"),
    ("D47", "24448_D47MO_H9SynGFP_112025"),
]


def download_s3(s3_url, local_path):
    r = subprocess.run(
        ["aws", "s3", "cp", s3_url, local_path, "--endpoint-url", ENDPOINT],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())


# ── 1. Load all recordings ─────────────────────────────────────────────────────
sds = []
burst_data = []

for label, rec_stem in RECORDINGS:
    print(f"\n{'─'*55}")
    print(f"{label}: {rec_stem}")
    tmp = tempfile.mkdtemp()
    try:
        zip_path = os.path.join(tmp, "phy.zip")
        print("  downloading ...")
        download_s3(f"{S3_BASE}/{rec_stem}_phy.zip", zip_path)
        phy_dir = os.path.join(tmp, "phy")
        os.makedirs(phy_dir)
        print("  extracting ...")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(phy_dir)
        print("  loading ...")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sd = load_spikedata_from_kilosort(
                phy_dir, fs_Hz=FS_HZ,
                cluster_info_tsv="cluster_group.tsv",
                include_noise=False,
            )
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── 2. Build figure ────────────────────────────────────────────────────────────
n_recs = len(RECORDINGS)
fig = plt.figure(figsize=(18, n_recs * 5.5))
outer_gs = gridspec.GridSpec(n_recs, 1, figure=fig, hspace=0.40)

axes_per_rec = []
for i in range(n_recs):
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

for i, (label, rec_stem) in enumerate(RECORDINGS):
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

    if i < n_recs - 1:
        plt.setp(p_ax.get_xticklabels(), visible=False)
        p_ax.set_xlabel("")

fig.suptitle(
    "24448  H9SynGFP MO  —  Control  —  D42–D47  (first 5 min, 100 ms burst detection)",
    fontsize=13, fontweight="bold", y=1.005,
)

save_path = os.path.join(OUT_DIR, "24448_Control_D42-D47_timeline_5min.png")
fig.savefig(save_path, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"\nSaved → {save_path}")
