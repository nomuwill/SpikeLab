# Experiment Plan — TEMPLATE

> **Do not fill this file directly.** At the start of a new orchestrator task, copy this template to `orchestrator/<plan_id>/experiment_plan.md` and fill it in there. `<plan_id>` is a short descriptor of the task joined to the date the plan was created, e.g. `maturity-pilot_2026-04-14`, `ttx-screen_2026-05-01`, `stim-sweep-alpha_2026-06-15`. All state, logs, and recordings for that task live under `orchestrator/<plan_id>/`.

This file defines the contract for one autonomous orchestrator task. The orchestrator reads the filled plan at session start and executes it independently.

**This template is intentionally open-ended.** The orchestrator coordinates `ephys`, `habitat-remote`, `spikelab`, `ntp-check`, and other subagents. Pharma screens, stim protocol sweeps, long-duration observation, impedance tracking, acute recordings, maturity cycling, and many other shapes should fit the same template.

---

## 1. Meta

- **plan_id:** _`<one-or-two-word-descriptor>_YYYY-MM-DD` — e.g. `maturity-pilot_2026-04-14`. This is also the name of the state subdirectory under `orchestrator/`._
- **created_at:** _ISO-8601 timestamp when this plan was finalized_
- **operator:** _name of the person running this task_
- **expected_duration:** _days / weeks / open-ended_
- **cell_line / sample_notes:** _what's on the plate (line, DIV at start, any known characteristics)_

## 2. Goal

_One paragraph in plain English. What are we trying to learn / demonstrate / produce? Success looks like what?_

## 3. Targets

- **Rig:** _`maxone` or `maxtwo`. Determines sampling rate (MaxOne = 20 kHz, MaxTwo = 10 kHz) and well count (MaxOne = 1 well, addressed as `--wells 0`; MaxTwo = up to 6 wells). The orchestrator passes this value to `verify_manifest.py --rig` on every record step, so record it explicitly — do not infer from well count._
- **Biological units:** _which wells / chips / samples are in scope? (e.g. wells 0–5, or well 2 only, or chip `d6a066f7bddb`)_
- **Subagents in use:** _which of `ephys`, `habitat-remote`, `spikelab`, `ntp-check`, others. Note any subagent NOT used so the orchestrator doesn't default to invoking it._
- **External systems cross-referenced:** _e.g. habitat task logs, behavioural rigs. Specify how events from these systems are linked into recording manifests (`events[]` cross-refs)._

## 4. Cadence

- **Type:** _fixed interval / variable interval / event-driven / one-shot / hybrid_
- **Interval (if fixed):** _e.g. every 6 h_
- **Trigger (if event-driven):** _what condition advances the experiment_
- **Termination:** _when does the orchestrator stop? (date, number of cycles, a condition on targets, "until user stops")_

## 5. Per-cycle workflow

Step-by-step protocol. Be concrete:

- Which subagent is invoked, in what order
- Parameters that matter (durations, concentrations, stim protocols, etc.)
- Output directory convention (default: `orchestrator/<plan_id>/recordings/YYYY-MM-DD/HHMMSS_<kind>_<targets>/`)
- Manifest `--kind` value per invocation (`maturity`, `baseline`, `pharma`, `stim`, `ad_hoc`, or extend vocabulary if needed)
- How cross-system events (e.g. habitat dispenses) get appended to the relevant recording's manifest
- **Fluidics air-backpad policy (required on every habitat dispense/aspirate step).** Habitat defaults to *no* backpad unless the caller asks for one, yet the chip-side tubing dead volume is typically ~100 µL per well. A small dispense (e.g. 10 µL drug) will not reach the well if the tubing upstream is filled with a different fluid. For every fluidics step, state explicitly: (a) whether an air backpad is applied before the dispense, and (b) the backpad volume. Default rule of thumb unless the plan argues otherwise: **backpad = no for same-fluid-to-same-fluid exchanges (media→media); backpad = yes, ≥ tubing dead volume, for any fluid transition (media→drug, drug→wash, etc.).** Record this decision at plan-build time — do not defer to runtime.

## 6. Decision rules / conditional branches

Anything that changes behavior mid-run based on results:

- **Gates:** conditions under which a sub-workflow triggers
- **Branches:** alternative paths based on current target state
- **Environmental / hardware monitoring (optional):** thresholds on habitat sensor and pump state that should trigger in-cycle action. Habitat direct MCP tools (`sensor_stats`, `sensor_latest`, `pump_status`, `pump_activity`) bypass the agent serial lock and are cheap enough to poll during long operations — see the orchestrator skill's "Cheap habitat polling" section. Typical thresholds to decide here at plan-build time:
  - **CO2:** acceptable band (e.g. 4.8–5.2%); drift threshold that triggers escalation; whether `co2=0.0` during warm-up should pause the plan or be ignored for the first ~2 min.
  - **Temperature / humidity:** acceptable bands and max drift over a window (use `sensor_stats(window_seconds=...)` for mean/stddev rather than point readings).
  - **Pump state:** whether any `error_code != 0` or new `ERROR` entry in `pump_activity` (e.g. `failed_only=True`) within the cycle window halts operations vs. logs-and-continues.
  - **Polling cadence:** e.g. every 30 s while recording, every 5 min while idle between cycles.
- **References to domain-specific knowledge:** if decisions depend on rich domain knowledge, point to a dedicated reference file (e.g. your own `<experiment>_reference.md` placed in this plan's directory) rather than inlining it here.

## 7. Experiment sub-workflows (if any)

For each sub-workflow (pharma dispense, stim protocol, calibration, wash, etc.):

- **Trigger condition** (from §6)
- **Steps in order**, including which subagent does what and how events are timestamped
- **Manifest entries:** a single `.raw.h5` can span multiple conditions if perturbation happens mid-recording (baseline+drug); a separate recording is a separate manifest entry (wash after drug)
- **Analysis requested** post-hoc and how it references baseline
- **What the orchestrator logs** in the target's state file

## 8. Scientific-judgment escalation

**Escalate to user (examples — edit for this task):**
- Unusual artifacts flagged by a subagent
- Anomalous results that diverge from recent history without clear cause
- Ambiguous state transitions
- Multiple subagent retries failing on the same target

**Decide alone:**
- Routine tier-gate or threshold-adjacent calls (make the call, log reasoning, continue)
- Minor deviations from reference ranges

## 9. Success / termination criteria

- **Successful completion:** _what data / outcomes does a successful run produce?_
- **Early termination:** _conditions that end the task before scheduled end_

## 10. State to persist per target

List the files the orchestrator should maintain per target in `orchestrator/<plan_id>/<target>/`. Common pieces:

- `state.json` — current stage / cycle / experiment progress
- `log.json` — append-only event log with orchestrator reasoning
- `latest_report.json` — most recent structured analysis result
- `baseline.json` — stored baseline if the experiment has a baseline/comparison structure
- Custom: anything else this task needs

## 11. Known constraints / assumptions

- **Disk budget:** _expected total raw data volume; check headroom before starting_
- **Hardware limits relevant to this plan:** _well-specific fluidics ports, electrode budget per well, etc._
- **Fluidics dead volume & backpad:** _chip-side tubing dead volume per well (ask habitat at plan-build if unknown — typically ~100 µL). State backpad policy for each fluid transition in §5; reservoir volume requirements in §11 must include the backpad budget (e.g. if drug transitions use a 100 µL air backpad + 10 µL drug delivery, plan for 110 µL per well of reagent budget, not 10 µL)._
- **Clock alignment target:** _default <100 ms via ntp-check; tighter? relax?_
- **Concurrency:** _which subagents can run in parallel vs. must serialize_
- **Sorter(s):** _Default: `kilosort2` (use unless the plan has a specific reason to pick otherwise). Other options: `kilosort4`, `RT-Sort`. If multiple, say whether they run on the same recordings for comparison or on disjoint subsets. Decide this at plan-build time — do not defer to runtime._
- **`wake_up_max_runtime`:** _Hard ceiling for any single wake-up `claude -p` session. Passed as `--property=RuntimeMaxSec=<value>` to `systemd-run --user` when scheduling each wake-up timer; systemd kills the entire cgroup (claude + any subagent subprocesses, including spike sorts) if the session runs longer. This is a watchdog against wedged sessions — under-set and legitimate long work gets killed; over-set and a hung session holds the flock longer than needed. **Default: `6h`**. Sort-heavy plans (multiple long recordings + kilosort on each) should bump to 12h or 24h. Fluidics-only plans can drop to 1h. Estimate from the longest plausible session — readiness + all subagent calls within one cycle, not the whole experiment's wall-clock. Format: `systemd.time(7)` duration (`30min`, `6h`, `2d`, etc.)._
- **Anything else the orchestrator should know but can't derive from code_

---

## Appendix — how the orchestrator uses a filled plan

1. At session start, the orchestrator looks for filled plans under `orchestrator/<plan_id>/experiment_plan.md`. If none exist, or the user wants a new task, it copies this template and enters **plan-building mode**: walks the user through sections 1–11, assigns a `plan_id` with the user's input, creates `orchestrator/<plan_id>/`, writes the filled plan there, and asks the user to confirm before starting operations.
2. While running, the orchestrator consults the filled plan between cycles to remember the contract — especially sections 5 (workflow), 6 (decision rules), 8 (escalation), and 10 (state).
3. If the user wants to change the plan mid-run, edit the filled plan in `orchestrator/<plan_id>/experiment_plan.md`. The orchestrator reads it at the start of each cycle and adopts changes on the next cycle boundary.
4. When the task ends, the filled plan stays archived alongside the results — it's the provenance record of what was intended.
