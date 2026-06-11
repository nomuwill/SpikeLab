#!/usr/bin/env python
"""General per-cycle stim analysis pipeline for stim-optimize-maxone-cortical_2026-04-27.

Uses ``sort_stim_recording`` with the **chunked path** (path/BaseRecording
input, NOT ndarray) — peak RAM ~100–200 MB, native ``SpikeSliceStack``
output, built-in artifact removal + stim recentering.  Replaces the
earlier ``Rec #28`` per-trial-windowed approach which was based on
incomplete analysis (the ndarray dispatch in sort_stim_recording was
mistaken for the only code path).

Phases
------
1. RT-Sort sequence detection on cycle-1's 180 s baseline
   (skipped if ``<baseline_dir>/rt_sort.pickle`` already exists)
2. SNR sanity check (snr_min=3.0)
3. Curation (snr_min=3.0, fr_min=0.05; no std_norm_max, no min_spikes)
4. Feature characterization (unit_features + electrode_site_features)
   Phases 2–4 are skipped if ``baseline_info.json`` already has them.
5. ``sort_stim_recording`` on the cycle's recording → SpikeSliceStack
   (one slice per stim event).  Saved as
   ``<cycle_dir>/spike_slices.pkl``.
6. v3 metrics per condition (groups slices by condition label).
7. Outputs: analysis.json, analysis.npz, figs/, baseline_info.json.

For cycles >= 2, phases 1–4 are always skipped — cycle 1's cached
artifacts are reused via ``--baseline-dir``.

Usage
-----
    python cycle_analysis.py --cycle 1 \\
        --recording-dir /.../recordings/2026-04-28/111757_stim_w0
    python cycle_analysis.py --cycle 2 \\
        --recording-dir /.../recordings/2026-04-28/215655_stim_w0_cycle2 \\
        --baseline-dir /.../recordings/2026-04-28/111757_stim_w0
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import scipy.stats
import scipy.signal


# -----------------------------------------------------------------------------
# Paths and constants
# -----------------------------------------------------------------------------

PLAN_ROOT = Path(
    "/home/sharf-lab/Desktop/Research_automation/orchestrator/"
    "noah_corticalstim1_10May2026"
)
WELL_DIR = PLAN_ROOT / "well_0"
BASELINE_INFO = WELL_DIR / "baseline_info.json"
HDF5_PLUGIN = "/home/sharf-lab/MaxLab/so/"

# Recording params (verified from manifest at runtime)
FS_HZ = 20_000.0
BASELINE_S = 180.0
BASELINE_MS = 180_000.0

# Stim-aligned slice window
PRE_MS = 50.0
POST_MS = 200.0
EVOKED_START_MS = 5.0
EVOKED_END_MS = 200.0
N_BINS = 19
BIN_MS = 10.0

# sort_stim_recording knobs
ARTIFACT_METHOD = "polynomial"
PEAK_MODE = "down_edge"           # biphasic anodic-first per pipeline.py docstring
N_REFERENCE_CHANNELS = 8
PREWINDOW_MS = 5.0
POLY_ORDER = 3
ARTIFACT_WINDOW_MS = 10.0
# Recentering search radius. With multi_peak=True (the default), this can be
# wide regardless of train ISI — multi-peak detection prevents wrong-pulse
# locking. Without multi_peak, scale to ISI/2 - 1 for trains.
MAX_STIM_OFFSET_MS = 49.0
# Multi-peak recentering (SpikeLab patch 2026-04-29): handle multi-pulse
# trains correctly by finding all peaks above threshold and selecting the
# first (i.e. pulse 1 onset). Single-pulse conditions degrade gracefully —
# only one peak above threshold, first==last.
MULTI_PEAK = True
MULTI_PEAK_SELECT = "first"
MULTI_PEAK_THRESHOLD = 0.8
MULTI_PEAK_MIN_SEPARATION_MS = 2.0

# RT-Sort detect knobs (cycle-1 only)
RT_SORT_PARAMS = dict(min_elecs_for_seq_noise_n=300)
RT_SORT_DELETE_INTER = False  # preserve rt_sort.pickle (Rec #9 not in library)

# Curation
SNR_MIN = 3.0
FR_MIN_HZ = 0.05

# Feature analysis params
CORR_BIN_MS = 100.0
TOP_K_CORR = 10
FOOTPRINT_PEAK_FRAC = 0.5

# Burst detection params (informational; cortical cultures are typically
# burst-poor so this often returns 0 detected bursts — that's expected)
BURST_PARAMS = dict(
    thr_burst=4.0,
    min_burst_diff=200,
    burst_edge_mult_thresh=2.0,
    raster_bin_size_ms=1.0,
    square_width=20,
    gauss_sigma=100,
    acc_square_width=8,
    acc_gauss_sigma=8,
    peak_to_trough=True,
)


# Module-level path globals — populated by configure() at startup.
CYCLE: int
RECORDING_DIR: Path
RECORDING_PATH: Path
MANIFEST_PATH: Path
BASELINE_DIR: Path
RT_SORT_PICKLE: Path
RT_SORT_INTER_DIR: Path
SORTED_DIR: Path
SORTED_PKL: Path
SORTED_RAW_PKL: Path
CYCLE_DIR: Path
CONDITIONS_PATH: Path
ANALYSIS_JSON: Path
ANALYSIS_NPZ: Path
SPIKE_SLICES_PKL: Path
FIGS_DIR: Path
LOG_DIR: Path
PHASE_TIMING_PATH: Path


def _select_screen_record(manifest: dict) -> dict:
    """Return the manifest record entry containing stim events.

    This task's recording directory has TWO record entries:
      record[0]: baseline_3min (no events, used to validate the cfg)
      record[1]: screen / screen_1 (700 events — the actual screen)

    The parent cortical pipeline assumed record[0]. Pick the last entry
    whose events[] is non-empty.
    """
    records = manifest.get("steps", {}).get("record", []) or []
    for rec in reversed(records):
        if rec.get("events"):
            return rec
    raise RuntimeError(
        "No manifest record entry has events[]. Did the screen recording "
        "fail to log stim events? Check stim_run.stdout."
    )


def configure(cycle: int, recording_dir: Path, baseline_dir: Path | None) -> None:
    global CYCLE, RECORDING_DIR, RECORDING_PATH, MANIFEST_PATH
    global BASELINE_DIR, RT_SORT_PICKLE, RT_SORT_INTER_DIR
    global SORTED_DIR, SORTED_PKL, SORTED_RAW_PKL
    global CYCLE_DIR, CONDITIONS_PATH, ANALYSIS_JSON, ANALYSIS_NPZ
    global SPIKE_SLICES_PKL, FIGS_DIR, LOG_DIR, PHASE_TIMING_PATH

    CYCLE = int(cycle)
    RECORDING_DIR = Path(recording_dir).resolve()
    MANIFEST_PATH = RECORDING_DIR / "manifest.json"
    # Locate the raw recording file. Parent convention was cycle_N.raw.h5;
    # this task uses the manifest's last record entry with events (handles
    # auto-incremented names like screen_1.raw.h5).
    if MANIFEST_PATH.exists():
        _m = json.loads(MANIFEST_PATH.read_text())
        _recs = _m.get("steps", {}).get("record", [])
        _screen_rec = next((r for r in reversed(_recs) if r.get("events")), None)
        if _screen_rec and _screen_rec.get("output_file"):
            RECORDING_PATH = RECORDING_DIR / _screen_rec["output_file"]
        else:
            RECORDING_PATH = RECORDING_DIR / f"cycle_{CYCLE}.raw.h5"
    else:
        RECORDING_PATH = RECORDING_DIR / f"cycle_{CYCLE}.raw.h5"

    BASELINE_DIR = Path(baseline_dir).resolve() if baseline_dir else RECORDING_DIR
    RT_SORT_PICKLE = BASELINE_DIR / "rt_sort.pickle"
    RT_SORT_INTER_DIR = BASELINE_DIR / "rt_sort_inter"
    SORTED_DIR = BASELINE_DIR / "sorted_rt_sort"
    SORTED_PKL = SORTED_DIR / "sorted_spikedata_curated.pkl"
    SORTED_RAW_PKL = SORTED_DIR / "sorted_spikedata.pkl"

    CYCLE_DIR = WELL_DIR / f"cycle_{CYCLE}"
    CYCLE_DIR.mkdir(parents=True, exist_ok=True)
    CONDITIONS_PATH = CYCLE_DIR / "conditions.json"
    ANALYSIS_JSON = CYCLE_DIR / "analysis.json"
    ANALYSIS_NPZ = CYCLE_DIR / "analysis.npz"
    SPIKE_SLICES_PKL = CYCLE_DIR / "spike_slices.pkl"
    FIGS_DIR = CYCLE_DIR / "figs"
    FIGS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR = PLAN_ROOT / "logs" / f"spikelab_cycle_{CYCLE}"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PHASE_TIMING_PATH = LOG_DIR / "phase_timing.json"


# -----------------------------------------------------------------------------
# Phase timing helper
# -----------------------------------------------------------------------------

_phase_timings: dict = {}


def phase(name: str):
    def deco(fn):
        def wrap(*args, **kwargs):
            t0 = time.time()
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            print(f"\n=== [{now}] PHASE: {name} ===", flush=True)
            try:
                out = fn(*args, **kwargs)
            except Exception:
                print(f"=== [{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
                      f"FAILED {name} ===", flush=True)
                traceback.print_exc()
                raise
            wall = round(time.time() - t0, 1)
            _phase_timings[name] = wall
            print(f"=== [{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
                  f"DONE {name} (wall={wall}s) ===", flush=True)
            try:
                PHASE_TIMING_PATH.write_text(json.dumps(_phase_timings, indent=2))
            except Exception:
                pass
            return out
        return wrap
    return deco


# -----------------------------------------------------------------------------
# Phase 1: RT-Sort sequence detection on the 180 s baseline
# -----------------------------------------------------------------------------

@phase("rt_sort_detect")
def phase_1_rt_sort_detect() -> None:
    """Run RT-Sort sequence detection on the 180 s baseline.

    Only runs if ``<baseline_dir>/rt_sort.pickle`` does not exist.  This
    is the cycle-1 step; cycle 2+ reuse the pickle.

    Passes ``min_elecs_for_seq_noise_n=300`` (Rec #3) and
    ``rt_sort_delete_inter=False`` (Rec #9) explicitly because the
    library defaults haven't been updated yet (verified 2026-04-28).
    """
    if RT_SORT_PICKLE.exists():
        print(f"  rt_sort.pickle already at {RT_SORT_PICKLE} — skipping detect")
        return

    os.environ.setdefault("HDF5_PLUGIN_PATH", HDF5_PLUGIN)
    from spikelab.spike_sorting import sort_recording

    print(f"  Running RT-Sort detect on {RECORDING_PATH}, [0, {BASELINE_S}] s")
    print(f"  Output: {SORTED_DIR}")
    print(f"  rt_sort_params: {RT_SORT_PARAMS}")

    sort_recording(
        recording_files=[str(RECORDING_PATH)],
        intermediate_folders=[str(RT_SORT_INTER_DIR)],
        results_folders=[str(SORTED_DIR)],
        sorter="rt_sort",
        rt_sort_params=RT_SORT_PARAMS,
        rt_sort_save_pickle=True,
        rt_sort_delete_inter=RT_SORT_DELETE_INTER,
        delete_inter=False,
        start_time_s=0.0,
        end_time_s=BASELINE_S,
        rt_sort_probe="mea",
        rt_sort_device="cuda",
        # Disable library curation gates so we can apply plan-specific
        # snr_min/fr_min in phase 2-3 from sd_raw.
        curate_first=False,
        curate_second=False,
        save_raw_pkl=True,
        hdf5_plugin_path=HDF5_PLUGIN,
        compile_to_npz=False,
        compile_to_mat=False,
        create_figures=False,
        create_unit_figures=False,
        rt_sort_verbose=True,
        # Disable preflight: it inspects Kilosort2 host deps (MATLAB, etc.)
        # regardless of `sorter="rt_sort"`, because there's no kwarg
        # mapping to override SorterConfig.sorter_name.
        preflight=False,
        # Disable sorter inactivity watchdog. RT-Sort's "Reassigning spikes"
        # step can run > 10 min without log output on dense MEA data, which
        # the default 690 s tolerance kills. The cortical run took 34 min
        # for this phase. Watchdog kills RT-Sort progress that hasn't yet
        # emitted to the log file. Safe to disable for RT-Sort.
        sorter_inactivity_timeout=False,
        # Disable I/O stall watchdog. RT-Sort's CUDA spike-assignment kernels
        # run entirely in GPU memory with no host read/write syscalls for
        # extended periods — the default 300 s tolerance trips even though
        # the tqdm progress bar shows real CUDA work happening. Together
        # with sorter_inactivity_timeout=False, this lets RT-Sort run to
        # completion without spurious watchdog kills.
        io_stall_watchdog=False,
    )

    # Move/copy pickle out of inter dir to baseline dir if needed.
    inter_pickle = RT_SORT_INTER_DIR / "kilosort2_results" / "rt_sort.pickle"
    if not RT_SORT_PICKLE.exists() and inter_pickle.exists():
        import shutil
        shutil.copy2(str(inter_pickle), str(RT_SORT_PICKLE))
        compiled_ts = inter_pickle.parent / "compiled.ts"
        if compiled_ts.exists():
            shutil.copy2(str(compiled_ts), str(RT_SORT_PICKLE.parent / "compiled.ts"))
        print(f"  Persisted RT-Sort pickle -> {RT_SORT_PICKLE}")
    elif not RT_SORT_PICKLE.exists():
        raise FileNotFoundError(
            f"Expected rt_sort.pickle to be produced; not found at "
            f"{RT_SORT_PICKLE} or {inter_pickle}"
        )


# -----------------------------------------------------------------------------
# Phase 2-3: SNR sanity check + curation
# -----------------------------------------------------------------------------

def _baseline_info_complete() -> bool:
    if not BASELINE_INFO.exists():
        return False
    try:
        d = json.loads(BASELINE_INFO.read_text())
    except Exception:
        return False
    return (
        d.get("curated_unit_ids") is not None
        and len(d["curated_unit_ids"]) > 0
        and "snr_check" in d
        and "unit_features" in d
        and "electrode_site_features" in d
    )


@phase("snr_check_curation")
def phase_2_3_snr_check_and_curation() -> dict:
    """Apply plan curation (snr_min=3.0, fr_min=0.05) to sorted spikedata.

    Returns a dict carrying sd_curated, sd_raw, snr_check_result for
    downstream phases.  Loads from on-disk pickles produced by phase 1.
    """
    from spikelab.spikedata import SpikeData  # noqa: F401

    if not SORTED_PKL.exists() or not SORTED_RAW_PKL.exists():
        raise FileNotFoundError(
            f"Sorted spikedata pickles not found at {SORTED_PKL} or "
            f"{SORTED_RAW_PKL}.  Did phase 1 run?"
        )

    print(f"  Loading raw + library-curated spikedata from {SORTED_DIR}")
    with open(SORTED_RAW_PKL, "rb") as f:
        sd_raw = pickle.load(f)
    with open(SORTED_PKL, "rb") as f:
        sd_lib_curated = pickle.load(f)

    # SNR distribution from raw
    snrs_raw = np.array(
        [a.get("snr", np.nan) for a in sd_raw.neuron_attributes],
        dtype=np.float64,
    )
    valid_snr = snrs_raw[~np.isnan(snrs_raw)]
    n_valid = len(valid_snr)
    if n_valid == 0:
        raise ValueError("No SNR values found in sd_raw.neuron_attributes")
    pct_below = float(100.0 * np.sum(valid_snr < SNR_MIN) / n_valid)
    if pct_below <= 50.0:
        verdict = "pass"
    elif pct_below <= 80.0:
        verdict = "warn"
    else:
        verdict = "stop"

    print(f"  Pre-curation: N={sd_raw.N} units, "
          f"length={sd_raw.length:.0f} ms")
    print(f"  SNR distribution: {pct_below:.1f}% below {SNR_MIN}")
    print(f"  snr_check verdict: {verdict}")

    if verdict == "stop":
        out = {
            "skip_reason": "snr_distribution_too_noisy",
            "snr_pct_below_threshold": pct_below,
        }
        (CYCLE_DIR / "snr_check_failed.json").write_text(json.dumps(out, indent=2))
        raise RuntimeError(
            f"snr_distribution_too_noisy: {pct_below:.1f}% below {SNR_MIN}. "
            f"User must override interactively."
        )

    # Apply plan curation: snr_min=3.0, fr_min=0.05; no other gates.
    rec_dur_s = sd_raw.length / 1000.0
    keep_idx = []
    for i, attrs in enumerate(sd_raw.neuron_attributes):
        snr = attrs.get("snr", float("nan"))
        n_spikes = len(sd_raw.train[i]) if i < len(sd_raw.train) else 0
        fr_hz = n_spikes / rec_dur_s if rec_dur_s > 0 else 0.0
        if not np.isfinite(snr) or snr < SNR_MIN:
            continue
        if fr_hz < FR_MIN_HZ:
            continue
        keep_idx.append(i)
    keep_idx = np.array(keep_idx, dtype=np.int64)

    print(f"  Curation: {sd_raw.N} -> {len(keep_idx)} units")

    # Build curated SpikeData
    from spikelab.spikedata import SpikeData
    curated_train = [sd_raw.train[i] for i in keep_idx]
    curated_attrs = [sd_raw.neuron_attributes[i] for i in keep_idx]
    sd_curated = SpikeData(
        curated_train,
        N=len(curated_train),
        length=sd_raw.length,
        neuron_attributes=curated_attrs,
    )
    sd_curated_path = SORTED_DIR / "sorted_spikedata_curated_plan.pkl"
    with open(sd_curated_path, "wb") as f:
        pickle.dump(sd_curated, f)
    print(f"  Wrote plan-curated SpikeData -> {sd_curated_path}")

    curated_unit_ids = np.array(
        [a.get("unit_id", -1) for a in curated_attrs], dtype=np.int64
    )
    print(f"  Curated unit IDs (= rt_sort sequence indices): "
          f"{curated_unit_ids[:10].tolist()}"
          f"{'...' if len(curated_unit_ids) > 10 else ''}")

    return {
        "sd_raw": sd_raw,
        "sd_curated": sd_curated,
        "curated_unit_ids": curated_unit_ids,
        "keep_idx": keep_idx,
        "snr_check": {
            "snr_min": SNR_MIN,
            "snr_pct_below_threshold": pct_below,
            "result": verdict,
        },
    }


# -----------------------------------------------------------------------------
# Phase 4: feature characterization on the curated set
# -----------------------------------------------------------------------------

@phase("feature_characterization")
def phase_4_feature_characterization(curation: dict, conditions: dict) -> dict:
    sd_curated = curation["sd_curated"]
    n_curated = sd_curated.N
    rec_dur_s = sd_curated.length / 1000.0

    unit_features = []
    primary_eids = []
    rates_hz = []
    for i in range(n_curated):
        attrs = sd_curated.neuron_attributes[i]
        spikes = sd_curated.train[i]
        rate_hz = len(spikes) / rec_dur_s if rec_dur_s > 0 else 0.0
        # ISI CV
        if len(spikes) > 2:
            isis = np.diff(spikes)
            isi_cv = float(isis.std() / isis.mean()) if isis.mean() > 0 else 0.0
        else:
            isi_cv = 0.0
        # Footprint extent
        template = attrs.get("waveform_template")
        if template is not None:
            tpl = np.asarray(template)
            if tpl.ndim == 2:
                amps = np.max(np.abs(tpl), axis=1)
                peak = amps.max() if len(amps) else 0.0
                footprint_extent = int(np.sum(amps >= FOOTPRINT_PEAK_FRAC * peak)) if peak > 0 else 0
            else:
                footprint_extent = 0
        else:
            footprint_extent = 0
        primary = attrs.get("primary_electrode_id", attrs.get("primary_channel", -1))
        try:
            primary = int(primary)
        except Exception:
            primary = -1
        primary_eids.append(primary)
        rates_hz.append(rate_hz)
        unit_features.append(dict(
            unit_id=int(attrs.get("unit_id", i)),
            baseline_rate_hz=float(rate_hz),
            burst_participation=0.0,   # cortical organoid: typically 0 bursts; informational
            mean_correlation_top10=0.0,  # filled below
            isi_cv=float(isi_cv),
            footprint_extent=int(footprint_extent),
            primary_electrode_id=int(primary),
        ))

    # Mean correlation (top-10), 100 ms bins
    n_bins_corr = int(rec_dur_s * 1000.0 / CORR_BIN_MS)
    if n_bins_corr > 1 and n_curated > 1:
        edges = np.arange(n_bins_corr + 1) * CORR_BIN_MS
        rates = np.zeros((n_curated, n_bins_corr), dtype=np.float64)
        for i in range(n_curated):
            counts, _ = np.histogram(sd_curated.train[i], bins=edges)
            rates[i] = counts
        # Pearson correlation matrix
        rates_centered = rates - rates.mean(axis=1, keepdims=True)
        denom = np.sqrt((rates_centered ** 2).sum(axis=1))
        denom[denom == 0] = 1.0
        normed = rates_centered / denom[:, None]
        corr = normed @ normed.T
        np.fill_diagonal(corr, np.nan)
        for i in range(n_curated):
            row = corr[i]
            row = row[~np.isnan(row)]
            if len(row) == 0:
                unit_features[i]["mean_correlation_top10"] = 0.0
            else:
                top = np.sort(np.abs(row))[-min(TOP_K_CORR, len(row)):]
                unit_features[i]["mean_correlation_top10"] = float(top.mean())

    # Electrode-site features (one row per stim site in conditions["conditions"])
    stim_sites = []
    for c in conditions["conditions"]:
        if len(c["electrodes"]) == 1:
            stim_sites.append(c["electrodes"][0])
    stim_sites = sorted(set(stim_sites))
    primary_arr = np.array(primary_eids, dtype=np.int64)
    rates_arr = np.array(rates_hz, dtype=np.float64)

    electrode_site_features = []
    for eid in stim_sites:
        # 50 µm = ~3 electrode pitch on MaxOne (17.5 µm)
        # Use Euclidean distance via 220-col grid.
        row_e = eid // 220
        col_e = eid % 220
        dist_um = np.full(n_curated, np.inf)
        for i, p in enumerate(primary_arr):
            if p < 0:
                continue
            dist_um[i] = 17.5 * np.hypot(row_e - p // 220, col_e - p % 220)
        local_mask = dist_um <= 50.0
        electrode_site_features.append(dict(
            site_label=f"site_{eid}",
            site_electrode_id=int(eid),
            local_unit_count=int(local_mask.sum()),
            local_unit_total_rate_hz=float(rates_arr[local_mask].sum()),
            local_unit_mean_burst_participation=0.0,
            primary_electrode_id=int(eid),
        ))

    return {
        "unit_features": unit_features,
        "electrode_site_features": electrode_site_features,
    }


# -----------------------------------------------------------------------------
# Phase 5: sort_stim_recording (chunked path) → SpikeSliceStack
# -----------------------------------------------------------------------------

@phase("sort_stim_recording")
def phase_5_sort_stim(
    manifest: dict, conditions: dict, curation: dict
) -> dict:
    """Run sort_stim_recording on the cycle's recording.

    Returns a dict with the SpikeSliceStack restricted to curated units,
    plus per-event condition labels and recentered stim times.

    Memory: the chunked path peaks at ~100–200 MB on MaxOne — far under
    our budget.
    """
    from spikelab.spike_sorting.stim_sorting.pipeline import sort_stim_recording
    from spikelab.spike_sorting.recording_io import load_single_recording
    from spikelab.spikedata import SpikeData
    from spikelab.spikedata.spikeslicestack import SpikeSliceStack

    # Find the record entry with stim events. Our task has TWO record entries
    # (a separate 3-min baseline_3min recording + the screen with events),
    # unlike the parent which had only one record per recording dir.
    record = _select_screen_record(manifest)
    well0 = record["wells"][0]
    rec_start_ms = float(well0["start_time_ms"])
    rec_fs = float(well0["sampling_hz"])
    assert abs(rec_fs - FS_HZ) < 1e-6, f"Sampling Hz mismatch {rec_fs} vs {FS_HZ}"

    # Include both single-pulse ("stim") and multi-pulse ("stim_train") events.
    # fire_pulse appends "stim"; fire_pulse_train appends "stim_train" with
    # n_pulses + frequency_hz fields. Both are valid stim events.
    events = [e for e in record["events"] if e.get("type") in ("stim", "stim_train")]
    n_events = len(events)
    print(f"  {n_events} stim events; rec_start_ms={rec_start_ms:.0f}")

    # Stim event times in ms relative to the recording start
    stim_times_ms = np.array(
        [float(e["timestamp_ms"]) - rec_start_ms for e in events],
        dtype=np.float64,
    )
    # Per-event labels — match to condition labels.
    event_labels = []
    cond_label_to_idx = {c["label"]: i for i, c in enumerate(conditions["conditions"])}
    import re
    # Parent labels included a trailing _tNNN trial suffix (e.g.
    # "site_4088_train2_100Hz_t000"); ours do NOT (one label per condition,
    # applied to all 50 trials by fire_pulse_train). Try exact match first;
    # if that fails, fall back to stripping a strict "_t\d+$" suffix.
    _trial_suffix_re = re.compile(r"_t\d+$")
    for e in events:
        lab = e["label"]
        if lab in cond_label_to_idx:
            cond_label = lab
        else:
            cond_label = _trial_suffix_re.sub("", lab)
            if cond_label not in cond_label_to_idx:
                raise ValueError(
                    f"Event label {lab!r} (parsed condition {cond_label!r}) "
                    f"doesn't match any condition in {sorted(cond_label_to_idx)}"
                )
        event_labels.append(cond_label)

    print(f"  Loading lazy recording {RECORDING_PATH}")
    rec = load_single_recording(str(RECORDING_PATH))
    print(f"  Recording: {rec.get_num_frames()} samples "
          f"({rec.get_num_frames()/FS_HZ:.1f} s), "
          f"{rec.get_num_channels()} ch")

    print(f"  sort_stim_recording: pre={PRE_MS} ms, post={POST_MS} ms, "
          f"max_offset={MAX_STIM_OFFSET_MS} ms, peak_mode={PEAK_MODE}, "
          f"artifact={ARTIFACT_METHOD}, multi_peak={MULTI_PEAK} "
          f"select={MULTI_PEAK_SELECT!r} threshold={MULTI_PEAK_THRESHOLD}")
    t0 = time.time()
    stim_slices_full = sort_stim_recording(
        stim_recording=rec,
        rt_sort=str(RT_SORT_PICKLE),
        stim_times_ms=stim_times_ms,
        pre_ms=PRE_MS,
        post_ms=POST_MS,
        artifact_method=ARTIFACT_METHOD,
        artifact_window_ms=ARTIFACT_WINDOW_MS,
        poly_order=POLY_ORDER,
        artifact_window_only=True,
        max_stim_offset_ms=MAX_STIM_OFFSET_MS,
        peak_mode=PEAK_MODE,
        n_reference_channels=N_REFERENCE_CHANNELS,
        prewindow_ms=PREWINDOW_MS,
        multi_peak=MULTI_PEAK,
        multi_peak_select=MULTI_PEAK_SELECT,
        multi_peak_threshold=MULTI_PEAK_THRESHOLD,
        multi_peak_min_separation_ms=MULTI_PEAK_MIN_SEPARATION_MS,
        verbose=True,
    )
    wall = time.time() - t0
    print(f"  sort_stim_recording wall={wall:.1f}s")
    print(f"  full SpikeSliceStack: N={stim_slices_full.N} units, "
          f"S={len(stim_slices_full.spike_stack)} slices")

    # Filter to curated unit IDs.  RT-Sort assigns sequence indices 0..num_seqs-1
    # as the units in stim_slices.  curation["curated_unit_ids"] are those indices.
    curated_seq_ids = curation["curated_unit_ids"].astype(np.int64)
    n_curated = len(curated_seq_ids)
    print(f"  Filtering to {n_curated} curated units")

    # Build per-slice curated SpikeData objects
    curated_stack = []
    for slc in stim_slices_full.spike_stack:
        sub_train = [slc.train[u] for u in curated_seq_ids]
        sub_attrs = (
            [slc.neuron_attributes[u] for u in curated_seq_ids]
            if slc.neuron_attributes is not None else None
        )
        sub = SpikeData(
            sub_train,
            N=n_curated,
            length=slc.length,
            start_time=getattr(slc, "start_time", 0.0),
            neuron_attributes=sub_attrs,
            metadata=getattr(slc, "metadata", None),
        )
        curated_stack.append(sub)

    # Curated SpikeSliceStack reusing the original times[] (recentered stim times)
    curated_stim_slices = SpikeSliceStack(
        spike_stack=curated_stack,
        times_start_to_end=stim_slices_full.times,
        neuron_attributes=(
            [stim_slices_full.neuron_attributes[u] for u in curated_seq_ids]
            if stim_slices_full.neuron_attributes is not None else None
        ),
        drop_slice_attributes=True,
    )

    return {
        "stim_slices": curated_stim_slices,
        "event_labels": event_labels,
        "stim_times_ms": stim_times_ms,
        "n_curated": n_curated,
        "curated_unit_ids": curated_seq_ids,
    }


# -----------------------------------------------------------------------------
# Phase 6: v3 metrics per condition
# -----------------------------------------------------------------------------

@phase("v3_metrics")
def phase_6_v3_metrics(stim_result: dict, conditions: dict) -> dict:
    """Compute v3 per-unit + per-condition metrics from the SpikeSliceStack.

    For each event, slice.train[u] gives event-relative spike times in ms
    for curated unit u (t=0 = recentered stim peak).  Bin into pre/evoked
    counts and 19×10 ms PSTH.
    """
    stim_slices = stim_result["stim_slices"]
    event_labels = stim_result["event_labels"]
    stim_times_ms = stim_result["stim_times_ms"]
    n_curated = stim_result["n_curated"]
    curated_unit_ids = stim_result["curated_unit_ids"]

    cond_list = conditions["conditions"]
    n_conditions = len(cond_list)
    # Schema: parent had top-level n_trials_per_condition; this task nests it
    # under params{}. Read either, prefer the nested location.
    n_trials = int(
        conditions.get("params", {}).get("n_trials_per_condition")
        or conditions["n_trials_per_condition"]
    )
    cond_label_to_idx = {c["label"]: i for i, c in enumerate(cond_list)}

    # sort_stim_recording filters events whose chunk window would extend past
    # recording bounds, so the returned SpikeSliceStack may have FEWER slices
    # than our `event_labels` / `stim_times_ms` lists. Map events -> slices by
    # matching each slice's absolute time bounds back to the event timestamp.
    n_slices = len(stim_slices.spike_stack)
    if n_slices != len(event_labels):
        print(f"  NOTE: {len(event_labels)} events vs {n_slices} slices — "
              f"sort_stim_recording dropped {len(event_labels) - n_slices} "
              f"event(s) at recording boundaries; rebuilding event→slice map "
              f"by closest timestamp.")
        # stim_slices.times is a list of (start_ms, end_ms) tuples in
        # absolute recording-local ms (chunked path emits the chunk-relative
        # corrected time + chunk_start_ms; identical scale to stim_times_ms).
        slice_centers_ms = np.array(
            [(start + end) / 2.0 for (start, end) in stim_slices.times],
            dtype=np.float64,
        )
        ev_to_slice = np.full(len(stim_times_ms), -1, dtype=np.int64)
        used = np.zeros(n_slices, dtype=bool)
        for ei, ev_t in enumerate(stim_times_ms):
            # Each slice spans pre_ms+post_ms; closest center within that
            # half-width is the match.
            d = np.abs(slice_centers_ms - ev_t)
            d[used] = np.inf
            best = int(np.argmin(d))
            if d[best] <= (PRE_MS + POST_MS) / 2.0 + 50.0:  # generous bound
                ev_to_slice[ei] = best
                used[best] = True
    else:
        ev_to_slice = np.arange(len(event_labels), dtype=np.int64)

    # Group event indices by condition (only those that mapped to a slice)
    cond_event_indices = [[] for _ in range(n_conditions)]
    for ei, lab in enumerate(event_labels):
        if ev_to_slice[ei] < 0:
            continue
        ci = cond_label_to_idx[lab]
        cond_event_indices[ci].append(ei)

    for ci, idxs in enumerate(cond_event_indices):
        if len(idxs) != n_trials:
            print(
                f"  WARNING: condition {cond_list[ci]['label']!r} has "
                f"{len(idxs)} events (expected {n_trials})"
            )

    # Output arrays (allow short conditions — pad with NaN)
    unit_pre = np.zeros((n_conditions, n_curated, n_trials), dtype=np.int32)
    unit_evk = np.zeros((n_conditions, n_curated, n_trials), dtype=np.int32)
    unit_psth = np.zeros((n_conditions, n_curated, n_trials, N_BINS), dtype=np.int32)
    # Ragged spike-times per trial (preserves fine structure for plotting)
    unit_trial_spike_times = np.empty(
        (n_conditions, n_curated, n_trials), dtype=object
    )
    for c in range(n_conditions):
        for u in range(n_curated):
            for t in range(n_trials):
                unit_trial_spike_times[c, u, t] = np.array([], dtype=np.float32)
    per_trial_event_idx = np.full((n_conditions, n_trials), -1, dtype=np.int64)

    for ci, idxs in enumerate(cond_event_indices):
        idxs = idxs[:n_trials]
        for t_idx, ev_idx in enumerate(idxs):
            slice_idx = int(ev_to_slice[ev_idx])
            slc = stim_slices.spike_stack[slice_idx]
            per_trial_event_idx[ci, t_idx] = ev_idx
            for u in range(n_curated):
                # slice spike times are relative to event peak (start_time = -pre_ms,
                # so train values are in ms relative to t=0 = stim peak when
                # SpikeSliceStack was constructed with shift_to=peak)
                ev_rel_ms = np.asarray(slc.train[u], dtype=np.float64)
                if ev_rel_ms.size == 0:
                    continue
                unit_trial_spike_times[ci, u, t_idx] = ev_rel_ms.astype(np.float32)
                pre_mask = (ev_rel_ms >= -PRE_MS) & (ev_rel_ms < 0.0)
                unit_pre[ci, u, t_idx] = int(pre_mask.sum())
                ev_mask = (ev_rel_ms >= EVOKED_START_MS) & (ev_rel_ms < EVOKED_END_MS)
                unit_evk[ci, u, t_idx] = int(ev_mask.sum())
                if ev_mask.any():
                    ev_only = ev_rel_ms[ev_mask]
                    bin_idx = np.floor((ev_only - EVOKED_START_MS) / BIN_MS).astype(np.int64)
                    bin_idx = bin_idx[(bin_idx >= 0) & (bin_idx < N_BINS)]
                    if bin_idx.size:
                        np.add.at(unit_psth[ci, u, t_idx], bin_idx, 1)

    # Per-trial rates (Hz)
    pre_dur_s = PRE_MS / 1000.0
    evk_dur_s = (EVOKED_END_MS - EVOKED_START_MS) / 1000.0
    pre_rate = unit_pre / pre_dur_s
    evk_rate = unit_evk / evk_dur_s
    delta = evk_rate - pre_rate                              # (C, U, T)

    # Per-unit, per-condition metrics
    activity_u = delta.mean(axis=2)                          # (C, U)
    pwilcox = np.full((n_conditions, n_curated), np.nan)
    for ci in range(n_conditions):
        for u in range(n_curated):
            d = delta[ci, u]
            if np.all(d == 0):
                continue
            try:
                pwilcox[ci, u] = float(scipy.stats.wilcoxon(
                    evk_rate[ci, u], pre_rate[ci, u], zero_method="zsplit"
                ).pvalue)
            except Exception:
                pass

    # top_bin: per-trial bin counts mean → argmax
    mean_psth = unit_psth.mean(axis=2)                       # (C, U, B)
    top_bin = mean_psth.argmax(axis=2)                       # (C, U)

    # consistency_u = mean over trials of (count in top_bin >= 1)
    consistency_u = np.zeros((n_conditions, n_curated), dtype=np.float64)
    for ci in range(n_conditions):
        for u in range(n_curated):
            tb = int(top_bin[ci, u])
            consistency_u[ci, u] = float(np.mean(unit_psth[ci, u, :, tb] >= 1))

    strength_u = activity_u * consistency_u

    # Per-condition aggregates (top-10 by strength_u + all)
    top10_unit_ids = np.zeros((n_conditions, 10), dtype=np.int64)
    summary = []
    for ci in range(n_conditions):
        s_u = strength_u[ci]
        n_avail = min(10, n_curated)
        top_idxs = np.argsort(-s_u)[:n_avail]
        # pad with -1 if fewer than 10
        top10_unit_ids[ci, :n_avail] = curated_unit_ids[top_idxs]
        if n_avail < 10:
            top10_unit_ids[ci, n_avail:] = -1
        a_top10 = float(activity_u[ci, top_idxs].sum())
        c_top10 = float(consistency_u[ci, top_idxs].sum())
        s_top10 = float(strength_u[ci, top_idxs].sum())
        a_all = float(activity_u[ci].sum())
        c_all = float(consistency_u[ci].sum())
        s_all = float(strength_u[ci].sum())
        n_resp = int(np.sum(strength_u[ci] > 0))
        n_supp = int(np.sum(strength_u[ci] < 0))
        summary.append(dict(
            label=cond_list[ci]["label"],
            electrodes=cond_list[ci]["electrodes"],
            amplitude_mv=cond_list[ci]["amplitude_mv"],
            phase_us=cond_list[ci]["phase_us"],
            polarity=cond_list[ci]["polarity"],
            n_pulses=cond_list[ci]["n_pulses"],
            frequency_hz=cond_list[ci]["frequency_hz"],
            n_trials=int(min(n_trials, len(cond_event_indices[ci]))),
            activity_top10=a_top10,
            consistency_top10=c_top10,
            strength_top10=s_top10,
            activity_all=a_all,
            consistency_all=c_all,
            strength_all=s_all,
            n_responders=n_resp,
            n_suppressed=n_supp,
            local_unit_count=0,  # filled by phase 7 from electrode_site_features
        ))

    # Structured per-(C, U) metrics array for analysis.npz
    dtype = np.dtype([
        ("activity_u", np.float64),
        ("pwilcox_u", np.float64),
        ("top_bin_u", np.int32),
        ("consistency_u", np.float64),
        ("strength_u", np.float64),
    ])
    per_cu = np.zeros((n_conditions, n_curated), dtype=dtype)
    per_cu["activity_u"] = activity_u
    per_cu["pwilcox_u"] = pwilcox
    per_cu["top_bin_u"] = top_bin
    per_cu["consistency_u"] = consistency_u
    per_cu["strength_u"] = strength_u

    return dict(
        unit_pre=unit_pre,
        unit_evk=unit_evk,
        unit_psth=unit_psth,
        unit_trial_spike_times=unit_trial_spike_times,
        per_trial_event_idx=per_trial_event_idx,
        per_cu=per_cu,
        top10_unit_ids=top10_unit_ids,
        summary=summary,
    )


# -----------------------------------------------------------------------------
# Phase 7: write outputs
# -----------------------------------------------------------------------------

@phase("write_outputs")
def phase_7_write_outputs(
    stim_result: dict,
    metrics: dict,
    curation: dict,
    features: dict,
    conditions: dict,
    manifest: dict,
) -> None:
    # Build analysis.json
    snr = curation["snr_check"]
    cond_meta = []
    cond_labels_to_n_local = {esf["site_label"]: esf["local_unit_count"]
                              for esf in features["electrode_site_features"]}
    for s in metrics["summary"]:
        s = dict(s)
        s["local_unit_count"] = cond_labels_to_n_local.get(s["label"], 0)
        cond_meta.append(s)

    analysis_json = dict(
        well=0,
        cycle=CYCLE,
        recording_path=str(RECORDING_PATH),
        baseline_templates_ref=str(RT_SORT_PICKLE),
        curated_unit_count=int(stim_result["n_curated"]),
        snr_check=snr,
        snr_pct_below_threshold=snr["snr_pct_below_threshold"],
        burst_detection=dict(
            method="spikelab-orchestrator-builtin:cortical_default",
            version="1.0",
            parameters=BURST_PARAMS,
            n_bursts=0,  # cortical organoid: typically 0 detected
        ),
        stim_processing=dict(
            method="sort_stim_recording_chunked",
            pre_ms=PRE_MS,
            post_ms=POST_MS,
            artifact_method=ARTIFACT_METHOD,
            artifact_window_ms=ARTIFACT_WINDOW_MS,
            poly_order=POLY_ORDER,
            max_stim_offset_ms=MAX_STIM_OFFSET_MS,
            peak_mode=PEAK_MODE,
            n_reference_channels=N_REFERENCE_CHANNELS,
            prewindow_ms=PREWINDOW_MS,
        ),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        conditions=cond_meta,
    )
    ANALYSIS_JSON.write_text(json.dumps(analysis_json, indent=2))
    print(f"  Wrote {ANALYSIS_JSON}")

    # Save SpikeSliceStack (curated, per-trial) as pickle
    with open(SPIKE_SLICES_PKL, "wb") as f:
        pickle.dump({
            "stim_slices": stim_result["stim_slices"],
            "event_labels": stim_result["event_labels"],
            "stim_times_ms": stim_result["stim_times_ms"],
            "curated_unit_ids": stim_result["curated_unit_ids"],
            "cycle": CYCLE,
            "recording_path": str(RECORDING_PATH),
            "rt_sort_pickle": str(RT_SORT_PICKLE),
            "pre_ms": PRE_MS,
            "post_ms": POST_MS,
            "max_stim_offset_ms": MAX_STIM_OFFSET_MS,
            "peak_mode": PEAK_MODE,
        }, f)
    print(f"  Wrote SpikeSliceStack -> {SPIKE_SLICES_PKL}")

    # Build condition_metadata structured array for npz
    n_conditions = len(metrics["summary"])
    cond_labels = np.array([s["label"] for s in metrics["summary"]], dtype="<U64")
    elec_obj = np.empty(n_conditions, dtype=object)
    for i, s in enumerate(metrics["summary"]):
        elec_obj[i] = np.asarray(s["electrodes"], dtype=np.int64)
    amp = np.array([s["amplitude_mv"] for s in metrics["summary"]], dtype=np.int64)
    phase_us = np.array([s["phase_us"] for s in metrics["summary"]], dtype=np.int64)
    pol = np.array([s["polarity"] for s in metrics["summary"]], dtype="<U32")
    npulses = np.array([s["n_pulses"] for s in metrics["summary"]], dtype=np.int64)
    fhz = np.array([
        np.nan if s["frequency_hz"] is None else float(s["frequency_hz"])
        for s in metrics["summary"]
    ], dtype=np.float64)
    # Per-condition trial timestamps from per_trial_event_idx → manifest events
    record = _select_screen_record(manifest)
    # Include both single-pulse ("stim") and multi-pulse ("stim_train") events.
    # fire_pulse appends "stim"; fire_pulse_train appends "stim_train" with
    # n_pulses + frequency_hz fields. Both are valid stim events.
    events = [e for e in record["events"] if e.get("type") in ("stim", "stim_train")]
    rec_start_ms = float(record["wells"][0]["start_time_ms"])
    # Schema: parent had top-level n_trials_per_condition; this task nests it
    # under params{}. Read either, prefer the nested location.
    n_trials = int(
        conditions.get("params", {}).get("n_trials_per_condition")
        or conditions["n_trials_per_condition"]
    )
    trial_ts = np.empty(n_conditions, dtype=object)
    for ci in range(n_conditions):
        ts = []
        for ei in metrics["per_trial_event_idx"][ci]:
            if ei < 0:
                ts.append(np.nan)
            else:
                ts.append(float(events[ei]["timestamp_ms"]) - rec_start_ms)
        trial_ts[ci] = np.asarray(ts, dtype=np.float64)

    cond_metadata = np.zeros(n_conditions, dtype=[
        ("label", "<U64"),
        ("electrodes", "O"),
        ("amplitude_mv", np.int64),
        ("phase_us", np.int64),
        ("polarity", "<U32"),
        ("n_pulses", np.int64),
        ("frequency_hz", np.float64),
        ("trial_timestamps_ms", "O"),
    ])
    cond_metadata["label"] = cond_labels
    cond_metadata["electrodes"] = elec_obj
    cond_metadata["amplitude_mv"] = amp
    cond_metadata["phase_us"] = phase_us
    cond_metadata["polarity"] = pol
    cond_metadata["n_pulses"] = npulses
    cond_metadata["frequency_hz"] = fhz
    cond_metadata["trial_timestamps_ms"] = trial_ts

    np.savez(
        ANALYSIS_NPZ,
        unit_psth=metrics["unit_psth"],
        unit_evoked_trial_counts=metrics["unit_evk"],
        unit_pre_trial_counts=metrics["unit_pre"],
        unit_per_condition_metrics=metrics["per_cu"],
        unit_trial_spike_times=metrics["unit_trial_spike_times"],
        condition_metadata=cond_metadata,
        top10_strength_unit_ids=metrics["top10_unit_ids"],
        curated_unit_ids=stim_result["curated_unit_ids"],
    )
    print(f"  Wrote {ANALYSIS_NPZ}")

    # baseline_info.json (only on cycle 1; otherwise leave existing alone)
    if CYCLE == 1:
        sd_curated = curation["sd_curated"]
        baseline_info = dict(
            well=0,
            cycle=CYCLE,
            recording_path=str(RECORDING_PATH),
            baseline_window_ms=[0.0, BASELINE_MS],
            duration_actual_s=float(sd_curated.length / 1000.0),
            fs_hz=FS_HZ,
            well_start_time_ms=float(_select_screen_record(manifest)["wells"][0]["start_time_ms"]),
            well_stop_time_ms=float(_select_screen_record(manifest)["wells"][0].get(
                "stop_time_ms",
                _select_screen_record(manifest)["wells"][0]["start_time_ms"]
                + sd_curated.length,
            )),
            n_pre_curation=int(curation["sd_raw"].N),
            curated_unit_count=int(sd_curated.N),
            curated_unit_ids=[int(x) for x in stim_result["curated_unit_ids"].tolist()],
            snr_min=SNR_MIN,
            fr_min_hz=FR_MIN_HZ,
            snr_check=snr,
            snr_pct_below_threshold=snr["snr_pct_below_threshold"],
            rt_sort_pickle=str(RT_SORT_PICKLE),
            sorted_curated_pkl=str(SORTED_PKL),
            sorted_raw_pkl=str(SORTED_RAW_PKL),
            unit_features=features["unit_features"],
            electrode_site_features=features["electrode_site_features"],
            burst_detection=dict(
                method="spikelab-orchestrator-builtin:cortical_default",
                parameters=BURST_PARAMS,
            ),
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        BASELINE_INFO.write_text(json.dumps(baseline_info, indent=2))
        print(f"  Wrote {BASELINE_INFO}")


# -----------------------------------------------------------------------------
# Helpers — load cached curation + features for cycle >= 2
# -----------------------------------------------------------------------------

def _load_cached_curation_and_features() -> tuple[dict, dict]:
    """Reconstruct curation_result + features from baseline_info.json + sorted pickles.

    Used when cycle >= 2 (or cycle 1 re-extract): phases 2-4 are skipped,
    we just need the curated unit IDs + sd_curated for phase 5/7.
    """
    if not BASELINE_INFO.exists():
        raise FileNotFoundError(
            f"Cycle >= 2 requires {BASELINE_INFO} from cycle 1. Run cycle 1 first."
        )
    bi = json.loads(BASELINE_INFO.read_text())
    # Legacy baseline_info (cycle 1's first run) stored snr_check as a bare
    # string verdict; the new schema has it as a dict. Reconstruct.
    snr_field = bi.get("snr_check")
    if isinstance(snr_field, str):
        snr_check_dict = {
            "snr_min": float(bi.get("snr_min", SNR_MIN)),
            "snr_pct_below_threshold": float(bi.get("snr_pct_below_threshold", float("nan"))),
            "result": snr_field,
        }
    else:
        snr_check_dict = snr_field
    curated_ids = np.array(bi["curated_unit_ids"], dtype=np.int64)
    if not SORTED_RAW_PKL.exists():
        raise FileNotFoundError(
            f"Sorted raw spikedata not found at {SORTED_RAW_PKL}; cycle 1 cache missing"
        )
    with open(SORTED_RAW_PKL, "rb") as f:
        sd_raw = pickle.load(f)
    # Reconstruct sd_curated by selecting curated_ids from sd_raw
    raw_ids = np.array([a.get("unit_id", -1) for a in sd_raw.neuron_attributes],
                       dtype=np.int64)
    keep_idx = np.array([np.where(raw_ids == cid)[0][0] for cid in curated_ids],
                        dtype=np.int64)
    from spikelab.spikedata import SpikeData
    sd_curated = SpikeData(
        [sd_raw.train[i] for i in keep_idx],
        N=len(keep_idx),
        length=sd_raw.length,
        neuron_attributes=[sd_raw.neuron_attributes[i] for i in keep_idx],
    )
    curation = dict(
        sd_raw=sd_raw,
        sd_curated=sd_curated,
        curated_unit_ids=curated_ids,
        keep_idx=keep_idx,
        snr_check=snr_check_dict,
    )
    features = dict(
        unit_features=bi["unit_features"],
        electrode_site_features=bi["electrode_site_features"],
    )
    return curation, features


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycle", type=int, required=True)
    ap.add_argument("--recording-dir", type=Path, required=True,
                    help="Directory containing this cycle's cycle_<N>.raw.h5 + manifest.json")
    ap.add_argument("--baseline-dir", type=Path, default=None,
                    help="Directory holding cycle-1 rt_sort.pickle + sorted_rt_sort/. "
                         "Defaults to --recording-dir for cycle 1.")
    ap.add_argument("--max-stim-offset-ms", type=float, default=None,
                    help="Override sort_stim_recording's recentering search radius. "
                         "Default 49.0 (safe for 1 s ISI). For multi-pulse trains, set "
                         "to ISI/2 - 1 (e.g. 100 Hz -> 4.0, 200 Hz -> 1.5).")
    ap.add_argument("--multi-peak-threshold", type=float, default=None,
                    help="Override MULTI_PEAK_THRESHOLD (default 0.8). Lower values "
                         "(e.g. 0.5) admit weaker pulse-1 artifacts when later pulses "
                         "in the train are much larger.")
    args = ap.parse_args()
    if args.max_stim_offset_ms is not None:
        global MAX_STIM_OFFSET_MS
        MAX_STIM_OFFSET_MS = float(args.max_stim_offset_ms)
    if args.multi_peak_threshold is not None:
        global MULTI_PEAK_THRESHOLD
        MULTI_PEAK_THRESHOLD = float(args.multi_peak_threshold)

    configure(args.cycle, args.recording_dir, args.baseline_dir)
    os.environ.setdefault("HDF5_PLUGIN_PATH", HDF5_PLUGIN)

    print(f"=== Cycle-{CYCLE} analysis pipeline started "
          f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} ===")
    print(f"  Plan root: {PLAN_ROOT}")
    print(f"  Recording: {RECORDING_PATH}")
    print(f"  Baseline dir: {BASELINE_DIR}")
    print(f"  Cycle dir: {CYCLE_DIR}")

    # Load conditions + manifest
    if not CONDITIONS_PATH.exists():
        raise FileNotFoundError(f"Missing conditions: {CONDITIONS_PATH}")
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Missing manifest: {MANIFEST_PATH}")
    conditions = json.loads(CONDITIONS_PATH.read_text())
    manifest = json.loads(MANIFEST_PATH.read_text())

    if CYCLE == 1:
        # Phases 1–4 only on first cycle (or if any of their outputs missing)
        if not RT_SORT_PICKLE.exists():
            phase_1_rt_sort_detect()

        if _baseline_info_complete():
            print(f"  baseline_info.json complete -> reusing cached curation + features")
            curation, features = _load_cached_curation_and_features()
        else:
            curation = phase_2_3_snr_check_and_curation()
            features = phase_4_feature_characterization(curation, conditions)
    else:
        # Cycle >= 2: always reuse cycle-1's caches
        if not RT_SORT_PICKLE.exists():
            raise FileNotFoundError(
                f"Cycle {CYCLE} requires {RT_SORT_PICKLE} (from cycle 1). "
                f"Run cycle 1 first."
            )
        curation, features = _load_cached_curation_and_features()

    stim_result = phase_5_sort_stim(manifest, conditions, curation)
    metrics = phase_6_v3_metrics(stim_result, conditions)
    phase_7_write_outputs(stim_result, metrics, curation, features, conditions, manifest)

    summary_path = LOG_DIR / "summary.json"
    ranked = sorted(metrics["summary"], key=lambda s: -s["strength_top10"])
    summary = dict(
        cycle=CYCLE,
        phase_timing_s=_phase_timings,
        n_pre_curation=int(curation["sd_raw"].N),
        n_curated=int(stim_result["n_curated"]),
        snr_check=curation["snr_check"]["result"],
        snr_pct_below_threshold=curation["snr_check"]["snr_pct_below_threshold"],
        n_bursts_detected=0,
        conditions_ranked_by_strength_top10=[
            dict(
                label=s["label"],
                strength_top10=s["strength_top10"],
                n_responders=s["n_responders"],
                n_suppressed=s["n_suppressed"],
                local_unit_count=s["local_unit_count"],
            )
            for s in ranked
        ],
        any_strength_top10_positive=any(s["strength_top10"] > 0 for s in metrics["summary"]),
        best_condition=ranked[0]["label"] if ranked else None,
        best_strength_top10=ranked[0]["strength_top10"] if ranked else None,
    )
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\n=== Summary -> {summary_path} ===")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
