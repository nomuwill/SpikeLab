# Experiment Summary — Orchestrator Experiments

**Generated:** 2026-06-10  
**Coverage:** All orchestrator experiments with actual run data  
**Base path:** `/home/sharf-lab/Desktop/Research_automation/orchestrator/`

---

## Table of Contents

1. [scan-select-compare_2026-04-14](#1-scan-select-compare_2026-04-14)
2. [maturity-pilot_2026-04-15](#2-maturity-pilot_2026-04-15)
3. [stim-optimize_2026-04-20](#3-stim-optimize_2026-04-20-maxtwo-plan-plan-only-no-run)
4. [stim-optimize-maxone_2026-04-20](#4-stim-optimize-maxone_2026-04-20)
5. [stim-optimize-maxone-cortical_2026-04-27](#5-stim-optimize-maxone-cortical_2026-04-27)
6. [hCO-stim-demo_2026-05-13](#6-hco-stim-demo_2026-05-13)
7. [hub-induction_2026-05-18](#7-hub-induction_2026-05-18)
8. [stim-candidates_2026-05-22](#8-stim-candidates_2026-05-22)
9. [noah_corticalstim1_10May2026](#9-noah_corticalstim1_10may2026)
10. [noah_corticalstim2_11May2026](#10-noah_corticalstim2_11may2026)
11. [noah_corticalstim3_2026-05-15](#11-noah_corticalstim3_2026-05-15)
12. [noah_corticalstim4_2026-05-21](#12-noah_corticalstim4_2026-05-21)
13. [noah_midbrainstim2_priming_2026-05-18](#13-noah_midbrainstim2_priming_2026-05-18)
14. [noah_devStim_1](#14-noah_devstim_1)
15. [midbrain-stim-detect_2026-06-09](#15-midbrain-stim-detect_2026-06-09)

---

## 1. scan-select-compare_2026-04-14

### Goal
Methodological comparison: determine which combination of activity-scan mode (`sparse_7x` / `checkerboard` / `full`) and electrode-selection strategy (`hotspots40` = 40 spatial hotspot regions; `top1020` = top 1020 individual electrodes by amplitude, no spatial clustering) yields the best downstream neural data quality. Biology was secondary; this was a pure pipeline benchmarking run.

### Setup
- **Rig:** MaxOne (single well, well 0)
- **Sampling rate:** 20 kHz (confirmed from HDF5 files; project notes had said 10 kHz — discrepancy flagged)
- **Cell line / culture:** Not biological — any culture on the chip at the time; methodological test
- **Operator:** TJ

### What Was Done
**Phase A — Activity scans (3 scans):**
- `sparse_7x`: 5.3 min, 1449 active electrodes
- `checkerboard`: 10.0 min, 2642 active electrodes
- `full`: 19.0 min, 4034 active electrodes

**Phase B — Electrode selection + 3-minute recordings (6 recordings, ~4 min each):**
- Each scan mode × each selection strategy → `selected_electrodes.cfg` + 3 min `recording.raw.h5`
- `hotspots40` routed 972–998 channels; `top1020` routed 1009–1020 channels

**Phase C — Spike sorting + comparison:**
- All 6 recordings sorted with Kilosort2 (Docker container, RTX 5090 Blackwell GPU)
- First sort took 748.5 s due to PTX JIT warm-up; subsequent sorts 263–494 s
- Comparison written to `recordings/2026-04-14/analysis/comparison.md`

**Total wall time:** ~2 h 20 min (hardware 57 min, spikelab 1 h 23 min)

### Key Results

| Scan | Strategy | Routed ch | Curated units | Median SNR | Total spikes | Units/100ch |
|------|----------|-----------|---------------|------------|--------------|-------------|
| sparse_7x | hotspots40 | 980 | 94 | 10.71 | 57,886 | 9.59 |
| sparse_7x | top1020 | 1020 | 84 | 9.18 | 50,361 | 8.24 |
| checkerboard | hotspots40 | 972 | 90 | 10.99 | 51,449 | 9.26 |
| checkerboard | top1020 | 1020 | 139 | 9.60 | 74,704 | 13.63 |
| full | hotspots40 | 973 | 112 | 9.80 | 59,222 | 11.51 |
| **full** | **top1020** | 1009 | **184** | 9.14 | **103,083** | **18.24** |

- **Scan mode:** denser scans increase yield. `full` averages 148.0 curated units, `checkerboard` 114.5, `sparse_7x` 89.0. Median SNR is comparable across modes (9.47–10.30).
- **Selection strategy:** `top1020` yields more units on average (135.7 vs 98.7 for `hotspots40`) and better spatial coverage (up to 162 unique channels used vs 89), at the cost of slightly lower median SNR (9.31 vs 10.50).
- **Recommended combination:** `full` × `top1020` — 184 curated units, 18.24 units/100ch, 74 high-SNR units (SNR ≥ 10).

**Technical notes:**
- A Kilosort2 channel-count bug (MaxwellRecordingExtractor returning shape `(T, 1024)` even for <1024 routed channels) caused all 6 sorts to fail on first attempt (`sort_all.py`). Fixed in `sort_all_v2.py` via `select_channels` + monkey-patching.
- Sampling rate confirmed as 20 kHz, not 10 kHz as noted in project memory.

### Output Files
- `recordings/2026-04-14/analysis/comparison.md` — main results
- `recordings/2026-04-14/analysis/comparison_metrics.json` — machine-readable metrics
- `recordings/2026-04-14/analysis/sorted/<scan>__<strategy>/` — 6 × sorted outputs (`.npz`, `.pkl`)
- `reports/run_001_2026-04-14T195422Z.md` — end-of-run report
- `recordings/2026-04-14/analysis/comparison_figures/` — distribution and metric PNGs

### Status
**COMPLETED** — all 6 recordings acquired, all 6 sorted successfully, comparison report written.

---

## 2. maturity-pilot_2026-04-15

### Goal
Automate periodic intrinsic-activity snapshots of a 6-well MaxTwo plate every 6 hours. Each snapshot: `sparse_7x` scan → 40-hotspot amplitude electrode selection → 5-minute recording → spike sort → plot first 60 s → preliminary maturity assessment per well. Goal: build a continuous baseline activity profile over days to track culture development.

### Setup
- **Rig:** MaxTwo (6 wells, wells 0–5)
- **Sampling rate:** 10 kHz
- **Scan mode:** `sparse_7x`, 30 s/block
- **Electrode selection:** 40 hotspot regions by amplitude
- **Recording duration:** 300 s (5 min) per cycle
- **Cadence:** every 6 hours (4 runs/day), open-ended
- **Operator:** tjitse@openculturesci.com
- **Plan created:** 2026-04-15; first run 2026-04-16T12:07 PDT

### What Was Done
4 complete runs executed over approximately 24 hours (2026-04-16 to 2026-04-17):

**Run 1 (2026-04-16 12:07 PDT, ~2 h 36 min wall clock):**
- Recording: 302 s, 5.3 GB, 6 wells simultaneously
- Sort: Kilosort2 failed on all wells initially (HDF5_PLUGIN_PATH misconfiguration, missing `docker-py`, missing `cuda-python` packages — all fixed mid-run)
- Well 1 hit a SpikeLab SNR curation bug after KS2 found units
- Only well 4 produced 1 curated unit (KS2, 0.86 Hz)
- **Status: PARTIAL** — environment issues on first run

**Run 2 (2026-04-16 18:06 PDT):**
- Recording: 302 s, all 6 wells
- KS2 permanently replaced with **Kilosort4** (KS2 incompatible with RTX 5090 / Blackwell CUDA)
- 5/6 wells yielded ≥1 unit with KS4
- All wells Tier 1 (<30 active units)

**Run 3 (2026-04-17 00:06 PDT, ~43 min):**
- Recording: 302 s, 949 active electrodes, 1017 routed
- 3/6 wells yielded units (0, 1, 2)
- Well 5 had a DNS retry on Docker (recovered)

**Run 4 (2026-04-17 06:06 PDT, ~50 min):**
- Recording: 302 s, 951 active electrodes
- 3/6 wells yielded units (0, 3, 5)

### Key Results — Per-Well Progression (Curated Units)

| Well | Run 1 | Run 2 | Run 3 | Run 4 | Stage (Run 4) |
|------|-------|-------|-------|-------|---------------|
| 0 | 0 | 1 (0.15 Hz) | 1 (0.87 Hz) | 3 (4.62 Hz) | emerging |
| 1 | 0* | 1 (0.49 Hz) | 2 (0.65 Hz) | 0 | too_young |
| 2 | 0 | 1 (1.04 Hz) | 2 (1.25 Hz) | 0 | too_young |
| 3 | 0 | 0 | 0 | 1 (0.92 Hz) | emerging |
| 4 | 1 | 1 (0.98 Hz) | 0 | 0 | too_young |
| 5 | 0 | 1 (2.97 Hz) | 0 | 4 (2.15 Hz) | emerging |

*Well 1 Run 1: KS2 found units but hit SNR curation bug.

- All wells remained at Tier 1 (<30 active units) across all 4 runs — cultures are early-stage
- Well 0 shows the clearest upward trend: 0→1→1→3 units, firing rate 0→0.15→0.87→4.62 Hz
- Well 5 most variable: 0→1→0→4, highest single-run firing rate (2.97 Hz at Run 2, 2.15 Hz at Run 4)
- Well 3 slowest to emerge: first unit appeared at Run 4
- Active electrode count stable across runs (938–951 electrodes)

**Standing deviations:**
- Kilosort2 → Kilosort4 substitution (permanent, RTX 5090 CUDA incompatibility)
- Run-to-run fluctuation in young cultures is expected — marginal units fluctuate around curation threshold

### Output Files
- `recordings/2026-04-16/121000_maturity_all/recording.raw.h5` — Run 1 (5.3 GB)
- `recordings/2026-04-16/181000_maturity_all/recording.raw.h5` — Run 2
- `recordings/2026-04-17/000604_maturity_all/recording.raw.h5` — Run 3 (5.59 GB)
- `recordings/2026-04-17/060654_maturity_all/recording.raw.h5` — Run 4 (5.2 GB)
- `reports/run_001_2026-04-16T214320Z.md` through `run_004_2026-04-17T135700Z.md`
- `logs/orchestrator.log`, `logs/wakeups/` (8 primary+backup timer logs)

### Status
**COMPLETED (4 runs, then stopped).** The cadence ran as designed. Stopped after Run 4 — the 4-run dataset provided the intended baseline activity snapshot. No well reached Tier 2 (≥30 active units), consistent with young cultures still developing.

---

## 3. stim-optimize_2026-04-20 (MaxTwo plan — PLAN ONLY, NO RUN)

### Goal
Six-well MaxTwo version of the stim-optimization experiment: find, per well, the stimulation paradigm eliciting the most widespread and consistent evoked response (5–200 ms post-stim window). Adaptive optimization over cycles: single-site → multi-site pairs/triplets → parameter sweeps. Metric: `n_responsive × reliability` (responsive sorted units × mean peak-bin trial fraction).

### Setup
- **Rig:** MaxTwo (10 kHz, 6 wells)
- **Operator:** TJ
- **Plan created:** 2026-04-20

### What Was Done
**Only the experiment plan was written.** No recordings, no scans, no cycles were executed under this plan ID. The MaxTwo version was never run — instead, the MaxOne version (`stim-optimize-maxone_2026-04-20`) was executed on the same date.

### Status
**PLAN ONLY — NO ACTUAL RUN.** The `stim-optimize_2026-04-20` directory contains only `experiment_plan.md` (866 lines of detailed protocol). Skip this entry when reviewing run data.

---

## 4. stim-optimize-maxone_2026-04-20

### Goal
Find the stimulation paradigm that elicits the most widespread and consistent evoked response from a single-well MaxOne human midbrain organoid. Adaptive multi-cycle optimization: start with single-site pulses, expand to pairs/triplets, then parameter tweaks. Metric evolved from v1 (SE-corrected AUROC) → v2 (AUROC-rank-based) → v3 (`cons_topK`: sum of top-bin trial-fractions across K_consensus units).

### Setup
- **Rig:** MaxOne (20 kHz, 1 well — well 0)
- **Culture:** Human midbrain organoid, ~DIV 40
- **Recording channels:** 1006 wired (permanent cfg)
- **Candidate pool:** 16 stim electrodes
- **Operator:** TJ
- **Session dates:** 2026-04-25 to 2026-04-26 (~32 hours total)
- **Cycles:** 20

### What Was Done

**Phase A (2026-04-25, scan + cfg):**
- `sparse_7x` activity scan → 1006-channel permanent cfg
- Initial candidate pool selected by amplitude/rate union with 100 µm spread filter

**Cycles 1–20 (2026-04-25 to 2026-04-26):**
- Cycle 1: 16 single-site conditions (pool candidates), 100 trials each, 600 mV / 200 µs / positive-first
- Cycles 2–4: pairs and triplets explored (C(4,2) pairs, C(4,3) triplets from top single-site responders)
- Cycle 5: metric v1 deprecated due to instability (CV=0.62); v2 AUROC adopted
- Cycles 6–13: pool rotation with feature-based candidate selection; cross-cycle synthesis
- Cycle 8: hub electrode 18505 identified (hosts 8 of top-15 v2 units)
- Cycle 13: pivot to v3 metric (`cons_topK`, K_consensus built from cycles 1–14)
- Cycles 14–15: train paradigm introduced (3 pulses @ 100 Hz); site_22522_train3_100Hz shows +11.5% vs single
- Cycles 16–20: train axis exploration — amplitude variants (700 mV), frequency variants (200 Hz), multi-site train combinations, tournament (cycle 19), 200-trial final cycle (cycle 20)

**Key technical incident — alignment bug discovered post-run (2026-04-27):**
- `cycle_analyze.py` used `max_stim_offset_ms=50` which caused per-trial recentering to pick pulse 2 or 3 of multi-pulse trains (not pulse 1). This diluted train responses by 0.5–0.8 cons_topK. Reanalysis with first-pulse (FP) tight recentering corrected this.

### Key Results (First-Pulse Aligned, Corrected)

**Final ranking (mean cons_topK_v3, FP-aligned, n ≥ 2 replicates):**

| Rank | Condition | Mean cons_topK | Std | n |
|------|-----------|----------------|-----|---|
| 1 | `site_22522_amp600_train3_200Hz` | 15.410 | 0.622 | 3 |
| 2 | `site_22522_amp700_train3_100Hz` | 15.167 | 0.737 | 3 |
| 3 | `site_22522_amp600_train3_100Hz` | 14.984 | 0.658 | 5 |
| 4 | `triplet_13244_17216_22522_amp600` (single pulse) | 14.440 | 0.680 | 5 |
| 5 | `pair_13244_17216_amp600` (single pulse) | 14.417 | **0.076** | 3 |

**Recommended winner:** `site_22522_amp600_train3_200Hz` — 3-pulse biphasic anodic-first train at 200 Hz on electrode 22522, 600 mV, 200 µs phase.

**Reproducibility-first alternative:** `pair_13244_17216_amp600` — std=0.076 (8–10× lower than train winners), mean ~1.0 cons_topK below the lead.

**Train benefit transfer catalogue (11 paired single-vs-train comparisons, FP-aligned):**
- Median train benefit: +4.8% (range −3.1% to +15.0%)
- 10/12 sites show positive train benefit; 6/12 show >5% benefit
- Under correct alignment, trains recruit 50–130% more K_consensus excited units AND fire ~50–150% more total spikes vs single pulses — trains genuinely augment, not just spread, the response

**K_consensus set:** 287 excited + 80 suppressed units (frozen from cycles 1–14)

**Other findings:**
- Hub electrode 18505 was the strongest single-electrode train responder (cycle 14 cons_topK 14.74) but was never incorporated into the standard 9-pool
- Single-pulse anchor `site_22522_amp600` remained stable across cycles 15–20 (cons_topK 13.50–13.75), confirming no major culture drift over the session

### Output Files
- `well_0/cycle_K/analysis.json` and `analysis.npz` for K=1..20
- `well_0/cycle_K/sort_cache.pkl`
- `well_0/log.json` (40+ entries)
- `well_0/state.json` (terminated_at_cycle: 20)
- `well_0/findings.md`
- `scratch/k_consensus_responders.json` (K_consensus: 287 excit + 80 supp)
- `scratch/all_cycles_v3_first_pulse.json` — corrected FP-aligned metrics cycles 14–20
- `scratch/final_consolidation.json` — original-alignment metrics
- `scratch/raster_plots_final_winners_v4/` — FP-aligned rasters (USE THESE)
- `maxtwo_recommendations.md` — 24 methodology recommendations for future experiments
- `reports/run_001_20260426T195028Z.md` — final report (post-alignment-correction version)

### Status
**COMPLETED** — 20 cycles, user-directed termination at cycle 20, winner declared. Post-run alignment correction applied on 2026-04-27. ~7.7 GB on-disk footprint at plan level.

---

## 5. stim-optimize-maxone-cortical_2026-04-27

### Goal
Replicate the stim-optimization pipeline on a human cortical organoid (vs midbrain in the prior experiment). Find the stimulation paradigm that maximizes non-local network recruitment. This experiment ran for 25 cycles over ~52 hours and produced three major scientific findings about polarity, pulse count, and network recruitment mechanisms.

### Setup
- **Rig:** MaxOne (20 kHz, 1 well — well 0)
- **Culture:** Human cortical organoid (species/line unspecified), single-well MaxOne
- **Sampling:** 20 kHz
- **Curated units (from cycle-1 baseline):** 52 (SNR ≥ 3.0, FR ≥ 0.05 Hz; unchanged across all 25 cycles)
- **Wired stim pool (Phase A):** 16 electrodes (final compatible pool after 16 routing passes)
- **Operator:** TJ
- **Session window:** 2026-04-28T10:57 → 2026-04-30T16:22 PT (~52 hours)
- **Cycles:** 25

### What Was Done

**Phase A (2026-04-28 11:04 PT):**
- Activity scan: 1032 active electrodes
- Strict pool: 12 electrodes (top-10-rate ∪ top-10-amp, spread-filtered to 50 µm)
- Over-provisioned to 32 candidates for compat check
- 16 iterative routing passes → final 16-electrode wired pool: `[14226, 15956, 9322, 4088, 16402, 1862, 20360, 5378, 18636, 18586, 15498, 13742, 15844, 14220, 9880, 11576]`
- RT-Sort from cycle-1 baseline: 462 sequences → 198 pre-curation → **52 curated units**
- NTP bound: 22.5 ms (PASS)
- 0 bursts detected on 180 s baseline (consistent with cortical organoid)
- `local_unit_count = 0` at 50 µm threshold for every site → §7.6 Unit A centroid undefined; rotation used percentile ranking only

**Cycles 1–10 (Original arc, ~21 hours, 2026-04-28 to 2026-04-29):**
- C1: 16 single-site, best `site_20360` at strength_top10=1.244 (re-extracted with chunked sort, shifted from `site_9322` original)
- C2: all 6 pairs underperformed singles; cycle-1 winner `site_20360` dropped −69%
- C3: train paradigm (2-pulse, 3-pulse @ 100 Hz): `site_9880_train2_100Hz = 1.207` (+10× over single); `multi_peak` recentering developed to fix train alignment
- C4: `pair_9880+9322_train2_100Hz = 1.921` — first confirmed multi-site × multi-pulse synergy
- C5: Stim_unit collision bug corrupted 4/12 conditions; accidental zero-stim conditions provided noise-floor calibration (~0.7–1.0)
- C6: Wiring guard implemented; `pair_9880+14664_train2_100Hz = 4.473` — new run-best. Driver electrode 14664 identified by burst-participation / population-coupling ranking. Cycle-5 "winners" failed to replicate — confirmed noise.
- C7: Variance test confirmed `pair_9880+14664_train2_100Hz = 4.045` (stable)
- C8: `pair_9880+14664_train3_100Hz = 4.933` (peak; +10% over 2-pulse)
- C9: 50 Hz hurts (score = noise floor); 100 Hz and 200 Hz comparable; `pair_9322+14664_train3_100Hz = 4.088` nominally #1
- C10 (200-trial consolidation): **most conditions collapsed** — `pair_9880+14664_train3_100Hz` dropped from 4.933 → 0.261 (−95%). Only `pair_9880+14664_train3_200Hz` held at 2.217 (Δ −4% from c9). Termination by judgment (culture degrading).

**Original declared winner (c1–c10):** `pair_9880+14664_train3_200Hz` — electrodes [9880, 14664], 600 mV/200 µs/positive_first/3 pulses @ 200 Hz.

**Post-hoc invalidation:** 90% of the c1–c10 winner's score came from direct drive of unit 297 (primary electrode = 14664, i.e., directly stimulated). Under non-local exclusion (100 µm radius), the score collapsed. Decision: define `strength_nonlocal_top10` metric and restart hypothesis testing.

**Cycles 11–25 (Extended arc, ~31 more hours, 2026-04-29 to 2026-04-30):**

Phase B (C11–C13): Non-local metric established; escalating input-drive tested. C13 breakthrough: `site_15498_train5_100Hz` produced `nl=19.6` — first clear above-noise non-local signal in 13 cycles. Unit 461 (primary electrode 19920) dominated; site_15498 → unit 461 pathway identified as indirect/axonal (site_20360 at 35 µm from u461 gives noise).

Phase C (C14–C18): Pulse-count dose-response, polarity, frequency:
- C14: `site_15498` dose-response: nl_top10 scales linearly train1=0.56 → train10=76. No saturation.
- C15: `site_20360` (35 µm from u461) gives noise → pathway is via specific axon, not field spread. Frequency: 50 < 100 < 200 Hz at train5. Amplitude: sharp 400–600 mV threshold.
- C16–C18: Polarity exploration; `pair_15498+15956_train5` synergy (nl=16–17); `site_15498_train10_negfirst` peaks at nl=44 then crashes to 7.2.

Phase D (C19–C22): Polarity hypothesis emerges and confirmed:
- C20: `pair_15498+15956_train10_negative_first` hits **nl=98.3** — new all-time peak.
- C21: Positive_first version = noise (0.28 vs 98). Polarity is real.
- C22: Definitive within-cycle paired pos/neg test confirmed: 150–170× ratio for pair_15498+X combos with negative_first.

Phase E (C23–C25): Polarity electrode-mapping across 12 wired sites:
- 4 cathodic-preferring sites: 15498, 15956, 13742, 4088 (ratios 1.5×–10×)
- 1 anodic-preferring site: 16402 (ratio 13× anodic over cathodic)
- 5 noise sites: no response at train10 either polarity
- **Site_4088 discovered as major responder in C25** (nl=71 neg-first, 47 pos at train10) — missed earlier because it was never tested at train10.

### Key Results

**Final recommended conditions:**

| Rank | Condition | Mean nl_top10 | n replicates | Notes |
|------|-----------|---------------|-------------|-------|
| 1 | `pair_15498+15956_train10_100Hz_negative_first` @ 600 mV / 200 µs | **88** | 3 (C20, C22, C24) | Most robust; range 69–98 |
| 2 | `pair_15498+16402_train10_100Hz_negative_first` @ 600 mV / 200 µs | 77 | 2 (C22, C25) | Same magnitude, fewer replicates |
| 3 | `site_15498_train15_100Hz` (either polarity) @ 600 mV / 200 µs | ~78 | 2 (C21, C22) | Best single-site option |

**Three major scientific findings:**
1. **Pulse-count drives network recruitment:** site_15498 dose-response nl=0.56 (train1) → 76 (train10) → ~90 (train15). At least 5 pulses required.
2. **Polarity preference is electrode-specific:** cathodic-first wins for 4 sites; anodic-first wins for site_16402 by 13×. Most likely axon-orientation-specific.
3. **Multi-site combos amplify when polarity matches both pathways:** `pair_15498+15956` (both cathodic-preferring) at train10 negative_first reaches nl=98 vs 0.45 with positive_first (218× boost).

**Literature context:** Wagenaar et al. 2004 found anodic-first wins as a population average in 2D dissociated cortical cultures. Our 3D organoid result is consistent with their finding for site_16402 but shows the opposite for 4 other sites — suggesting the per-electrode polarity preference depends on local axon orientation distribution, which is more variable in 3D.

**Total stim events fired:** ~22,000 across cycles 1–25  
**Total recordings disk:** ~190 GB (25 recordings + sort outputs)

### Output Files
- `well_0/cycle_{1..25}/analysis.json` + `analysis.npz` + `spike_slices.pkl`
- `well_0/log.json`, `well_0/state.json` (terminated), `well_0/findings.md`
- `well_0/cycle_5/candidate_ranking_drivers.json` — burst-driver ranking that identified electrode 14664
- `recordings/2026-04-28/111757_stim_w0/selected_electrodes.cfg` — permanent cfg
- `recordings/2026-04-28/111757_stim_w0/rt_sort.pickle` — cycle-1 templates (reused all 25 cycles)
- `scratch/reanalysis_nonlocal/global_ranking.md` — all-cycle nonlocal rankings
- `scratch/plots_c22_polarity/` — polarity comparison rasters
- `scratch/plots_fatigue/`, `scratch/plots_intrinsic_15498/`
- `reports/run_001_2026-04-28T221213Z.md` — interim after C1
- `reports/run_010_FINAL_2026-04-29T145300Z.md` — final report C1–C10
- `reports/run_019_2026-04-30T094300Z.md` — interim after C19
- `reports/run_026_2026-04-30T235300Z.md` — **final report, all 25 cycles**
- `maxtwo_recommendations.md` — methodology recommendations

### Status
**COMPLETED** — 25 cycles, autonomous termination judgment at cycle 25. Well_0 status: `terminated`. All scientific goals reached per the final report.

---

## 6. hCO-stim-demo_2026-05-13

### Goal
Demonstrate a complete stimulation experiment pipeline end-to-end on a KOLF hCO (human cortical organoid). Record a 3-minute spontaneous baseline, RT-Sort it, detect RT-Sequences, then deliver 100 biphasic negative-first pulses at 1 Hz from the highest-activity burst-driver electrode and characterize evoked responses.

### Setup
- **Rig:** MaxOne (well 0)
- **Culture:** KOLF hCO, DIV 136
- **Operator:** Tjitse
- **Session:** 2026-05-13T21:47 → 2026-05-14T14:57 UTC (~17 h wall clock, likely includes overnight pause)

### What Was Done
1. Sparse activity scan → electrode selection
2. 3-minute spontaneous baseline recording → RT-Sort
3. 11 curated units, 11 RT-Sequences detected
4. Stim electrode 9380 selected (rate × amplitude composite; composite score 0.107)
5. 100 biphasic negative-first pulses at 1 Hz, 600 mV, 200 µs; 99 valid trials (1 dropped)

### Key Results

**Baseline sort:**
- 11 curated units, SNR range 5.10–45.80, FR range 0.61–4.30 Hz
- Highest SNR unit: uid 121, electrode 10042, SNR 45.8

**Evoked responses (99 valid trials):**
10/11 units responsive (uid 121 was silenced during stim — possibly desensitized):

| Unit | Electrode | SNR | Response prob | Peak latency (ms) | FR ratio post/pre |
|------|-----------|-----|---------------|-------------------|-------------------|
| 724 | 13543 | 6.94 | 0.747 | **25** | 1.05 |
| 392 | 14424 | 11.16 | 0.697 | **75** | 1.05 |
| 786 | 4313 | 8.10 | 0.343 | 85 | 1.41 |
| 573 | 16374 | 7.27 | 0.606 | 195 | **0.56 (suppressed)** |
| 91 | 8064 | 6.59 | **0.949** | 215 | 1.14 |
| 427 | 16836 | 5.17 | **0.970** | 215 | 1.10 |
| 24 | 13983 | 10.69 | 0.838 | 585 | 1.29 |
| 658 | 4746 | 6.56 | 0.727 | 585 | 1.63 |
| 594 | 14427 | 10.09 | 0.465 | 645 | 1.09 |
| 793 | 13550 | 5.10 | **0.970** | 735 | 1.34 |
| 121 | 10042 | 45.80 | **0.000** | — | — (silenced) |

**Key observations:**
- Units 427 and 793 responded on essentially every trial (prob=0.970)
- Fast (direct-evoked) response at 25 ms (uid 724); late network-propagated responses at 585–735 ms
- One suppressed unit (573, FR ratio 0.56)
- Responding units cluster spatially in ~2150–2280 µm x / ~1067–1138 µm y region
- Uid 121 (highest SNR, 45.8) was active at baseline (1.17 Hz) but fully silenced during stim — possible desensitization or electrode drift; flagged for review

### Output Files
- `recordings/2026-05-13/144722_scan_well0/baseline.raw.h5` — baseline recording
- `well0/baseline_rt_sort/` — sorted output (11 units, `sorted_spikedata_curated.pkl`, 6.51 MB)
- `reports/figures/` — per-unit PSTH and FR plots
- `well0/latest_report.json`
- `reports/run_001_2026-05-14T145800Z.md`

### Status
**COMPLETED** — all 10 workflow steps executed. Success criterion met (10/11 units with computable PSTHs). One anomaly flagged (uid 121 silence during stim).

---

## 7. hub-induction_2026-05-18

### Goal
Use daily electrical stimulation to induce two spatially-independent activity hubs in a developing iPSC-derived midbrain organoid before it becomes spontaneously active. Two stim sites chosen from impedance footprint for maximal spatial separation. Hypothesis: repeated site-specific stimulation will entrain local networks and create two functionally distinct hubs; over development, spontaneous activity should concentrate around those sites.

### Setup
- **Rig:** MaxOne (20 kHz, single well, well 0)
- **Culture:** iPSC-derived midbrain organoid (DIV not yet filled in)
- **Cadence:** daily (one session/day, ~80 min per session: 10 min scan + 10 min baseline + 60 min stim + 5 min post-stim)
- **Operator:** Will
- **Plan created:** 2026-05-18

### What Was Done
**Setup run only — no daily sessions were executed.**

The experiment plan was created and the state file was initialized, but the setup run (impedance scan, stim site selection, user confirmation) was never completed. The `chip_hub/state.json` remains at stage `"setup"` with all fields null:
```
"stage": "setup", "div_at_start": null, "stim_site_0_electrode": null, "stim_site_1_electrode": null
```
No recordings exist in `recordings/` (only the `setup/` subdirectory was created). No wakeup timers were ever scheduled.

### Status
**PLAN ONLY — SETUP RUN NOT COMPLETED.** The experiment never started. The `chip_hub/log.json` is empty (`[]`).

---

## 8. stim-candidates_2026-05-22

### Goal
One-shot screen of candidate stimulation sites on chip 2263 (hCO KOLF) using proven parameters to rapidly identify the best stim electrode for future experiments. Screen 7 candidate sites at both polarities, 10-pulse 100 Hz trains, to find the strongest non-local recruiter.

### Setup
- **Rig:** MaxOne (well 0)
- **Culture:** hCO KOLF, chip 2263, DIV ~145
- **Stim parameters:** 10 pulses @ 100 Hz, 600 mV, 200 µs, both polarities, 30 trials each
- **Metric:** `strength_nonlocal_top10` (100 µm exclusion)
- **Operator:** implied TJ / sharf-lab
- **Session date:** 2026-05-22

### What Was Done
1. Activity scan: 819 active electrodes on chip 2263
2. Original 10 candidates → pruned to 7 after compat check + hardware routing
3. Candidate pool: 16456, 19492, 8994, 16760, 19004, 24798, 15088
4. Screen recording: 180 s baseline + 420 stim trials (14 conditions × 30 trials), 4.8 GB, 849.7 s
5. RT-Sort: 874 sequences → **127 curated units** (SNR ≥ 3.0, FR ≥ 0.05 Hz)
6. Per-condition analysis with non-local exclusion

### Key Results

| Rank | Condition | Electrode | Polarity | nl_strength | n responders |
|------|-----------|-----------|----------|-------------|--------------|
| **1** | site_19004_train10_100Hz_**neg** | 19004 | neg | **2.187** | 2 |
| 2 | site_24798_train10_100Hz_neg | 24798 | neg | 0.727 | 1 |
| 3 | site_19492_train10_100Hz_neg | 19492 | neg | 0.647 | 2 |
| 4 | site_16760_train10_100Hz_neg | 16760 | neg | 0.627 | 4 |
| 5 | site_16760_train10_100Hz_pos | 16760 | pos | 0.607 | 3 |

- **Winner: electrode 19004, negative-first** — 3× stronger than #2, clear separation
- Negative polarity dominates top-3; positive polarity consistently 5–10× weaker for same site
- site_16760 notable: symmetric pos/neg scores (0.627 vs 0.607) with most responders (4 units)
- All 14 conditions scored >0 (no artifact concern)

**Recommendation:** Use electrode 19004, negative-first polarity as primary stim site for future experiments on chip 2263.

### Output Files
- `recordings/2026-05-22/134228_stim_w0_cycle1/screen.raw.h5` (4.8 GB)
- `well_0/cycle_1/analysis.json` — full ranked table
- `well_0/cycle_1/figs/raster_*.png` — top 3 condition rasters + combined top-10 unit raster
- `well_0/state.json`
- `sorted/stim_slices.pkl` (874 units × 420 slices)
- `sorted/curated_units.json` (127 units)
- `sorted/sorting_report.md`
- `reports/run_001_2026-05-22T224609Z.md`

### Status
**COMPLETED** — one-shot screen, success criterion met (strength_nonlocal_top10 > 1 AND n_responders ≥ 1). No further cycles scheduled.

---

## 9. noah_corticalstim1_10May2026

### Goal
Streamlined one-shot cortical stim screen: identify in a single ~1.5 h sitting the stim conditions driving the broadest non-local network response on a MaxOne cortical organoid. Also emit a per-unit reliability catalog. Derived from the 25-cycle parent experiment's three key findings: (1) 5+ pulse trains at 100 Hz dominate, (2) test both polarities, (3) use `strength_nonlocal_top10`.

### Setup
- **Rig:** MaxOne (20 kHz, 1 well, well 0)
- **Culture:** Cortical organoid (chip unspecified in plan; derived from `stim-optimize-maxone-cortical_2026-04-27`)
- **Stim parameters:** 5 pulses @ 100 Hz, 600 mV, 200 µs, both polarities, 50 trials per condition
- **Metric:** `strength_nonlocal_top10` (100 µm exclusion)
- **Operator:** Noah / TJ (pipeline scripts)
- **Session date:** 2026-05-10 (recording `191521_screen`)
- **Design:** flat condition grid (no branch tree), 14 conditions total (7 wired electrodes × 2 polarities)

### What Was Done
1. Phase A: sparse activity scan → pool build → compat check → `02_select_electrodes` → 7 wired stim electrodes + 1024 recording channels
2. Phase B: conditions.json built (7 sites × 2 polarities × 5-pulse train @ 100 Hz × 50 trials)
3. Phase C: screen recording — 180 s baseline + 1,600 trials × 1 s ISI (~30 min stim portion)
4. Phase D: RT-Sort (baseline only) → curation → per-condition `strength_nonlocal_top10` analysis

**Sort outcome:** 178 curated units (SNR range 1.04–14.7; note: lower SNR cutoff of 3.0 used), mean FR 5.14 Hz, total curated spikes 164,812

### Key Results

**Top 5 conditions (from `screen_summary.json`):**

| Rank | Condition | Electrode | Polarity | nl_strength | n_responders_nl |
|------|-----------|-----------|----------|-------------|-----------------|
| 1 | site_9380_train5_100Hz_**neg** | 9380 | neg | **8.62** | 25 |
| 2 | site_14204_train5_100Hz_**neg** | 14204 | neg | 8.47 | 29 |
| 3 | site_15944_train5_100Hz_**neg** | 15944 | neg | 7.03 | 22 |
| 4 | site_14226_train5_100Hz_**neg** | 14226 | neg | 5.51 | 22 |
| 5 | site_9380_train5_100Hz_pos | 9380 | pos | 5.15 | 7 |

- **All 7 wired sites prefer negative_first polarity**
- Top 2 conditions (9380 and 14204 neg) score nearly equally (~8.5 nl_strength, 25–29 responders)
- `site_10206_neg` dropped 58% under non-local exclusion (unit 165 direct-drive) — demonstrates importance of non-local metric
- Noise floor estimate: ~1.0–1.4 at 50 trials
- Top reliable unit: uid 363 (electrode 15738, x=2065 µm, y=1242.5 µm), SNR 3.95, 4.61 Hz baseline FR, responding to 8 conditions

### Output Files
- `recordings/2026-05-10/191521_screen/screen_1.raw.h5` — screen recording
- `recordings/2026-05-10/191521_screen/sorted_rt_sort/sorted_spikedata_curated.pkl` (108 MB)
- `recordings/2026-05-10/191521_screen/results/` — `conditions_ranked.csv`, `units_responding.csv`, `screen_summary.json`, `analysis_raw.npz`, multiple figure PNGs (rasters, heatmaps, PCA plots)
- Figures include: `rasters_top5.png`, `response_heatmap.png`, `multiunit_waveform_top5.png`, `baseline_raster.png`, `intrinsic_top_vs_rest.png`, `fine_lag_top5.png`, `multivar_logistic.png`

### Status
**COMPLETED** — one-shot pipeline successful. Top conditions clearly identified. Results are the input to subsequent experiments on this culture.

---

## 10. noah_corticalstim2_11May2026

### Goal
Same flat-screen design as `noah_corticalstim1`, but maximizing the number of stim sites tested by filling all 32 MaxOne stim-unit slots (vs 7 in the prior run). Used TJ's tiered-spread over-provisioning approach to reach the hardware maximum. Added 10 Hz arm alongside 100 Hz to test whether lower-frequency trains engage distinct network dynamics.

### Setup
- **Rig:** MaxOne (20 kHz, 1 well)
- **Culture:** Cortical organoid, MaxOne
- **Stim parameters:** 5 pulses × {10 Hz, 100 Hz}, 600 mV, 200 µs, both polarities, 30 trials
- **Metric:** `strength_nonlocal_top10`
- **Operator:** Noah
- **Recording date:** 2026-05-14 (recording `151535_screen`) — despite plan name "11May2026", the actual recording was 2026-05-14
- **Design:** 100 conditions (50 sites × 2 polarities × 2 frequencies — note: data says 100 conditions with 5 curated units)
- **Evoked windows:** 10 Hz: [5, 500] ms; 100 Hz: [5, 140] ms

### What Was Done
Phase A: over-provisioned candidate pool to top-60 by FR ∪ amp (120 unique candidates); spread filter at 100 µm; targeted 32 wired electrodes.

Recording: screen.raw.h5 at `recordings/2026-05-14/151535_screen/`

Sort: RT-Sort → **5 curated units** (SNR range 3.09–4.27, FR range 0.19–5.96 Hz, mean 1.89 Hz; very low yield compared to corticalstim1's 178 units)

### Key Results (screen_summary.json)
- **n_conditions: 100, n_curated_units: 5** — very low unit yield
- Noise floor at 30 trials: ~1.6 (scales as 1/√N from parent ~1.0 at 100 trials); conditions with nl_strength < 2.5 treated as marginal
- **All top conditions were at or near the noise floor** — top condition was `site_11800_train5_10Hz_neg` at nl_strength = 1.075, with only 3 non-local responders

| Rank | Condition | nl_strength | n_responders_nl |
|------|-----------|-------------|-----------------|
| 1 | site_11800_train5_10Hz_neg | 1.075 | 3 |
| 2 | site_6273_train5_100Hz_pos | 0.914 | 3 |
| 3 | site_16176_train5_100Hz_neg | 0.894 | 3 |
| 4 | site_12183_train5_100Hz_pos | 0.705 | 2 |
| 5 | site_10695_train5_100Hz_neg | 0.703 | 3 |

- With only 5 curated units, the culture was likely too young/inactive for meaningful stim screening at this session
- Top unit (uid 49): responded to 49/100 conditions at mean consistency 0.151 — broadly but weakly responsive
- **No frequency comparison is valid** at this SNR level

### Output Files
- `recordings/2026-05-14/151535_screen/screen.raw.h5`
- `recordings/2026-05-14/151535_screen/results/screen_summary.json`, `conditions_ranked.csv`, `units_responding.csv`
- `sorted/sorting_report.md` (5 units, successful sort)

### Status
**COMPLETED** but results are underpowered — only 5 curated units, all top conditions at noise floor. This run did not yield actionable stim recommendations. Likely the culture needed more time to mature.

---

## 11. noah_corticalstim3_2026-05-15

### Goal
Adapted version of the cortical stim screen with: (1) both 10 Hz and 100 Hz train frequencies tested, (2) pulse count increased to 10 (from 5), (3) 30 trials per condition, (4) 5-minute baseline with RT-Sort on first 3 minutes, (5) cap at 16 wired electrodes. Target culture: cortical organoid on MaxOne, DIV and chip not filled in before run.

### Setup
- **Rig:** MaxOne (20 kHz, 1 well)
- **Culture:** Cortical organoid, well 0 (DIV/chip not specified)
- **Stim parameters:** 10 pulses × {10 Hz, 100 Hz}, 600 mV, 200 µs, both polarities, 30 trials
- **Wired electrodes:** up to 16 (cap)
- **Operator:** Noah
- **Session date:** 2026-05-15 (recording `111449_screen`)
- **Conditions:** 36 (18 electrodes × 2 polarities, or 9 electrodes × 2 frequencies × 2 polarities)

### What Was Done
Phase A → D pipeline as in corticalstim1/2.

Sort: RT-Sort → **23 curated units** (RT-Sort succeeded, reasonable yield)

### Key Results (screen_summary.json)

**Top 5 conditions (overall):**

| Rank | Condition | nl_strength | n_responders_nl |
|------|-----------|-------------|-----------------|
| **1** | site_15034_train10_100Hz_**pos** | **1.682** | 5 |
| 2 | site_7230_train10_100Hz_pos | 0.263 | 4 |
| 3 | site_15034_train10_100Hz_neg | 0.225 | 1 |
| 4 | site_15530_train10_100Hz_pos | 0.181 | 4 |
| 5 | site_4900_train10_100Hz_neg | 0.175 | 1 |

- **site_15034_train10_100Hz_pos** clearly dominates (1.682 vs next at 0.263 — 6× margin)
- This run shows **positive_first preference** for the top site (opposite from corticalstim1 where all sites preferred neg)
- 10 Hz conditions all scored near zero: best 10 Hz condition was `site_2254_train10_10Hz_neg` at 0.018 — ~100× worse than best 100 Hz
- **Phase E trigger not met** (threshold not cleared for either frequency arm)
- Noise floor at 30 trials: ~1.8; the top condition (1.682) is just below noise floor threshold (2.5) — technically marginal
- Top reliable unit: uid 49, responds to 13 conditions, best at site_15034_train10_100Hz_neg
- uid 652 shows highest peak strength (1.45) specifically for site_15034_train10_100Hz_pos

### Output Files
- `recordings/2026-05-15/111449_screen/results/screen_summary.json`, `conditions_ranked.csv`
- `sorted/sorting_report.md` (23 units)

### Status
**COMPLETED** — one-shot screen. Results suggest site_15034 at 100 Hz positive-first is the best candidate, but all values are near or below the noise-floor threshold for 30 trials. 10 Hz arm showed essentially no response.

---

## 12. noah_corticalstim4_2026-05-21

### Goal
Fast targeted experiment on a new cortical organoid chip. Use proven parameters (`stim-optimize-maxone-cortical_2026-04-27` parameter set) to identify a small set of solid non-local responding units. Screen top 5 stim-compatible electrodes at both polarities with train10 @ 100 Hz. Output: driver–responder pairs on this chip.

### Setup
- **Rig:** MaxOne (20 kHz, 1 well, well 0)
- **Culture:** Cortical organoid, new chip (DIV/chip not filled in before run)
- **Stim parameters:** 10 pulses @ 100 Hz, 600 mV, 200 µs, both polarities, 30 trials
- **Wired electrodes:** 5 (strict pool target)
- **Operator:** Noah
- **Session date:** 2026-05-21 (recording at `recordings/2026-05-21/161137_select_well0/`)
- **Conditions:** 8 (4 electrodes × 2 polarities — note: plan targeted 5 but 4 were wired)

### What Was Done
Phase A: activity scan → pool of 5 candidates → compat resolved → routing.

Phase D (sort): **FAILED** — `EmptyWaveformMetricsError`: `Cannot compute 'std_norm': no precomputed values in neuron_attributes and raw_data is empty. Call compute_waveform_metrics() first, or attach raw voltage traces.` (same bug as maturity-pilot Run 1 / Well 1 SNR curation issue). Sort exited after ~15 min at the EXTRACTING WAVEFORMS stage.

Despite the sort failure, `results/screen_summary.json` exists with 4 curated units — suggesting either the analysis script was rerun successfully after the sort, or a partial result was extracted separately.

### Key Results (from results/screen_summary.json)
- **4 curated units, 3 solid (consistency ≥ 0.5 on at least one condition)**
- **Top condition: `site_24717_train10_100Hz_pos` at nl_strength 0.9956**, n_responders_nonlocal = 3

| Rank | Condition | Polarity | nl_strength | n_responders_nl |
|------|-----------|----------|-------------|-----------------|
| 1 | site_24717_train10_100Hz_**pos** | pos | 0.9956 | 3 |
| 2 | site_1719_train10_100Hz_neg | neg | 0.7756 | 2 |
| 3 | site_7772_train10_100Hz_pos | pos | 0.4978 | 3 |
| 4 | site_18164_train10_100Hz_pos | pos | 0.3867 | 2 |
| 5 | site_24717_train10_100Hz_neg | neg | 0.1222 | 2 |

- Top site (24717) shows positive_first preference (0.996 vs 0.122 for neg — ~8× ratio)
- All conditions score <1.0 — modest but above zero response

### Output Files
- `recordings/2026-05-21/161137_select_well0/screen.raw.h5`
- `sorted/sorting_report.md` (status: FAILED)
- `results/screen_summary.json` (4 units, 8 conditions)

### Status
**PARTIAL** — sort step failed with `EmptyWaveformMetricsError`. Screen summary appears to have been produced subsequently (possibly from a partial sort or manual recovery). Results are marginal (all conditions <1.0 nl_strength, only 4 units).

---

## 13. noah_midbrainstim2_priming_2026-05-18

### Goal
Test whether a slow priming stimulation followed by a fast depolarizing burst can drive network-wide activity in a midbrain organoid. The hypothesis (grounded in dopaminergic tonic→phasic firing literature) is that slow priming trains partially depolarize neurons and relieve Mg²⁺ block of NMDA receptors, lowering the threshold for the subsequent fast burst to recruit the network. Success criterion: at least one condition with `strength_nonlocal_top10 > 5.0`.

### Setup
- **Rig:** MaxOne (20 kHz, 1 well, well 0)
- **Culture:** Midbrain organoid, chip 24478, new organoid
- **Base recording config:** pre-existing 1016-channel cfg at `/home/sharf-lab/configs/260518/10h23m25s.cfg`
- **Stim sites:** 7 amplitude-ranked sites (≥150 µm spacing), pre-verified routable:
  - 22780 (42.9 µV), 20578 (31.0 µV), 16200 (28.7 µV), 13580 (19.7 µV), 17745 (16.3 µV), 13779 (17.3 µV), 11366 (21.5 µV)
- **Operator:** Noah
- **Session date:** 2026-05-18 (recording `125255_screen`)
- **Conditions:** 96 (7 sites × 4 prime_freq {4, 10 Hz} × depo_freq {40, 80 Hz} × gap {0, 350 ms} × 2 polarities × 5 trials = 112 designed, but 96 executed per summary)
- **Evoked window:** [5, 500] ms

### What Was Done
Each condition = priming train (slow freq, ~5 pulses) + optional gap + depolarizing burst (fast freq, ~5 pulses). Both on same electrode. 5 trials per condition.

RT-Sort → **7 curated units**

### Key Results

**Success criterion MET** — `strength_nonlocal_top10 > 5.0` achieved.

**Top 5 conditions:**

| Rank | Condition | Electrode | Prime Hz | Depo Hz | Gap ms | Polarity | nl_strength | n_responders_nl |
|------|-----------|-----------|----------|---------|--------|----------|-------------|-----------------|
| 1 | site_22780_prime10hz_depo40hz_gap0ms_**neg** | 22780 | 10 | 40 | 0 | neg | **6.42** | 3 |
| 2 | site_20578_prime4hz_depo80hz_gap0ms_pos | 20578 | 4 | 80 | 0 | pos | 6.01 | 3 |
| 3 | site_22780_prime10hz_depo40hz_gap350ms_pos | 22780 | 10 | 40 | 350 | pos | 5.22 | 4 |
| 4 | site_22780_prime10hz_depo40hz_gap0ms_pos | 22780 | 10 | 40 | 0 | pos | 4.90 | 6 |
| 5 | site_13779_prime4hz_depo80hz_gap350ms_neg | 13779 | 4 | 80 | 350 | neg | 4.70 | 4 |

**Axis-level summaries (mean nl_strength by axis value):**

| Axis | Value | Mean nl_strength |
|------|-------|-----------------|
| Prime freq | 4 Hz | 1.354 |
| Prime freq | 10 Hz | 1.146 |
| Depo freq | 40 Hz | 1.210 |
| Depo freq | 80 Hz | 1.290 |
| Gap | 0 ms | **1.523** |
| Gap | 350 ms | 0.977 |
| Polarity | positive_first | 1.099 |
| Polarity | negative_first | **1.400** |

**Key findings:**
- **Gap=0 ms outperforms gap=350 ms** (1.52 vs 0.98 mean) — immediate depolarizing burst after priming is more effective than waiting 350 ms. This argues against NMDA temporal dynamics (which predict gap should help) and more toward fast AMPA facilitation or direct membrane summation.
- Top unit (uid 468): responds to 54/96 conditions at mean consistency 0.274, peak strength 6.26 — broadly responsive across sites and parameters
- Site 22780 (highest amplitude, most neighbors) consistently produces the strongest responses
- All three top conditions involve site 22780 with 10 Hz priming at 40 Hz depolarizing

### Output Files
- `recordings/2026-05-18/125255_screen/` — screen recording
- `recordings/2026-05-18/125255_screen/results/screen_summary.json`
- `sorted/sorting_report.md` (7 units)

### Status
**COMPLETED** — one-shot screen, success criterion met (nl_strength > 5.0 achieved by conditions 1 and 2). The priming paradigm works, but the gap data suggests the mechanism is faster than NMDA de-blocking.

---

## 14. noah_devStim_1

### Goal
Identical design to `hub-induction_2026-05-18`: use daily stimulation to induce two spatially-independent activity hubs in a developing iPSC-derived midbrain organoid. Two stim sites locked from impedance scan, constant throughout experiment lifetime.

### Setup
- **Rig:** MaxOne (20 kHz, single well, well 0)
- **Culture:** iPSC-derived midbrain organoid (DIV not yet filled in)
- **Cadence:** daily, open-ended
- **Operator:** Will
- **Plan created:** 2026-05-18 (same date as hub-induction_2026-05-18)

### What Was Done
**Setup run only — no daily sessions were executed.**

State file at `chip_hub/state.json` is identical to `hub-induction_2026-05-18`:
```
"stage": "setup", "div_at_start": null, "stim_site_0_electrode": null, "stim_site_1_electrode": null
```
`chip_hub/log.json` is empty (`[]`). No recordings exist in `recordings/`. No wakeup timers were scheduled.

**Relationship to hub-induction_2026-05-18:** This appears to be a second parallel instance of the hub-induction plan (intended for a separate chip running the "chip_hub" structured stimulation condition), created on the same date as `hub-induction_2026-05-18`. Neither plan's setup run was completed.

### Status
**PLAN ONLY — SETUP RUN NOT COMPLETED.** Never started.

---

## 15. midbrain-stim-detect_2026-06-09

### Goal
Screen stimulation sites on midbrain organoid chip 32315 using both single-site and simultaneous dual-site (co-stimulation) protocols. Phase 1: identify all candidate sites by non-local recruitment strength (single-site trains). Phase 2: test all pairwise co-stimulation combinations within top-10 and bottom-10 sites to determine whether simultaneous dual-site stimulation enhances network recruitment relative to the best individual site in each pair. The comparison between top-pair and bottom-pair responses addresses whether spatial summation can rescue minimally active sites.

### Setup
- **Rig:** MaxOne (20 kHz, 1 well, well 0)
- **Culture:** Midbrain organoid, chip 32315
- **Stim parameters (both phases):** 10 pulses @ 100 Hz, 600 mV, 200 µs, both polarities, 30 trials
- **Phase C1 (single-site):** ~20 candidates × 2 polarities × 30 trials + 300 s baseline
- **Phase C2 (pairs):** (45 top pairs + 45 bottom pairs) × 2 polarities × 30 trials + 120 s baseline; ~3.5 h recording
- **Target pool:** ≥20 sites (so top-10 and bottom-10 are disjoint)
- **Operator:** Noah / sharf-lab
- **Plan created:** 2026-06-09 (today)

### What Was Done
**Plan created only — no recordings have been run yet.**

The `pending_wakeups.json` file exists alongside `experiment_plan.md` but there are no recording directories, no `well_0/` state files, and no sort outputs. This plan was written on the same day as this summary.

### Status
**PLAN ONLY — NOT YET RUN.** The experiment is planned for an upcoming single 5.5–6 hour session. Expected total disk use: ~20–25 GB.

---

## Summary Table

| Experiment | Date | Culture | Rig | Cycles/Runs | Status | Key Finding |
|------------|------|---------|-----|-------------|--------|-------------|
| scan-select-compare | 2026-04-14 | MaxOne (methodological) | MaxOne | 1 (6 conditions) | COMPLETED | `full` × `top1020` best: 184 units, 18.24 units/100ch |
| maturity-pilot | 2026-04-15 | MaxTwo, 6-well | MaxTwo | 4 runs (of open-ended) | COMPLETED (stopped) | All wells Tier 1 (<30 units); wells 0/2/5 emerging; KS2→KS4 substitution |
| stim-optimize (MaxTwo) | 2026-04-20 | — | MaxTwo | 0 (plan only) | PLAN ONLY | N/A |
| stim-optimize-maxone | 2026-04-20 | midbrain organoid, ~DIV 40 | MaxOne | 20 cycles | COMPLETED | Winner: site_22522_train3_200Hz (cons_topK 15.41); train median +4.8% over single |
| stim-optimize-cortical | 2026-04-27 | cortical organoid | MaxOne | 25 cycles | COMPLETED | Polarity electrode-specific; pair_15498+15956_train10_negfirst nl=88 (3 replicates) |
| hCO-stim-demo | 2026-05-13 | KOLF hCO, DIV 136 | MaxOne | 1 (demo) | COMPLETED | 10/11 units responsive; 2 units at 97% response prob |
| hub-induction | 2026-05-18 | midbrain organoid (iPSC) | MaxOne | 0 (setup not done) | PLAN ONLY | N/A |
| stim-candidates | 2026-05-22 | hCO KOLF, DIV ~145, chip 2263 | MaxOne | 1 (screen) | COMPLETED | electrode 19004 neg-first best (nl=2.19, 3× over #2) |
| noah_corticalstim1 | 2026-05-10 | cortical organoid | MaxOne | 1 (screen) | COMPLETED | site_9380/14204_neg top (nl≈8.5, 25–29 responders each) |
| noah_corticalstim2 | 2026-05-14 | cortical organoid | MaxOne | 1 (screen) | COMPLETED (underpowered) | Only 5 curated units; all conditions at noise floor |
| noah_corticalstim3 | 2026-05-15 | cortical organoid | MaxOne | 1 (screen) | COMPLETED | site_15034_train10_100Hz_pos top (nl=1.68, marginal); 10 Hz arm ≈ no response |
| noah_corticalstim4 | 2026-05-21 | cortical organoid (new chip) | MaxOne | 1 (screen, partial) | PARTIAL | Sort failed (EmptyWaveformMetricsError); 4 units recovered; site_24717_pos top (nl=1.0) |
| noah_midbrainstim2_priming | 2026-05-18 | midbrain organoid, chip 24478 | MaxOne | 1 (screen) | COMPLETED | Priming paradigm works; best site_22780_prime10hz_depo40hz_gap0ms_neg (nl=6.42); gap=0 > gap=350ms |
| noah_devStim_1 | 2026-05-18 | midbrain organoid (iPSC) | MaxOne | 0 (setup not done) | PLAN ONLY | N/A |
| midbrain-stim-detect | 2026-06-09 | midbrain organoid, chip 32315 | MaxOne | 0 (not yet run) | PLANNED | N/A — single vs co-stim comparison |

---

## Cross-Experiment Methodology Notes

### Metric evolution
- **v1 (SE-corrected AUROC):** Used in stim-optimize-maxone cycles 1–12; deprecated (CV=0.62, unstable)
- **v2 (rank-based AUROC):** Used from cycle 13 in stim-optimize-maxone; also deprecated by end
- **v3 (`cons_topK`):** Sum of top-bin trial fractions across K_consensus units; used from mid-run onward
- **`strength_nonlocal_top10`:** Standard metric in all Noah experiments and stim-candidates; non-local exclusion radius 100 µm prevents direct-drive confound. Adopted after discovering the original strength_top10 was 90% direct-drive in stim-optimize-cortical cycles 1–10.

### Consistent findings across experiments
1. **Multi-pulse trains (≥5 pulses at 100 Hz) are necessary** for meaningful network recruitment in both midbrain and cortical organoids. Single pulses rarely drive non-local responses above noise floor.
2. **Polarity preference is electrode-specific** — negative-first (cathodic-first) dominates in midbrain and cortical organoids, but anodic-first sites exist (~1 in 5 responsive sites in the cortical experiment).
3. **Non-local metric is essential** — `strength_top10` conflates direct electrode drive with genuine network recruitment. `strength_nonlocal_top10` (100 µm exclusion) is the correct measure.
4. **Kilosort2 is permanently incompatible with the RTX 5090 GPU (Blackwell architecture).** All experiments from maturity-pilot onward use Kilosort4 or RT-Sort.
5. **Sampling rate is 20 kHz** on MaxOne (not 10 kHz as some project notes stated); MaxTwo records at 10 kHz per well.

### Infrastructure discoveries
- `MaxwellRecordingExtractor` channel-count bug: returns shape `(T, 1024)` regardless of routed channel count; fixed in spikelab via `select_channels` (scan-select-compare).
- `cycle_analyze.py` train alignment bug: `max_stim_offset_ms=50` caused wrong-pulse selection for multi-pulse trains; fixed with first-pulse tight recentering (stim-optimize-maxone post-correction).
- Stim_unit collision at runtime: detected in stim-optimize-cortical cycle 5; fixed with wiring guard in stim_run.py.
- `EmptyWaveformMetricsError` in SpikeLab curation: affects SNR computation when raw traces not loaded; hit in maturity-pilot Run 1 and noah_corticalstim4.
