# `manifest.json` — Portable Specification

A `manifest.json` file co-located with experimental recordings that captures everything needed to understand, reproduce, and analyse a run without opening the raw data files or grepping terminal logs. This document is **self-contained** — an implementer in a different codebase should be able to produce compatible manifests from this alone.

---

## 1. Purpose

Each experimental "timepoint" (a directory containing one or more recordings plus any ancillary files like configurations or scan results) has exactly one `manifest.json` at its root. The manifest is **the canonical summary** of what happened. Downstream code — analysis, orchestrators, human browsing — reads the manifest first and only opens raw files when it needs bulk data.

**Not goals:**

- The manifest is **not** a copy of data that lives elsewhere. If it's in the raw file already and trivially readable, don't duplicate it; reference it.
- The manifest is **not** a log file. No progress messages, no timing debug, no long free-form text. Structured fields only.
- The manifest is **not** version-controlled or cryptographically signed. It is a convenience summary, not a chain-of-custody record.

---

## 2. File format

- **Encoding:** UTF-8 JSON.
- **Pretty-printed** with 2-space indent for human diffing (`json.dump(..., indent=2)`).
- **Filename:** `manifest.json` at the root of the output directory. Never named differently.
- **One manifest per directory.** A directory either has a manifest or doesn't; never two.

---

## 3. Top-level shape

```json
{
  "manifest_version": 1,
  "timepoint_dir": "/absolute/path/to/output_dir",
  "created_at": "2026-04-13T03:15:00.123-07:00",
  "kind": "pharma",
  "wells_requested": [2],
  "pipeline": {
    "run_all_invoked": true,
    "cli_args": { ... },
    "config_snapshot": { ... }
  },
  "steps": {
    "scan":   { ... },
    "select": { ... },
    "record": [ { ... }, { ... } ]
  },
  "environment": {
    "host": "example-host",
    "mxw_version": "25.1.8.1",
    "maxlab_python": "/path/to/python3"
  },
  "errors": []
}
```

### Field-by-field

| Field | Type | Required | Notes |
|---|---|---|---|
| `manifest_version` | integer | yes | Current: `1`. Bump on breaking schema change. |
| `timepoint_dir` | absolute path string | yes | The **one** absolute path in the manifest. Everything else is relative. |
| `created_at` | ISO-8601 string | yes | Written once, on initial creation. Not updated by later writers. |
| `kind` | enum string | optional | One of: `maturity`, `baseline`, `pharma`, `stim`, `ad_hoc`. Domain-specific — extend as needed. Omitted if no writer supplied it. |
| `wells_requested` | list of integers | yes | Physical well IDs the run was launched against (0–5 in our setup). |
| `pipeline` | object | yes | See §5. |
| `steps` | object | yes | See §6. |
| `environment` | object | yes | See §7. |
| `errors` | list of strings | yes | Always present. Empty list on clean run. |

---

## 4. Paths inside the manifest

**Rule:** `timepoint_dir` is the only absolute path. All file references inside the manifest are **relative filenames** in that directory (e.g. `"recording.raw.h5"`, not `"/path/to/recording.raw.h5"`).

**Why:** the directory can be moved, copied, archived, or mounted under a different path. Relative filenames stay valid; absolute paths break.

---

## 5. `pipeline`

Records how the run was *launched* (not what happened — that's `steps`).

```json
"pipeline": {
  "run_all_invoked": true,
  "cli_args": {
    "mode": "sparse_7x",
    "wells": [2],
    "output_dir": "/abs/path",
    "kind": "pharma",
    "duration": 300.0,
    "name": "baseline_drug"
  },
  "config_snapshot": {
    "DETECTION_THRESHOLD": 5.0,
    "SCAN_SECONDS_PER_BLOCK": 30.0,
    "MIN_REGIONS": 40,
    "MAX_SELECTED_ELECTRODES": 1020
  }
}
```

- `run_all_invoked` — `true` if the run was launched via the top-level orchestrator script; `false` if individual steps were invoked standalone. Once set to `true`, never flipped back.
- `cli_args` — snapshot of the command-line arguments that produced the **initial** manifest. Freeform by design; the orchestrator (or a human) can interpret it. Not updated by later writers.
- `config_snapshot` — key config constants that affect the output but are not CLI-settable. Captured on initial creation so later behaviour changes don't falsify old manifests.

---

## 6. `steps`

One key per step. The keys for our pipeline are `scan`, `select`, `record`, but the structure generalises: any step that produces output becomes a key.

### 6.1 Single-invocation steps (`scan`, `select`)

An object. **Overwritten** on re-run (re-running a scan in the same directory replaces the prior `steps.scan`).

Common fields:

| Field | Type | Notes |
|---|---|---|
| `status` | `"ok"` \| `"failed"` | Required. |
| `started_at` | ISO-8601 string | Required. When this step started. |
| `duration_s` | number | Required on `ok`. Wall-clock seconds. |
| `output_file` | string | Relative filename produced by this step. |
| `output_bytes` | integer | `os.path.getsize(output_file)`. |

Step-specific fields are free — record whatever makes the step understandable (for us: `mode`, `blocks`, `electrodes_scanned`, `active_electrodes` for `scan`; `by`, `total_regions`, `electrodes_selected` for `select`).

### 6.2 Multi-invocation steps (`record`)

A **list**. Each invocation that produces a new recording appends one entry; no entry is ever overwritten by a later append.

```json
"record": [
  {
    "status": "ok",
    "started_at": "2026-04-13T03:15:05-07:00",
    "name": "baseline_drug",
    "output_file": "baseline_drug.raw.h5",
    "output_bytes": 722837504,
    "duration_requested_s": 600.0,
    "duration_actual_s": 602.1,
    "wells": [
      {
        "well_id": 2,
        "data_group": "data0000",
        "start_time_ms": 1776074916278,
        "stop_time_ms": 1776075516278,
        "sampling_hz": 10000.0,
        "spike_count": 4127
      }
    ],
    "events": [
      {
        "type": "habitat_operation",
        "timestamp_ms": 1776075216278,
        "frame_offset": 3000000,
        "habitat_task_id": "abc-123",
        "summary": "drug_addition TTX 1.0uM well 2"
      }
    ]
  },
  { "...": "second recording, e.g. wash" }
]
```

Field notes:

- `wells` — one object per recorded well. Data extracted from the raw file (not re-reported from stdout):
  - `well_id` — physical well identifier (NOT the index in the raw file's data structure — see §10 for why).
  - `data_group` — the key inside the raw file where this well's data lives (if applicable).
  - `start_time_ms` / `stop_time_ms` — Unix epoch milliseconds. The raw file's own timestamps.
  - `sampling_hz` — recorded sample rate. Verify against the raw file; don't hard-code.
  - `spike_count` — number of detected events, if the file has an online spike table.
- `events` — in-file perturbations. Array of cross-reference records:
  - `type` — short identifier (`habitat_operation`, `stim_pulse`, …).
  - `timestamp_ms` — Unix epoch milliseconds of the event.
  - `frame_offset` — `(timestamp_ms - start_time_ms) * sampling_hz / 1000`, pre-computed for convenience.
  - `habitat_task_id` / `stim_id` / …  — cross-reference key into another system's log.
  - `summary` — short human-readable string. **Not** the source of truth for event details; that lives in the cross-referenced log.
  - Empty `[]` is valid and expected for recordings without interventions.

**Appending events after the fact.** The recording script writes the entry with `events: []`. External systems (an orchestrator, a fluidics controller) append events later via a dedicated helper that loads the manifest, appends to `steps.record[-1].events`, and writes atomically. The helper MUST auto-compute `frame_offset` from `timestamp_ms` and the recording's first well's `start_time_ms` / `sampling_hz` if not supplied by the caller. For multi-well recordings, consumers needing per-well alignment should compute from per-well `start_time_ms` values themselves — the manifest stores one canonical offset for convenience, not N.

---

## 7. `environment`

Captured once on initial creation; later writers may enrich fields that were missing (e.g. software version extracted from the first recording produced). Never overwrites non-empty fields.

```json
"environment": {
  "host": "recording-workstation-01",
  "mxw_version": "25.1.8.1",
  "maxlab_python": "/home/.../python3"
}
```

Fields are domain-specific — capture whatever's needed to reproduce the environment later. Include software versions, interpreter path, hardware identifier. Exclude anything that changes between invocations (PID, timestamps, etc. — those belong at the top level or in `steps`).

---

## 8. Writer behaviour

The manifest is written by multiple scripts, potentially over time. Writer rules:

### 8.1 Always merge, never clobber

1. Before writing, open the existing `manifest.json` if present and deserialise it.
2. Apply the writer's intended change (set a step, append a recording, add a missing field).
3. Write the modified object back.

**Never** unconditionally overwrite the whole file. A run with only a new recording must not erase the prior `scan` / `select` entries, and vice versa.

### 8.2 Atomic writes

Writers MUST use write-then-rename:

```
write to <output_dir>/.manifest.XXXXXX.tmp
fsync
os.replace(tmp, <output_dir>/manifest.json)
```

Readers must never see a half-written manifest. A failed write leaves the existing manifest untouched.

### 8.3 First-writer-wins for identity fields

Fields that describe the **run as a whole**, not the current step, are set only if absent:

- `created_at` — set on initial creation only.
- `kind` — set when first writer provides it. Later writers preserve the existing value even if they're called with a different `--kind`.
- `pipeline.cli_args`, `pipeline.config_snapshot` — set on initial creation only.

Rationale: the first writer sees the pipeline as launched; later writers may be partial re-runs or appends that shouldn't rewrite the historical record.

### 8.4 Monotonic fields

`pipeline.run_all_invoked`: once `true`, never flipped back to `false`. (A partial re-run of one step, invoked standalone, doesn't retroactively claim the run wasn't orchestrated.)

### 8.5 Step-specific write rules

- **`scan`, `select`** (single-invocation steps): `set_step(manifest, name, payload)` — overwrite. A re-run is authoritative.
- **`record`** (multi-invocation): `append_recording(manifest, entry)` — append to list. Re-runs accumulate.

### 8.6 Error capture

On failure, write a `steps.<name>` entry (or append a failed record to `steps.record`) with `status: "failed"`, and append a one-line summary to the top-level `errors` array. Don't skip the manifest write just because the step failed — a manifest without the failing step is indistinguishable from "never ran."

**Required fields in a failed step entry:**

| Field | Type | Notes |
|---|---|---|
| `status` | `"failed"` | Required. |
| `error_type` | string | The exception class name (`FileNotFoundError`, `TimeoutError`, …). |
| `error_message` | string | `str(exception)` — the message only, not a traceback. |

**Recommended context fields** (best-effort — include whichever are known at failure time):

- `started_at` — when the step began, so duration can be bounded.
- `duration_s` — wall time up to the failure.
- Whatever step-specific context has already been computed (e.g. `mode` for scan, `by` for select, `name` / `duration_requested_s` for record). Missing fields can be `null` or omitted.

**Top-level `errors` format:** each entry is a short string `"<step>: <ErrorType>: <message>"`. This is for humans skimming manifests; machine readers should use the per-step `error_type` / `error_message` fields.

**Best-effort rule:** if the manifest write itself fails during error capture (disk full, permission denied), DO NOT mask the original exception — swallow the manifest failure and re-raise the underlying step exception. The caller needs the real cause.

**Example failed step:**

```json
"steps": {
  "select": {
    "status": "failed",
    "error_type": "FileNotFoundError",
    "error_message": "scan results not found at /path/scan_results.npz. Run 01 first.",
    "started_at": "2026-04-13T04:31:51.147-07:00",
    "duration_s": 0.01,
    "by": "spike_rate"
  }
},
"errors": [
  "select: FileNotFoundError: scan results not found at /path/scan_results.npz. Run 01 first."
]
```

A subsequent successful re-run of the step **overwrites** the failed entry (for `scan` / `select`) or appends a new entry (for `record`) — failed record attempts stay in history, they're not garbage-collected.

---

## 9. Reader expectations

Readers must tolerate:

- Missing optional fields (`kind`, `environment.mxw_version`, etc.).
- Unknown keys (future versions may add fields).
- An `errors` array that's non-empty — inspect and surface to the user.

Readers must reject:

- `manifest_version` newer than the reader understands, unless the reader has an explicit compatibility policy.

---

## 10. Cross-references to other systems

The manifest is a **join table**, not a copy. When events or outputs involve another system (e.g. a fluidics controller, a stimulator), the manifest records a short cross-reference:

- A stable identifier from the other system (`habitat_task_id`, `stim_session_id`).
- A timestamp in **Unix epoch milliseconds**, in the same time base as the raw file's own timestamps.
- A one-line human summary — for eyeballing, not for parsing.

Full details stay in the other system's log. This keeps the manifest small and avoids two places going out of sync.

**Time-base requirement:** cross-referenced systems MUST agree on wall-clock time at the precision needed to align events. If the precision target is ~100 ms (typical for ephys + fluidics), all participating hosts MUST be NTP-synced. If tighter alignment is needed, use a hardware trigger with a known offset rather than NTP.

---

## 11. Reference implementation (Python)

A minimal, portable implementation:

```python
import json, os, socket, tempfile
from datetime import datetime, timezone

MANIFEST_FILENAME = "manifest.json"
MANIFEST_VERSION = 1
KIND_CHOICES = ["maturity", "baseline", "pharma", "stim", "ad_hoc"]

def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")

def manifest_path(output_dir):
    return os.path.join(output_dir, MANIFEST_FILENAME)

def load_or_init(output_dir, *, kind=None, wells_requested=None,
                 cli_args=None, config_snapshot=None, run_all_invoked=False):
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
        },
        "errors": [],
    }
    if kind:
        m["kind"] = kind
    return m

def write_atomic(manifest, output_dir):
    path = manifest_path(output_dir)
    fd, tmp = tempfile.mkstemp(prefix=".manifest.", suffix=".tmp", dir=output_dir)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(manifest, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise

def set_step(manifest, step_name, step_data):
    manifest["steps"][step_name] = step_data

def append_recording(manifest, record_entry):
    manifest["steps"].setdefault("record", []).append(record_entry)

def append_event_to_last_recording(output_dir, event):
    """For external callers (orchestrator) to attach events to the most
    recent recording. Auto-computes frame_offset if absent.
    Raises RuntimeError if there are no recordings yet."""
    path = manifest_path(output_dir)
    with open(path) as f:
        m = json.load(f)
    records = m.get("steps", {}).get("record", [])
    if not records:
        raise RuntimeError(f"No recordings in {path}")
    rec = records[-1]
    ev = dict(event)
    if "frame_offset" not in ev and rec.get("wells"):
        w0 = rec["wells"][0]
        start_ms = w0.get("start_time_ms")
        sr = w0.get("sampling_hz")
        ts = ev.get("timestamp_ms")
        if start_ms is not None and sr and ts is not None:
            ev["frame_offset"] = int((ts - start_ms) * sr / 1000)
    rec.setdefault("events", []).append(ev)
    write_atomic(m, output_dir)

def record_step_failure(output_dir, step_name, exception, *,
                        kind=None, wells_requested=None, extra=None):
    """Best-effort failed-step writer. Call from a top-level except
    clause before re-raising. Swallows its own errors so it never masks
    the underlying exception."""
    try:
        os.makedirs(output_dir, exist_ok=True)
        m = load_or_init(output_dir, kind=kind, wells_requested=wells_requested)
        entry = {
            "status": "failed",
            "error_type": type(exception).__name__,
            "error_message": str(exception),
        }
        if extra:
            entry.update(extra)
        if step_name == "record":
            append_recording(m, entry)
        else:
            set_step(m, step_name, entry)
        m["errors"].append(f"{step_name}: {type(exception).__name__}: {exception}")
        write_atomic(m, output_dir)
    except Exception:
        pass  # never mask the real exception
```

All six functions are short and translate directly to any language with a JSON library and atomic file operations.

---

## 12. Versioning

`manifest_version: 1` today. A new version is issued when an existing field's type or semantics change (breaking). Adding a new optional field is NOT a version bump — readers must tolerate unknown keys (§9).
