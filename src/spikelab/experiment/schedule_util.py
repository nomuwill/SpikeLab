"""
schedule_util.py — locked read-modify-write for orchestrator/<plan_id>/schedule.json.

Why: schedule.json is updated from multiple sites during a run (start of every
run, end of every run, on termination). Before the 2026-04-15 wake-up race
incident, a bare tmp+os.replace write was the only discipline — atomic on disk
but NOT exclusive against concurrent RMW. Two processes could both read the
same snapshot, each apply their delta, and each write back; the second write
would silently overwrite the first's append to run_history.

This module takes an exclusive flock on orchestrator/<plan_id>/.schedule_lock
for the entire RMW duration, so only one process mutates schedule.json at a
time. Reads without modification also take the lock (briefly) to avoid tearing.

Usage:

    from schedule_util import schedule_locked, read_schedule, write_schedule, append_run

    # High-level: append one run_history entry under the lock.
    append_run("maturity-pilot_2026-04-14", {
        "run_number": 4, "started_at": "...", "completed_at": "...",
        "status": "ok", "step": "MC4", "notes": "..."
    })

    # Low-level: multi-field update under one lock.
    with schedule_locked("maturity-pilot_2026-04-14") as path:
        sched = json.load(open(path))
        sched["current_run_number"] = 5
        sched["last_run_completed_at"] = "..."
        write_schedule_unlocked(path, sched)

CLI: for shell callers who can't/won't import this module.

    python schedule_util.py append-run \\
        --plan-id maturity-pilot_2026-04-14 \\
        --entry '{"run_number":4,"status":"ok","step":"MC4"}'

    python schedule_util.py set \\
        --plan-id maturity-pilot_2026-04-14 \\
        --kv current_run_number=5 \\
        --kv last_run_completed_at=2026-04-15T12:00:00Z

Runs with the `automation` conda env or any Python 3 with stdlib only.
"""

import argparse
import contextlib
import fcntl
import json
import os
import sys
import tempfile
from pathlib import Path

ORCH_DIR = Path("/home/sharf-lab/Desktop/Research_automation/orchestrator")


def _plan_dir(plan_id: str) -> Path:
    d = ORCH_DIR / plan_id
    if not d.is_dir():
        raise FileNotFoundError(f"Plan directory does not exist: {d}")
    return d


def _schedule_path(plan_id: str) -> Path:
    return _plan_dir(plan_id) / "schedule.json"


def _lock_path(plan_id: str) -> Path:
    return _plan_dir(plan_id) / ".schedule_lock"


@contextlib.contextmanager
def schedule_locked(plan_id: str):
    """Hold an exclusive flock on the plan's .schedule_lock for RMW on schedule.json.

    Yields the absolute path to schedule.json. The lock is released when the
    context exits (normally or via exception).
    """
    lock_path = _lock_path(plan_id)
    # Create the lock file if it doesn't exist yet (first run of a plan).
    lock_path.touch(exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield _schedule_path(plan_id)
    finally:
        # flock released when fd closes, but be explicit.
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def read_schedule(plan_id: str) -> dict:
    """Read schedule.json under the lock. Returns an empty dict if the file
    doesn't exist yet (fresh plan)."""
    with schedule_locked(plan_id) as path:
        if not path.exists():
            return {}
        with open(path) as f:
            return json.load(f)


def write_schedule_unlocked(path: Path, data: dict) -> None:
    """Atomic tmp+os.replace write of `data` to `path`. Caller must already hold
    the schedule lock for the plan."""
    path = Path(path)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=".schedule_", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def append_run(plan_id: str, entry: dict) -> dict:
    """Append an entry to schedule.json run_history, return the full schedule dict.
    Takes the schedule lock for the whole RMW."""
    with schedule_locked(plan_id) as path:
        if path.exists():
            with open(path) as f:
                sched = json.load(f)
        else:
            sched = {
                "plan_id": plan_id,
                "current_run_number": 0,
                "last_run_started_at": None,
                "last_run_completed_at": None,
                "next_timepoint": None,
                "run_history": [],
            }
        sched.setdefault("run_history", []).append(entry)
        if "completed_at" in entry and entry.get("status") in ("ok", "partial", "failed"):
            sched["last_run_completed_at"] = entry["completed_at"]
        if "started_at" in entry:
            sched["last_run_started_at"] = entry["started_at"]
        if "run_number" in entry:
            sched["current_run_number"] = max(
                sched.get("current_run_number", 0) or 0, entry["run_number"]
            )
        write_schedule_unlocked(path, sched)
        return sched


def set_fields(plan_id: str, updates: dict) -> dict:
    """Merge `updates` into schedule.json under the lock. Top-level keys only."""
    with schedule_locked(plan_id) as path:
        if path.exists():
            with open(path) as f:
                sched = json.load(f)
        else:
            sched = {"plan_id": plan_id, "run_history": []}
        sched.update(updates)
        write_schedule_unlocked(path, sched)
        return sched


def _cli():
    ap = argparse.ArgumentParser(description="Locked RMW helper for schedule.json.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_append = sub.add_parser("append-run", help="Append a run_history entry.")
    p_append.add_argument("--plan-id", required=True)
    p_append.add_argument(
        "--entry", required=True, help="JSON dict with the run_history entry."
    )

    p_set = sub.add_parser("set", help="Set one or more top-level fields.")
    p_set.add_argument("--plan-id", required=True)
    p_set.add_argument(
        "--kv", action="append", required=True,
        help="KEY=VALUE, repeatable. VALUE is JSON (e.g. 5, \"a\", null).",
    )

    p_read = sub.add_parser("read", help="Print current schedule.json to stdout.")
    p_read.add_argument("--plan-id", required=True)

    args = ap.parse_args()

    if args.cmd == "append-run":
        entry = json.loads(args.entry)
        sched = append_run(args.plan_id, entry)
        print(json.dumps(sched, indent=2))
    elif args.cmd == "set":
        updates = {}
        for kv in args.kv:
            if "=" not in kv:
                raise SystemExit(f"--kv expects KEY=VALUE, got: {kv!r}")
            k, v = kv.split("=", 1)
            try:
                updates[k] = json.loads(v)
            except json.JSONDecodeError:
                # Bare word → treat as string.
                updates[k] = v
        sched = set_fields(args.plan_id, updates)
        print(json.dumps(sched, indent=2))
    elif args.cmd == "read":
        sched = read_schedule(args.plan_id)
        print(json.dumps(sched, indent=2))
    else:
        ap.print_help()
        sys.exit(2)


if __name__ == "__main__":
    _cli()
