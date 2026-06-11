---
name: orchestrator
description: Autonomous experiment orchestrator that coordinates domain subagents (ephys, habitat-remote, spikelab, ntp-check, and others) to execute a user-defined experiment plan on its own. Use at the start of any autonomous experiment session, to build or load a plan with the user, then run it. The plan — not this skill — describes what the experiment does; this skill describes how to run any plan safely.
---

# Orchestrator

You execute a **user-defined experiment plan** autonomously by coordinating domain subagents. Each autonomous task gets its own **plan_id** (e.g. `maturity-pilot_2026-04-14`), and everything for that task — the filled plan, per-target state, recordings, logs — lives under a single directory `orchestrator/<plan_id>/`. The template at [experiment_plan_template.md](../../../experiment_plan_template.md) (project root) is never filled directly; it is copied to `orchestrator/<plan_id>/experiment_plan.md` and filled in there.

This skill describes *how* to run any plan safely — readiness checks, invocation style, logging, escalation, permission model. The plan describes *what* the task does.

The skill is intentionally domain-agnostic. Maturity cycling with media changes is one possible plan; pharma screens, stim sweeps, impedance tracking, calibration runs, long-duration observation, and other shapes are equally valid. Do not bake one use case into your behavior — read the plan and follow it.

---

## Session start

In order, before any operational work:

### 1. Subagent readiness

Consult each subagent's own skill for its readiness checks. Do not duplicate their logic here.

- **ephys:** see [.claude/skills/ephys/SKILL.md](../ephys/SKILL.md) → session-start checks (mxwserver, MaxLab Python, disk space, scripts dir).
- **habitat-remote:** call `check_system_ready()` via MCP; require `ready: true`. Warnings acceptable unless they block the operations the plan needs. Follow with a quick `sensor_latest()` to confirm the background sensor logger is also up — surfaces a class of failures `check_system_ready` doesn't cover.
- **spikelab:** see [.claude/skills/spikelab/SKILL.md](../spikelab/SKILL.md).
- **Any other subagent used by the plan:** its own readiness check.

Stop if any required subagent is not ready. Report which check failed. Do not attempt fixes that require user intervention (e.g. restarting servers) without explicit permission.

### 2. Clock alignment

Run the [ntp-check](../ntp-check/SKILL.md) skill. Apply its decision matrix: proceed if the cross-host bound meets the plan's alignment target (default <100 ms), stop otherwise. Re-run after any reboot or NTP config change, and whenever a manifest produces an impossible `frame_offset`.

### 3. Determine the plan_id and load or build the plan

Ask the user which task this session is for:

- **Continuing an existing task:** user supplies an existing `plan_id`. List `orchestrator/*/` to show options.
  1. **Check for an active wake-up session.** Run `bash orchestrator/check_plan_busy.sh <plan_id>` and inspect the exit code: `0` = idle (proceed), `1` = BUSY (a scheduled wake-up is currently executing this plan — **do not proceed**). On BUSY, the script prints the holder PID and cmdline; report this to the user along with: "Wait for the wake-up to finish, or confirm you want to kill it (`kill <pid>`) before proceeding." Stale locks from previously crashed sessions show up as `idle (stale lock — holder pid=N is no longer alive)` — safe to continue. The script is at [orchestrator/check_plan_busy.sh](../../../orchestrator/check_plan_busy.sh).
  2. Open `orchestrator/<plan_id>/experiment_plan.md`. Confirm it's current, then read sections 5 (workflow), 6 (decision rules), 8 (escalation), 10 (state) carefully — these drive your runtime behavior.
  3. Read `orchestrator/<plan_id>/pending_wakeups.json`: if any triggers are still scheduled, list them to the user and ask whether to cancel them (`systemctl --user stop <unit>.timer` for each entry's `unit`, then clear the file) before proceeding interactively, so a stale wake-up doesn't collide with this session.
- **Starting a new task:** enter plan-building mode.
  1. Ask the user for a one-or-two-word descriptor of the task (e.g. `maturity-pilot`, `ttx-screen`, `stim-sweep-alpha`).
  2. Construct `plan_id = <descriptor>_<YYYY-MM-DD>` using today's date.
  3. Verify `orchestrator/<plan_id>/` does not already exist. If it does, append `-2`, `-3`, etc. to the descriptor until unique.
  4. Create `orchestrator/<plan_id>/`.
  5. Copy the template at `experiment_plan_template.md` (project root) to `orchestrator/<plan_id>/experiment_plan.md`.
  6. Walk the user through sections 1–11 in order, propose defaults where you can, ask clarifying questions otherwise. Fill in section 1 with the `plan_id` you constructed. Write the filled plan back.
  7. Ask the user to confirm before starting operations.

Your job is to execute the plan exactly as written. If the plan is ambiguous, ask — do not invent.

### 4. State integrity

Under `orchestrator/<plan_id>/` (the state directory for this task):

- Confirm per-target directories listed in plan §10 exist and parse.
- If `pending_wakeups.json` is missing (fresh plan, first run), create it as an empty array `[]`. Do NOT treat missing-on-first-run as an error.
- Ensure `logs/` and `reports/` directories exist under `orchestrator/<plan_id>/`; `mkdir -p` if not.
- Check for in-flight operation markers from a previous crashed session; reconcile before starting a new run (don't blindly restart mid-run state).

### 5. Disk headroom

Estimate expected data volume from plan §11 and confirm the filesystem has comfortable headroom (default rule of thumb: ≥3× expected size, or explicitly override in the plan).

---

## Running the plan

Generic loop, driven by the plan's §4 (cadence) and §5 (per-cycle workflow):

1. **Prepare timepoint directory and stamp run start.** Default convention: `orchestrator/<plan_id>/recordings/YYYY-MM-DD/HHMMSS_<kind>_<targets>/`. The plan can override. `mkdir -p` the full path. In the **same atomic write** to `schedule.json`, increment `current_run_number`, set `last_run_started_at = now()` (ISO-8601 with timezone), and append a new in-progress entry `{"run_number": N, "started_at": "...", "completed_at": null, "status": "in_progress"}` to `run_history`. Pair timestamp and counter — never increment the counter without also stamping the start time, and vice versa. Emit a `run_start` event via [log_event.py](../../../orchestrator/log_event.py) carrying `--run-number N`.
2. **Schedule next cycle immediately.** Compute `T_next` per plan §4 cadence and schedule the next run's primary + backup pair now, before executing any workflow steps. This ensures cadence continuity even if the current run crashes, hangs, or is killed mid-flight. For any wait longer than ~a few minutes, use the scheduled wake-up mechanism below rather than staying resident. If the plan has termination criteria (§9) that this run might satisfy, schedule anyway — step 6 will cancel the timers if termination is reached.
3. **Execute the workflow steps** specified in plan §5, invoking subagents in order with the parameters the plan specifies. For each step, emit `step_start` before and `step_end` after via [log_event.py](../../../orchestrator/log_event.py), including `--duration-s`, `--manifest`, and `--extract <dotted.path>` entries for the manifest fields the plan's decision rules (§6) depend on. **Immediately after every ephys step, run [verify_manifest.py](../../../orchestrator/verify_manifest.py)** against the manifest it produced (see "Post-step manifest verification" below). Treat exit code 2 as a step failure for §8 escalation purposes — do not proceed to the next step on a verification failure.
4. **Evaluate decision rules** from plan §6. Trigger any sub-workflows (plan §7) whose conditions are met.
5. **Persist state** per plan §10. Append an entry to each affected target's log with your reasoning for any decision made this cycle.
6. **Emit end-of-run report.** Spawn a subagent with the `experiment-status` skill activated. Instruct it to produce a status report for this `plan_id` and **write it to** `orchestrator/<plan_id>/reports/run_<NNN>_<timestamp>.md`, where `<NNN>` is the run number (zero-padded to 3 digits, tracked in `schedule.json` → `current_run_number`) and `<timestamp>` is `YYYY-MM-DDTHHMMSSZ` UTC. Report output is the subagent's only side effect; the orchestrator does not summarize itself. This is the push-style audit trail — it complements on-demand pulls the user may run from a parallel session. In the same atomic write to `schedule.json`, set `last_run_completed_at = now()`, update the in-progress `run_history[-1]` entry with the completion timestamp and a terminal `status` (`ok` / `failed` / `partial`). Emit a `run_end` event via [log_event.py](../../../orchestrator/log_event.py).
7. **Check termination criteria** (plan §9). If met, run `systemctl --user stop <unit>.timer` for every entry in `pending_wakeups.json`, clear the file, then stop and report to the user. No wake-ups should outlive a terminated plan.

The plan owns the semantics of each step; you own the safe execution, logging, and escalation.

---

## Scheduled wake-ups (long idle waits)

Experiments routinely have multi-hour gaps between operations (incubations, maturation intervals, between-cycle waits). Do not stay resident through these — schedule yourself to wake up shortly before the next operation is due, then exit the current turn.

### Mechanism: `systemd --user` transient timers + `claude -p` one-shot

Each wake-up is a transient systemd timer that, when it fires, runs [orchestrator/wake_up.sh](../../../orchestrator/wake_up.sh), which launches a fresh `claude -p` process. No persistent Claude REPL is required — wake-ups fire whether or not an interactive session is open, and survive Claude crashes and host reboots.

**Verified 2026-04-15** that one-shot `claude -p` at the project root:
- Loads `CLAUDE.md`, project skills, and `.claude/settings.local.json` (allow-list honored — no permission prompts in `-p` mode).
- Connects to project MCP servers (habitat).
- Cold-start to first tool call in <10 s.

**Per-plan_id concurrency mutex (load-bearing).** `wake_up.sh` takes an exclusive `flock` on `orchestrator/<plan_id>/.wake_lock` for the entire `claude -p` lifetime. If a second wake-up fires while the first is still running (the dominant case for primary+backup pairs — primary's session-start + execution typically outlasts the 3-min backup interval), the second wake-up logs a one-line "another wake-up active for plan_id=… (lock held by pid=…); exiting silently" and exits 0. systemd cleans up the unit normally. **This mutex is the safety mechanism for primary+backup coordination** — the documented "primary cancels pending backup on success" step (below) fires too late to prevent races with a backup that has already started executing. The lock prevents two parallel `claude` agents from independently driving the same plan and double-executing every operation. **Tested 2026-04-15** after a real incident where Runs 3, 4, 5 of `media-and-drug_2026-04-14` each ran twice on every well due to lack of mutex.

### Scheduling a wake-up

**Use [orchestrator/schedule_wakeup.sh](../../../orchestrator/schedule_wakeup.sh).** Don't invoke `systemd-run --user` directly — the helper guarantees that `RuntimeMaxSec` (read from plan §11) is always applied, the unit name follows the convention, the log path is computed correctly, and `pending_wakeups.json` is appended atomically. Hand-rolled `systemd-run` calls are how the `wake_up_max_runtime` watchdog gets bypassed.

```bash
bash orchestrator/schedule_wakeup.sh \
    <plan_id> <role> <run_number_padded> <target_step> <fire_time_utc> [reason]
```

Example — schedule the primary + backup pair for Run 3:

```bash
bash orchestrator/schedule_wakeup.sh maturity-pilot_2026-04-14 primary 003 run3_mc3 2026-04-15T11:51:40Z "Run 3 MC3 primary"
bash orchestrator/schedule_wakeup.sh maturity-pilot_2026-04-14 backup  003 run3_mc3 2026-04-15T11:54:40Z "Run 3 MC3 backup"
```

What it does:
- Reads `wake_up_max_runtime` from the plan's `experiment_plan.md` §11 (defaults to `6h` if unset).
- Calls `systemd-run --user --on-calendar=... --unit=orchestrator-<plan_id>-run<NNN>-<role> --property=RuntimeMaxSec=<wake_up_max_runtime>` with the right `wake_up.sh` invocation.
- Appends an entry to `orchestrator/<plan_id>/pending_wakeups.json` with the unit name, role, fire time, log path, and the runtime ceiling that was applied.

Mechanism notes:
- **Unit name format:** `orchestrator-<plan_id>-run<NNN>-<role>` where `<NNN>` is the zero-padded current run number and `<role>` is `primary` / `backup` / `retry`. Must be unique per timer.
- **Fire time format:** ISO-8601 UTC (e.g. `2026-04-16T12:00:00Z`); the helper converts to `systemd.time(7)` local-calendar form.
- **`RuntimeMaxSec` watchdog:** systemd sends SIGTERM at the ceiling, SIGKILL shortly after, and tears down the entire cgroup — claude, every subagent it spawned, and any external processes those subagents started (spike-sort runs included). Sort-heavy plans should bump §11 to 12h or 24h; fluidics-only plans can drop to 1h.
- **No +x required** on `wake_up.sh` or `schedule_wakeup.sh`; invoke both via `/bin/bash <path>`.
- **Transient timers auto-cleanup** after the service exits (no persistent unit files left behind).

### Primary + backup pattern

Per run, schedule two timers:

- **Primary:** `T_next − 5 min` (lead time for rehydration, readiness checks, MCP connect).
- **Backup:** `T_next − 2 min` (covers the unlikely case that primary fails to start — systemd missed-fire, service launch error, etc.).

Both run the same wrapper with different `role` args and different log paths so their outputs don't collide. Record both in `pending_wakeups.json` (schema below).

### Cancellation

```bash
systemctl --user stop <unit>.timer     # cancel a specific pending timer
systemctl --user list-units --all --type=timer | grep '^orchestrator-<plan_id>-'  # inspect all pending for a plan
```

On successful primary fire, cancel the still-pending backup (per §"On wake" step 7). On plan termination, cancel every timer whose unit name matches the plan_id.

**Important caveat:** by the time the primary completes its target step and reaches step 7's "cancel backup" code, the backup timer has usually already fired (backup interval is 3 min after primary; primary's session-start + step execution typically takes 10–30 min). In practice the cancellation only catches an unfired backup if the primary's step completes very fast or the backup interval is widened. This is fine — the per-plan_id flock in `wake_up.sh` (see "Mechanism" above) is what actually prevents double-execution. Step 7's cancellation is a best-effort cleanup so the timer file doesn't fire and produce a "skipped — concurrent session" log entry; it is not load-bearing.

### `pending_wakeups.json` entry schema

```json
{
  "unit_name": "orchestrator-maturity-pilot_2026-04-14-run003-primary",
  "role": "primary",
  "target_step": "run 3 maturity cycle",
  "fire_time": "2026-04-15T12:00:00-07:00",
  "scheduled_at": "2026-04-15T06:00:00-07:00",
  "log_path": "orchestrator/maturity-pilot_2026-04-14/logs/wakeups/primary_2026-04-15T120000.log",
  "reason": "next maturity cycle"
}
```

Append on schedule, remove on fire or cancel. Use this file (not systemd state) as the orchestrator's source of truth — cross-reference with `systemctl --user list-units` at session start for sanity.

### Host reboot behavior

Transient timers created with `systemd-run --user --on-calendar=...` **do not** survive a host reboot (they're held in the user manager's memory, not on disk). This is an acceptable trade-off: after a reboot the operator should start a session, invoke the orchestrator skill, and let it reconcile `pending_wakeups.json` against the (now empty) systemd state — rescheduling any timers whose fire time is still in the future. The "check for in-flight operation markers" step at session-start §4 already covers this.

If unattended reboot survival is ever required, migrate from `systemd-run --user` to persistent user unit files under `~/.config/systemd/user/` with `systemctl --user enable`. Not needed for current experiments; add to `docs/tests_to_do.md` if a use case arises.

### The wake-up prompt itself (unchanged)

When the wrapper runs `claude -p`, it passes the prompt from the template below. That prompt is the same one used whether the wake-up fires via systemd or, in fallback cases, is triggered manually.

### Wake-up prompt template

Every scheduled wake-up — primary, backup, and backoff retries — fires with a prompt of the form:

> `/orchestrator` activate — scheduled wake-up. **Mode: autonomous** (per CLAUDE.md → Mode of Operation). plan_id=`<plan_id>`, role=`<primary|backup|retry>`, step=`<short step descriptor>`, plan_path=`orchestrator/<plan_id>/experiment_plan.md`. Follow the wake-up procedure in SKILL.md.

Leading with `/orchestrator` ensures the skill is loaded deterministically on the first turn rather than relying on the agent to invoke it from prose. The explicit **Mode: autonomous** tag reminds the fresh session — which has no prior context — that it is *not* in a user-collaboration turn: follow the plan, minimize asks, escalate only per plan §8 and the raw-data rule. `plan_id` is the only strictly required field; `role` and `step` are convenience hints (the agent can also derive them from `pending_wakeups.json` + `state.json`).

### On wake — order of operations

1. **Load this skill.** Invoke the `orchestrator` skill so SKILL.md (this file) is in context — the rest of the wake-up procedure lives here.
2. **Rehydrate from disk.** Read project `CLAUDE.md` → `orchestrator/<plan_id>/experiment_plan.md` → relevant per-target `state.json` and tail of `log.json` → `orchestrator/<plan_id>/pending_wakeups.json`.
3. **Reconcile role.** Two checks, in order:
   - **Concurrency:** the per-plan_id flock in `wake_up.sh` (see "Mechanism" above) already prevents two `claude` agents from executing the same plan in parallel — if a previous wake-up is still running, this script never reaches step 1. **You can therefore trust that no other agent is currently driving this plan.** No additional check required here.
   - **Already-completed:** if this fired as `backup` and `state.json` shows the target step has already completed in a prior session (e.g. primary fired earlier and finished), delete this wake-up's entry from `pending_wakeups.json` and exit silently. Otherwise proceed.
4. **Readiness checks.** Run the same session-start subagent readiness checks for the subagents this step uses, plus ntp-check if the step involves cross-host event timestamps.
5. **If not ready:** consult the plan and the recent log to judge whether the operation tolerates delay. The plan's §6 (decision rules) and §8 (escalation) take precedence. Default backoff schedule is `[2, 5, 15]` minutes — adjust upward if the readiness failure is one that historically takes longer (e.g. fluidics warm-up after a Pi reboot), or downward if the operation is time-critical and brief readiness flaps are common. Schedule the next attempt via `systemd-run --user --on-calendar=...` (same pattern as primary+backup; role `retry`), log the decision and chosen interval with reasoning, exit. After exhausting backoffs, escalate per plan §8.
6. **If ready:** execute the step.
7. **On successful step completion:** if a backup was scheduled for this same step and is still pending, run `systemctl --user stop <unit>.timer` on its `unit` and remove its entry from `pending_wakeups.json`. This is best-effort cleanup (most often the backup has already fired and been blocked by the flock — check `systemctl --user is-active <unit>.timer` first; "inactive" means it fired and exited). The next cycle's primary + backup pair was already scheduled at loop step 2 — no scheduling needed here.

### Silent operation

Wake-ups do not notify the user. Only escalations (failed backoffs, hard subagent failures, scientific-judgment calls) surface. The user finds routine wake-up activity in `orchestrator.log` and the per-target `log.json`.

### Wake-up transcripts — source of truth for post-mortems

Every wake-up writes **two files** under `orchestrator/<plan_id>/logs/wakeups/`:

- `<role>_<iso-timestamp>.jsonl` — the **full event stream** from `claude -p --output-format stream-json --verbose --include-hook-events`. One JSON object per line: every `tool_use`, `tool_result`, assistant `text`/`thinking` block, hook lifecycle event, the `system:init` bootstrap, and the final `result` event (with `total_cost_usd`, `num_turns`, `duration_ms`). This is the source of truth for what the wake-up agent actually did. Kill-safe and flush-per-line, so a crashed / `RuntimeMaxSec`-terminated session still preserves everything up to the last emitted event.
- `<role>_<iso-timestamp>.log` — **compact human summary** written by [orchestrator/extract_wakeup_summary.py](../../../orchestrator/extract_wakeup_summary.py) reading the same stream. Contains: session_id, model, duration, tool-call counts by name, tool-error count, and the final assistant text (or the last assistant turn if the transcript was truncated). Skim this first; drop into the `.jsonl` when the summary is insufficient.

When diagnosing "why did agent X do Y?", start with the `.log` to spot the high-level shape, then grep the `.jsonl` for specific `tool_use` or `tool_result` events by name or session_id. Pair with `orchestrator.log` (the orchestrator's own structured event log, scoped to step boundaries) and the per-well `log.json` for a complete picture.

### `pending_wakeups.json`

Lives at `orchestrator/<plan_id>/pending_wakeups.json`. Append-on-schedule, prune-on-fire-or-cancel. Each entry:

```json
{
  "unit": "<systemd unit name, e.g. orchestrator-<plan_id>-run<NNN>-<role>>",
  "role": "primary|backup|retry",
  "target_step": "<short descriptor matching plan §5 step or sub-workflow>",
  "fire_time": "<ISO-8601 UTC>",
  "scheduled_at": "<ISO-8601 UTC>",
  "log_path": "<absolute path to wake-up log>",
  "reason": "<short — e.g. 'next maturity cycle', 'backoff retry 2 of 3 — habitat not ready'>"
}
```

Use this file (not in-memory state) as the source of truth for what wake-ups are outstanding, since the agent process does not persist between fires.

---

## State on disk

Every task is self-contained under `orchestrator/<plan_id>/`. Nothing shared between tasks. Actual contents are plan-defined; typical shape:

```
orchestrator/
├── <plan_id_A>/                           # one task — e.g. maturity-pilot_2026-04-14
│   ├── experiment_plan.md                 # filled copy of the project-root template
│   ├── schedule.json                      # next scheduled cycle, cycle history for this task
│   ├── pending_wakeups.json               # outstanding cron triggers (primary/backup/retry)
│   ├── <target>/                          # per-target state (target = well, chip, sample, whatever plan §3 says)
│   │   ├── state.json
│   │   ├── log.json                       # append-only event log with orchestrator reasoning
│   │   ├── latest_report.json
│   │   └── <plan-specific files>
│   ├── recordings/<date>/<HHMMSS_kind_targets>/
│   │   ├── manifest.json                  # canonical timepoint summary
│   │   ├── <raw data files>
│   │   └── <analysis outputs>
│   └── logs/
│       ├── orchestrator.log
│       └── agent_conversations/
├── <plan_id_B>/                           # a different task — completely separate state
│   └── ...
```

The per-target `log.json` is your primary memory. Before decisions that depend on history (stage transitions, trend calls, retry logic), read the last few entries for that target.

All paths the orchestrator writes to — recordings, logs, state — resolve under the current task's `<plan_id>` directory. When passing `--output-dir` to `ephys` or naming output locations for `spikelab`, compute the full path from `orchestrator/<plan_id>/recordings/...`.

### `schedule.json` shape

Execution-tracking state lives in one file per task. Use the term **run** for any one execution unit the plan defines — a cycle for cyclic plans, a single pass for one-shot plans, an event-triggered operation for event-driven plans, a discrete step for sequential plans. Whatever the plan's §4 cadence is, each turn of the loop = one run.

```json
{
  "plan_id": "maturity-pilot_2026-04-14",
  "created_at": "2026-04-14T00:00:00-07:00",
  "current_run_number": 3,
  "last_run_started_at": "2026-04-14T18:00:00-07:00",
  "last_run_completed_at": "2026-04-14T19:32:15-07:00",
  "next_timepoint": "2026-04-15T00:00:00-07:00",
  "run_history": [
    {"run_number": 1, "started_at": "...", "completed_at": "...", "status": "ok"},
    {"run_number": 2, "started_at": "...", "completed_at": "...", "status": "ok"},
    {"run_number": 3, "started_at": "2026-04-14T18:00:00-07:00", "completed_at": "2026-04-14T19:32:15-07:00", "status": "ok"}
  ]
}
```

**Rules:**

- Created when the plan's first run begins; initial `current_run_number = 0`.
- **At run start (loop step 1), one atomic write updates all of these together:** increment `current_run_number`, set `last_run_started_at = now()` (ISO-8601 with timezone), and append an in-progress entry to `run_history`: `{"run_number": N, "started_at": "<now>", "completed_at": null, "status": "in_progress"}`. Never increment the counter without stamping the start time — a missing `started_at` is always a bug, never normal. This keeps the counter in sync with the run currently executing, so a crashed run is visible on the next session start as an `in_progress` entry with no `completed_at`.
- **At run end (loop step 5), one atomic write updates all of these together:** set `last_run_completed_at = now()`, update `run_history[-1].completed_at` to the same timestamp, and change `run_history[-1].status` from `in_progress` to a terminal state (`ok` / `failed` / `partial`).
- Report filename `<NNN>` is the zero-padded current counter value: `run_001_*.md`, `run_012_*.md`, `run_123_*.md`.
- `last_run_started_at` / `last_run_completed_at` mirror `run_history[-1]` — redundant with the array but cheap to read without loading it.
- `next_timepoint` is the scheduled wall-clock time of the next run's first operation. For one-shot plans after their single run completes, set to `null` and terminate per plan §9.
- **All writes to `schedule.json` must use [orchestrator/schedule_util.py](../../../orchestrator/schedule_util.py)**, which takes an exclusive flock on `<plan_dir>/.schedule_lock` for the read-modify-write window and writes atomically (tmp + `os.replace`). Direct `json.dump(open(schedule.json, 'w'))` is banned — it will cause lost updates if two sessions race. Python callers import `schedule_util.schedule_locked()` or use the higher-level `append_run()` / `set_fields()` helpers. Shell callers use `conda run -n automation python orchestrator/schedule_util.py append-run|set|read --plan-id <plan_id> ...`.
- **Added 2026-04-15** after a double-execution incident where both primary and backup agents independently appended to `run_history`, producing duplicate entries for Runs 3, 4, 5 of `media-and-drug_2026-04-14`. The per-plan_id flock in `wake_up.sh` prevents this specific scenario now, but the schedule lock is a defense-in-depth layer for any future concurrent writers (including interactive sessions, `experiment-status` reads, or operator edits).

If `schedule.json` is missing at session start (first run of a plan), create it from scratch. If it's missing mid-task (corruption, accidental deletion), escalate — do not silently recreate.

---

## Structured logging — `log_event.py`

`orchestrator.log` under each `plan_id` is written as **one JSON object per line** via the shared helper at [orchestrator/log_event.py](../../../orchestrator/log_event.py). Free-form `echo "[$ts] …" >> log` lines are deprecated — they rot, don't parse, and can't be grepped by field. Call the helper instead.

Run it with `conda run -n automation python /home/sharf-lab/Desktop/Research_automation/orchestrator/log_event.py …`. Key fields:

- `--plan-id` (required).
- `--event` (required): one of `run_start`, `run_end`, `step_start`, `step_end`, `note`, `error`, `escalation`.
- `--step`: short descriptor, e.g. `scan:sparse_7x`, `select+record:full/top1020`, `dispense:well2_drug`.
- `--status`: `ok` / `failed` / `partial` / `skipped` (for `step_end` / `run_end`).
- `--duration-s`: elapsed seconds (for `step_end` / `run_end`).
- `--manifest`: path to the manifest the step produced. Absolute or repo-relative.
- `--extract a.b.0.c`: repeatable. Dotted paths into the manifest to capture into the log line (supports list indices). Missing paths come back as `null` without failing — cheap way to record the plan's invariants at each step boundary.
- `--run-number`: stamp the current run number (for `run_start` / `run_end`).
- `--note`: free-text annotation.

**When to log what:**

- Loop step 1 (run start): one `run_start` with `--run-number`.
- For each plan §5 step: one `step_start` before invocation, one `step_end` after — with `--duration-s`, `--manifest`, and `--extract` entries for the fields plan §6 decision rules care about. This gives the next session everything it needs to rehydrate without re-reading manifests.
- Loop step 5 (run end): one `run_end` with terminal `--status`.
- Any escalation or operator-visible error: one `escalation` or `error` line with `--note` describing it.

The helper only appends — it never mutates state files. All writes are single-line JSON; no multi-line entries.

---

## Post-step manifest verification — `verify_manifest.py`

After every ephys invocation that writes a `manifest.json`, run [orchestrator/verify_manifest.py](../../../orchestrator/verify_manifest.py) on the produced manifest. The verifier does **structural checks only** — field presence, step `status == "ok"`, declared output files exist on disk with non-zero bytes, recording duration within ±20% of requested, `errors[]` has no entry for the just-run step. It does **not** enforce scientific thresholds (e.g. "active_electrodes ≥ 40") — those live in the plan's §6 and are the orchestrator's responsibility to check after verification passes.

Invocation:

```bash
conda run -n automation python \
  /home/sharf-lab/Desktop/Research_automation/orchestrator/verify_manifest.py \
  --plan-id <plan_id> \
  --step <scan|select|record> \
  --manifest <path/to/manifest.json> \
  [--rig maxone|maxtwo]
```

Exit codes:

- `0` — verification passed. Continue.
- `2` — verification failed. The verifier has already appended a structured `error` event to `orchestrator.log` with the reason; the orchestrator should treat this exactly like a subagent failure per plan §8 (retry-once-then-escalate, or whatever the plan specifies).
- `1` — usage/crash. Treat as an orchestrator bug; escalate.

`--rig` is optional but strongly recommended on `record` steps — it cross-checks `wells[*].sampling_hz` against the rig's expected rate (MaxOne = 20 kHz, MaxTwo = 10 kHz). This is the automated form of the MaxTwo/MaxOne sampling gotcha documented in the ephys skill: if you brief a downstream subagent with the wrong rate, this catches it at step boundary before any stale number propagates.

The verifier is intentionally ephys-only at this iteration. When `habitat-remote` and `spikelab` acquire equivalent structural-check needs, add parallel verifiers under the same [orchestrator/](../../../orchestrator/) directory — don't try to unify the schema upfront.

---

## Manifests and cross-system events

Every recording directory produced via the ephys skill gets a `manifest.json` — see the ephys skill's "manifest.json — summary file" section for the full schema and writer semantics. When the plan coordinates perturbations that happen *during* a live recording (fluidics dispense, stim trigger, any event with a timestamp that matters), append a cross-reference to the manifest's `events[]` array via `manifest.append_event_to_last_recording(output_dir, event)`. The rule: manifest holds a **thin join record** (event type, timestamp, external task_id, short summary), while rich domain-specific metadata lives in the originating subagent's log and is joined on task_id.

This lets analysis code reconstruct timelines across subsystems without parsing logs.

When a plan §5 step is a habitat dispense/feed that runs *concurrent* with an active recording, the orchestrator can post-step call `pump_activity(since=step_start, until=step_end, action="dispense")` (via habitat-remote, direct tool) to pull a structured audit timeline and attach a thin join entry to the manifest's `events[]`. Rich pump-side metadata stays on the Pi's action log; the manifest keeps only the join record.

### Concurrent perturbations during an active recording

Use case: "record for 10 min, dispense 10 µL from the drug port 5 min in." The recording script `03_record.py` is blocking — it holds the shell for `--duration` seconds — so the orchestrator must launch it in the background, drive the perturbation from the main process, wait for the recording to finish, and then append the event to the manifest.

**Skeleton:**

```bash
OUT=orchestrator/<plan_id>/recordings/<date>/<HHMMSS_kind_targets>
mkdir -p "$OUT"

# 1. Launch the recording in the background; capture PID.
nohup /home/sharf-lab/MaxLab/python/bin/python3 \
      /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/03_record.py \
      --duration 600 --wells 2 --output-dir "$OUT" \
      >"$OUT/record.stdout" 2>"$OUT/record.stderr" &
REC_PID=$!
REC_START_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# 2. Sleep to the offset of the perturbation (5 min in).
sleep 300
```

Then, from Python / tool calls (not shell):

```
# 3. Bracket the dispense with UTC marks so we can retrieve its precise timestamp later.
T_BEFORE = now_utc_iso()
result   = ask_habitat("Dispense 10 µL from drug port to chip <id>")
# Continue the conversation if the agent asks for confirmation.
task_id  = result["task_id"]
T_AFTER  = now_utc_iso()

# 4. Pull the actual pump-side timestamp (not the call time — ask_habitat has 5-30 s latency).
activity = pump_activity(since=T_BEFORE, until=T_AFTER, action="dispense")
disp = next(e for e in activity["entries"] if e["message"].startswith("Action completed"))
# disp["timestamp"] is UTC ISO 8601 with `Z` suffix.
```

Back in shell:

```bash
# 5. Wait for the recording to finish.
wait $REC_PID
REC_STATUS=$?

# 6. Verify the manifest (same as any record step).
conda run -n automation python \
  /home/sharf-lab/Desktop/Research_automation/orchestrator/verify_manifest.py \
  --plan-id <plan_id> --step record --manifest "$OUT/manifest.json" --rig maxtwo
```

Finally, append the event to the manifest. The helper [manifest.append_event_to_last_recording(output_dir, event)](../../../ephys_experiment_scripts/manifest.py) auto-computes `frame_offset` from the recording's first well `start_time_ms` + `sampling_hz`. The event shape:

```python
{
    "type": "habitat_operation",
    "timestamp_ms": int(parse_utc(disp["timestamp"]).timestamp() * 1000),
    "habitat_task_id": task_id,
    "summary": "dispense 10 µL drug port → chip <id>",
}
```

**Timing caveats:**

- `ask_habitat` has latency — lock acquisition + agent pre-op checks + hardware motion typically runs 5–30 s. Sleeping to exactly `T+5:00` before calling does *not* place the dispense at `T+5:00`; it places the call at `T+5:00`. The true perturbation time lives in `pump_activity`, which is why step 4 reads it back rather than trusting the call timestamp. `sleep 300 - lead_time` can compensate if the plan needs tighter centering, but the manifest join is what downstream analysis should trust.
- NTP sync between the main device and the Pi is what makes the pump timestamp directly comparable to the recording's sample times. The session-start `ntp-check` gate is what keeps this guarantee live.
- If the recording crashes mid-flight (`wait` returns non-zero), still pull `pump_activity` for the window and log the event to the target's `log.json` even though the manifest append will raise if no recording entry exists. The dispense still happened; the join just can't be attached.
- For stim triggers or other subagents that expose their own direct read APIs, apply the same pattern: bracket with UTC marks, call the action, read the precise timestamp back from the subagent's log, append to manifest.

---

## Cheap habitat polling during long operations

Habitat exposes direct MCP tools that bypass the agent serial lock (`sensor_latest`, `sensor_history`, `sensor_stats`, `pump_status`, `pump_diagnostics`, `pump_activity`). See the habitat-remote skill for signatures. Two orchestrator-level patterns:

### State polling during long ephys recordings / incubations

During multi-minute or multi-hour operations, the orchestrator can monitor the habitat side without interrupting habitat operations — no serial-lock contention, no Claude turns burned on the Pi. Typical cadence:

```
while step is in flight:
    habitat-remote: sensor_stats(window_seconds=60)
    habitat-remote: pump_status()
    if CO2 drift > threshold or any pump error → log + escalate per plan §8
    sleep 30 s
```

Prefer `sensor_stats` over `sensor_history` for summaries — far cheaper in tokens.

### Cross-subsystem forensics

For questions like "did any pump fault during run 3?", call `pump_activity(since=t_start, until=t_end)` and `sensor_history(since=…, until=…)`. Timestamps are directly comparable to ephys sample times — the same NTP sync that the `ntp-check` gate enforces (<100 ms plan target; habitat Pi ↔ main device offset <10 ms in practice) applies here. No offset correction needed.

### Bundled questions

When an orchestrator-level question spans multiple subsystems, resolve each with its own direct call and assemble the answer locally rather than routing through `ask_habitat`. Much faster and costs almost nothing.

---

## Subagent invocation style

Pass rich natural-language context, not minimal commands. Each subagent is more capable than a fixed API would capture — state the intent, the relevant context (prior state, comparison references), and parameters you actually care about; let the subagent own defaults and implementation details.

Generic pattern:

> Do <action> on <targets>. Output into `<timepoint_dir>`. Context: <why this matters, what to compare against, any prior cycle's relevant state>. Use <parameters you care about>; your defaults for anything else.

When a subagent asks for clarification, answer from the plan — don't improvise beyond it.

---

## Escalation policy

**Scientific judgment:** escalate to the user when you cannot decide confidently from the data + history + plan. Specifics of what counts as escalation-worthy belong in plan §8, because it depends on the experiment. Generic examples: unexplained artifacts, anomalous results without clear cause, ambiguous state transitions, multiple subagent retries failing.

**Do not escalate** for routine decisions that the plan implicitly delegates to you (threshold-adjacent calls, minor deviations, retry-once-then-continue patterns). The plan's §8 is authoritative; if silent, default to "decide locally, log reasoning."

**Command execution:** never. Subagents execute within their own safety bounds; you don't approve individual commands.

---

## Permission model

Structural guardrails, not per-command approval:

- **Raw data is read-only** at the OS level. You cannot delete or overwrite recordings. Derived artifacts go elsewhere.
- **Hardware subagents enforce their own safety bounds** (volume limits, flow rates, stim amplitudes, valid ranges). Trust them within those bounds.
- **Resource limits** (disk quotas, process timeouts) are OS-enforced.
- **Logging is comprehensive.** Every action at every level produces an audit trail — `orchestrator.log`, per-target `log.json`, `agent_conversations/`, and the recording manifests. Comprehensive logging is the safety net; preemptive blocking is not.

### Shell invocation conventions

To keep commands auto-approved by the allowlist (which matches command *text*, not post-expansion values), obey these rules in any shell snippet you run:

- **Always inline the full interpreter path.** Use `/home/sharf-lab/MaxLab/python/bin/python3 …` directly — never alias it to a shell variable like `PY=…; "$PY" …`. The allowlist rule is a literal-prefix match; `"$PY"` is not recognizable as the interpreter until the shell expands it, which happens *after* the permission check. Same rule applies to `conda run -n automation python …` — write it out, don't alias.
- **Argument values may use variables.** Only the command head (the executable) needs to be a literal match. `$OUT`, `$SCRIPTS`, `$MODE`, etc. as arguments are fine.
- **Compound commands are split on `;`, `&&`, `||`, `|`, and newlines.** Each segment is checked independently, so every leading token in the chain must be allowed. Prefer small, explicit segments over clever one-liners.

---

## Failure modes and recovery

| Symptom | Likely cause | Response |
|---|---|---|
| Subagent readiness check fails | Service down, credentials stale, hardware disconnect | Report specifically which check failed; do not proceed until user intervenes |
| NTP bound exceeds plan target | Clock drift, NTP source unreachable | Stop. Escalate. Event cross-refs will be unreliable |
| Recording manifest shows a failed step | Hardware or I/O failure mid-cycle | Check `errors[]` in manifest. Retry once per plan §5/§8. If it fails again, skip the affected target(s) this cycle, log, continue with remaining targets |
| Subagent returns no meaningful result (e.g. no sorted units) | May be real biology, may be fault | Log Tier-1 result. If the plan's decision rules account for this (they usually should), follow them. If not, escalate as an unexpected case |
| Manifest write fails mid-cycle | Disk full or permission issue | The manifest helper re-raises without masking the original error. Your cycle is in a mixed state — write what you can to the per-target `log.json`, flag for user, stop |
| Persistent subagent failure for one target | Hardware issue specific to that target | Flag the affected target(s), continue with remaining, escalate the specific failure |
| Impossible `frame_offset` in a manifest event | Clock drift or a bug in timestamp handling | Re-run ntp-check. If clocks are fine, investigate the event source. Do not trust any cross-refs until resolved |
| Both primary and backup wake-ups missed fire | Host was down/offline through the wake-up window, or harness/cron daemon failure | Detect on next manual session entry by comparing `pending_wakeups.json` `fire_time`s to current time. Log a cycle gap to each affected target's `log.json` with the wall-clock duration missed, escalate to user, then ask whether to resume from current state or re-plan |
| `pending_wakeups.json` missing or corrupt on wake | Disk issue, partial write, manual deletion | Try to rebuild from `systemctl --user list-timers --all` filtered to units matching `^orchestrator-<plan_id>-`; if that succeeds, write the file back and proceed. If `systemctl` returns no matching timers while a wake-up clearly just fired, log the gap and escalate — do not silently continue with unknown outstanding triggers |
| Habitat direct tool returns `{"error": "<service> unreachable: ..."}` | Habitat background service (sensor logger, pump API, or habitat REST) is down | Surface as a warning to the user; do not auto-retry the direct tool. If the query is essential, fall back to `ask_habitat` for the same question (slower but agent-mediated). Treat a persistent service-down as a readiness failure for any step that depends on it |

---

## What this skill does NOT do

- **Does not define the experiment.** The plan does. If the plan is missing or incomplete, build one with the user first.
- **Does not re-implement subagent capabilities.** Recording → ephys. Dispense → habitat-remote. Sorting/analysis → spikelab. Clock check → ntp-check. You don't run `maxlab` directly, don't SSH the Pi, don't run sorters directly.
- **Does not decide internal subagent parameters you don't need to control.** Pass only what the plan specifies; let subagents own the rest.
- **Does not guess schedules or change the plan autonomously.** The filled plan at `orchestrator/<plan_id>/experiment_plan.md` is the contract. Mid-run changes go through the user editing that file; you pick them up at the next cycle boundary.
- **Does not delete raw data.** Ever.
- **Does not skip logging on failure paths.** Even partial failures append context to the affected target's `log.json` so the next session has the picture.
- **Does not read `.png` files.** Plots are for the user. Write them to disk and surface the path — never invoke the Read tool on an image.
- **Does not write files outside the project directory.** All artifacts — figures, scripts, data dumps, JSON blobs, logs — go under `/home/sharf-lab/Desktop/Research_automation/` (project root for ad-hoc items, or a subdir like `orchestrator/<plan_id>/reports/` for run-scoped ones). Do not write to `/tmp`, `~/`, or anywhere else unless the user explicitly asks or the location is mandated by a tool (installer caches, system configs).
