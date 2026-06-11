"""
2310i KOLF2.1J MO progression raster plots — first 5 min per recording.
UUID: 2026-04-18-e-KOLF21J_MO_control_sch_halo_dopamine_04272026
Output: ~/Desktop/Greg/Organized Recording Raster Plots/2310i_progression.png
"""

import os, sys, shutil, subprocess, tempfile, warnings
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from spikelab.data_loaders import load_spikedata_from_pickle
from spikelab.spikedata.plot_utils import plot_recording

UUID      = "2026-04-18-e-KOLF21J_MO_control_sch_halo_dopamine_04272026"
S3_PKL    = f"s3://braingeneers/ephys/{UUID}/derived/ks2SpikeLab"
ENDPOINT  = "https://s3.braingeneers.gi.ucsc.edu"
WINDOW_MS = 300_000  # 5 min

OUT_DIR = "/home/sharf-lab/Desktop/Greg/Organized Recording Raster Plots"
os.makedirs(OUT_DIR, exist_ok=True)

# (label, stem, date)
RECORDINGS = [
    ("D30 — Control",                   "2310i_KOLF21J_MO_D30_control_04132026",                       "04/13/2026"),
    ("D31 — Control",                   "2310i_KOLF21J_MO_D31_control_04142026",                       "04/14/2026"),
    ("D32 — Control",                   "2310i_KOLF21J_MO_D32_control_04152026",                       "04/15/2026"),
    ("D33 — Control (Connected)",       "2310i_KOLF21J_MO_D33_control_connectedconfig_04162026",       "04/16/2026"),
    ("D33 — Control (New)",             "2310i_KOLF21J_MO_D33_control_newconfig_04162026",             "04/16/2026"),
    ("D34 — Control (Connected)",       "2310i_KOLF21J_MO_D34_control_connectedconfig_04172026",       "04/17/2026"),
    ("D34 — Control (New)",             "2310i_KOLF21J_MO_D34_control_newconfig_04172026",             "04/17/2026"),
    ("D35 — Control (Connected)",       "2310i_KOLF21J_MO_D35_control_connectedconfig_04182026",       "04/18/2026"),
    ("D35 — Control (New)",             "2310i_KOLF21J_MO_D35_control_newconfig_04182026",             "04/18/2026"),
    ("D36 — Control",                   "2310i_KOLF21J_MO_D36_control_04192026",                       "04/19/2026"),
    ("D37 — Control",                   "2310i_KOLF21J_MO_D37_control_04202026",                       "04/20/2026"),
    ("D38 — Control",                   "2310i_KOLF21J_MO_D38_control_04212026",                       "04/21/2026"),
    ("D39 — Control",                   "2310i_KOLF21J_MO_D39_control_04222026",                       "04/22/2026"),
    ("D40 — Control 24hr post (Con.)",  "2310i_KOLF21J_MO_D40_control_24hr_connectedconfig_04232026",  "04/23/2026"),
    ("D40 — Control 24hr post (New)",   "2310i_KOLF21J_MO_D40_control_24hr_newconfig_04232026",        "04/23/2026"),
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


def burst_stats(tburst, edges, window_ms):
    """Return burst stats dict for bursts within window_ms."""
    if tburst is None or len(tburst) == 0:
        return {"n": 0, "width_mean": np.nan, "width_std": np.nan,
                "ibi_mean": np.nan, "ibi_std": np.nan, "width_cv": np.nan}
    mask = tburst < window_ms
    tb_w = tburst[mask]
    ed_w = edges[mask]
    n = len(tb_w)
    if n == 0:
        return {"n": 0, "width_mean": np.nan, "width_std": np.nan,
                "ibi_mean": np.nan, "ibi_std": np.nan, "width_cv": np.nan}
    widths = ed_w[:, 1] - ed_w[:, 0]          # ms
    ibis   = np.diff(tb_w) / 1000.0 if n > 1 else np.array([np.nan])  # s
    wm, ws = float(np.mean(widths)), float(np.std(widths))
    return {
        "n":          n,
        "width_mean": wm,
        "width_std":  ws,
        "ibi_mean":   float(np.nanmean(ibis)),
        "ibi_std":    float(np.nanstd(ibis)),
        "width_cv":   ws / wm if wm > 0 else np.nan,
    }


# ── load all recordings ────────────────────────────────────────────────────────
sds, burst_data, stats_list = [], [], []

for label, stem, date in RECORDINGS:
    print(f"\n{'─'*60}")
    print(f"  [{label}]  {stem}")
    try:
        print("  loading ...")
        sd = load_from_pkl(stem)
        print(f"  units: {sd.N},  duration: {sd.length/1000:.1f} s")
        tburst, edges, _ = sd.get_bursts(
            1.5, 200, 0.3, square_width=100, gauss_sigma=100,
        )
        st = burst_stats(tburst, edges, WINDOW_MS)
        n5 = int((tburst < WINDOW_MS).sum())
        print(f"  bursts in 5 min: {n5} / {len(tburst)}")
        sds.append(sd)
        burst_data.append((tburst, edges))
        stats_list.append(st)
    except Exception as exc:
        print(f"  ERROR: {exc}", file=sys.stderr)
        sds.append(None)
        burst_data.append((None, None))
        stats_list.append(None)


# ── figure layout ─────────────────────────────────────────────────────────────
n = len(RECORDINGS)
rec_h = 3.5           # inches per recording panel
fig_h = n * rec_h
fig   = plt.figure(figsize=(20, fig_h))
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


for i, (label, stem, date) in enumerate(RECORDINGS):
    sd      = sds[i]
    tburst, edges = burst_data[i]
    st      = stats_list[i]
    r_ax, _ = axes_per_rec[i][0]
    p_ax, _ = axes_per_rec[i][1]

    if sd is None:
        r_ax.text(0.5, 0.5, f"{label}: load failed",
                  transform=r_ax.transAxes,
                  ha="center", va="center", color="red", fontsize=10)
        p_ax.axis("off")
        continue

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
        font_size=9,
        show=False,
        save_path=None,
    )

    # Build stats string
    if st and st["n"] > 0:
        w_str = (f"{st['width_mean']:.0f} ± {st['width_std']:.0f} ms"
                 if not np.isnan(st['width_mean']) else "—")
        ibi_str = (f"{st['ibi_mean']:.2f} ± {st['ibi_std']:.2f} s"
                   if not np.isnan(st['ibi_mean']) else "—")
        cv_str  = f"{st['width_cv']:.2f}" if not np.isnan(st['width_cv']) else "—"
        stats_str = (f"{st['n']} bursts  |  width: {w_str}  |  "
                     f"IBI: {ibi_str}  |  width CV: {cv_str}")
    else:
        stats_str = "0 bursts in 5 min"

    r_ax.set_title(
        f"{label}  ({date})  —  {sd.N} units  —  {stats_str}",
        loc="left", fontsize=9, fontweight="bold", pad=3,
    )

    if i < n - 1:
        plt.setp(p_ax.get_xticklabels(), visible=False)
        p_ax.set_xlabel("")

fig.suptitle(
    "MEA 2310i  —  KOLF2.1J MO  —  Control Progression  —  First 5 min  —  100 ms burst detection",
    fontsize=13, fontweight="bold", y=1.002,
)

save_path = os.path.join(OUT_DIR, "2310i_control_progression.png")
fig.savefig(save_path, dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"\nSaved → {save_path}")
