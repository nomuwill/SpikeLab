"""Continuous full-recording sort + burst-phase analysis on cycle 20.

Pipeline:
  1. Sort_offline cycle 1's RT-Sort templates against the FULL 982-s cycle-20
     recording (not just baseline).
  2. Detect population bursts via the same sync_fraction(>=10%) method used
     in cycle_analyze.py, but spanning the entire recording. Mask out 30 ms
     windows around each stim event in the bin-counts to avoid stim-artifact-
     driven false bursts.
  3. For each population burst, compute its peak time (bin of max sync
     fraction within the burst window).
  4. For each stim event in the manifest, compute dt = (stim_time - latest
     preceding burst peak time). Events with no prior burst get dt=NaN.
  5. Bin events by dt. For each bin x condition, compute mean evoked
     response (per-K_consensus_excit unit) using the existing per-event slice
     spike data from sort_cache.
  6. Plot:
        - Δt distribution (histogram)
        - Evoked response (mean spikes per slice in [5, 200] ms, summed over
          K_consensus_excit) vs Δt, per condition
        - Heat map condition x Δt-bin -> mean evoked spikes

Outputs to scratch/burst_phase_c20/.
"""
from __future__ import annotations
import json, os, pickle, time, sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PLAN = Path("/home/sharf-lab/Desktop/Research_automation/orchestrator/stim-optimize-maxone_2026-04-20")
WELL = PLAN / "well_0"
RECORDING_DIR = PLAN / "recordings/2026-04-26/120133_stim_w0_cycle20"
RAW_H5 = next(RECORDING_DIR.glob("cycle_*.raw.h5"))
MANIFEST_PATH = RECORDING_DIR / "manifest.json"
PICKLE_PATH = WELL / "rt_sort_cycle1.pickle"
INTER_DIR = WELL / "rt_sort_cycle1_inter"
CYCLE_DIR = WELL / "cycle_20"

OUT_DIR = PLAN / "scratch/burst_phase_c20"
OUT_DIR.mkdir(exist_ok=True)
FULL_SORT_INTER = OUT_DIR / "full_sort_inter"
FULL_SORT_INTER.mkdir(exist_ok=True)
FULL_SORT_CACHE = OUT_DIR / "full_sort.pkl"

# Burst detection params (match cycle_analyze.py)
BURST_BIN_MS = 100.0
BURST_SYNC_FRAC = 0.10
ARTIFACT_MASK_MS = 30.0  # mask this many ms after each stim onset

# Evoked-response window (match cons_topK_v3 def)
PRE_LEN_MS = 50.0
EVK_LEN_MS = 195.0
EVK_BIN_MS = 10.0
N_EVK_BINS = 19

# K_consensus
kc = json.load(open(PLAN / "scratch/k_consensus_responders.json"))
K_excit = set(kc["K_consensus_excit"])
K_supp = set(kc["K_consensus_supp"])
print(f"K_consensus_excit={len(K_excit)}, K_consensus_supp={len(K_supp)}")

mxwbio = "/home/sharf-lab/MaxLab/python/lib/python3.10/site-packages/maxwbio/hdf5/lib/plugin"
if os.path.isdir(mxwbio):
    os.environ.setdefault("HDF5_PLUGIN_PATH", mxwbio)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# Step 1 — Full-recording sort_offline (or load cache)
if FULL_SORT_CACHE.exists():
    print(f"[{now_iso()}] Loading cached full-recording sort from {FULL_SORT_CACHE}")
    with open(FULL_SORT_CACHE, "rb") as f:
        cache = pickle.load(f)
    full_trains_ms = cache["full_trains_ms"]  # uid -> spike-time array (ms)
    fs_hz = cache["fs_hz"]
    duration_s = cache["duration_s"]
    print(f"  Loaded full sort: {len(full_trains_ms)} units, duration={duration_s:.1f} s, fs={fs_hz}")
else:
    print(f"[{now_iso()}] Running sort_offline on FULL cycle-20 recording (982 s) -> {FULL_SORT_INTER}")
    from spikelab.spike_sorting.rt_sort_runner import load_rt_sort  # noqa: F401
    from spikelab.spike_sorting.rt_sort._algorithm import RTSort as _RTSort
    from spikelab.spike_sorting.rt_sort.model import ModelSpikeSorter
    from spikelab.spike_sorting.recording_io import load_single_recording

    rt_sort = _RTSort.load_from_file(PICKLE_PATH, model=None)
    rt_sort.model = ModelSpikeSorter.load_compiled(INTER_DIR)

    rec = load_single_recording(str(RAW_H5))
    fs_hz = rec.get_sampling_frequency()
    n_samples = rec.get_num_samples()
    duration_s = n_samples / fs_hz
    print(f"  fs={fs_hz}, samples={n_samples}, duration={duration_s:.1f} s")

    t0 = time.time()
    full_sorting = rt_sort.sort_offline(
        recording=rec,
        inter_path=FULL_SORT_INTER,
        recording_window_ms=(0, int(duration_s * 1000)),
        return_spikeinterface_sorter=True,
        verbose=True,
        reset=True,
    )
    elapsed = time.time() - t0
    print(f"  full sort_offline done in {elapsed:.0f} s ({elapsed/60:.1f} min)")

    full_trains_ms = {}
    for uid in sorted(int(u) for u in full_sorting.get_unit_ids()):
        samples = full_sorting.get_unit_spike_train(uid)
        full_trains_ms[uid] = samples.astype(np.float64) / fs_hz * 1000.0

    with open(FULL_SORT_CACHE, "wb") as f:
        pickle.dump({"full_trains_ms": full_trains_ms, "fs_hz": fs_hz, "duration_s": duration_s}, f)
    print(f"  Cached -> {FULL_SORT_CACHE}")

n_units = max(full_trains_ms.keys()) + 1
duration_ms = duration_s * 1000.0
total_spikes = sum(len(t) for t in full_trains_ms.values())
print(f"  n_units (max+1) = {n_units}, total spikes = {total_spikes}")


# Step 2 — Sync-fraction time series + artifact masking
print(f"[{now_iso()}] Building sync-fraction time series (bin={BURST_BIN_MS} ms)")
n_bins = int(np.ceil(duration_ms / BURST_BIN_MS))
bin_edges = np.arange(n_bins + 1) * BURST_BIN_MS
bin_centres = bin_edges[:-1] + BURST_BIN_MS / 2

# fired_in_bin[u, b] = whether unit u fired in bin b
fired_in_bin = np.zeros((n_units, n_bins), dtype=bool)
unit_bin_counts = np.zeros((n_units, n_bins), dtype=np.int32)
unit_ids_present = sorted(full_trains_ms.keys())
for uid in unit_ids_present:
    sp = full_trains_ms[uid]
    if len(sp) == 0:
        continue
    counts, _ = np.histogram(sp, bins=bin_edges)
    unit_bin_counts[uid] = counts.astype(np.int32)
    fired_in_bin[uid] = counts > 0

# Load manifest events for stim-time masking and later analysis
manifest = json.load(open(MANIFEST_PATH))
record0 = manifest["steps"]["record"][0]
recording_start_ms = record0["wells"][0]["start_time_ms"]
events = record0["events"]
stim_times_ms_logged = np.array([e["timestamp_ms"] - recording_start_ms for e in events], dtype=np.float64)
n_events = len(events)
print(f"  n_events = {n_events}")

# Artifact mask: zero out fired_in_bin in bins overlapping [stim, stim+ARTIFACT_MASK_MS]
artifact_bins = set()
for st in stim_times_ms_logged:
    b_lo = max(0, int(np.floor(st / BURST_BIN_MS)))
    b_hi = min(n_bins, int(np.ceil((st + ARTIFACT_MASK_MS) / BURST_BIN_MS)))
    for b in range(b_lo, b_hi):
        artifact_bins.add(b)
print(f"  Bins masked due to stim artifacts: {len(artifact_bins)} / {n_bins}")
fired_masked = fired_in_bin.copy()
for b in artifact_bins:
    fired_masked[:, b] = False  # treat as if no firing for sync-fraction purposes

n_units_active = sum(1 for u in unit_ids_present if len(full_trains_ms[u]) > 0)
sync_frac = fired_masked.sum(axis=0) / max(n_units_active, 1)
print(f"  n_units_active={n_units_active}; sync_frac stats: max={sync_frac.max():.3f}, mean={sync_frac.mean():.4f}")


# Step 3 — Burst detection (contiguous bins with sync_frac >= threshold)
print(f"[{now_iso()}] Detecting bursts (sync >= {BURST_SYNC_FRAC})")
above = sync_frac >= BURST_SYNC_FRAC
edges = np.diff(above.astype(np.int8))
starts = np.where(edges == 1)[0] + 1
stops = np.where(edges == -1)[0] + 1
if above[0]:
    starts = np.concatenate([[0], starts])
if above[-1]:
    stops = np.concatenate([stops, [n_bins]])
burst_windows_bins = list(zip(starts, stops))

# For each burst, find peak bin and peak time
burst_starts_ms = []
burst_ends_ms = []
burst_peaks_ms = []
burst_peak_sync = []
for (s, e) in burst_windows_bins:
    seg_sync = sync_frac[s:e]
    if len(seg_sync) == 0:
        continue
    peak_offset = int(np.argmax(seg_sync))
    peak_bin = s + peak_offset
    burst_starts_ms.append(bin_edges[s])
    burst_ends_ms.append(bin_edges[min(e, n_bins)])
    burst_peaks_ms.append(bin_centres[peak_bin])
    burst_peak_sync.append(float(seg_sync[peak_offset]))
burst_starts_ms = np.array(burst_starts_ms)
burst_ends_ms = np.array(burst_ends_ms)
burst_peaks_ms = np.array(burst_peaks_ms)
burst_peak_sync = np.array(burst_peak_sync)
n_bursts = len(burst_peaks_ms)
print(f"  n_bursts (full recording): {n_bursts}; mean peak sync = {np.mean(burst_peak_sync) if n_bursts else 0:.3f}")
print(f"  burst rate: {n_bursts / duration_s:.3f} per second  ({60*n_bursts/duration_s:.1f} per minute)")


# Step 4 — Per-stim Δt to latest preceding burst peak
print(f"[{now_iso()}] Computing Δt per stim event")
dt_to_last_burst_ms = np.full(n_events, np.nan, dtype=np.float64)
last_burst_idx = np.full(n_events, -1, dtype=np.int64)
for ei, st in enumerate(stim_times_ms_logged):
    prior = burst_peaks_ms[burst_peaks_ms < st]
    if len(prior) == 0:
        continue
    dt_to_last_burst_ms[ei] = st - prior.max()
    last_burst_idx[ei] = int(np.argmax(burst_peaks_ms == prior.max()))

valid_dt = ~np.isnan(dt_to_last_burst_ms)
print(f"  events with prior burst: {valid_dt.sum()} / {n_events}")
if valid_dt.sum() > 0:
    dt_arr = dt_to_last_burst_ms[valid_dt]
    print(f"  Δt stats (ms): min={dt_arr.min():.0f}, median={np.median(dt_arr):.0f}, "
          f"75th={np.percentile(dt_arr, 75):.0f}, max={dt_arr.max():.0f}")


# Step 5 — Per-event evoked response from existing sort_cache
print(f"[{now_iso()}] Loading existing sort_cache for per-event slice spikes")
with open(CYCLE_DIR / "sort_cache.pkl", "rb") as f:
    sort_cache = pickle.load(f)
slices_list = sort_cache["slices_list"]
n_slices = len(slices_list)
print(f"  n_slices = {n_slices}")
n_units_slice = getattr(slices_list[0], "N", len(slices_list[0].train))

# Per-event: total evoked spikes across K_consensus_excit (counts)
evk_count_K = np.zeros(n_events, dtype=np.int64)
pre_count_K = np.zeros(n_events, dtype=np.int64)
# Also per-event: evoked-rate delta summed over K_excit (for v3-like activity)
delta_act_K = np.zeros(n_events, dtype=np.float64)

for ei, slc in enumerate(slices_list):
    if not hasattr(slc, "train"):
        continue
    n_u = min(n_units_slice, getattr(slc, "N", len(slc.train)))
    for u in range(n_u):
        if u not in K_excit:
            continue
        sp = np.asarray(slc.train[u])
        if len(sp) == 0:
            continue
        n_pre = int(np.sum((sp >= -PRE_LEN_MS) & (sp < 0)))
        n_evk = int(np.sum((sp >= 5) & (sp < 200)))
        pre_count_K[ei] += n_pre
        evk_count_K[ei] += n_evk
        delta_act_K[ei] += (n_evk / EVK_LEN_MS - n_pre / PRE_LEN_MS)

print(f"  evk_count_K stats: mean={evk_count_K.mean():.2f}, max={evk_count_K.max()}")


# Step 6 — Bin Δt and aggregate evoked response per bin per condition
DT_BINS_MS = [0, 100, 200, 400, 800, 1600, 3200, 6400, 12800, np.inf]
DT_BIN_LABELS = []
for i in range(len(DT_BINS_MS) - 1):
    lo = DT_BINS_MS[i]
    hi = DT_BINS_MS[i + 1]
    if np.isinf(hi):
        DT_BIN_LABELS.append(f">{lo}ms")
    else:
        DT_BIN_LABELS.append(f"{lo}-{hi}ms")

def cond_label_of(L):
    return L.rsplit("_t", 1)[0] if "_t" in L else L

cond_to_event_idxs = defaultdict(list)
for ei, e in enumerate(events):
    cond_to_event_idxs[cond_label_of(e["label"])].append(ei)
condition_labels = sorted(cond_to_event_idxs.keys())
print(f"  Conditions: {len(condition_labels)} -> {condition_labels}")

dt_bin_idx = np.full(n_events, -1, dtype=np.int32)
for ei in range(n_events):
    if not valid_dt[ei]:
        continue
    dt = dt_to_last_burst_ms[ei]
    for bi in range(len(DT_BINS_MS) - 1):
        if DT_BINS_MS[bi] <= dt < DT_BINS_MS[bi + 1]:
            dt_bin_idx[ei] = bi
            break

results_per_cond = {}
for cond in condition_labels:
    eidx = np.array(cond_to_event_idxs[cond], dtype=np.int64)
    mean_per_bin = {}
    n_per_bin = {}
    for bi in range(len(DT_BINS_MS) - 1):
        sel = eidx[dt_bin_idx[eidx] == bi]
        if len(sel) == 0:
            mean_per_bin[bi] = float("nan")
            n_per_bin[bi] = 0
            continue
        mean_per_bin[bi] = float(evk_count_K[sel].mean())
        n_per_bin[bi] = int(len(sel))
    results_per_cond[cond] = {"mean_per_bin": mean_per_bin, "n_per_bin": n_per_bin}

# Print summary
print(f"\n=== Mean evoked spikes (summed across K_excit) per Δt bin per condition ===")
header = f"{'condition':<48}" + "".join(f"{L:>12}" for L in DT_BIN_LABELS) + f"{'TOTAL':>10}"
print(header)
for cond, res in results_per_cond.items():
    row = f"{cond:<48}"
    total_n = 0
    for bi in range(len(DT_BINS_MS) - 1):
        n = res["n_per_bin"][bi]
        m = res["mean_per_bin"][bi]
        total_n += n
        if n == 0:
            row += f"{'-':>12}"
        else:
            row += f"{m:>7.1f}(n={n:>2})"[:12].rjust(12)
    row += f"{total_n:>10}"
    print(row)


# Step 7 — Save and plot
print(f"\n[{now_iso()}] Saving results + plots")
result_dump = {
    "n_bursts": int(n_bursts),
    "burst_rate_per_s": float(n_bursts / duration_s),
    "duration_s": float(duration_s),
    "dt_bins_ms": DT_BINS_MS[:-1] + [None],  # None for inf
    "dt_bin_labels": DT_BIN_LABELS,
    "results_per_cond": {
        cond: {
            "mean_per_bin": [(None if np.isnan(v) else v) for v in res["mean_per_bin"].values()],
            "n_per_bin": list(res["n_per_bin"].values()),
        }
        for cond, res in results_per_cond.items()
    },
    "n_events": n_events,
    "n_events_with_prior_burst": int(valid_dt.sum()),
    "median_dt_ms": float(np.median(dt_to_last_burst_ms[valid_dt])) if valid_dt.sum() else None,
    "burst_peak_sync_mean": float(np.mean(burst_peak_sync)) if n_bursts else None,
    "burst_peak_sync_max": float(np.max(burst_peak_sync)) if n_bursts else None,
    "stim_times_ms": stim_times_ms_logged.tolist(),
    "burst_peaks_ms": burst_peaks_ms.tolist(),
    "burst_peak_sync": burst_peak_sync.tolist(),
    "dt_to_last_burst_ms_per_event": [None if np.isnan(d) else float(d) for d in dt_to_last_burst_ms],
    "evk_count_K_per_event": evk_count_K.tolist(),
    "pre_count_K_per_event": pre_count_K.tolist(),
}
with open(OUT_DIR / "burst_phase_results.json", "w") as f:
    json.dump(result_dump, f, indent=2)
print(f"  Saved -> {OUT_DIR / 'burst_phase_results.json'}")

# Plot 1 — Δt distribution
fig, ax = plt.subplots(1, 1, figsize=(9, 5))
dt_finite = dt_to_last_burst_ms[valid_dt]
ax.hist(np.log10(dt_finite[dt_finite > 0] + 1), bins=50, color="steelblue", alpha=0.7, edgecolor="black")
ax.set_xlabel("log10(Δt + 1)  [ms];  Δt = stim time − latest preceding burst peak")
ax.set_ylabel("count")
ax.set_title(f"Cycle 20: distribution of Δt (latest burst peak → stim)\nn_bursts={n_bursts}, n_events={n_events} (valid={valid_dt.sum()})")
ax.grid(alpha=0.3)
plt.savefig(OUT_DIR / "01_dt_distribution.png", dpi=110, bbox_inches="tight")
plt.close()

# Plot 2 — Heat map condition x Δt-bin -> mean evoked spike count
fig, ax = plt.subplots(1, 1, figsize=(11, 6))
heat = np.full((len(condition_labels), len(DT_BIN_LABELS)), np.nan)
for ci, cond in enumerate(condition_labels):
    res = results_per_cond[cond]
    for bi in range(len(DT_BIN_LABELS)):
        m = res["mean_per_bin"][bi]
        if not np.isnan(m):
            heat[ci, bi] = m
im = ax.imshow(heat, aspect="auto", cmap="viridis", interpolation="nearest")
ax.set_xticks(range(len(DT_BIN_LABELS)))
ax.set_xticklabels(DT_BIN_LABELS, rotation=30, ha="right")
ax.set_yticks(range(len(condition_labels)))
ax.set_yticklabels(condition_labels, fontsize=8)
plt.colorbar(im, ax=ax, label=f"mean evoked spikes per trial\n(summed across {len(K_excit)} K_excit units, [5, 200] ms)")
ax.set_title(f"Cycle 20: evoked response vs Δt-to-last-burst-peak")
# Annotate with N and mean
for ci in range(len(condition_labels)):
    for bi in range(len(DT_BIN_LABELS)):
        n = results_per_cond[condition_labels[ci]]["n_per_bin"][bi]
        m = results_per_cond[condition_labels[ci]]["mean_per_bin"][bi]
        if n == 0:
            continue
        ax.text(bi, ci, f"{m:.0f}\nn={n}", ha="center", va="center",
                color="white" if heat[ci, bi] < 0.6 * np.nanmax(heat) else "black",
                fontsize=7)
plt.savefig(OUT_DIR / "02_heatmap_condition_x_dt_bin.png", dpi=110, bbox_inches="tight")
plt.close()

# Plot 3 — Per-condition line plot evk vs Δt
fig, ax = plt.subplots(1, 1, figsize=(11, 7))
bin_centres_x = []
for i in range(len(DT_BIN_LABELS)):
    lo = DT_BINS_MS[i]
    hi = DT_BINS_MS[i + 1]
    if np.isinf(hi):
        c = lo * 2  # arbitrary placeholder for plotting; use lo + ~lo
    else:
        c = (lo + hi) / 2
    bin_centres_x.append(c)
bin_centres_x = np.array(bin_centres_x, dtype=np.float64)
bin_centres_x_pos = np.where(bin_centres_x == 0, 1, bin_centres_x)  # avoid log(0)

import itertools
colors = plt.cm.tab10.colors
markers = ["o", "s", "^", "D", "v", "P", "*", "X"]
for ci, cond in enumerate(condition_labels):
    res = results_per_cond[cond]
    xs = []
    ys = []
    for bi in range(len(DT_BIN_LABELS)):
        n = res["n_per_bin"][bi]
        m = res["mean_per_bin"][bi]
        if n >= 3 and not np.isnan(m):
            xs.append(bin_centres_x_pos[bi])
            ys.append(m)
    if not xs:
        continue
    ax.plot(xs, ys, marker=markers[ci % len(markers)], color=colors[ci % len(colors)],
            label=cond, linewidth=1.5, markersize=8, alpha=0.85)
ax.set_xscale("log")
ax.set_xlabel("Δt to latest burst peak (ms, log scale)")
ax.set_ylabel(f"mean evoked spikes per trial\n(summed across {len(K_excit)} K_excit units, [5, 200] ms)")
ax.set_title(f"Cycle 20: evoked response vs burst-phase  (n>=3 trials per bin shown)")
ax.legend(fontsize=7, loc="best")
ax.grid(alpha=0.3, which="both")
plt.savefig(OUT_DIR / "03_evk_vs_dt_lines.png", dpi=110, bbox_inches="tight")
plt.close()

# Plot 4 — Sync fraction time series with burst peaks + stim events
fig, ax = plt.subplots(1, 1, figsize=(14, 5))
ax.plot(bin_centres / 1000, sync_frac, color="black", linewidth=0.5, alpha=0.7)
ax.axhline(BURST_SYNC_FRAC, color="red", linestyle="--", alpha=0.7, label=f"burst threshold={BURST_SYNC_FRAC}")
ax.scatter(burst_peaks_ms / 1000, burst_peak_sync, color="orange", s=20, zorder=5, label=f"burst peaks (n={n_bursts})")
# Stim events at low y
ax.scatter(stim_times_ms_logged / 1000, np.full(n_events, -0.005), color="purple", s=2, marker="|", alpha=0.5, label="stim events")
ax.set_xlim(0, duration_s)
ax.set_ylim(-0.02, sync_frac.max() * 1.1)
ax.set_xlabel("recording time (s)")
ax.set_ylabel("sync fraction (units firing in 100 ms bin)")
ax.set_title(f"Cycle 20 sync_fraction full recording  (artifact bins masked)")
ax.legend(loc="upper right", fontsize=8)
ax.grid(alpha=0.3)
plt.savefig(OUT_DIR / "04_sync_fraction_full_recording.png", dpi=110, bbox_inches="tight")
plt.close()

print(f"\n=== Done. {len(list(OUT_DIR.glob('*.png')))} PNGs in {OUT_DIR}")
print(f"  Result JSON: burst_phase_results.json")
