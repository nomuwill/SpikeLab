"""
KOLF21J MO control progression — 2310i, 2312i, 23206i — D30 through D40.
UUID: 2026-04-18-e-KOLF21J_MO_control_sch_halo_dopamine_04272026
Outputs: /home/sharf-lab/Desktop/Greg/{mea_id}_Control_progression_5min.png
"""

import os, sys, shutil, subprocess, tempfile, warnings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from spikelab.data_loaders import load_spikedata_from_pickle
from spikelab.spikedata.plot_utils import plot_recording

UUID      = "2026-04-18-e-KOLF21J_MO_control_sch_halo_dopamine_04272026"
S3_BASE   = f"s3://braingeneers/ephys/{UUID}/derived/ks2SpikeLab"
ENDPOINT  = "https://s3.braingeneers.gi.ucsc.edu"
WINDOW_MS = 300_000
OUT_DIR   = "/home/sharf-lab/Desktop/Greg"
os.makedirs(OUT_DIR, exist_ok=True)

MEAS = {
    "2310i": [
        ("D30",           "2310i_KOLF21J_MO_D30_control_04132026"),
        ("D31",           "2310i_KOLF21J_MO_D31_control_04142026"),
        ("D32",           "2310i_KOLF21J_MO_D32_control_04152026"),
        ("D33 connected", "2310i_KOLF21J_MO_D33_control_connectedconfig_04162026"),
        ("D33 new",       "2310i_KOLF21J_MO_D33_control_newconfig_04162026"),
        ("D34 connected", "2310i_KOLF21J_MO_D34_control_connectedconfig_04172026"),
        ("D34 new",       "2310i_KOLF21J_MO_D34_control_newconfig_04172026"),
        ("D35 connected", "2310i_KOLF21J_MO_D35_control_connectedconfig_04182026"),
        ("D35 new",       "2310i_KOLF21J_MO_D35_control_newconfig_04182026"),
        ("D36",           "2310i_KOLF21J_MO_D36_control_04192026"),
        ("D37",           "2310i_KOLF21J_MO_D37_control_04202026"),
        ("D38",           "2310i_KOLF21J_MO_D38_control_04212026"),
        ("D39",           "2310i_KOLF21J_MO_D39_control_04222026"),
        ("D40 ctrl 24hr", "2310i_KOLF21J_MO_D40_control_24hr_newconfig_04232026"),
    ],
    "2312i": [
        ("D32",           "2312i_KOLF21J_MO_D32_control_04152026"),
        ("D33 connected", "2312i_KOLF21J_MO_D33_control_connectedconfig_04162026"),
        ("D33 new",       "2312i_KOLF21J_MO_D33_control_newconfig_04162026"),
        ("D34 connected", "2312i_KOLF21J_MO_D34_control_connectedconfig_04172026"),
        ("D34 new",       "2312i_KOLF21J_MO_D34_control_newconfig_04172026"),
        ("D35 connected", "2312i_KOLF21J_MO_D35_control_connectedconfig_04182026"),
        ("D35 new",       "2312i_KOLF21J_MO_D35_control_newconfig_04182026"),
        ("D36",           "2312i_KOLF21J_MO_D36_control_04192026"),
        ("D37",           "2312i_KOLF21J_MO_D37_control_04202026"),
        ("D38",           "2312i_KOLF21J_MO_D38_control_04212026"),
        ("D39",           "2312i_KOLF21J_MO_D39_control_04222026"),
        ("D40 ctrl 24hr", "2312i_KOLF21J_MO_D40_control_24hr_newconfig_04232026"),
    ],
    "23206i": [
        ("D30",           "23206i_KOLF21J_MO_D30_04132026"),
        ("D31",           "23206i_KOLF21J_MO_D31_control_04142026"),
        ("D32",           "23206i_KOLF21J_MO_D32_control_04152026"),
        ("D33 connected", "23206i_KOLF21J_MO_D33_control_connectedconfig_04162026"),
        ("D33 new",       "23206i_KOLF21J_MO_D33_control_newconfig_04162026"),
        ("D34 connected", "23206i_KOLF21J_MO_D34_control_connectedconfig_04172026"),
        ("D34 new",       "23206i_KOLF21J_MO_D34_control_newconfig_04172026"),
        ("D35 connected", "23206i_KOLF21J_MO_D35_control_connectedconfig_04182026"),
        ("D35 new",       "23206i_KOLF21J_MO_D35_control_newconfig_04182026"),
        ("D36",           "23206i_KOLF21J_MO_D36_control_04192026"),
        ("D37",           "23206i_KOLF21J_MO_D37_control_04202026"),
        ("D38",           "23206i_KOLF21J_MO_D38_control_04212026"),
        ("D39",           "23206i_KOLF21J_MO_D39_control_04222026"),
        ("D40 ctrl 24hr", "23206i_KOLF21J_MO_D40_control_24hr_newconfig_04232026"),
    ],
}


def load_recording(stem):
    pkl_url = f"{S3_BASE}/{stem}/sorted_spikedata_curated.pkl"
    tmp = tempfile.mkdtemp()
    try:
        local_pkl = os.path.join(tmp, "spikedata.pkl")
        r = subprocess.run(
            ["aws", "s3", "cp", pkl_url, local_pkl, "--endpoint-url", ENDPOINT],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sd = load_spikedata_from_pickle(local_pkl)
        return sd
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


for mea_id, recordings in MEAS.items():
    print(f"\n{'═'*60}")
    print(f"MEA: {mea_id}")

    sds = []
    burst_data = []

    for label, stem in recordings:
        print(f"\n{'─'*55}")
        print(f"  {label}: {stem}")
        try:
            print("  downloading & loading ...")
            sd = load_recording(stem)
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

    # Build figure
    n_recs = len(recordings)
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

    for i, (label, stem) in enumerate(recordings):
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
        f"{mea_id}  KOLF21J MO  —  Control  —  D30–D40  (first 5 min, 100 ms burst detection)",
        fontsize=13, fontweight="bold", y=1.005,
    )

    save_path = os.path.join(OUT_DIR, f"{mea_id}_Control_progression_5min.png")
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\nSaved → {save_path}")
