"""
Raster progression plots for haloperidol-series MEAs.
- f-MEA recordings: loaded from S3 phy.zip via load_spikedata_from_kilosort
- G-MEA recordings: loaded from local sort_workdir pkl
Layout: recordings stacked vertically per MEA, raster + pop-rate + burst stats.
Output: Desktop/Greg/Organized Recording Raster Plots/
"""

import os, re, pickle, shutil, subprocess, tempfile, warnings, zipfile
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
from spikelab.data_loaders import load_spikedata_from_kilosort

warnings.filterwarnings("ignore")

# ── Paths & constants ─────────────────────────────────────────────────────
ENDPOINT = "https://s3.braingeneers.gi.ucsc.edu"
S3_ROOT  = "s3://braingeneers/ephys"
WORKDIR  = "/home/sharf-lab/Desktop/Analysis_shared/sort_workdir"
OUTPUT   = "/home/sharf-lab/Desktop/Greg/Organized Recording Raster Plots"
os.makedirs(OUTPUT, exist_ok=True)

DUR_MS = 600_000   # 10 min
BIN_MS = 100       # 100 ms burst bins
FS_HZ  = 20000.0

# UUID lookup for S3 downloads
UUID_MAP = {
    "2025-12-18": "2025-12-18-e-MO_H9SynGFP_D36_control_baseline_haloperidol",
    "2025-12-20": "2025-12-20-e-H9SynGFP_Midbrain_control_baseline_haloperidol_rotenone",
    "2025-12-21": "2025-12-21-e-H9SynGFP_Midbrain_haloperidol_rotenone_series2",
}

# ── Loading helpers ───────────────────────────────────────────────────────

def load_from_pkl(folder_name):
    """Load SpikeData from local sort_workdir pkl."""
    pkl = os.path.join(WORKDIR, folder_name, "sorted_spikedata_curated.pkl")
    if not os.path.exists(pkl):
        return None
    with open(pkl, "rb") as f:
        return pickle.load(f)

def load_from_phy_s3(stem, uuid_key):
    """Download phy.zip from S3 and load as SpikeData."""
    uuid = UUID_MAP[uuid_key]
    s3_zip = f"{S3_ROOT}/{uuid}/derived/kilosort2/{stem}_phy.zip"
    tmp = tempfile.mkdtemp()
    try:
        zip_path = os.path.join(tmp, "phy.zip")
        r = subprocess.run(
            ["aws", "s3", "cp", s3_zip, zip_path, "--endpoint-url", ENDPOINT],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"    S3 download failed: {r.stderr.strip()}")
            return None
        if os.path.getsize(zip_path) < 10_000:
            print(f"    phy.zip too small ({os.path.getsize(zip_path)} bytes) — likely empty")
            return None
        phy_dir = os.path.join(tmp, "phy")
        os.makedirs(phy_dir)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(phy_dir)
        # cluster_group.tsv marks "good" units; fall back to cluster_info.tsv
        tsv = "cluster_group.tsv"
        if not any(f.endswith("cluster_group.tsv") for f in os.listdir(phy_dir)):
            tsv = "cluster_info.tsv"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return load_spikedata_from_kilosort(
                phy_dir, fs_Hz=FS_HZ,
                cluster_info_tsv=tsv,
                include_noise=False,
            )
    except Exception as e:
        print(f"    load_from_phy_s3 error: {e}")
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# ── Pop-rate & burst detection (identical to raster_progression.py) ───────

def pop_rate(train, N, end_ms, bin_ms=BIN_MS):
    n_bins = int(np.ceil(end_ms / bin_ms))
    counts = np.zeros(n_bins)
    for sp in train:
        idx = (np.asarray(sp) / bin_ms).astype(int)
        idx = idx[(idx >= 0) & (idx < n_bins)]
        np.add.at(counts, idx, 1)
    t = np.arange(n_bins) * bin_ms / 1000.0
    r = counts / (N * bin_ms / 1000.0)
    return t, r

def detect_bursts(t, r, bin_ms=BIN_MS):
    if r.max() < 0.5:
        return [], [], [], np.array([]), np.array([])
    thresh = max(r.max() * 0.15, 0.3)
    above  = (r > thresh).astype(int)
    d      = np.diff(np.concatenate(([0], above, [0])))
    starts = np.where(d ==  1)[0]
    ends   = np.where(d == -1)[0] - 1
    if len(starts) == 0:
        return [], [], [], np.array([]), np.array([])
    gap = max(1, int(500 / bin_ms))
    ms, me = [starts[0]], [ends[0]]
    for s, e in zip(starts[1:], ends[1:]):
        if s - me[-1] <= gap:
            me[-1] = e
        else:
            ms.append(s); me.append(e)
    pairs = [(s, e) for s, e in zip(ms, me) if e - s >= 1]
    if not pairs:
        return [], [], [], np.array([]), np.array([])
    ms, me  = zip(*pairs)
    s_s     = [t[s] for s in ms]
    e_s     = [t[min(e + 1, len(t) - 1)] for e in me]
    pk_s    = [t[s + int(np.argmax(r[s:e + 1]))] for s, e in zip(ms, me)]
    w_ms    = np.array([(e - s + 1) * bin_ms for s, e in zip(ms, me)], float)
    ibi_s   = np.diff(s_s) if len(s_s) > 1 else np.array([])
    return s_s, e_s, pk_s, w_ms, ibi_s

def row_title(label, N, n_b, w_ms, ibi_s):
    s = f"{label} — {N} units — {n_b} bursts"
    if n_b > 0 and len(w_ms):
        cv = w_ms.std() / w_ms.mean() if w_ms.mean() > 0 else 0
        s += f"  |  width: {w_ms.mean():.0f} ± {w_ms.std():.0f} ms"
        if len(ibi_s):
            s += f"  |  IBI: {ibi_s.mean():.2f} ± {ibi_s.std():.2f} s"
        s += f"  |  width CV: {cv:.2f}"
    return s

# ── Figure renderer (same layout as raster_progression.py) ───────────────

RASTER_H  = 2.0
POPRATE_H = 0.9
ROWGAP    = 0.30
FIG_W     = 18.0

def render_mea(mea, cl, title, recordings):
    """
    recordings: list of (label, sd_or_None)
    """
    n = len(recordings)
    gs_h = []
    for i in range(n):
        gs_h.append(RASTER_H)
        gs_h.append(POPRATE_H)
        if i < n - 1:
            gs_h.append(ROWGAP)

    fig_h = sum(gs_h) + 0.75
    fig   = plt.figure(figsize=(FIG_W, fig_h), facecolor="white")
    gs    = GridSpec(
        len(gs_h), 1, figure=fig,
        height_ratios=gs_h, hspace=0.0,
        left=0.065, right=0.99, top=0.97, bottom=0.045,
    )
    fig.suptitle(
        f"MEA {mea}  —  {cl}  —  {title}  —  First 10 min  —  100 ms burst detection",
        fontsize=11, fontweight="bold", y=0.995,
    )

    for rec_i, (label, sd) in enumerate(recordings):
        gs_r = rec_i * 3 if n > 1 else 0
        gs_p = rec_i * 3 + 1 if n > 1 else 1
        ax_r = fig.add_subplot(gs[gs_r])
        ax_p = fig.add_subplot(gs[gs_p])
        is_last = (rec_i == n - 1)

        if sd is None:
            for ax in (ax_r, ax_p):
                ax.set_facecolor("#fff4f4")
                ax.set_xlim(0, 600)
                ax.set_xticks(np.arange(0, 601, 100))
                ax.set_xticklabels([])
                ax.spines[["top", "right"]].set_visible(False)
            ax_r.set_yticks([]); ax_r.set_ylim(0, 1)
            ax_p.set_yticks([]); ax_p.set_ylim(0, 1)
            ax_r.text(0.5, 0.5, "Recording not found",
                      ha="center", va="center", transform=ax_r.transAxes,
                      fontsize=10, color="#c0392b", style="italic")
            ax_r.set_title(f"{label} — recording not found",
                           fontsize=8.5, loc="left", pad=3, color="#c0392b")
            continue

        end_ms = min(DUR_MS, sd.length)
        sd_sub = sd.subtime(0, end_ms)
        end_s  = end_ms / 1000.0
        t, r   = pop_rate(sd_sub.train, sd_sub.N, end_ms)
        s_s, e_s, pk_s, w_ms, ibi_s = detect_bursts(t, r)
        xticks = np.arange(0, end_s + 1, 100)

        # Raster
        ax_r.set_facecolor("white")
        for bs, be in zip(s_s, e_s):
            ax_r.axvspan(bs, be, color="#cce4f7", alpha=0.45, zorder=1)
        for uid, spikes in enumerate(sd_sub.train):
            if len(spikes):
                ax_r.scatter(spikes / 1000.0, np.full(len(spikes), uid),
                             c="black", s=0.12, linewidths=0,
                             rasterized=True, zorder=2)
        ax_r.set_xlim(0, end_s); ax_r.set_ylim(-0.5, sd_sub.N - 0.5)
        ax_r.set_xticks(xticks); ax_r.set_xticklabels([])
        ax_r.tick_params(axis="y", labelsize=7)
        ax_r.set_ylabel("Unit", fontsize=8, labelpad=2)
        ax_r.spines[["top", "right"]].set_visible(False)
        ax_r.set_title(row_title(label, sd_sub.N, len(s_s), w_ms, ibi_s),
                       fontsize=8.5, loc="left", pad=3, fontweight="normal")

        # Pop rate
        ax_p.set_facecolor("white")
        for bs, be in zip(s_s, e_s):
            ax_p.axvspan(bs, be, color="#90CAF9", alpha=0.40, zorder=1)
        ax_p.plot(t, r, color="#1565C0", lw=0.85, zorder=3)
        for pk in pk_s:
            idx = min(int(round(pk / (BIN_MS / 1000.0))), len(r) - 1)
            ax_p.scatter(pk, r[idx], c="black", s=16, zorder=5)
        ax_p.set_xlim(0, end_s)
        ax_p.set_ylim(0, max(float(r.max()) * 1.15, 1.0))
        ax_p.set_xticks(xticks)
        ax_p.tick_params(axis="both", labelsize=7)
        ax_p.set_ylabel("Pop. rate\n(Hz/unit)", fontsize=7, labelpad=2)
        ax_p.spines[["top", "right"]].set_visible(False)
        if is_last:
            ax_p.set_xticklabels([f"{int(x)}" for x in xticks], fontsize=7)
            ax_p.set_xlabel("Time (s)", fontsize=9)
        else:
            ax_p.set_xticklabels([])

    out = os.path.join(OUTPUT, f"{mea}_progression_10min.png")
    fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  → {out}")

# ── MEA group definitions ─────────────────────────────────────────────────
# Each recording: (display_label, load_spec)
# load_spec = ("pkl", folder_name) or ("phy", stem, uuid_key)

MEA_GROUPS = [
    dict(mea="21965f", cl="H9SynGFP MO", title="Haloperidol Progression", recs=[
        ("D37 — Baseline (12/19/2025)",           ("phy", "21965f_MO_H9SynGFP_D37_12192025",                       "2025-12-20")),
        ("D37 — Haloperidol (12/19/2025)",         ("phy", "21965f_MO_H9SynGFP_D37_haloperidol_12192025",           "2025-12-20")),
        ("D38 — Halo 24hr Baseline (12/20/2025)",  ("phy", "21965f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025", "2025-12-20")),
        ("D39 — Halo 24hr (12/21/2025)",           ("phy", "21965f_MO_H9SynGFP_D39_haloperidol24hr_122212025",      "2025-12-21")),
    ]),
    dict(mea="23137f", cl="H9SynGFP MO", title="Control Progression", recs=[
        ("D36 — Haloperidol 30min (12/18/2025)",   ("phy", "23137f_MO_H9SynGFP_D36_haloperidol_30min_12182025",    "2025-12-18")),
        ("D40 — Control (12/22/2025)",             ("phy", "23137f_MO_H9SynGFP_D40_Control_12222025",              "2025-12-21")),
        ("D41 — Control (12/23/2025)",             ("phy", "23137f_MO_H9SynGFP_D41_Control_12232025",              "2025-12-21")),
    ]),
    dict(mea="23156f", cl="H9SynGFP MO", title="Haloperidol Progression", recs=[
        ("D37 — Baseline (12/19/2025)",            ("phy", "23156f_MO_H9SynGFP_D37_12192025",                       "2025-12-20")),
        ("D37 — Haloperidol (12/19/2025)",         ("phy", "23156f_MO_H9SynGFP_D37_haloperidol_12192025",           "2025-12-20")),
        ("D38 — Halo 24hr Baseline (12/20/2025)",  ("phy", "23156f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025", "2025-12-20")),
    ]),
    dict(mea="23198f", cl="H9SynGFP MO", title="Haloperidol Progression", recs=[
        ("D37 — Baseline (12/19/2025)",            ("phy", "23198f_MO_H9SynGFP_D37_12192025",                       "2025-12-20")),
        ("D37 — Haloperidol (12/19/2025)",         ("phy", "23198f_MO_H9SynGFP_D37_haloperidol_12192025",           "2025-12-20")),
        ("D38 — Halo 24hr Baseline (12/20/2025)",  ("phy", "23198f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025", "2025-12-20")),
        ("D39 — Halo 24hr (12/21/2025)",           ("phy", "23198f_MO_H9SynGFP_D39_haloperidol24hr_122212025",      "2025-12-21")),
    ]),
    dict(mea="23206f", cl="H9SynGFP MO", title="Haloperidol Progression", recs=[
        ("D37 — Baseline (12/19/2025)",            ("phy", "23206f_MO_H9SynGFP_D37_12192025",                       "2025-12-20")),
        ("D37 — Haloperidol (12/19/2025)",         ("phy", "23206f_MO_H9SynGFP_D37_haloperidol_12192025",           "2025-12-20")),
        ("D38 — Halo 24hr Baseline (12/20/2025)",  ("phy", "23206f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025", "2025-12-20")),
    ]),
    dict(mea="23215f", cl="H9SynGFP MO", title="Haloperidol Progression", recs=[
        ("D37 — Baseline (12/19/2025)",            ("phy", "23215f_MO_H9SynGFP_D37_12192025",                       "2025-12-20")),
        ("D37 — Haloperidol (12/19/2025)",         ("phy", "23215f_MO_H9SynGFP_D37_haloperidol_12192025",           "2025-12-20")),
        ("D38 — Halo 24hr Baseline (12/20/2025)",  ("phy", "23215f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025", "2025-12-20")),
    ]),
    dict(mea="24500f", cl="H9SynGFP MO", title="Haloperidol Progression", recs=[
        ("D37 — Baseline (12/19/2025)",            ("pkl", "24500f_MO_H9SynGFP_D37_12192025")),          # phy.zip corrupted; use local pkl
        ("D37 — Haloperidol (12/19/2025)",         ("phy", "24500f_MO_H9SynGFP_D37_haloperidol_12192025",           "2025-12-20")),
        ("D38 — Halo 24hr Baseline (12/20/2025)",  ("phy", "24500f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025", "2025-12-20")),
    ]),
    dict(mea="24655f", cl="H9SynGFP MO", title="Haloperidol Progression", recs=[
        ("D37 — Baseline (12/19/2025)",            ("phy", "24655f_MO_H9SynGFP_D37_12192025",                       "2025-12-20")),
        ("D37 — Haloperidol (12/19/2025)",         ("phy", "24655f_MO_H9SynGFP_D37_haloperidol_12192025",           "2025-12-20")),
        ("D38 — Halo 24hr Baseline (12/20/2025)",  ("phy", "24655f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025", "2025-12-20")),
    ]),
    dict(mea="25168f", cl="H9SynGFP MO", title="Haloperidol Progression", recs=[
        ("D37 — Baseline (12/19/2025)",            ("phy", "25168f_MO_H9SynGFP_D37_12192025",                       "2025-12-20")),
        ("D37 — Haloperidol (12/19/2025)",         ("phy", "25168f_MO_H9SynGFP_D37_haloperidol_12192025",           "2025-12-20")),
        ("D38 — Halo 24hr Baseline (12/20/2025)",  ("phy", "25168f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025", "2025-12-20")),
    ]),
    dict(mea="24478G", cl="H9SynGFP MO", title="Haloperidol Progression", recs=[
        ("D38 — Baseline (02/19/2026)",            ("pkl", "24478G_MO_H9SynGFP_D38_Baseline_02192026")),
        ("D38 — Haloperidol (02/19/2026)",         ("pkl", "24478G_MO_H9SynGFP_D38_haloperidol_02192026")),
        ("D39 — Baseline (02/20/2026)",            ("pkl", "24478G_MO_H9SynGFP_D39_baseline_02202026")),
        ("D39 — Haloperidol 12hr (02/20/2026)",   ("pkl", "24478G_MO_H9SynGFP_D39_haloperidol_12hr_02202026")),
        ("D39 — Haloperidol 24hr (02/20/2026)",   ("pkl", "24478G_MO_H9SynGFP_D39_haloperidol_24hr_02202026")),
    ]),
    dict(mea="21965G", cl="H9SynGFP MO", title="Haloperidol Progression", recs=[
        ("D38 — Baseline (02/19/2026)",            ("pkl", "21965G_MO_H9SynGFP_D38_Baseline_02192026")),
        ("D38 — Haloperidol (02/19/2026)",         ("pkl", "21965G_MO_H9SynGFP_D38_haloperidol_02192026")),
        ("D39 — Baseline (02/20/2026)",            ("pkl", "21965G_MO_H9SynGFP_D39_baseline_02202026")),
        ("D39 — Haloperidol 12hr (02/20/2026)",   ("pkl", "21965G_MO_H9SynGFP_D39_haloperidol_12hr_02202026")),
        ("D39 — Haloperidol 24hr (02/20/2026)",   ("pkl", "21965G_MO_H9SynGFP_D39_haloperidol_24hr_02202026")),
    ]),
    dict(mea="2280G", cl="H9SynGFP MO", title="Haloperidol Progression", recs=[
        ("D38 — Baseline (02/19/2026)",            ("pkl", "2280G_MO_H9SynGFP_D38_Baseline_02192026")),
        ("D38 — Haloperidol (02/19/2026)",         ("pkl", "2280G_MO_H9SynGFP_D38_haloperidol_02192026")),
        ("D39 — Baseline (02/20/2026)",            ("pkl", "2280G_MO_H9SynGFP_D39_baseline_02202026")),
        ("D39 — Haloperidol 12hr (02/20/2026)",   ("pkl", "2280G_MO_H9SynGFP_D39_haloperidol_12hr_02202026")),
        ("D39 — Haloperidol 24hr (02/20/2026)",   ("pkl", "2280G_MO_H9SynGFP_D39_haloperidol_24hr_02202026")),
    ]),
]

# ── Main ──────────────────────────────────────────────────────────────────

import sys
skip_mea = set(sys.argv[1:])   # e.g. python script.py 2280G  to skip 2280G
for grp in MEA_GROUPS:
    mea, cl, title = grp["mea"], grp["cl"], grp["title"]
    print(f"\n{'='*60}")
    print(f"MEA {mea}  ({len(grp['recs'])} recordings)")

    loaded = []
    if grp["mea"] in skip_mea:
        print(f"\nSkipping {grp['mea']} (pending sort)")
        continue

    for label, spec in grp["recs"]:
        print(f"  Loading: {label} ...", end=" ", flush=True)
        if spec[0] == "pkl":
            sd = load_from_pkl(spec[1])
        else:   # phy
            sd = load_from_phy_s3(spec[1], spec[2])

        if sd is None:
            print("NOT FOUND")
        else:
            end_ms = min(DUR_MS, sd.length)
            print(f"N={sd.N}, {end_ms/1000:.0f}s")
        loaded.append((label, sd))

    render_mea(mea, cl, title, loaded)

print("\n═══ All done ═══")
