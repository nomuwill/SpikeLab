# to_promote/

Scripts staged for promotion into the `spikelab` library. All sourced from
`Research_automation/orchestrator/` experiment dirs and scratch dirs.

The task for each: extract the reusable functions, parameterize any hardcoded
constants, add to the appropriate module under `src/spikelab/`, and write tests.

---

## tier1/ — Novel algorithms, high priority

| File | Extract | Target module |
|---|---|---|
| `build_candidate_pool.py` | `interleave_unique()`, `greedy_spread_filter()`, `build_strict_pool()`, `build_overprovisioning_pool()` | `spikelab/experiment/candidate_selection.py` |
| `compare_multiunit_waveform_features.py` | `population_features()`, `waveform_features()`, `driver_score` metric | `spikelab/spikedata/features.py` |
| `lag_fine_and_multivar.py` | `multivar_loo()`, `population_features_finebin()`, permutation testing | `spikelab/spikedata/stat_utils.py` |
| `burst_phase_analysis_full_sort.py` | Stim-artifact-aware burst detection, Δt-binning | `spikelab/spike_sorting/stim_sorting/burst_utils.py` |
| `response_vs_dt_analysis.py` | `detect_bursts()`, burst-state-dependent response aggregation, log-linear fit | `spikelab/spike_sorting/stim_sorting/burst_utils.py` |
| `cross_cycle_fatigue.py` | Fatigue trajectory: time-since-last-stim, recent-load, cumulative-load | `spikelab/experiment/fatigue.py` |

---

## tier2/ — Useful functions to extract, need parameterization

| File | Extract | Target module |
|---|---|---|
| `cycle_analysis.py` | SE-corrected significance criterion, v3 per-condition metrics | `spikelab/spike_sorting/stim_sorting/pipeline.py` |
| `compare_intrinsic_features.py` | `spike_width_ms()`, `footprint_extent_real()`, `refractory_viol_frac()` | `spikelab/spikedata/features.py` |
| `stable_responder_analysis.py` | Stable-core aggregation, Mahalanobis centroid ranking | `spikelab/spikedata/stat_utils.py` |
| `score_v2_continuous.py` | `auroc()` rank-sum implementation, multi-cycle metric aggregation | `spikelab/spikedata/stat_utils.py` |
| `compute_drift_metrics.py` | Per-unit log2 rate ratios, directional-change fraction, spatial correlation | `spikelab/spikedata/utils.py` |

---

## tier3/ — Salvage specific functions only; hardware-coupled otherwise

| File | Salvageable | Not salvageable |
|---|---|---|
| `verify_stim_configs.py` | `resolve_conflicts()`, `_detect_amp_peaks()` (Gaussian blob detection) | Hardware routing calls (`arr.connect_electrode_to_stimulation`) |
| `build_stim_config.py` | Neighbourhood-building (8 nearest within 100 µm), budget-aware electrode selection | HDF5 parsing + MaxWell-specific routing |
