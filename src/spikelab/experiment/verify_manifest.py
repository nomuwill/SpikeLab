#!/usr/bin/env python3
"""Structural verifier for ephys-written `manifest.json` files.

Runs after each ephys step (`scan` / `select` / `record`) and checks the
invariants the orchestrator relies on — field presence, step status, file
existence, non-zero file size. Numeric-threshold checks (e.g. "at least N
active electrodes") are intentionally out of scope: those belong in the
plan's §6 decision rules, not here.

Exit codes:
  0  verification passed
  2  verification failed (structural problem)
  1  usage / crash (argparse, I/O, etc. — distinguishable from a real failure)

On exit 2 an `error` event is appended to `orchestrator.log` via the same
plumbing as `log_event.py`, so failures are visible in the audit trail
without the caller having to do extra work.

Usage:

    conda run -n automation python \
        /home/sharf-lab/Desktop/Research_automation/orchestrator/verify_manifest.py \
        --plan-id scan-select-compare_2026-04-14 \
        --step scan \
        --manifest /.../sparse_7x/manifest.json \
        --rig maxone
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path("/home/sharf-lab/Desktop/Research_automation")

RIG_EXPECTED_SAMPLING_HZ = {
    "maxtwo": 10000.0,
    "maxone": 20000.0,
}


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _log_error(plan_id: str, step: str, manifest: str, reason: str) -> None:
    """Append a structured error event. Best-effort — never raises."""
    try:
        log_dir = REPO_ROOT / "orchestrator" / plan_id / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": _now_iso(),
            "plan_id": plan_id,
            "event": "error",
            "step": f"verify:{step}",
            "status": "failed",
            "manifest": manifest,
            "note": reason,
        }
        with open(log_dir / "orchestrator.log", "a") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError:
        pass


class VerificationFailure(Exception):
    """Raised when a structural check fails. Carries a one-line reason."""


def _require(cond: bool, reason: str) -> None:
    if not cond:
        raise VerificationFailure(reason)


def _load_manifest(path: str) -> dict:
    _require(os.path.exists(path), f"manifest not found: {path}")
    _require(os.path.getsize(path) > 0, f"manifest is empty: {path}")
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise VerificationFailure(f"manifest is not valid JSON: {exc}") from exc


def _check_errors_array(manifest: dict, step: str) -> None:
    """Fail if there's a current-step entry in errors[]. Historical entries
    from other steps are allowed (a re-run of a different step doesn't clear
    the sibling error). The step's own `status == ok` is the authoritative
    signal; this is a belt-and-suspenders cross-check.

    Per the ephys-skill manifest spec, top-level errors[] are short
    "<step>: <ErrorType>: <message>" strings, not dicts. Earlier code here
    assumed dicts; updated to handle both string and dict entries.
    """
    prefix = f"{step}:"
    for err in manifest.get("errors", []) or []:
        if isinstance(err, str):
            # String form: "<step>: <ErrorType>: <message>"
            if err.startswith(prefix):
                raise VerificationFailure(
                    f"errors[] contains an entry for step {step!r}: {err}"
                )
        elif isinstance(err, dict):
            if err.get("step") == step:
                raise VerificationFailure(
                    f"errors[] contains an entry for step {step!r}: "
                    f"{err.get('error_type', '?')} — {err.get('error_message', '?')}"
                )
        # Unknown entry type: ignore silently rather than crash.


def _check_file_on_disk(timepoint_dir: str, filename: str, recorded_bytes) -> None:
    _require(filename is not None, "output_file field is missing")
    path = os.path.join(timepoint_dir, filename)
    _require(os.path.exists(path), f"declared output file does not exist on disk: {path}")
    size = os.path.getsize(path)
    _require(size > 0, f"declared output file is zero bytes: {path}")
    if recorded_bytes is not None:
        # Allow small discrepancies (HDF5 can flush after manifest write);
        # require the on-disk size to be at least the recorded value × 0.5.
        # Flag only wildly-off cases (e.g. truncated file).
        _require(
            size >= int(recorded_bytes) * 0.5,
            f"on-disk size ({size}) is much smaller than manifest-recorded "
            f"output_bytes ({recorded_bytes}) for {path}",
        )


def verify_scan(manifest: dict, timepoint_dir: str) -> None:
    step = (manifest.get("steps") or {}).get("scan")
    _require(step is not None, "steps.scan is missing")
    _require(step.get("status") == "ok",
             f"steps.scan.status is {step.get('status')!r}, expected 'ok'")
    _require(isinstance(step.get("active_electrodes"), int),
             "steps.scan.active_electrodes is missing or not an int")
    _require(isinstance(step.get("electrodes_scanned"), int),
             "steps.scan.electrodes_scanned is missing or not an int")
    _check_file_on_disk(timepoint_dir, step.get("output_file"), step.get("output_bytes"))
    _check_errors_array(manifest, "scan")


def verify_select(manifest: dict, timepoint_dir: str) -> None:
    step = (manifest.get("steps") or {}).get("select")
    _require(step is not None, "steps.select is missing")
    _require(step.get("status") == "ok",
             f"steps.select.status is {step.get('status')!r}, expected 'ok'")
    _require(isinstance(step.get("electrodes_selected"), int) and step["electrodes_selected"] > 0,
             "steps.select.electrodes_selected is missing, non-int, or zero")
    _require(isinstance(step.get("total_regions"), int) and step["total_regions"] > 0,
             "steps.select.total_regions is missing, non-int, or zero")
    _check_file_on_disk(timepoint_dir, step.get("output_file"), step.get("output_bytes"))
    _check_errors_array(manifest, "select")


def verify_record(manifest: dict, timepoint_dir: str, rig: str | None) -> None:
    records = (manifest.get("steps") or {}).get("record")
    _require(isinstance(records, list) and len(records) > 0,
             "steps.record is missing or not a non-empty list")
    step = records[-1]
    _require(step.get("status") == "ok",
             f"steps.record[-1].status is {step.get('status')!r}, expected 'ok'")
    _require(isinstance(step.get("duration_actual_s"), (int, float)),
             "steps.record[-1].duration_actual_s is missing or not numeric")
    requested = step.get("duration_requested_s")
    actual = step.get("duration_actual_s")
    if isinstance(requested, (int, float)) and requested > 0:
        # Require actual within ±20% of requested — catches aborted recordings
        # without false-positiving on the normal ~1–2 s overshoot.
        _require(
            0.8 * requested <= actual <= 1.2 * requested,
            f"duration_actual_s ({actual}) is >20% off duration_requested_s ({requested})",
        )
    _check_file_on_disk(timepoint_dir, step.get("output_file"), step.get("output_bytes"))

    wells = step.get("wells") or []
    _require(len(wells) > 0, "steps.record[-1].wells is empty")
    for w in wells:
        _require(isinstance(w.get("sampling_hz"), (int, float)),
                 f"wells[{w.get('well_id', '?')}].sampling_hz is missing or not numeric")
        if rig:
            expected = RIG_EXPECTED_SAMPLING_HZ.get(rig.lower())
            if expected is not None:
                got = float(w["sampling_hz"])
                _require(
                    abs(got - expected) <= 1.0,
                    f"wells[{w.get('well_id')}].sampling_hz is {got} Hz; "
                    f"expected ~{expected} Hz for rig={rig!r}",
                )

    _check_errors_array(manifest, "record")


VERIFIERS = {
    "scan": lambda m, d, rig: verify_scan(m, d),
    "select": lambda m, d, rig: verify_select(m, d),
    "record": lambda m, d, rig: verify_record(m, d, rig),
}


def main() -> int:
    p = argparse.ArgumentParser(description="Structurally verify an ephys manifest.json")
    p.add_argument("--plan-id", required=True)
    p.add_argument("--step", required=True, choices=sorted(VERIFIERS.keys()))
    p.add_argument("--manifest", required=True, help="Path to manifest.json (absolute or repo-relative)")
    p.add_argument("--rig", default=None, choices=sorted(RIG_EXPECTED_SAMPLING_HZ.keys()),
                   help="Rig identity for sampling-rate cross-check (record step only). "
                        "Omit to skip the sampling-rate assertion.")
    args = p.parse_args()

    manifest_path = args.manifest
    if not os.path.isabs(manifest_path):
        manifest_path = str(REPO_ROOT / manifest_path)

    try:
        manifest = _load_manifest(manifest_path)
        timepoint_dir = manifest.get("timepoint_dir") or os.path.dirname(manifest_path)
        VERIFIERS[args.step](manifest, timepoint_dir, args.rig)
    except VerificationFailure as exc:
        reason = str(exc)
        sys.stderr.write(f"verify_manifest: FAILED ({args.step}): {reason}\n")
        _log_error(args.plan_id, args.step, manifest_path, reason)
        return 2

    print(f"verify_manifest: OK ({args.step})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
