#!/usr/bin/env python3
"""Structured event logger for orchestrator runs.

One line per event, appended to `orchestrator/<plan_id>/logs/orchestrator.log`.
Each line is a single JSON object so downstream tooling (and the orchestrator
itself on session rehydrate) can parse it without regex.

Usage from the orchestrator's shell steps:

    conda run -n automation python /home/sharf-lab/Desktop/Research_automation/orchestrator/log_event.py \
        --plan-id scan-select-compare_2026-04-14 \
        --event step_end \
        --step "scan:sparse_7x" \
        --status ok \
        --duration-s 317.8 \
        --manifest orchestrator/scan-select-compare_2026-04-14/recordings/2026-04-14/sparse_7x/manifest.json \
        --extract steps.scan.active_electrodes \
        --note "first of three scans"

For a step_start event, omit --duration-s / --manifest / --extract.

Event line shape:

    {"ts": "2026-04-14T17:03:22-07:00", "plan_id": "...", "event": "step_end",
     "step": "scan:sparse_7x", "status": "ok", "duration_s": 317.8,
     "manifest": ".../manifest.json", "extracted": {"steps.scan.active_electrodes": 1449},
     "note": "..."}

The helper deliberately does NOT mutate state files — it only appends to the log.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path("/home/sharf-lab/Desktop/Research_automation")


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _resolve_log_path(plan_id: str) -> Path:
    log_dir = REPO_ROOT / "orchestrator" / plan_id / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "orchestrator.log"


def _walk(obj, dotted: str):
    """Walk dotted path `a.b.0.c` into nested dicts/lists. Return None if missing."""
    cur = obj
    for part in dotted.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _extract(manifest_path: str, dotted_paths: list[str]) -> dict:
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {"_manifest_read_error": str(exc)}
    return {p: _walk(manifest, p) for p in dotted_paths}


def main() -> int:
    p = argparse.ArgumentParser(description="Append a structured event to orchestrator.log")
    p.add_argument("--plan-id", required=True)
    p.add_argument("--event", required=True,
                   choices=["run_start", "run_end", "step_start", "step_end",
                            "note", "error", "escalation"])
    p.add_argument("--step", default=None,
                   help="Short step descriptor, e.g. 'scan:sparse_7x' or 'select+record:full/top1020'")
    p.add_argument("--status", default=None, choices=[None, "ok", "failed", "partial", "skipped"])
    p.add_argument("--duration-s", type=float, default=None)
    p.add_argument("--manifest", default=None,
                   help="Path to the manifest.json produced by the step (absolute or repo-relative)")
    p.add_argument("--extract", action="append", default=[],
                   help="Dotted path into the manifest to capture (repeatable). "
                        "e.g. --extract steps.scan.active_electrodes "
                        "--extract steps.record.0.output_bytes")
    p.add_argument("--note", default=None, help="Free-text note")
    p.add_argument("--run-number", type=int, default=None)
    args = p.parse_args()

    entry: dict = {
        "ts": _now_iso(),
        "plan_id": args.plan_id,
        "event": args.event,
    }
    if args.run_number is not None:
        entry["run_number"] = args.run_number
    if args.step:
        entry["step"] = args.step
    if args.status:
        entry["status"] = args.status
    if args.duration_s is not None:
        entry["duration_s"] = args.duration_s
    if args.manifest:
        mpath = args.manifest
        if not os.path.isabs(mpath):
            mpath = str(REPO_ROOT / mpath)
        entry["manifest"] = mpath
        if args.extract:
            entry["extracted"] = _extract(mpath, args.extract)
    if args.note:
        entry["note"] = args.note

    log_path = _resolve_log_path(args.plan_id)
    with open(log_path, "a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    print(log_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
