# Research_automation — Architecture Overview

Retrospective overview of the autonomous neuroscience-experiment orchestrator built in this workspace. Describes **what exists and why**, not what remains to be done (see [ephys_todo.md](ephys_todo.md) for outstanding ephys items, and each skill's own docs for its own scope).

---

## Purpose

Coordinate three kinds of hardware/software — a MaxWell MaxTwo multi-electrode array, a Raspberry Pi-hosted custom fluidics platform, and a spike-sorting / analysis stack — into a single autonomous experiment runner driven by a human-written plan.

The first real application is a **maturity-cycle pilot** on 6 neural cultures: take each culture through its lifecycle (too_young → good_age → too_old), run one pharma experiment per well that reaches maturity, capture full provenance. The orchestrator is designed to be general — pharma screens, stim sweeps, impedance tracking, and other shapes fit the same template.

---

## System architecture

### Agents

- **Orchestrator** — LLM agent. Owns the per-session plan, scheduling, lifecycle judgment, experiment triggering, logging. Delegates everything hardware/compute-heavy to subagents.
- **Ephys** — controls the MaxTwo. Runs activity scans, electrode selection, recordings. Produces `.raw.h5` + structured manifests.
- **Habitat-remote** — controls the fluidics Pi over MCP. Media changes, reagent dispenses, wash protocols. Keeps its own rich log as source-of-truth for fluidics metadata.
- **Spikelab** — spike sorting and downstream electrophysiological analysis.
- **Ntp-check** — cross-host clock verification between MaxTwo and Pi.

### Two-tier conversation model

- **Tier 1 (orchestrator ↔ subagent):** natural-language, goal-oriented. "Record 5 minutes on all 6 wells for maturity assessment." "Spike sort the plate recording, then for well 2 give me Tier 3 burst analysis."
- **Tier 2 (subagent ↔ internal skills):** implementation-level. The subagent decomposes the orchestrator's request into internal skill invocations, handles intermediate results, and returns assembled results. The orchestrator never sees tier-2 routing.

Rationale: subagents are more capable than this application alone requires. A fixed API would hide that capability; conversation preserves it. Adding a new analysis method or experiment variant means teaching the relevant subagent, with no orchestrator change.

### State per task

Every autonomous task has a `plan_id` (descriptor + date, e.g. `maturity-pilot_2026-04-14`) and lives in its own directory:

```
orchestrator/<plan_id>/
├── experiment_plan.md       # filled copy of experiment_plan_template.md
├── schedule.json
├── <target>/{state,log,latest_report,baseline}.json
├── recordings/<date>/<HHMMSS_kind_targets>/
│   ├── manifest.json        # per ephys_manifest_spec.md
│   └── <raw data + analysis outputs>
└── logs/
```

Per-target `log.json` is the orchestrator's primary memory — every cycle appends events with timestamps, references, and the orchestrator's reasoning. Nothing is shared between `plan_id`s; each task is self-contained.

---

## Documents in this repo

| File | Purpose |
|---|---|
| [experiment_plan_template.md](../experiment_plan_template.md) | Template for per-task plans (at project root). Copied into `orchestrator/<plan_id>/experiment_plan.md` and filled in at session start with the user. |
| [culture_lifecycle_reference.md](culture_lifecycle_reference.md) | Domain reference for maturity-cycle experiments: tiered analysis, burst-report schema, lifecycle stage definitions (too_young / good_age / too_old), reference ranges, transition rules. |
| [ephys_manifest_spec.md](ephys_manifest_spec.md) | Portable specification for the per-timepoint `manifest.json` — schema, writer/reader rules, cross-system event format. Self-contained; implementable in any codebase. |
| [ephys_todo.md](ephys_todo.md) | Outstanding ephys items: live-culture validation, edge cases, deferred CLI improvements. |
| [orchestrator_plan.md](orchestrator_plan.md) | This file. |

---

## Skills

Each skill is a Claude Code skill with its own `SKILL.md` under `.claude/skills/`.

| Skill | Role |
|---|---|
| `orchestrator` | Executes a user-defined plan. Session-start readiness, cycle loop, manifest usage, escalation, permission model. Domain-agnostic — reads the plan for semantics. |
| `ephys` | MaxTwo operations. Scan / select / record, each step with `--kind`, `--wells`, `--output-dir`. Writes per-timepoint `manifest.json`. |
| `habitat-remote` | Pi fluidics via MCP. Readiness checks, `ask_habitat` for multi-turn ops, task_id-based cross-referencing. |
| `spikelab` | Spike sorting + electrophysiological analysis pipelines. |
| `ntp-check` | MaxTwo ↔ Pi clock verification. Indirect-bound methodology (see below). |

---

## Key design decisions

### Manifest as the join table, not a data copy

Each timepoint directory gets a single `manifest.json` ([ephys_manifest_spec.md](ephys_manifest_spec.md)). The orchestrator uses it to reconstruct *what happened* without parsing logs or opening HDF5s. Cross-system events (fluidics dispenses, stim triggers) are recorded as thin references — `type`, `timestamp_ms`, `frame_offset`, `task_id`, short `summary`. Rich fluidics metadata stays in the habitat log and is joined on `task_id`. No duplication; one source of truth per piece of information.

### Atomic manifest writes, always-merge on append

Writers load → modify → write via tmp + `os.replace`. Never overwrite a field that describes the run as a whole (`created_at`, `kind`, `cli_args`); step-level entries for `scan`/`select` are replaced on re-run, while `record` entries append (a single directory can hold baseline+drug, wash, etc. as separate entries).

### One recording can span multiple conditions

Pharma drugs are added *during* a live recording — so `baseline` and `drug` share one `.raw.h5` with a `habitat_operation` event at the dispense timestamp. The wash is a separate recording because fluidics interrupts the acquisition. Analysis code uses `frame_offset` to split conditions within a file.

### NTP verified indirectly, not via round-trip

The MCP channel introduces multi-second latency; Claude Code's parallel-tool scheduling doesn't fire tightly enough to bracket send/receive events. Direct round-trip timing through the MCP path produces noise many seconds wide — confidently misleading. Instead, the `ntp-check` skill reads each host's NTP subsystem's own offset/dispersion/jitter report and sums them as an upper-bound on cross-host offset. On this setup (MaxTwo stratum 2 → `ntp.ubuntu.com`, Pi stratum 3 via chrony) the bound is ~3.5 ms — well under the 100 ms target for baseline/drug event splits. For sub-ms alignment, NTP is insufficient; a hardware trigger is required.

### Permission model: structural guardrails + comprehensive logging

No per-command approval (would be an impractical bottleneck). Instead:
- Raw data directories are read-only at the OS level.
- Hardware subagents enforce their own safety bounds internally.
- Resource limits are OS-enforced.
- Every action is logged with full context (orchestrator.log, per-target log.json, manifest files, agent_conversations/). Comprehensive logging is the safety net; preemptive blocking is not.

Orchestrator escalates to the user only for **scientific-judgment** questions (anomalous results, ambiguous transitions, unexplained artifacts), not for command execution.

### MaxTwo-specific facts

Verified during the 2026-04-13 empty-plate end-to-end test and saved as persistent memory:

- Sampling is **10 kHz per well** (not 20 kHz as original handoff doc claimed).
- HDF5 layout is per-well under `data_store/dataNNNN/`. `NNNN` is **sequential among recorded wells starting at 0000** — NOT the physical well number. Always read `well_id` to identify the physical well.
- Per-well `start_time`s align within ~3 ms when all 6 wells are recorded together.
- Amplifier gain: the scripts use the hardware default (earlier `set_gain(1024)` override produced `settings/gain = 512` in the HDF5; the override was removed to eliminate the mismatch).

---

## Work completed

### Ephys pipeline and scripts
- Standalone scripts `01_activity_scan.py` → `02_select_electrodes.py` → `03_record.py` → `run_all.py`, invokable individually or chained.
- Three-mode activity scan: `--mode full | sparse_7x | checkerboard`. Sparse_7x (7 blocks, ~6 min) is the default; checkerboard (13 blocks, ~12 min) is the middle ground; full (26 blocks, ~22 min) is exhaustive.
- CLI args on every script: `--wells 0,1,2,3,4,5` (comma-separated subset, validated 0–5), `--output-dir PATH` (overrides `config.OUTPUT_DIR`), `--kind maturity|baseline|pharma|stim|ad_hoc` (tags the manifest).
- `run_all.py` forwards `--wells`, `--output-dir`, `--kind` to all three steps and pre-touches the manifest with `run_all_invoked=true`.
- Removed the ineffective `set_gain(1024)` override; scripts now use the hardware default so there is no mismatch between config and HDF5 `settings/gain`.
- End-to-end verified on an empty plate 2026-04-13: sparse_7x scan (388 s for 7 blocks over 6 wells), 41-region electrode selection (1019/1020 electrodes), 30 s recording (689 MB, 6-well `data0000`–`data0005` all present), subset recording (`--wells 2` → single `data0000` with `well_id=2`).

### Manifest system
- Shared `manifest.py` module with `load_or_init`, `set_step`, `append_recording`, `write_atomic` (tmp + fsync + rename), `append_event_to_last_recording` (with auto-computed `frame_offset`), `record_step_failure` (best-effort, non-masking).
- Schema documented in the ephys skill SKILL.md (inline) and in the portable `ephys_manifest_spec.md` (language-agnostic, self-contained).
- Each script merges its own portion: `01` → `steps.scan`, `02` → `steps.select`, `03` → appends `steps.record` entry with per-well `start_time_ms` / `stop_time_ms` / `sampling_hz` / `spike_count` pulled from the HDF5.
- Always-merge on append: multiple `03_record.py` invocations in the same directory accumulate in `steps.record` (enables baseline+drug + wash workflows).
- First-writer-wins for identity fields (`created_at`, `kind`, `pipeline.cli_args`, `pipeline.config_snapshot`); monotonic for `pipeline.run_all_invoked`.
- Error capture: top-level try/except in each script writes a failed-step entry and appends to `errors[]` before re-raising. Atomic manifest write failures do NOT mask the underlying exception.
- Cross-system event append helper verified (drug-addition event 2 s into a recording → `frame_offset=20000` at 10 kHz).

### NTP verification
- MaxTwo ↔ Pi cross-host offset bounded at ~3.5 ms worst case (2026-04-13), well under the 100 ms target for baseline/drug event splits.
- Extracted `ntp-check` skill with decision matrix and explicit "don't round-trip via MCP" guidance.

### MaxTwo hardware facts (corrected)
- Sampling rate is **10 kHz per well**, not 20 kHz (original handoff doc was wrong; confirmed via `settings/sampling` and frame count / wall-clock duration).
- HDF5 layout is per-well under `data_store/dataNNNN/`. `NNNN` is sequential among recorded wells starting at `0000` — NOT the physical well number. Always read `well_id`.
- Per-well `start_time`s align within ~3 ms when all 6 wells are recorded together.

### Skills and documents
- Skills: `orchestrator` (domain-agnostic plan executor), `ephys`, `habitat-remote` (pre-existing), `spikelab` (pre-existing), `ntp-check`.
- Reference docs consolidated under `docs/`: this overview, `ephys_todo.md` (outstanding items only), `ephys_manifest_spec.md` (portable manifest spec), `culture_lifecycle_reference.md` (domain knowledge for maturity cycles).
- `experiment_plan_template.md` at project root: template for per-task plans. Never filled directly — copied to `orchestrator/<plan_id>/experiment_plan.md`.
- First filled plan: `orchestrator/maturity-pilot_2026-04-14/experiment_plan.md`.

---

## Memory

Claude Code's persistent memory for this project captures facts and feedback that outlive individual sessions. Key entries:

- MaxTwo hardware facts (sampling, HDF5 layout, gain).
- MaxTwo ↔ Pi NTP alignment (~3.5 ms bound, re-verify after reboots).
- No git for experiment scripts (user preference — this is a one-off experiment).

See `~/.claude/projects/.../memory/MEMORY.md` for the full index.
