---
name: experiment-status
description: Read-only status report for an autonomous experiment. Given a plan_id, reads the on-disk state under orchestrator/<plan_id>/ and returns a human-readable summary. Designed to run in a parallel session while the orchestrator executes — must not mutate any state, invoke subagents, touch hardware, or create/delete cron triggers.
---

# experiment-status

You produce a **human-readable status report** for one autonomous experiment, identified by its `plan_id`. The typical caller is a user running a second Claude session alongside a live orchestrator run who wants to check in without disturbing it.

## Hard constraints — read-only

This skill is strictly observational. You MUST NOT:

- Edit or delete any existing file. The one permitted write is creating a new report file under `orchestrator/<plan_id>/reports/` (or a user-specified path), and only when the user explicitly asks — see "Saving the report to a file" below.
- Invoke any subagent skill (`ephys`, `habitat-remote`, `spikelab`, `ntp-check`, `orchestrator`).
- Invoke `ask_habitat` or any other agent-mediated habitat MCP tool. `ask_habitat` takes the per-task serial lock and would block or collide with the live orchestrator session.

Read-only habitat direct MCP tools (`sensor_latest`, `sensor_history`, `sensor_stats`, `pump_status`, `pump_diagnostics`, `pump_activity`, `check_system_ready`) are permitted and explicitly parallel-safe — they bypass the serial lock and do not mutate state. Use them sparingly to enrich the report window (e.g. habitat conditions during the last run's start/end, pump faults in the window), not to replace on-disk state as the source of truth.
- Create, modify, or delete cron triggers (`CronCreate`, `CronDelete`). `CronList` is acceptable only if needed to cross-reference `pending_wakeups.json`.
- Run scripts, start servers, or execute commands with side effects.

Permitted tools: `Read`, `Glob`, `Grep`, `Bash` for side-effect-free introspection only (`ls`, `stat`, `df`, `du`), and the read-only habitat direct MCP tools listed above. If in doubt, don't run it.

## Inputs

Single argument: `plan_id` (e.g. `maturity-pilot_2026-04-14`).

1. Verify `orchestrator/<plan_id>/` exists. If not, list available plan_ids via `ls orchestrator/` and ask the user to pick one. Do not guess.
2. Treat `orchestrator/<plan_id>/experiment_plan.md` as the ground truth for plan structure, targets, cadence, and termination criteria.

## Files to read

Under `orchestrator/<plan_id>/`:

- `experiment_plan.md` — plan sections 1 (id), 3 (targets), 4 (cadence), 5 (workflow), 9 (termination), 11 (expected volume).
- `schedule.json` — next scheduled cycle + cycle history, if present.
- `pending_wakeups.json` — outstanding cron triggers.
- Each `<target>/state.json` — current target state.
- Each `<target>/log.json` — tail for recent reasoning + to count cycles and surface errors. Do not dump raw entries; extract meaning.
- Each `<target>/latest_report.json` — most recent per-target summary, if present.
- `recordings/<date>/<HHMMSS_*>/manifest.json` — most recent N (default 5) timepoints. Read `started_at`, `kind`, `targets`, `events[]`, `errors[]`.
- `logs/orchestrator.log` — scan (don't dump) for recent escalations, backoff retries, failures.

Missing files are information, not errors: note "no schedule.json yet" rather than failing.

## Report format

Produce a single human-readable markdown response in this order. Keep it tight — summarize, don't paste raw JSON or log tails.

### 1. Overall summary + progress (open with this)

- `plan_id`, descriptor, start date (from plan §1 or earliest log entry).
- Experiment kind in one phrase (from plan §2 purpose, not a verbatim copy).
- Cadence and target count.
- **Progress:** cycles completed / planned (or "open-ended"), time since last cycle, time until next scheduled operation (from `pending_wakeups.json` primary trigger, computed against current time).
- Overall state: `running` / `idle-between-cycles` / `backoff-retry` / `escalated` / `terminated` — infer from most recent log entries and pending wake-ups.

### 2. Per-target state

One short paragraph or bullet per target:

- Current stage / status from `state.json`.
- Last action and when (from `log.json` tail, paraphrased).
- Any target-specific trend worth noting from recent entries (e.g. "stage advanced this cycle", "retry-once triggered for recording").

### 3. Recent recordings

Last ~5 timepoints as a compact table or list:

- Timestamp, kind, targets, duration (if in manifest), and a flag if `errors[]` is non-empty or `events[]` contains cross-system events.

### 4. Scheduled wake-ups

Summarize `pending_wakeups.json`: next primary, its backup, any active retries — each with fire time and role. If empty and plan is not terminated, call that out as potentially notable.

### 5. Anomalies & escalations (end with concerns)

Surface anything a human watching the run would want to know:

- Any `errors[]` entries in recent manifests.
- Backoff / retry sequences in `orchestrator.log` or per-target logs.
- Escalations logged (look for escalation markers per plan §8).
- Gaps between expected and actual cycle times (cron misses, missed wake-ups).
- Anomalies the orchestrator flagged in its own reasoning.

If there are none, say so explicitly ("No anomalies flagged since <time>.").

### 6. Habitat conditions during the last run (optional, include if habitat is in plan §3 subagents)

Only if the plan uses habitat-remote. Pull via direct MCP tools, scoped to the last run's window:

- `sensor_stats(since=<last_run_started_at>, until=<last_run_completed_at or now>)` — report CO2 mean/stddev, `temp_stc` mean, humidity mean. Flag if any exceed the plan §6 thresholds (if defined).
- `pump_activity(since=..., until=..., failed_only=True)` — count of pump errors in the window; if non-zero, list them briefly (timestamp, action, error).

Keep it to ~3 lines. Skip the section entirely if habitat is not in scope or the calls return `{"error": "... unreachable"}` (note the service-down instead of failing the report).

### 7. Disk headroom

- `df -h` on the filesystem containing `orchestrator/<plan_id>/`.
- `du -sh orchestrator/<plan_id>/` for actual consumption so far.
- Compare against expected total from plan §11 if present; flag if remaining headroom is under the plan's override or under ~3× remaining expected volume.

## Tone

Neutral, observational, concise. This is a check-in, not a narrative. Don't speculate about causes of anomalies beyond what the logs say — point to evidence and let the user decide whether to intervene. Don't suggest remediation steps unless the user asks; another session is actively running and any change is their call, not yours.

## Saving the report to a file

Default output is in-chat only. If the user **explicitly** asks to save the report (e.g. "write the report to a file", "save this as markdown"):

- Write the full report as markdown to `orchestrator/<plan_id>/reports/status_<UTC-timestamp>.md` (create `reports/` if missing). Timestamp format: `YYYY-MM-DDTHHMMSSZ`.
- Use the exact same report structure and content defined above.
- If the user specifies a different path, use it verbatim after confirming it doesn't overwrite an existing file.
- **Do not also print the report to chat.** Return only: the output path, total size, and a one-line status ("Report written.") so the chat stays clean. If the user wants both, they'll ask.
- This is the only write this skill is permitted to perform, and only when explicitly requested. Writing under `reports/` does not touch raw data, state, logs, or manifests.

## What this skill does NOT do

- Does not modify the experiment.
- Does not communicate with the live orchestrator session.
- Does not make scientific judgments or propose plan changes.
- Does not query hardware, the Pi, or any external system.
- Does not write a report file unless the user explicitly asks; when it does, it writes only under `orchestrator/<plan_id>/reports/` (or a user-specified path) and nowhere else.
