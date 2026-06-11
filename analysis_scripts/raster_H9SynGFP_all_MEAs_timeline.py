"""
Per-MEA timeline raster figures — all recordings, 5 min each, stacked chronologically.
UUID: 2025-12-20-e-H9SynGFP_Midbrain_control_baseline_haloperidol_rotenone
Output: /home/sharf-lab/Desktop/Greg/  —  one PNG per MEA
"""

import os, sys, shutil, subprocess, tempfile, zipfile, warnings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from spikelab.data_loaders import load_spikedata_from_kilosort
from spikelab.spikedata.plot_utils import plot_recording

UUID     = "2025-12-20-e-H9SynGFP_Midbrain_control_baseline_haloperidol_rotenone"
S3_BASE  = f"s3://braingeneers/ephys/{UUID}/derived/kilosort2"
ENDPOINT = "https://s3.braingeneers.gi.ucsc.edu"
FS_HZ    = 20000.0
WINDOW_MS = 300_000
OUT_DIR  = "/home/sharf-lab/Desktop/Greg"
os.makedirs(OUT_DIR, exist_ok=True)

# All MEAs and their recordings in chronological order
# (label, recording_stem)
MEAS = {
    "21965f": [
        ("D37 baseline",               "21965f_MO_H9SynGFP_D37_12192025"),
        ("D37 haloperidol",            "21965f_MO_H9SynGFP_D37_haloperidol_12192025"),
        ("D38 halo24hr baseline",      "21965f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025"),
    ],
    "22187f": [
        ("D37 baseline",               "22187f_MO_H9SynGFP_D37_12192025"),
    ],
    "23124f": [
        ("D37 baseline",               "23124f_MO_H9SynGFP_D37_12192025"),
        ("D38 rotenone 24hr",          "23124f_MO_H9SynGFP_D38_rotenone24hr_12202025"),
    ],
    "23137f": [
        ("D33 control",                "23137f_MO_H9SynGFP_D33_Control_12152025"),
        ("D34 control",                "23137f_MO_H9SynGFP_D34_Control_12162025"),
        ("D35 control",                "23137f_MO_H9SynGFP_D35_Control_12172025"),
        ("D36 control",                "23137f_MO_H9SynGFP_D36_Control_12182025"),
        ("D37 control",                "23137f_MO_H9SynGFP_D37_Control_12192025"),
        ("D38 control",                "23137f_MO_H9SynGFP_D38_Control_12202025"),
    ],
    "23156f": [
        ("D37 baseline",               "23156f_MO_H9SynGFP_D37_12192025"),
        ("D37 haloperidol",            "23156f_MO_H9SynGFP_D37_haloperidol_12192025"),
        ("D38 halo24hr baseline",      "23156f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025"),
    ],
    "23192f": [
        ("D37 baseline",               "23192f_MO_H9SynGFP_D37_12192025"),
        ("D38 rotenone 24hr",          "23192f_MO_H9SynGFP_D38_rotenone24hr_12202025"),
    ],
    "23198f": [
        ("D37 baseline",               "23198f_MO_H9SynGFP_D37_12192025"),
        ("D37 haloperidol",            "23198f_MO_H9SynGFP_D37_haloperidol_12192025"),
        ("D38 halo24hr baseline",      "23198f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025"),
    ],
    "23206f": [
        ("D37 baseline",               "23206f_MO_H9SynGFP_D37_12192025"),
        ("D37 haloperidol",            "23206f_MO_H9SynGFP_D37_haloperidol_12192025"),
        ("D38 halo24hr baseline",      "23206f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025"),
    ],
    "23215f": [
        ("D37 baseline",               "23215f_MO_H9SynGFP_D37_12192025"),
        ("D37 haloperidol",            "23215f_MO_H9SynGFP_D37_haloperidol_12192025"),
        ("D38 halo24hr baseline",      "23215f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025"),
    ],
    "24500f": [
        ("D37 baseline",               "24500f_MO_H9SynGFP_D37_12192025"),
        ("D37 haloperidol",            "24500f_MO_H9SynGFP_D37_haloperidol_12192025"),
        ("D38 halo24hr baseline",      "24500f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025"),
    ],
    "24655f": [
        ("D37 baseline",               "24655f_MO_H9SynGFP_D37_12192025"),
        ("D37 haloperidol",            "24655f_MO_H9SynGFP_D37_haloperidol_12192025"),
        ("D38 halo24hr baseline",      "24655f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025"),
    ],
    "25168f": [
        ("D37 baseline",               "25168f_MO_H9SynGFP_D37_12192025"),
        ("D37 haloperidol",            "25168f_MO_H9SynGFP_D37_haloperidol_12192025"),
        ("D38 halo24hr baseline",      "25168f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025"),
    ],
}


def download_s3(s3_url, local_path):
    r = subprocess.run(
        ["aws", "s3", "cp", s3_url, local_path, "--endpoint-url", ENDPOINT],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())


def load_recording(rec_stem):
    """Download phy.zip, extract, load SpikeData. Returns sd or raises."""
    tmp = tempfile.mkdtemp()
    try:
        zip_path = os.path.join(tmp, "phy.zip")
        download_s3(f"{S3_BASE}/{rec_stem}_phy.zip", zip_path)
        phy_dir = os.path.join(tmp, "phy")
        os.makedirs(phy_dir)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(phy_dir)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sd = load_spikedata_from_kilosort(
                phy_dir, fs_Hz=FS_HZ,
                cluster_info_tsv="cluster_group.tsv",
                include_noise=False,
            )
        return sd
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def make_mea_figure(mea_id, recordings, sds, burst_data):
    """Build and save a stacked timeline figure for one MEA."""
    n = len(recordings)
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

    for i, (label, rec_stem) in enumerate(recordings):
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
        f"MEA {mea_id}  —  H9SynGFP Midbrain  —  first 5 min  —  100 ms burst detection",
        fontsize=13, fontweight="bold", y=1.005,
    )

    save_path = os.path.join(OUT_DIR, f"{mea_id}_progression_5min.png")
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return save_path


# ── Main loop ─────────────────────────────────────────────────────────────────
for mea_id, recordings in MEAS.items():
    print(f"\n{'='*60}")
    print(f"MEA: {mea_id}  ({len(recordings)} recording(s))")

    sds = []
    burst_data = []

    for label, rec_stem in recordings:
        print(f"  [{label}]  {rec_stem}")
        try:
            print(f"    loading ...")
            sd = load_recording(rec_stem)
            print(f"    units: {sd.N},  duration: {sd.length/1000:.1f} s")
            tburst, edges, _ = sd.get_bursts(
                1.5, 200, 0.3, square_width=100, gauss_sigma=100,
            )
            n5 = int((tburst < WINDOW_MS).sum())
            print(f"    bursts 5 min: {n5} / {len(tburst)}")
            sds.append(sd)
            burst_data.append((tburst, edges))
        except Exception as exc:
            print(f"    ERROR: {exc}", file=sys.stderr)
            sds.append(None)
            burst_data.append((None, None))

    print(f"  building figure ...")
    path = make_mea_figure(mea_id, recordings, sds, burst_data)
    print(f"  saved -> {path}")

print(f"\nAll done. Figures in {OUT_DIR}/")
