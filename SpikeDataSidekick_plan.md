# SpikeDataSidekick — Plan & Status

Fork of SpikeLab at `/home/sharf-lab/Desktop/Noah/SpikeLab/`
Remote: `git@github.com:nomuwill/SpikeLab.git`

---

## What this fork adds over upstream SpikeLab

All of Research_automation's core functionality (non-fluidics), plus a staged set of
analysis scripts to promote to library functions over time.

---

## Completed

### Research_automation core copy
- [x] `experiments/` — 12 runnable Maxwell experiment scripts (scan, select, record, stim demos)
- [x] `src/spikelab/experiment/` — Python module: `experiment_lib`, `config`, `manifest`, `schedule_util`, `log_event`, `extract_wakeup_summary`, `verify_manifest`
- [x] `src/spikelab/experiment/paradigms/` — `frequency_stim`, `paired_pulse`, `stim_response_curve`
- [x] `scripts/` — `schedule_wakeup.sh`, `check_plan_busy.sh`
- [x] `docs/source/` — `ephys_manifest_spec.md`, `culture_lifecycle_reference.md`, `orchestrator_plan.md`, `ephys_todo.md`
- [x] `experiment_plan_template.md` — repo root
- [x] `.claude/skills/` — `ephys`, `orchestrator`, `experiment-status`, `ntp-check`

### Library promotion staging
- [x] `to_promote/` — 13 scripts from orchestrator/scratch dirs staged in tier1/tier2/tier3
  - See `to_promote/README.md` for extraction targets and destination modules

---

## Remaining work

### 1. Promote `to_promote/` scripts to library functions

**Tier 1 — high priority (novel algorithms):**

| File | Extract | Target |
|---|---|---|
| `tier1/build_candidate_pool.py` | `interleave_unique()`, `greedy_spread_filter()`, `build_strict_pool()`, `build_overprovisioning_pool()` | `src/spikelab/experiment/candidate_selection.py` |
| `tier1/compare_multiunit_waveform_features.py` | `population_features()`, `waveform_features()`, `driver_score` | `src/spikelab/spikedata/features.py` |
| `tier1/lag_fine_and_multivar.py` | `multivar_loo()`, `population_features_finebin()`, permutation testing | `src/spikelab/spikedata/stat_utils.py` |
| `tier1/burst_phase_analysis_full_sort.py` | Stim-artifact-aware burst detection, Δt-binning | `src/spikelab/spike_sorting/stim_sorting/burst_utils.py` |
| `tier1/response_vs_dt_analysis.py` | `detect_bursts()`, burst-state response aggregation | `src/spikelab/spike_sorting/stim_sorting/burst_utils.py` |
| `tier1/cross_cycle_fatigue.py` | Fatigue trajectory: time-since, recent-load, cumulative-load | `src/spikelab/experiment/fatigue.py` |

**Tier 2 — medium priority (need parameterization):**

| File | Extract | Target |
|---|---|---|
| `tier2/cycle_analysis.py` | SE-corrected significance criterion, v3 per-condition metrics | `src/spikelab/spike_sorting/stim_sorting/pipeline.py` |
| `tier2/compare_intrinsic_features.py` | `spike_width_ms()`, `footprint_extent_real()`, `refractory_viol_frac()` | `src/spikelab/spikedata/features.py` |
| `tier2/stable_responder_analysis.py` | Stable-core aggregation, Mahalanobis centroid ranking | `src/spikelab/spikedata/stat_utils.py` |
| `tier2/score_v2_continuous.py` | `auroc()`, multi-cycle metric aggregation | `src/spikelab/spikedata/stat_utils.py` |
| `tier2/compute_drift_metrics.py` | Per-unit log2 rate ratios, spatial correlation drift detection | `src/spikelab/spikedata/utils.py` |

**Tier 3 — hardware-coupled, extract selectively:**

| File | Salvageable | Not salvageable |
|---|---|---|
| `tier3/verify_stim_configs.py` | `resolve_conflicts()`, `_detect_amp_peaks()` | MaxWell hardware routing calls |
| `tier3/build_stim_config.py` | Neighbourhood-building, budget-aware electrode selection | MaxWell-specific HDF5 parsing + routing |

### 2. Port local Analysis_shared patches into this fork

Local `Analysis_shared/SpikeLab` is 5 commits ahead of upstream braingeneers/SpikeLab.
Two are bugfixes worth bringing in:

| Commit | Fix | File |
|---|---|---|
| `8ea7077` | Curation: SNR uses bandpass-filtered traces | `src/spikelab/spikedata/curation.py` |
| `279aefe` | RT-Sort: fix partial-chunk write in `save_traces` | `src/spikelab/spike_sorting/rt_sort_runner.py` |

### 3. Claude Code skill for this fork

Write a `sidekick` skill under `src/spikelab/agent/skills/` that knows about the
`experiment/` module and can author thin experiment scripts without scaffolding from scratch.

### 4. Update spikelab skill in this repo's .claude/

The `.claude/skills/spikelab/SKILL.md` copied from Research_automation points at
the Analysis_shared SpikeLab install. Update it to point at this fork.

---

## Open questions

1. **maxlab dependency** — should `experiment/` fail gracefully if `maxlab` isn't installed
   (i.e. on a machine without Maxwell hardware), or is this only ever run on the MEA machine?
2. **Past experiment dirs** — should existing per-experiment dirs from Research_automation
   (e.g. `noah_corticalstim1` through `noah_corticalstim4`) be migrated here or left in place?
