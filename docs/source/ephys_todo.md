# Ephys — Outstanding Items

Open-ended items for the MaxWell MaxTwo pipeline. Completed work is logged in [orchestrator_plan.md](orchestrator_plan.md) §"Work completed". Pipeline implementation lives at [../ephys_experiment_scripts/](../ephys_experiment_scripts/) and is wrapped by the `ephys` skill at [../.claude/skills/ephys/SKILL.md](../.claude/skills/ephys/SKILL.md).

---

## Must validate with live cultures

The pipeline has been tested end-to-end on a MaxTwo **without active cultures**. These items need validation once cultures are available:

- [ ] **Meaningful activity map.** Verify the scan correctly identifies electrodes near active neurons (not just noise). Run with `SCAN_SECONDS_PER_BLOCK=30.0` on a well with a live culture and check that the ranked electrodes correspond to visible activity in MaxLab Scope.

- [ ] **Hotspot quality.** Confirm selected regions actually capture spike waveforms on multiple electrodes per neuron (the spike-sorting benefit). After a pipeline run, open the recording in a spike sorter and check that units have multi-electrode footprints.

- [ ] **Spike detection threshold tuning.** Default `DETECTION_THRESHOLD = 5.0` in [../ephys_experiment_scripts/config.py](../ephys_experiment_scripts/config.py). If a known-active culture shows very few active electrodes, try 3.5–4.0× RMS. If too many false positives, 6.0–8.0× RMS.

- [ ] **Recording quality.** Verify `.raw.h5` files contain clean neural data (not noise/artifact-dominated) via MaxLab Scope or SpikeInterface.

---

## NTP follow-ups

MaxTwo ↔ Pi cross-host offset is verified at ~3.5 ms on the current setup. Remaining items:

- [ ] Re-verify after any reboot or NTP-config change on either host (quickest path: invoke the `ntp-check` skill).
- [ ] Measure actual clock drift over time — typically <1 ms on LAN, 10–100 ms against internet pool. Worth a longitudinal check during the first extended run.
- [ ] If additional instruments (cameras, stimulators, behavioural rigs) enter the stack, document each one's clock source and ensure pairwise alignment with the HDF5 `start_time` reference. If any instrument cannot be NTP-synced, plan a shared trigger pulse.
- [ ] Sub-ms alignment (closed-loop stim, etc.) is out of NTP's reach — require a hardware trigger.

---

## Edge cases to watch for during live runs

- [ ] **Wells with no culture.** Noise-only scans rank low, so selections in empty wells are meaningless. Restrict `--wells` to populated wells.
- [ ] **Very dense cultures.** Sparse scans may miss optimal electrode positions between sampled rows/cols. Use `--mode sparse_7x` for screening, then `--mode checkerboard` or `--mode full` for final config if needed.
- [ ] **Scan-to-recording delay.** If hours pass between scan and record, activity may shift. Re-scan after perturbations (media change, temperature shift, etc.).

---

## Deferred / maybe

- [ ] CLI arg for `DETECTION_THRESHOLD` on all three scripts (currently only editable in [../ephys_experiment_scripts/config.py](../ephys_experiment_scripts/config.py)). Useful if threshold tuning per culture becomes routine.
