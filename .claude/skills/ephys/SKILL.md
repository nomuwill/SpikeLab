---
name: ephys
description: Run MaxWell MaxTwo MEA recordings on the local machine — activity scan, electrode selection, and recording. Use when the user wants to scan activity, build an electrode configuration, or record neural activity from one or more wells on the MaxTwo.
---

# Ephys (MaxWell MaxTwo)

You control the **MaxWell MaxTwo** multi-electrode array on the local machine. Recordings flow through a three-step pipeline — scan → select → record — implemented as standalone Python scripts. You invoke them as subprocesses; you do not call the `maxlab` SDK directly.

---

## Critical environment facts

- **MaxLab Python (required):** `/home/sharf-lab/MaxLab/python/bin/python3` — this is the **only** interpreter with `maxlab` installed. System Python and the `automation` conda env will fail with `ModuleNotFoundError: No module named 'maxlab'`.
- **Scripts directory:** `/home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/`
- **Output directory:** `ephys_experiment_scripts/experiment_output/` (resolved relative to the scripts, not the CWD — safe to invoke from anywhere)
- **mxwserver** must be running before any script executes. It listens on TCP 7204 (raw), 7206 (filtered/spikes), 7215 (control).
- **Sampling rate is hardware-dependent — verify from the HDF5, don't hardcode.**
  - **MaxTwo:** 10 kHz per well.
  - **MaxOne:** 20 kHz (single-well chip — `--wells 0`).
  - Always confirm by reading `sampling_rate` out of the recording (`manifest.json → steps.record[*].sampling_rate` when available, or directly from the HDF5) before briefing downstream subagents (spikelab, analysis scripts). Don't pass a sample rate into a subagent prompt from memory; read it from disk.
- The MaxTwo has 6 wells (indexed 0–5), 26,400 electrodes/well, 1,024 simultaneous readout channels.
- The MaxOne has 1 well (addressed as `--wells 0`), 26,400 electrodes, 1,024 simultaneous readout channels.

---

## Session-start readiness check

Before running any pipeline step, verify:

1. **mxwserver listening** — `ss -ltn | grep :7215` should show a LISTEN entry (or `nc -z localhost 7215`). If not, the server is down and no script will work.
2. **MaxLab Python exists** — `test -x /home/sharf-lab/MaxLab/python/bin/python3`
3. **Scripts present** — `test -d /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts`
4. **Disk space** — recordings are ~24 MB/sec across 6 wells (~1.4 GB/min). Before long recordings, `df -h /home/sharf-lab/Desktop` and compare against duration × wells. A 5-min 6-well recording is ~7 GB; a 1-hour 6-well recording is ~85 GB.

Report any failures and stop. **Default:** do not start or restart `mxwserver` yourself — ask the user first. `mxwserver` runs as a user-launched process (`/home/sharf-lab/MaxLab/bin/mxwserver.sh`), typically in an interactive terminal where the user watches the log. Restarting it drops any in-flight recording and interrupts the user's session.

### Restart recipe (only with explicit user permission)

When the user confirms the server is unresponsive and explicitly asks for a restart:

```bash
# 1. Stop any running mxwserver (script wrapper + binary)
pkill -f /home/sharf-lab/MaxLab/bin/mxwserver

# 2. Start it in the background, detached from this shell
nohup /home/sharf-lab/MaxLab/bin/mxwserver.sh \
      >/tmp/mxwserver.out 2>&1 &

# 3. Wait for ports to come up (up to ~10 s), then verify
for i in $(seq 1 10); do
  sleep 1
  ss -ltn | grep -qE ':(7204|7206|7215) ' && break
done
ss -ltn | grep -E ':(7204|7206|7215) '
```

All three ports (7204 raw, 7206 filtered/spikes, 7215 control) must be LISTEN before proceeding. If they don't come up within ~10 s, tail `/tmp/mxwserver.out` and report back.

---

## Wells policy

The orchestrator uses this skill in two distinct modes:

- **Maturity / activity cycles:** always use all 6 wells. Omit `--wells` (defaults to `[0,1,2,3,4,5]`).
- **Fluidics / pharmacology / stimulation experiments:** use a single well or subset. Pass `--wells 2` or `--wells 0,2,4`.

`--wells` is accepted on every script (`01`, `02`, `03`, `run_all`) as a comma-separated list of integers in 0–5. CLI value overrides `config.py`. Step 2 also reads the wells list back from `scan_results.npz` if `--wells` is not passed, so a scan → select chain stays consistent without repeating the argument.

---

## Operations

All commands use the MaxLab Python and may be invoked from any working directory.

### 1. Activity scan — `01_activity_scan.py`

Maps per-electrode spike rate + mean amplitude across the array. Scans all wells in parallel (HDF5 supports multi-well). Output: `experiment_output/scan_results.npz`.

```bash
/home/sharf-lab/MaxLab/python/bin/python3 \
  /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/01_activity_scan.py \
  [--mode full|sparse_7x|checkerboard] [--scan-seconds 30] [--wells 0,1,2,3,4,5] [--output-dir PATH]
```

- `--mode sparse_7x` (default): every other row/col (6,600 electrodes), ~7 blocks, ~6–7 min.
- `--mode checkerboard`: (row+col) even (13,200 electrodes), ~13 blocks, ~11–13 min.
- `--mode full`: all 26,400 electrodes, ~26 blocks, ~22–25 min.
- `--scan-seconds`: recording seconds per block (default 30, from `config.py`). Each block also incurs ~20 s for download + offset.
- Temporary per-block `.raw.h5` files are created and deleted automatically.

### 2. Electrode selection — `02_select_electrodes.py`

Ranks electrodes from the scan, places circular hotspot regions, routes them through the MaxWell SDK, and saves a `.cfg`. Output: `experiment_output/selected_electrodes.cfg`. Takes ~30 s.

```bash
/home/sharf-lab/MaxLab/python/bin/python3 \
  /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/02_select_electrodes.py \
  [--by spike_rate|amplitude] [--regions 40] [--wells 0,1,2,3,4,5] \
  [--output-dir PATH] [--scan-results PATH]
```

- `--by`: `spike_rate` (default) or `amplitude`.
- `--regions N`: minimum hotspot regions (default 40). Per-region budget = 1020 / N.
- Overlapping regions free up budget for additional regions in sparser areas (phase-2 placement).
- Requires a `scan_results.npz` from step 1. By default reads `{output-dir}/scan_results.npz`; pass `--scan-results PATH` to read from an arbitrary location without copying the file into the output dir.

### 3. Record — `03_record.py`

Loads `selected_electrodes.cfg` and records. Output: `experiment_output/<name>.raw.h5` (auto-increments on collision).

```bash
/home/sharf-lab/MaxLab/python/bin/python3 \
  /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/03_record.py \
  [--duration 300] [--name recording] [--wells 0,1,2,3,4,5] [--output-dir PATH]
```

- `--duration`: seconds (default 300).
- `--name`: filename prefix (default `recording`).
- Recording is real-time: 5 min wall clock = 5 min data.
- **The script blocks for the full duration.** For experiments that need a concurrent perturbation mid-recording (e.g. a fluidics dispense 5 min into a 10-min recording), the orchestrator launches `03_record.py` in the background (`nohup … &`), drives the perturbation from the main process, then `wait`s on the PID. The concrete pattern — including bracketing the dispense with UTC marks, reading the precise pump timestamp back from `pump_activity`, and appending the event to the manifest via `manifest.append_event_to_last_recording` — lives in the orchestrator skill's "Concurrent perturbations during an active recording" section.

### Full pipeline — `run_all.py`

Scan → select → record in sequence. Aborts on any step's failure. Forwards `--wells`, `--output-dir`, `--kind`, and `--stim-electrodes` to the relevant steps.

```bash
/home/sharf-lab/MaxLab/python/bin/python3 \
  /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/run_all.py \
  [--mode full|sparse_7x|checkerboard] [--scan-seconds 30] [--by spike_rate] [--regions 40] [--duration 300] [--name recording] [--wells 0,1,2,3,4,5] [--output-dir PATH] [--stim-electrodes 5280,12464] [--skip-record]
```

- `--stim-electrodes` is forwarded to step 2 only.
- `--skip-record` (or `--duration 0`) skips step 3 entirely. Use this when preparing a stim experiment whose recording is driven by a custom script via `experiment_lib`.

---

## Stimulation experiments

Stim experiments are **bespoke Python scripts** rather than CLI invocations. The skill writes a script that imports building blocks from `experiment_lib.py` and composes them with plain Python control flow (loops, sleeps, conditionals). The recording lifecycle is wrapped in a context manager; stim pulses are fired with one-line calls that also append events to `manifest.json`.

Stimulation is **single-well per recording** in this minimal implementation — `--wells` must be exactly one well when `--stim-electrodes` is passed. To stimulate multiple wells, run sequential single-well experiments (see "Multi-well stim experiments" below).

### Workflow

```
1. Run scan + select with --stim-electrodes to wire stim buffers
2. Run a custom experiment script that:
   a. Initialises hardware
   b. Loads the .cfg
   c. Reads stim_routing.json
   d. Opens a recording (context manager)
   e. Sleeps, fires pulses, sleeps in any pattern
   f. Closes the recording (manifest is written at exit)
```

### Step 1 — wire stim electrodes via `02_select_electrodes.py --stim-electrodes`

The same step that produces `selected_electrodes.cfg` also wires the requested stim electrodes into the routing. Each electrode is validated to map to a unique stim buffer (raises if not). The routing is persisted to `stim_routing.json` in the output directory.

```bash
/home/sharf-lab/MaxLab/python/bin/python3 \
  /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/02_select_electrodes.py \
  --kind stim --wells 2 --output-dir $OUT \
  --stim-electrodes 5280,12464
```

`--stim-electrodes` accepts a comma-separated electrode list (max 32, all must be in 0–26399). Each electrode is wired during routing via `select_stimulation_electrodes()`; the assigned stim unit IDs are queried and validated for uniqueness. The mapping ends up in:

- `stim_routing.json` — persistent file used by experiment scripts:
  ```json
  {
    "wells": [2],
    "stim_electrodes": [5280, 12464],
    "routing": {"5280": 5, "12464": 12}
  }
  ```
- `manifest.json` `steps.select.stim_electrodes` and `steps.select.stim_routing`.

### Step 2 — write and run an experiment script

The skill writes a script that imports `experiment_lib` and composes a stim flow. `experiment_lib.py` provides:

| Helper | Purpose |
|---|---|
| `prepare_hardware(wells, threshold=5.0)` | activate + initialise + set spike threshold |
| `load_and_route(cfg_path, wells)` | load `.cfg` + download + offset |
| `connect_stim_electrodes(output_dir)` | read `stim_routing.json`, power up the stim units; returns `{electrode: unit_id}` |
| `recording_session(output_dir, name, wells, kind)` | context manager — opens HDF5, yields a handle, stops + writes manifest on exit |
| `fire_pulse(routing, electrodes, amplitude_mv, phase_us, polarity, label, recording)` | fires one biphasic pulse on the listed electrodes (single DAC, identical waveform); when `recording=` is passed, the event is appended to the manifest |
| `fire_pulse_train(routing, electrodes, amplitude_mv, phase_us, frequency_hz, n_pulses, polarity, label, recording)` | hardware-timed train of identical pulses in a single `maxlab.Sequence` |

The recording context manager handles cleanup on exceptions and writes the manifest entry (including the per-well HDF5 timing and the full event list) when the `with` block exits.

#### Skeleton

```python
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from experiment_lib import (
    prepare_hardware, load_and_route, connect_stim_electrodes,
    recording_session, fire_pulse, fire_pulse_train,
)

OUTPUT_DIR = "/path/to/output"
WELLS = [2]
STIM_ELECTRODE = 5280

prepare_hardware(WELLS)
load_and_route(f"{OUTPUT_DIR}/selected_electrodes.cfg", WELLS)
routing = connect_stim_electrodes(OUTPUT_DIR)

with recording_session(OUTPUT_DIR, name="experiment", wells=WELLS,
                       kind="stim") as rec:
    time.sleep(60)                          # baseline
    for amp in [50, 100, 150, 200]:
        for _ in range(10):
            fire_pulse(routing, electrodes=STIM_ELECTRODE,
                       amplitude_mv=amp, phase_us=100,
                       label=f"{amp}mV", recording=rec)
            time.sleep(1)
        time.sleep(10)
```

Run with the MaxLab Python:

```bash
/home/sharf-lab/MaxLab/python/bin/python3 my_experiment.py
```

#### Pulse mechanics

- **Biphasic, single DAC.** All targeted electrodes share DAC 0 — they fire the same waveform at the same instant. To fire different amplitudes simultaneously, multiple DACs would be needed (not in scope for this minimal implementation).
- **Polarity:** `"positive_first"` (default — positive then negative phase) or `"negative_first"`.
- **Other electrodes:** stim units for non-target electrodes are powered down for the duration of the pulse, so only the target(s) actually receive voltage.
- **Sampling rate:** automatically detected (10 kHz on MaxTwo, 20 kHz on MaxOne). `phase_us` is converted to samples accordingly.
- **DAC LSB:** queried from the device on first pulse; cached for subsequent pulses.
- **Frequency limit:** for `fire_pulse_train`, the period must be at least one pulse footprint (`2 * phase_samples + 2`). At 10 kHz with 100 µs phase, max frequency ≈ 2 kHz; at 20 kHz, ≈ 5 kHz. The function raises if the requested frequency is too high for the chosen phase.
- **Varying parameters across pulses:** call `fire_pulse` repeatedly in a Python loop. Python-level timing (~ms jitter) is fine for tens-of-milliseconds gaps; for sub-ms-precision constant-rate trains, use `fire_pulse_train`.

#### Event log in manifest

Each `fire_pulse` / `fire_pulse_train` call with `recording=rec` appends an entry to the recording's event list. When the context manager exits, these events are written into the manifest:

```json
{
  "type": "stim",
  "timestamp_ms": 1776075123456,
  "electrodes": [5280],
  "amplitude_mv": 200,
  "phase_us": 100,
  "polarity": "positive_first",
  "label": "200mV_rep3"
}
```

For pulse trains, `type` is `"stim_train"` and the entry includes `frequency_hz`, `n_pulses`.

### Example scripts

Three annotated templates in `ephys_experiment_scripts/examples/`:

- **`stim_response_curve.py`** — sweep amplitudes on one electrode, repeated N times per amplitude. Useful for activation-threshold mapping.
- **`paired_pulse.py`** — two pulses with sweepable inter-stimulus interval. Useful for short-term plasticity (PPF/PPD).
- **`frequency_stim.py`** — continuous pulse train at a fixed frequency. Useful for tetanus / LTP induction protocols.

Each is a standalone script with editable constants at the top. The skill should adapt them rather than build experiment scripts from scratch.

### When to write a stim script vs use `run_all.py`

| Use `run_all.py` when | Use a custom stim script when |
|---|---|
| The experiment is "scan, select, record N seconds, done" | The recording needs interleaved stim pulses |
| No stimulation, or stim is not orchestrated from this process | Stim parameters change during the recording |
| Standard maturity / baseline / pharma cycles | Stim-response curves, paired-pulse, frequency stim, custom protocols |

For a stim experiment the skill typically still runs `run_all.py` with `--duration 0` (or just the `01` + `02` steps) to produce the `.cfg` and `stim_routing.json`, then invokes the custom script for the actual recording.

### Multi-well stim experiments — run sequentially

Each stim experiment covers one well.  When more than one well needs stimulation, **run the experiments one after another**, each in its own output directory.  Each well gets:

- its own activity scan + electrode selection (with the well's own stim electrodes wired)
- its own custom stim script
- its own `manifest.json` and HDF5 recording

```bash
WELLS_TO_STIM=(0 2 4)
DAY_DIR=/path/to/recordings/2026-04-19

for W in "${WELLS_TO_STIM[@]}"; do
  OUT=${DAY_DIR}/$(date -u +%H%M%S)_stim_w${W}

  # 1. Scan + select with stim electrodes wired (skip the recording step)
  /home/sharf-lab/MaxLab/python/bin/python3 \
    /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/run_all.py \
    --kind stim --wells $W --output-dir $OUT \
    --stim-electrodes <electrodes-for-well-$W> --skip-record

  # 2. Run the well-specific stim experiment script
  /home/sharf-lab/MaxLab/python/bin/python3 \
    /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/examples/stim_response_curve.py
  # (script edits OUTPUT_DIR / WELLS / STIM_ELECTRODE constants per well first)
done
```

Why sequential rather than concurrent:

- The minimal stim implementation requires one well per `--stim-electrodes` invocation.
- Sequential wells mean independent recordings that are simple to analyse separately — no need to demux which stim happened on which well from a shared HDF5.
- Multi-well recordings without stim are still fully supported via the regular pipeline; the sequential constraint only applies when stim is involved.

---

## Output files

After a full pipeline run the output directory contains:

```
<output_dir>/
├── scan_results.npz           # electrodes, spike_rates, mean_amplitudes, wells, scan_mode, ...
├── selected_electrodes.cfg    # MaxWell routing configuration
├── <name>.raw.h5              # one or more recordings (hundreds of MB to many GB each)
└── manifest.json              # structured summary of everything run in this directory
```

### manifest.json — summary file

Every script merges a record of its step into `manifest.json` in the output directory. `manifest.json` is the canonical summary of what happened in a timepoint directory — the orchestrator, analysis tools, and humans read it first and only open raw files when they need bulk data.

**File format:** UTF-8 JSON, pretty-printed (2-space indent). One per output directory, always named `manifest.json`.

**Top-level shape:**

```json
{
  "manifest_version": 1,
  "timepoint_dir": "/abs/path/to/output_dir",
  "created_at": "2026-04-13T03:15:00.123-07:00",
  "kind": "pharma",
  "wells_requested": [2],
  "pipeline": {
    "run_all_invoked": true,
    "cli_args": { "mode": "sparse_7x", "duration": 600.0, "name": "baseline_drug", "wells": [2], "output_dir": "/abs/path", "kind": "pharma" },
    "config_snapshot": { "DETECTION_THRESHOLD": 5.0, "SCAN_SECONDS_PER_BLOCK": 30.0, "MIN_REGIONS": 40, "MAX_SELECTED_ELECTRODES": 1020 }
  },
  "steps": {
    "scan":   { "status": "ok", "started_at": "...", "duration_s": 388.4, "mode": "sparse_7x", "blocks": 7, "electrodes_scanned": 6600, "active_electrodes": 919, "output_file": "scan_results.npz", "output_bytes": 184320 },
    "select": { "status": "ok", "started_at": "...", "duration_s": 31.2, "by": "spike_rate", "regions_phase1": 40, "regions_phase2": 1, "total_regions": 41, "electrodes_selected": 1019, "output_file": "selected_electrodes.cfg", "output_bytes": 26913 },
    "record": [
      {
        "status": "ok", "started_at": "...", "name": "baseline_drug",
        "output_file": "baseline_drug.raw.h5", "output_bytes": 722837504,
        "duration_requested_s": 600.0, "duration_actual_s": 602.1,
        "wells": [{"well_id": 2, "data_group": "data0000", "start_time_ms": 1776074916278, "stop_time_ms": 1776075516278, "sampling_hz": 10000.0, "spike_count": 4127}],
        "events": [{"type": "habitat_operation", "timestamp_ms": 1776075216278, "frame_offset": 3000000, "habitat_task_id": "abc-123", "summary": "drug_addition TTX 1.0uM well 2"}]
      }
    ]
  },
  "environment": { "host": "sharflab", "mxw_version": "25.1.8.1", "maxlab_python": "/home/sharf-lab/MaxLab/python/bin/python3" },
  "errors": []
}
```

**Field semantics:**

| Field | Behavior |
|---|---|
| `manifest_version` | Integer, currently `1`. Bump on breaking schema changes. |
| `timepoint_dir` | The one absolute path in the manifest. Everything else is relative filenames. |
| `created_at` | ISO-8601 with timezone. Set on initial creation only. |
| `kind` | First-writer-wins: set when first writer supplies `--kind`; later writers preserve. |
| `wells_requested` | Physical well IDs (0–5). |
| `pipeline.run_all_invoked` | `true` when `run_all.py` launched the run. Once true, never flipped back. |
| `pipeline.cli_args`, `pipeline.config_snapshot` | Set on initial creation only. |
| `steps.scan`, `steps.select` | Single-invocation steps. Overwritten on re-run — a re-scan is authoritative. |
| `steps.record` | **List** — one entry per recording. Appended on each `03_record.py` invocation; never overwritten. Enables baseline/drug/wash workflows. |
| `steps.record[].wells[]` | Per-well timing + spike count, extracted from the HDF5 file itself (not stdout). |
| `steps.record[].events` | Initially `[]`. External systems (orchestrator) append cross-references to mid-recording perturbations. |
| `environment` | Captured on creation; later writers may fill missing fields (e.g. `mxw_version` from the first produced `.raw.h5`). Never overwrites non-empty fields. |
| `errors` | Always present; empty on clean run. Top-level `errors[]` are short `"<step>: <ErrorType>: <message>"` lines. Per-step error detail lives in the step entry. |

**Paths inside the manifest are relative filenames** (e.g. `"recording.raw.h5"`). `timepoint_dir` is the only absolute path. This makes the directory portable — move/copy/archive and the manifest stays valid.

**Writes are atomic:** write to `.manifest.XXXXXX.tmp`, `fsync`, `os.replace` to `manifest.json`. Readers never see a half-written manifest.

**Error capture:** on failure, the step entry gets `status: "failed"`, `error_type`, `error_message`, and whatever context is available (`started_at`, `duration_s`, step-specific fields). A one-line entry is appended to top-level `errors[]`. The manifest write itself is best-effort — if it fails during error capture, the original exception is not masked. A subsequent successful re-run overwrites the failed step entry (for `scan`/`select`) or appends a new entry (for `record`).

**Event appending (orchestrator-side):** `append_event_to_last_recording(output_dir, event)` in [manifest.py](../../../ephys_experiment_scripts/manifest.py) loads the manifest, appends an event to `steps.record[-1].events`, and writes atomically. If `frame_offset` is absent it is computed from the recording's first well's `start_time_ms` + `sampling_hz`. Rich fluidics metadata is NOT copied into the manifest — only a thin cross-reference (`type`, `timestamp_ms`, `frame_offset`, external `task_id`, short `summary`). The habitat log is the source of truth for fluidics details.

**HDF5 data-group indexing reminder:** `data_store/dataNNNN/` is sequential among recorded wells starting at `0000`, NOT the physical well number. Always use `well_id` to identify the physical well. With `--wells 2`, the only group is `data0000` with `well_id: 2`.

### `--kind` CLI flag

All four scripts accept `--kind maturity|baseline|pharma|stim|ad_hoc`. The first writer to create `manifest.json` sets `kind`; subsequent writers preserve it. Usually passed to `run_all.py` or `03_record.py`; `01`/`02` accept it too for standalone use.

### Default location (standalone use)

Scripts currently write to `ephys_experiment_scripts/experiment_output/` (a fixed path relative to the script file, not the CWD). `03_record.py` auto-increments `<name>.raw.h5` on collision. This default is fine for ad-hoc use but accumulates everything in one directory — not suitable once the orchestrator is producing recordings every few hours.

### Recommended layout for orchestrator use

Given the project scope (6 cultures, ~6 h cycles across weeks of lifecycle, plus per-well fluidics/pharma/stim experiments), recordings should be organized by **timepoint and purpose** rather than flat-filed. Recommended structure under the orchestrator root:

```
orchestrator/recordings/
├── 2026-04-13/                                     # one directory per day
│   ├── 031500_maturity_all/                        # HHMMSS_<kind>_<wells>
│   │   ├── scan_results.npz
│   │   ├── selected_electrodes.cfg
│   │   ├── recording.raw.h5
│   │   └── manifest.json                           # skill-written summary
│   ├── 093000_maturity_all/
│   │   └── ...
│   └── 140500_pharma_w2/                           # single-well experiment
│       ├── scan_results.npz                        # scan for this experiment
│       ├── selected_electrodes.cfg
│       ├── baseline.raw.h5                         # multiple recordings
│       ├── drug.raw.h5                             #   share one config
│       └── wash.raw.h5
└── 2026-04-14/
    └── ...
```

Naming conventions:

- `<kind>` — orchestrator-defined: `maturity`, `baseline`, `pharma`, `stim`, `ad_hoc`, etc.
- `<wells>` — `all` when all 6 wells are recorded; otherwise a compact form like `w2` or `w0_2_4`.
- Fixed filenames inside each directory: `scan_results.npz`, `selected_electrodes.cfg`. Recordings use `--name` (e.g. `baseline`, `drug`) so multiple recordings can share one electrode configuration.

Why this shape:

- **Per-timepoint directories** co-locate the scan, the config, and the recording(s) — full reproducibility in one place, and easy to archive or delete a single cycle.
- **Day-level grouping** makes filesystem skimming and bulk operations (rsync, compression, archival) practical across weeks of data.
- **Shared config across related recordings** (e.g. baseline → drug → wash) means one scan + one `.cfg` per experiment block, not per recording.
- The orchestrator already tracks events in `orchestrator/wells/well_N/culture_log.json`; paths under this layout are reconstructable from `{timepoint, kind, wells}` fields in those logs.

### Writing to a custom directory

All four scripts accept `--output-dir <path>` to override the default. Pass the same directory to each step of a pipeline (step 2 reads `scan_results.npz` from it, step 3 reads `selected_electrodes.cfg` from it):

```bash
OUT=/path/to/orchestrator/recordings/2026-04-13/031500_maturity_all
mkdir -p "$OUT"
/home/sharf-lab/MaxLab/python/bin/python3 \
  /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/run_all.py \
  --mode sparse_7x --scan-seconds 30 --duration 300 --output-dir "$OUT"
```

`run_all.py` forwards `--output-dir` to all three steps automatically. When running individual steps, pass it to each.

### Inside each `.raw.h5`

Each `.raw.h5` contains MaxWell-server-written metadata, with **one entry per recorded well** under `data_store/dataNNNN/`. The `NNNN` index is **sequential among recorded wells starting at 0000** — it is NOT the physical well number. Always read `well_id` to find the physical well (0–5). When all 6 wells are recorded, `dataNNNN` happens to equal the well number; for any subset (e.g. `--wells 2`) the subset's single entry is still `data0000` with `well_id=2`.

Useful HDF5 paths (per recorded well):

| Path | Content |
|---|---|
| `data_store/dataNNNN/well_id` | Physical well index (0–5) |
| `data_store/dataNNNN/start_time` | Unix ms since epoch (divide by 1e3 for seconds) |
| `data_store/dataNNNN/stop_time` | Unix ms since epoch |
| `data_store/dataNNNN/settings/sampling` | Sampling rate in Hz (10000) |
| `data_store/dataNNNN/settings/lsb` | Voltage scaling (LSB → mV) |
| `data_store/dataNNNN/settings/gain` | Amplifier gain as recorded by mxwserver |
| `data_store/dataNNNN/settings/hpf` | High-pass filter (Hz) |
| `data_store/dataNNNN/settings/spike_threshold` | × RMS threshold |
| `data_store/dataNNNN/settings/mapping` | Channel → electrode mapping |
| `data_store/dataNNNN/groups/routed/raw` | Raw voltage (channels × frames) uint16 |
| `data_store/dataNNNN/groups/routed/frame_nos` | Frame numbers for alignment |
| `data_store/dataNNNN/spikes` | Spike events (frameno, channel, amplitude) |

---

## Parameters in `config.py`

Editable defaults (override at CLI where supported):

- `WELLS` — default well list (overridden by `--wells`)
- `DETECTION_THRESHOLD` — spike threshold in × RMS (default 5.0)
- `SCAN_SECONDS_PER_BLOCK` — 30.0
- `MIN_REGIONS` — 40 (overridden by `--regions`)
- `MAX_SELECTED_ELECTRODES` — 1020
- `RECORDING_DURATION_SEC` — 300.0 (overridden by `--duration`)

If tuning is needed (e.g. threshold for a quiet or noisy culture), edit `config.py` — there is no CLI arg for `DETECTION_THRESHOLD`.

---

## Common failure modes

| Symptom | Cause |
|---|---|
| `ModuleNotFoundError: No module named 'maxlab'` | Wrong Python — use `/home/sharf-lab/MaxLab/python/bin/python3` |
| Script hangs on hardware init | `mxwserver` not running |
| `scan results not found` | Step 2 invoked before step 1 |
| `configuration not found` | Step 3 invoked before step 2 |
| HDF5 write error mid-recording | Disk full |

Each script exits non-zero on failure with a clear message. `run_all.py` aborts the rest of the pipeline.

---

## What this skill does NOT do

- No spike sorting or electrophysiological analysis — that lives in the `spikelab` skill. Hand off the `.raw.h5` path.
- No plotting — `matplotlib` is not installed in the MaxLab Python.
- No ZMQ, binary wire protocol, or direct `maxlab` calls — the scripts are the only entry point.
- No fluidics — that's the `habitat-remote` skill.
- No deletion or overwrite of raw data — recordings auto-increment filenames on collision.

