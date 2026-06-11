"""
Raster progression plots — first 10 min per recording, one figure per MEA.
Layout: recordings stacked vertically, each row = raster + pop-rate panel.
Burst detection uses 100 ms bins; stats (width, IBI, CV) appear in each title.
"""

import os, re, pickle, warnings
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

warnings.filterwarnings('ignore')

# ── Paths ──────────────────────────────────────────────────────────────────
KOLF_BASE = (
    "/home/sharf-lab/Desktop/Greg/"
    "2026-04-18-e-KOLF21J_MO_control_sch_halo_dopamine_04272026"
)
H9_BASE  = "/home/sharf-lab/Desktop/Analysis_shared/sort_workdir"
OUTPUT   = "/home/sharf-lab/Desktop/Greg/Organized Recording Raster Plots"
os.makedirs(OUTPUT, exist_ok=True)

DUR_MS = 600_000   # 10 minutes
BIN_MS = 100       # 100 ms bins for burst detection

# ── Data helpers ───────────────────────────────────────────────────────────

def load_sd(base, name):
    if base is None:
        return None
    p = os.path.join(base, name, "sorted_spikedata_curated.pkl")
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        return pickle.load(f)

def parse_info(name):
    """Return (day, condition, MM/DD/YYYY) parsed from recording folder name."""
    parts  = name.split("_")
    day_i  = next((i for i, p in enumerate(parts) if re.match(r"^D\d+$", p)), -1)
    date_i = next((i for i, p in enumerate(parts) if re.match(r"^\d{8}$", p)), -1)
    day    = parts[day_i] if day_i >= 0 else "?"
    if date_i >= 0:
        ds   = parts[date_i]
        date = f"{ds[:2]}/{ds[2:4]}/{ds[4:]}"
    else:
        date = "?"
    s = (day_i + 1) if day_i >= 0 else 0
    e = date_i if (date_i > s) else len(parts)
    suffix = parts[date_i + 1:] if (date_i >= 0 and date_i < len(parts) - 1) else []
    cond   = "_".join(parts[s:e] + suffix) or "recording"
    return day, cond, date

# ── Pop-rate & burst detection ─────────────────────────────────────────────

def pop_rate(train, N, end_ms, bin_ms=BIN_MS):
    """Population firing rate in Hz/unit using bin_ms bins."""
    n_bins = int(np.ceil(end_ms / bin_ms))
    counts = np.zeros(n_bins)
    for sp in train:
        idx = (np.asarray(sp) / bin_ms).astype(int)
        idx = idx[(idx >= 0) & (idx < n_bins)]
        np.add.at(counts, idx, 1)
    t = np.arange(n_bins) * bin_ms / 1000.0   # seconds
    r = counts / (N * bin_ms / 1000.0)         # Hz/unit
    return t, r

def detect_bursts(t, r, bin_ms=BIN_MS):
    """
    Detect bursts from population-rate trace using threshold crossing.
    Threshold = 15% of peak rate (floor 0.3 Hz/unit).
    Bursts < 500 ms apart are merged; bursts < 2 bins wide are dropped.
    Returns: starts_s, ends_s, peaks_s, widths_ms (array), ibis_s (array)
    """
    if r.max() < 0.5:
        return [], [], [], np.array([]), np.array([])

    thresh = max(r.max() * 0.15, 0.3)
    above  = (r > thresh).astype(int)
    d      = np.diff(np.concatenate(([0], above, [0])))
    starts = np.where(d ==  1)[0]
    ends   = np.where(d == -1)[0] - 1   # inclusive

    if len(starts) == 0:
        return [], [], [], np.array([]), np.array([])

    # Merge bursts within 500 ms
    gap = max(1, int(500 / bin_ms))
    ms, me = [starts[0]], [ends[0]]
    for s, e in zip(starts[1:], ends[1:]):
        if s - me[-1] <= gap:
            me[-1] = e
        else:
            ms.append(s); me.append(e)

    # Drop bursts < 2 bins
    pairs  = [(s, e) for s, e in zip(ms, me) if e - s >= 1]
    if not pairs:
        return [], [], [], np.array([]), np.array([])

    ms, me  = zip(*pairs)
    s_s     = [t[s] for s in ms]
    e_s     = [t[min(e + 1, len(t) - 1)] for e in me]
    pk_s    = [t[s + int(np.argmax(r[s:e + 1]))] for s, e in zip(ms, me)]
    w_ms    = np.array([(e - s + 1) * bin_ms for s, e in zip(ms, me)], float)
    ibi_s   = np.diff(s_s) if len(s_s) > 1 else np.array([])

    return s_s, e_s, pk_s, w_ms, ibi_s

def row_title(name, N, n_b, w_ms, ibi_s):
    day, cond, date = parse_info(name)
    s = f"{day} — {cond} ({date}) — {N} units — {n_b} bursts"
    if n_b > 0 and len(w_ms):
        cv = w_ms.std() / w_ms.mean() if w_ms.mean() > 0 else 0
        s += f"  |  width: {w_ms.mean():.0f} ± {w_ms.std():.0f} ms"
        if len(ibi_s):
            s += f"  |  IBI: {ibi_s.mean():.2f} ± {ibi_s.std():.2f} s"
        s += f"  |  width CV: {cv:.2f}"
    return s

# ── MEA groups ─────────────────────────────────────────────────────────────

MEA_GROUPS = [
    dict(mea="2277i", cl="KOLF21J MO", recs=[
        (KOLF_BASE, "2277i_KOLF21J_MO_D32_04152026_sham"),
        (KOLF_BASE, "2277i_KOLF21J_MO_D32_SCH_04152026"),
        (KOLF_BASE, "2277i_KOLF21J_MO_D33_SCH_24hr_connectedconfig_04162026"),
        (KOLF_BASE, "2277i_KOLF21J_MO_D33_SCH_24hr_newconfig_04162026"),
    ]),
    dict(mea="2280i", cl="KOLF21J MO", recs=[
        (KOLF_BASE, "2280i_KOLF21J_MO_D34_baseline_04172026"),
        (KOLF_BASE, "2280i_KOLF21J_MO_D34_SCH_04172026"),
        (KOLF_BASE, "2280i_KOLF21J_MO_D35_SCH_24hr_connectedconfig_04182026"),
        (KOLF_BASE, "2280i_KOLF21J_MO_D35_SCH_24hr_newconfig_04182026"),
    ]),
    dict(mea="24655i", cl="KOLF21J MO", recs=[
        (KOLF_BASE, "24655i_KOLF21J_MO_D32_04152026_sham"),
        (KOLF_BASE, "24655i_KOLF21J_MO_D32_SCH_04152026"),
        (KOLF_BASE, "24655i_KOLF21J_MO_D33_sch_24hr_connectedconfig_04162026"),
        (KOLF_BASE, "24655i_KOLF21J_MO_D33_sch_24hr_newconfig_04162026"),
    ]),
    dict(mea="24478G", cl="H9SynGFP MO", recs=[
        (H9_BASE, "24478G_MO_H9SynGFP_D40_Baseline_02212026"),
        (None,    "24478G_MO_H9SynGFP_D40_Dopmanine_24hr_02212026"),
        (H9_BASE, "24478G_MO_H9SynGFP_D40_SCH_02212026"),
    ]),
    dict(mea="21965G", cl="H9SynGFP MO", recs=[
        (H9_BASE, "21965G_MO_H9SynGFP_D40_Baseline_02212026"),
        (H9_BASE, "21956G_MO_H9SynGFP_D40_SCH_02212026"),   # mislabeled chip; same MEA
    ]),
    dict(mea="2280G", cl="H9SynGFP MO", recs=[
        (H9_BASE, "2280G_MO_H9SynGFP_D40_Baseline_02212026"),
        (H9_BASE, "2280G_MO_H9SynGFP_D40_SCH_02212026"),
    ]),
]

# ── Layout constants ───────────────────────────────────────────────────────
RASTER_H  = 2.0   # inches — raster panel per recording
POPRATE_H = 0.9   # inches — pop-rate panel per recording
ROWGAP    = 0.30  # inches — visual gap between successive recordings
FIG_W     = 18.0

# ── Main render loop ───────────────────────────────────────────────────────

for grp in MEA_GROUPS:
    mea, cl, recs = grp["mea"], grp["cl"], grp["recs"]
    n = len(recs)
    print(f"\n── {mea}  ({n} recording{'s' if n > 1 else ''}) ──")

    # GridSpec row heights: [raster, poprate, spacer?, raster, poprate, ...]
    gs_h = []
    for i in range(n):
        gs_h.append(RASTER_H)
        gs_h.append(POPRATE_H)
        if i < n - 1:
            gs_h.append(ROWGAP)

    fig_h = sum(gs_h) + 0.75   # header margin
    fig   = plt.figure(figsize=(FIG_W, fig_h), facecolor="white")

    gs = GridSpec(
        len(gs_h), 1,
        figure=fig,
        height_ratios=gs_h,
        hspace=0.0,
        left=0.065, right=0.99,
        top=0.97,   bottom=0.045,
    )

    fig.suptitle(
        f"MEA {mea}  —  {cl}  —  Progression  —  First 10 min"
        f"  —  100 ms burst detection",
        fontsize=11, fontweight="bold", y=0.995,
    )

    row_axes = []  # list of (ax_raster, ax_poprate)

    for rec_i, (base, name) in enumerate(recs):
        gs_r = rec_i * 3       # raster row in GridSpec
        gs_p = rec_i * 3 + 1   # pop-rate row

        # For n=1 there are no spacer rows, so rows are just 0 and 1
        if n == 1:
            gs_r, gs_p = 0, 1

        ax_r = fig.add_subplot(gs[gs_r])
        ax_p = fig.add_subplot(gs[gs_p])
        row_axes.append((ax_r, ax_p))

        sd = load_sd(base, name)

        # ── Missing recording ──────────────────────────────────────────
        if sd is None:
            day, cond, date = parse_info(name)
            for ax in (ax_r, ax_p):
                ax.set_facecolor("#fff4f4")
                ax.set_xlim(0, 600)
                ax.set_xticks(np.arange(0, 601, 100))
                ax.spines[["top", "right"]].set_visible(False)
            ax_r.set_yticks([]); ax_r.set_ylim(0, 1)
            ax_p.set_yticks([]); ax_p.set_ylim(0, 1)
            ax_r.text(0.5, 0.5, "Recording not found",
                      ha="center", va="center", transform=ax_r.transAxes,
                      fontsize=10, color="#c0392b", style="italic")
            ax_r.set_title(
                f"{day} — {cond} ({date}) — recording not found",
                fontsize=8.5, loc="left", pad=3, color="#c0392b")
            ax_r.set_xticklabels([])
            ax_p.set_xticklabels([])
            print(f"  [MISSING]  {name}")
            continue

        # Clip to first 10 min
        end_ms = min(DUR_MS, sd.length)
        sd_sub = sd.subtime(0, end_ms)
        end_s  = end_ms / 1000.0

        # Pop rate & bursts
        t, r = pop_rate(sd_sub.train, sd_sub.N, end_ms)
        s_s, e_s, pk_s, w_ms, ibi_s = detect_bursts(t, r)

        xticks     = np.arange(0, end_s + 1, 100)
        is_last    = (rec_i == n - 1)

        # ── Raster panel ───────────────────────────────────────────────
        ax_r.set_facecolor("white")

        # Burst shading behind spikes
        for bs, be in zip(s_s, e_s):
            ax_r.axvspan(bs, be, color="#cce4f7", alpha=0.45, zorder=1)

        # Spike scatter
        for uid, spikes in enumerate(sd_sub.train):
            if len(spikes):
                ax_r.scatter(
                    spikes / 1000.0, np.full(len(spikes), uid),
                    c="black", s=0.12, linewidths=0,
                    rasterized=True, zorder=2,
                )

        ax_r.set_xlim(0, end_s)
        ax_r.set_ylim(-0.5, sd_sub.N - 0.5)
        ax_r.set_xticks(xticks)
        ax_r.set_xticklabels([])
        ax_r.tick_params(axis="y", labelsize=7)
        ax_r.set_ylabel("Unit", fontsize=8, labelpad=2)
        ax_r.spines[["top", "right"]].set_visible(False)

        title_str = row_title(name, sd_sub.N, len(s_s), w_ms, ibi_s)
        ax_r.set_title(title_str, fontsize=8.5, loc="left", pad=3, fontweight="normal")

        # ── Pop-rate panel ─────────────────────────────────────────────
        ax_p.set_facecolor("white")

        # Burst shading
        for bs, be in zip(s_s, e_s):
            ax_p.axvspan(bs, be, color="#90CAF9", alpha=0.40, zorder=1)

        # Rate trace
        ax_p.plot(t, r, color="#1565C0", lw=0.85, zorder=3)

        # Peak markers
        for pk in pk_s:
            idx = min(int(round(pk / (BIN_MS / 1000.0))), len(r) - 1)
            ax_p.scatter(pk, r[idx], c="black", s=16, zorder=5)

        y_max = max(float(r.max()) * 1.15, 1.0)
        ax_p.set_xlim(0, end_s)
        ax_p.set_ylim(0, y_max)
        ax_p.set_xticks(xticks)
        ax_p.tick_params(axis="both", labelsize=7)
        ax_p.set_ylabel("Pop. rate\n(Hz/unit)", fontsize=7, labelpad=2)
        ax_p.spines[["top", "right"]].set_visible(False)

        if is_last:
            ax_p.set_xticklabels([f"{int(x)}" for x in xticks], fontsize=7)
            ax_p.set_xlabel("Time (s)", fontsize=9)
        else:
            ax_p.set_xticklabels([])

        print(
            f"  [OK]  {name}"
            f"  N={sd_sub.N},  {len(s_s)} bursts"
            + (f",  width {w_ms.mean():.0f} ± {w_ms.std():.0f} ms"
               f",  IBI {ibi_s.mean():.2f} s" if len(s_s) and len(ibi_s) else "")
        )

    out = os.path.join(OUTPUT, f"{mea}_progression_10min.png")
    fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  → {out}")

print("\n═══ Done ═══")
