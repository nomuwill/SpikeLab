"""
Manifest file management for the ephys pipeline.

Each timepoint output directory contains one manifest.json summarising
what was run: scan parameters, electrode selection, every recording,
and per-well timing pulled from the HDF5. Scripts merge into an existing
manifest when present; only the first writer creates it.
"""

import json
import os
import socket
import tempfile
from datetime import datetime, timezone

MANIFEST_FILENAME = "manifest.json"
MANIFEST_VERSION = 1

KIND_CHOICES = ["maturity", "baseline", "pharma", "stim", "ad_hoc"]


def now_iso():
    """ISO-8601 timestamp with local timezone offset, ms precision."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def manifest_path(output_dir):
    return os.path.join(output_dir, MANIFEST_FILENAME)


def load_or_init(output_dir, kind=None, wells_requested=None,
                 cli_args=None, config_snapshot=None, run_all_invoked=False):
    """Load an existing manifest, or create a fresh one.

    If kind is provided and the manifest has no kind yet, it is set now.
    cli_args / config_snapshot / run_all_invoked are only recorded on
    initial creation (preserve the first writer's record of what launched
    the pipeline).
    """
    path = manifest_path(output_dir)
    if os.path.exists(path):
        with open(path) as f:
            m = json.load(f)
        if kind and "kind" not in m:
            m["kind"] = kind
        if run_all_invoked and not m["pipeline"].get("run_all_invoked"):
            m["pipeline"]["run_all_invoked"] = True
        return m

    m = {
        "manifest_version": MANIFEST_VERSION,
        "timepoint_dir": os.path.abspath(output_dir),
        "created_at": now_iso(),
        "wells_requested": list(wells_requested) if wells_requested is not None else [],
        "pipeline": {
            "run_all_invoked": run_all_invoked,
            "cli_args": cli_args or {},
            "config_snapshot": config_snapshot or {},
        },
        "steps": {},
        "environment": {
            "host": socket.gethostname(),
            "maxlab_python": "/home/sharf-lab/MaxLab/python/bin/python3",
        },
        "errors": [],
    }
    if kind:
        m["kind"] = kind
    return m


def write_atomic(manifest, output_dir):
    """Write manifest.json via tmp + rename. Never leaves a half-written file."""
    path = manifest_path(output_dir)
    fd, tmp = tempfile.mkstemp(prefix=".manifest.", suffix=".tmp", dir=output_dir)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(manifest, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def set_step(manifest, step_name, step_data):
    """Overwrite steps.<step_name>. For 'scan' and 'select'."""
    manifest["steps"][step_name] = step_data


def append_recording(manifest, record_entry):
    """Append to steps.record (creating the list if absent)."""
    manifest["steps"].setdefault("record", []).append(record_entry)


def append_event_to_last_recording(output_dir, event):
    """Append an event cross-reference to the most recent recording entry.

    For the orchestrator to call after triggering a habitat_operation, stim,
    etc. during a live recording. Loads the manifest, appends to
    steps.record[-1].events, writes atomically.

    event: dict with at minimum `type` and `timestamp_ms`. If `frame_offset`
    is absent it is computed from the recording's first well's start_time_ms
    and sampling_hz. For plate-wide events on multi-well recordings the
    frame_offset is identical across wells within sub-sample tolerance; for
    per-well analysis, compute offsets from the per-well start_time_ms in
    `wells[]` instead.

    Raises RuntimeError if no recordings exist yet.
    """
    path = manifest_path(output_dir)
    with open(path) as f:
        m = json.load(f)

    records = m.get("steps", {}).get("record", [])
    if not records:
        raise RuntimeError(
            f"No recordings in {path} — cannot attach event {event.get('type')!r}."
        )

    rec = records[-1]
    ev = dict(event)  # don't mutate caller's dict

    if "frame_offset" not in ev and rec.get("wells"):
        w0 = rec["wells"][0]
        start_ms = w0.get("start_time_ms")
        sr = w0.get("sampling_hz")
        ts = ev.get("timestamp_ms")
        if start_ms is not None and sr and ts is not None:
            ev["frame_offset"] = int((ts - start_ms) * sr / 1000)

    rec.setdefault("events", []).append(ev)
    write_atomic(m, output_dir)


def record_step_failure(output_dir, step_name, exception, *, kind=None,
                        wells_requested=None, extra=None):
    """Best-effort: record a failed step + exception into the manifest.

    Called from a script's top-level except clause before re-raising.
    Uses load_or_init so it works even if the manifest doesn't exist yet
    (e.g. scan failed before any successful write).
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        m = load_or_init(output_dir, kind=kind, wells_requested=wells_requested)
        step_entry = {
            "status": "failed",
            "error_type": type(exception).__name__,
            "error_message": str(exception),
        }
        if extra:
            step_entry.update(extra)
        if step_name == "record":
            append_recording(m, step_entry)
        else:
            set_step(m, step_name, step_entry)
        m["errors"].append(f"{step_name}: {type(exception).__name__}: {exception}")
        write_atomic(m, output_dir)
    except Exception:
        # Last-resort: do not mask the original exception with a manifest
        # write failure. The caller will re-raise the real one.
        pass


def extract_wells_from_h5(h5_path):
    """Read per-well summary data from a MaxWell .raw.h5 for the manifest."""
    import h5py
    wells = []
    with h5py.File(h5_path, "r") as h5:
        for k in sorted(h5["data_store"].keys()):
            ds = h5[f"data_store/{k}"]
            wells.append({
                "well_id": int(ds["well_id"][0]),
                "data_group": k,
                "start_time_ms": int(ds["start_time"][0]),
                "stop_time_ms": int(ds["stop_time"][0]),
                "sampling_hz": float(ds["settings/sampling"][0]),
                "spike_count": int(len(ds["spikes"])),
            })
    return wells


def read_mxw_version(h5_path):
    """Read mxw_version string from a .raw.h5 (used to enrich environment)."""
    import h5py
    with h5py.File(h5_path, "r") as h5:
        if "mxw_version" in h5:
            v = h5["mxw_version"][0]
            if isinstance(v, bytes):
                return v.decode("ascii", errors="replace")
            return str(v)
    return None
