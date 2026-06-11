"""Response size vs Δt-to-last-burst-peak analysis.

For each cycle with a full_sort.pkl cached:
  - Detect bursts (sync_fraction ≥ 0.10 in 100-ms bins, artifact-masked)
  - For each stim event, compute Δt = stim_logged - latest preceding burst peak
  - Use the FIRST-PULSE-aligned evoked spike count per event as response size
    (sum across K_consensus_excit units, [5, 200] ms post first pulse)
  - Per condition: scatter response vs Δt, fit log-linear regression,
    Spearman correlation + p-value
  - Test: does within-condition response size depend on burst phase?

Outputs: scratch/response_vs_dt/<cycle>_<condition>.png + summary table

Designed to handle multiple cycles. Run with --cycle <K> to analyze just one
cycle (skips cycles without full_sort.pkl), or --all to do all available.
"""
from __future__ import annotations
import argparse, json, os, pickle
from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import spearmanr, linregress

PLAN = Path("/home/sharf-lab/Desktop/Research_automation/orchestrator/stim-optimize-maxone_2026-04-20")
WELL = PLAN / "well_0"
OUT_DIR = PLAN / "scratch/response_vs_dt"
OUT_DIR.mkdir(exist_ok=True)

REC_DIR_BY_CYCLE = {
    1:  PLAN / "recordings/2026-04-25/045547_stim_w0",
    2:  PLAN / "recordings/2026-04-25/094516_stim_w0_cycle2",
    3:  PLAN / "recordings/2026-04-25/185500_stim_w0_cycle3",
    4:  PLAN / "recordings/2026-04-25/193500_stim_w0_cycle4",
    5:  PLAN / "recordings/2026-04-25/225600_stim_w0_cycle5",
    6:  PLAN / "recordings/2026-04-25/234500_stim_w0_cycle6",
    7:  PLAN / "recordings/2026-04-26/002400_stim_w0_cycle7",
    8:  PLAN / "recordings/2026-04-26/023900_stim_w0_cycle8_rerun",
    9:  PLAN / "recordings/2026-04-26/032200_stim_w0_cycle9",
    10: PLAN / "recordings/2026-04-26/041800_stim_w0_cycle10",
    11: PLAN / "recordings/2026-04-26/045900_stim_w0_cycle11",
    12: PLAN / "recordings/2026-04-26/054000_stim_w0_cycle12",
    13: PLAN / "recordings/2026-04-26/062300_stim_w0_cycle13",
    14: PLAN / "recordings/2026-04-26/065500_stim_w0_cycle14",
    15: PLAN / "recordings/2026-04-26/074500_stim_w0_cycle15",
    16: PLAN / "recordings/2026-04-26/082200_stim_w0_cycle16",
    17: PLAN / "recordings/2026-04-26/093515_stim_w0_cycle17",
    18: PLAN / "recordings/2026-04-26/102457_stim_w0_cycle18",
    19: PLAN / "recordings/2026-04-26/111339_stim_w0_cycle19",
    20: PLAN / "recordings/2026-04-26/120133_stim_w0_cycle20",
}

PRE_LEN_MS = 50.0
EVK_LEN_MS = 195.0
BURST_BIN_MS = 100.0
BURST_SYNC_FRAC = 0.10
ARTIFACT_MASK_MS = 30.0

kc = json.load(open(PLAN / "scratch/k_consensus_responders.json"))
K_excit = set(kc["K_consensus_excit"])
K_supp = set(kc["K_consensus_supp"])


def detect_bursts(full_trains_ms, duration_ms, stim_times_ms_logged):
    """Returns burst_peaks_ms array."""
    n_bins = int(np.ceil(duration_ms / BURST_BIN_MS))
    bin_edges = np.arange(n_bins + 1) * BURST_BIN_MS
    bin_centres = bin_edges[:-1] + BURST_BIN_MS / 2

    n_units_max = max(full_trains_ms.keys()) + 1
    fired_in_bin = np.zeros((n_units_max, n_bins), dtype=bool)
    for uid, sp in full_trains_ms.items():
        if len(sp) == 0:
            continue
        counts, _ = np.histogram(sp, bins=bin_edges)
        fired_in_bin[uid] = counts > 0

    artifact_bins = set()
    for st in stim_times_ms_logged:
        b_lo = max(0, int(np.floor(st / BURST_BIN_MS)))
        b_hi = min(n_bins, int(np.ceil((st + ARTIFACT_MASK_MS) / BURST_BIN_MS)))
        for b in range(b_lo, b_hi):
            artifact_bins.add(b)
    fired_masked = fired_in_bin.copy()
    for b in artifact_bins:
        fired_masked[:, b] = False

    n_active = sum(1 for uid, sp in full_trains_ms.items() if len(sp) > 0)
    sync_frac = fired_masked.sum(axis=0) / max(n_active, 1)
    above = sync_frac >= BURST_SYNC_FRAC
    edges = np.diff(above.astype(np.int8))
    starts = np.where(edges == 1)[0] + 1
    stops = np.where(edges == -1)[0] + 1
    if above[0]:
        starts = np.concatenate([[0], starts])
    if above[-1]:
        stops = np.concatenate([stops, [n_bins]])
    peaks = []
    for s, e in zip(starts, stops):
        seg = sync_frac[s:e]
        if len(seg) == 0:
            continue
        peaks.append(bin_centres[s + int(np.argmax(seg))])
    return np.array(peaks)


PRE_WINDOW_MS = 50.0   # [-50, 0)
POST_WINDOW_MS = 200.0  # [0, 200)


def compute_rate_change_FP(K, slices_list, fp_recentered_ms, existing_recentered_ms,
                            events, n_units):
    """Returns per-event rate change (post − pre) in spikes/sec, summed across
    K_consensus_excit units. Uses 50 ms pre + 200 ms post first-pulse windows.
    """
    n_events = len(events)
    pre_counts = np.zeros(n_events, dtype=np.int64)
    post_counts = np.zeros(n_events, dtype=np.int64)
    for ei in range(n_events):
        slc = slices_list[ei]
        if not hasattr(slc, "train"):
            continue
        n_u = min(n_units, getattr(slc, "N", len(slc.train)))
        shift = existing_recentered_ms[ei] - fp_recentered_ms[ei]
        if not np.isfinite(shift):
            shift = 0.0
        for u in range(n_u):
            if u not in K_excit:
                continue
            sp = np.asarray(slc.train[u])
            if len(sp) == 0:
                continue
            sp_shift = sp + shift
            pre_counts[ei] += int(np.sum((sp_shift >= -PRE_WINDOW_MS) & (sp_shift < 0)))
            post_counts[ei] += int(np.sum((sp_shift >= 0) & (sp_shift < POST_WINDOW_MS)))
    pre_rate_hz = pre_counts / (PRE_WINDOW_MS / 1000.0)
    post_rate_hz = post_counts / (POST_WINDOW_MS / 1000.0)
    rate_change_hz = post_rate_hz - pre_rate_hz
    return rate_change_hz, pre_counts, post_counts


def analyze_cycle(K):
    sort_cache_path = PLAN / f"scratch/burst_phase_c{K}/full_sort.pkl"
    if not sort_cache_path.exists():
        print(f"[cycle {K}] no full_sort.pkl yet; skipping")
        return None

    rec_dir = REC_DIR_BY_CYCLE[K]
    manifest = json.load(open(rec_dir / "manifest.json"))
    record0 = manifest["steps"]["record"][0]
    rec_start_ms = record0["wells"][0]["start_time_ms"]
    events = record0["events"]
    logged_ms = np.array([e["timestamp_ms"] - rec_start_ms for e in events], dtype=np.float64)

    cache = pickle.load(open(sort_cache_path, "rb"))
    full_trains_ms = cache["full_trains_ms"]
    duration_ms = cache["duration_s"] * 1000.0

    burst_peaks_ms = detect_bursts(full_trains_ms, duration_ms, logged_ms)
    print(f"[cycle {K}] {len(burst_peaks_ms)} bursts; rate {len(burst_peaks_ms)/cache['duration_s']:.2f}/s")

    # Δt to last preceding burst
    n_events = len(events)
    dt_ms = np.full(n_events, np.nan)
    for ei in range(n_events):
        prior = burst_peaks_ms[burst_peaks_ms < logged_ms[ei]]
        if len(prior) == 0:
            continue
        dt_ms[ei] = logged_ms[ei] - prior.max()

    # Evoked counts (FP-aligned)
    sort_cache_stim = pickle.load(open(WELL / f"cycle_{K}/sort_cache.pkl", "rb"))
    slices_list = sort_cache_stim["slices_list"]
    n_units = getattr(slices_list[0], "N", len(slices_list[0].train))
    rtv = sort_cache_stim["raw_times_value"]
    existing_recentered_ms = rtv[:, 0] + PRE_LEN_MS

    fp_path = PLAN / f"scratch/first_pulse_recentered_c{K}.json"
    if not fp_path.exists():
        print(f"[cycle {K}] no first_pulse_recentered_c{K}.json — using existing recentering")
        fp_recentered_ms = existing_recentered_ms.copy()
    else:
        fp_data = json.load(open(fp_path))
        fp_recentered_ms = np.array(
            [np.nan if x is None else float(x) for x in fp_data["first_pulse_recentered_ms"]],
            dtype=np.float64,
        )
    rate_change_hz, pre_counts, post_counts = compute_rate_change_FP(
        K, slices_list, fp_recentered_ms, existing_recentered_ms, events, n_units)

    # Per-condition analysis
    cond_to_event_idxs = defaultdict(list)
    for ei, e in enumerate(events):
        L = e["label"]
        if "_t" in L:
            L = L.rsplit("_t", 1)[0]
        cond_to_event_idxs[L].append(ei)

    results = {}
    for cond, eidx in sorted(cond_to_event_idxs.items()):
        eidx = np.array(eidx)
        valid = np.isfinite(dt_ms[eidx])
        sel = eidx[valid]
        if len(sel) < 10:
            continue
        x = dt_ms[sel]
        y = rate_change_hz[sel].astype(float)
        # Spearman rank correlation (insensitive to log-vs-linear)
        rho, p_spearman = spearmanr(x, y)
        # Linear regression on log10(Δt)
        log_x = np.log10(x + 1)
        try:
            lr = linregress(log_x, y)
            slope = lr.slope
            intercept = lr.intercept
            p_lr = lr.pvalue
            r_lr = lr.rvalue
        except Exception:
            slope = intercept = p_lr = r_lr = np.nan

        results[cond] = {
            "n": int(len(sel)),
            "rate_change_mean_hz": float(y.mean()),
            "rate_change_std_hz": float(y.std()),
            "dt_median_ms": float(np.median(x)),
            "dt_p25_ms": float(np.percentile(x, 25)),
            "dt_p75_ms": float(np.percentile(x, 75)),
            "spearman_rho": float(rho),
            "spearman_p": float(p_spearman),
            "log_lr_slope": float(slope),
            "log_lr_intercept": float(intercept),
            "log_lr_r": float(r_lr),
            "log_lr_p": float(p_lr),
            "x": x.tolist(),
            "y": y.tolist(),
        }
    return {
        "cycle": K,
        "n_bursts": int(len(burst_peaks_ms)),
        "duration_s": float(cache["duration_s"]),
        "n_events": int(n_events),
        "n_events_with_prior_burst": int(np.isfinite(dt_ms).sum()),
        "results_per_cond": results,
    }


def make_per_cycle_plots(per_cycle):
    K = per_cycle["cycle"]
    res = per_cycle["results_per_cond"]
    n_conds = len(res)
    if n_conds == 0:
        return
    ncols = 2
    nrows = (n_conds + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(13, 4.0 * nrows), sharex=True, sharey=False)
    axes = np.atleast_2d(axes).flatten() if nrows > 1 else np.atleast_1d(axes)

    for ax_idx, (cond, r) in enumerate(sorted(res.items())):
        ax = axes[ax_idx]
        x = np.array(r["x"])
        y = np.array(r["y"])
        ax.scatter(x, y, s=12, alpha=0.5, color="steelblue", edgecolor="none")
        # log-linear fit overlay
        if np.isfinite(r["log_lr_slope"]):
            xs = np.logspace(np.log10(max(x.min(), 1)), np.log10(x.max()), 100)
            ys = r["log_lr_slope"] * np.log10(xs + 1) + r["log_lr_intercept"]
            ax.plot(xs, ys, color="darkred", linewidth=1.5, alpha=0.8,
                    label=f"r={r['log_lr_r']:.2f}, p={r['log_lr_p']:.3g}")
        ax.set_xscale("log")
        ax.set_xlabel("Δt to last burst peak (ms, log)")
        ax.set_ylabel("rate change Hz (K_excit, post[0,200]−pre[-50,0])")
        sig = " *" if r["spearman_p"] < 0.05 else ""
        ax.set_title(f"{cond[:42]}{sig}\nρ={r['spearman_rho']:+.2f}, p={r['spearman_p']:.3g}, n={r['n']}",
                     fontsize=9)
        ax.legend(fontsize=8, loc="best")
        ax.grid(alpha=0.3, which="both")
    for idx in range(n_conds, len(axes)):
        axes[idx].axis("off")
    fig.suptitle(f"Cycle {K} — evoked response vs Δt-to-last-burst-peak (per condition)\n"
                 f"n_bursts={per_cycle['n_bursts']} | n_events_with_prior_burst={per_cycle['n_events_with_prior_burst']}/{per_cycle['n_events']}",
                 fontsize=11)
    plt.tight_layout()
    plt.savefig(OUT_DIR / f"cycle_{K:02d}_response_vs_dt.png", dpi=110, bbox_inches="tight")
    plt.close()


# Main
ap = argparse.ArgumentParser()
ap.add_argument("--all", action="store_true")
ap.add_argument("--cycle", type=int)
args = ap.parse_args()

cycles_to_do = []
if args.all:
    cycles_to_do = [K for K in REC_DIR_BY_CYCLE if (PLAN / f"scratch/burst_phase_c{K}/full_sort.pkl").exists()]
elif args.cycle is not None:
    cycles_to_do = [args.cycle]
else:
    cycles_to_do = [K for K in REC_DIR_BY_CYCLE if (PLAN / f"scratch/burst_phase_c{K}/full_sort.pkl").exists()]

print(f"=== Analyzing cycles: {cycles_to_do} ===")

all_results = {}
for K in cycles_to_do:
    res = analyze_cycle(K)
    if res is None:
        continue
    all_results[K] = res
    make_per_cycle_plots(res)

# Cross-cycle summary table
print(f"\n=== Per-condition Spearman ρ (rate change vs Δt) across cycles ===")
print(f"{'cycle':<5} {'condition':<48} {'n':>4} {'rate_mean_Hz':>13} {'rho':>8} {'p':>9} {'sig':>4}")
for K in sorted(all_results.keys()):
    res = all_results[K]
    for cond, r in sorted(res["results_per_cond"].items()):
        sig = "**" if r["spearman_p"] < 0.01 else ("*" if r["spearman_p"] < 0.05 else "")
        print(f"{K:<5} {cond:<48} {r['n']:>4} {r['rate_change_mean_hz']:>13.1f} {r['spearman_rho']:>8.3f} {r['spearman_p']:>9.3g} {sig:>4}")

# Save
with open(OUT_DIR / "response_vs_dt_summary.json", "w") as f:
    json.dump({str(k): v for k, v in all_results.items()}, f, indent=2)
print(f"\nSaved -> {OUT_DIR / 'response_vs_dt_summary.json'}")
