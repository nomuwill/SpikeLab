"""
Building blocks for writing custom MaxWell stimulation experiments.

This module provides procedural helpers that an experiment script can
compose into a complete recording-and-stimulation flow.  It is meant
to be used directly from short, purpose-written scripts in the
``examples/`` directory (or skill-authored equivalents).

Typical script shape::

    from experiment_lib import (
        prepare_hardware, load_and_route, connect_stim_electrodes,
        recording_session, fire_pulse, fire_pulse_train,
    )

    OUTPUT_DIR = "/path/to/output"
    WELLS = [2]

    prepare_hardware(WELLS)
    load_and_route(f"{OUTPUT_DIR}/selected_electrodes.cfg", WELLS)
    routing = connect_stim_electrodes(OUTPUT_DIR)

    with recording_session(OUTPUT_DIR, name="experiment", wells=WELLS,
                           kind="stim") as rec:
        time.sleep(60)                  # baseline
        fire_pulse(routing, electrodes=[5280],
                   amplitude_mv=200, phase_us=100, recording=rec)
        time.sleep(30)

The ``recording_session`` context manager handles HDF5 lifecycle and
manifest writing.  ``fire_pulse(...)`` and ``fire_pulse_train(...)``
build and send a maxlab.Sequence in one call; passing ``recording=rec``
attaches an event entry to the recording in ``manifest.json``.

This module is intentionally procedural — no classes for pulses, no
manager objects.  Compose flows in plain Python.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

import maxlab as mx

import manifest as manifest_mod
from config import (
    DETECTION_THRESHOLD,
    STIM_AMPLITUDE_MV,
    STIM_PHASE_US,
    STIM_POLARITY,
    STIM_ROUTING_FILE,
)


# ── DAC + timing constants (public MaxLab values) ──────────────────────

_DAC_MID = 512                    # zero-volt DAC code (10-bit DAC)
_DAC_CHANNEL = 0                  # only DAC 0 is used in this minimal API
_DEFAULT_LSB_MV = 2.981901        # query_DAC_lsb_mV fallback
_DEFAULT_SAMPLING_HZ = 20_000     # MaxOne; MaxTwo is queried at runtime
_TAIL_SAMPLES = 2                 # samples held at zero after the pulse


# ── Module-level cache for hardware-derived constants ──────────────────

_lsb_mv_cache: float | None = None
_sampling_hz_cache: int | None = None


def _query_lsb_mv() -> float:
    """Query the device DAC LSB value (mV per bit), with fallback."""
    global _lsb_mv_cache
    if _lsb_mv_cache is None:
        try:
            _lsb_mv_cache = float(str(mx.query_DAC_lsb_mV()).strip())
        except Exception:
            _lsb_mv_cache = _DEFAULT_LSB_MV
    return _lsb_mv_cache


def _query_sampling_hz() -> int:
    """Query the device sampling rate.  MaxOne = 20 kHz; MaxTwo = 10 kHz."""
    global _sampling_hz_cache
    if _sampling_hz_cache is None:
        try:
            v = int(str(mx.send_raw("wellplate_query_version")).strip())
        except Exception:
            v = 0
        _sampling_hz_cache = 10_000 if v >= 1 else 20_000
    return _sampling_hz_cache


def _mv_to_bits(amplitude_mv: float) -> int:
    """Convert an absolute amplitude (mV) to DAC bits."""
    return int(round(abs(amplitude_mv) / _query_lsb_mv()))


def _us_to_samples(duration_us: float) -> int:
    """Convert a duration (µs) to integer samples (minimum 1)."""
    return max(1, int(round(duration_us * _query_sampling_hz() / 1_000_000)))


# ── Hardware preparation ───────────────────────────────────────────────


def prepare_hardware(wells: list[int], threshold: float = DETECTION_THRESHOLD) -> None:
    """Activate the wells, initialise the chip, set the spike threshold.

    Mirrors the init sequence used by ``03_record.py`` so a stim
    experiment script can stand in for the recording step entirely.
    """
    mx.activate(wells)
    mx.initialize(wells)
    time.sleep(mx.Timing.waitInit)
    mx.send(mx.Core().enable_stimulation_power(True))
    mx.send_raw(f"stream_set_event_threshold {threshold}")


def load_and_route(cfg_path: str, wells: list[int]):
    """Load an electrode .cfg, push it to the chip, run offset compensation.

    Returns the maxlab.chip.Array handle; most callers can ignore it.
    """
    arr = mx.Array("online")
    arr.load_config(cfg_path)
    arr.download(wells)
    time.sleep(mx.Timing.waitAfterDownload)
    mx.offset()
    return arr


# ── Stim electrode connection ──────────────────────────────────────────


def connect_stim_electrodes(output_dir: str) -> dict[int, int]:
    """Read ``stim_routing.json`` and power up the listed stim units.

    Expects ``02_select_electrodes.py`` to have been run with
    ``--stim-electrodes``, which produces ``stim_routing.json`` next to
    the .cfg.  Powers up each unit in voltage mode on DAC 0.

    Returns
    -------
    dict[int, int]
        ``{electrode_id: stim_unit_id}`` for the wired electrodes.
    """
    path = os.path.join(output_dir, STIM_ROUTING_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Stim routing file not found at {path}.  Run "
            "02_select_electrodes.py with --stim-electrodes first."
        )
    with open(path) as f:
        data = json.load(f)
    routing = {int(e): int(u) for e, u in data["routing"].items()}

    # Power up each stim unit, voltage mode, DAC 0.
    for unit_id in routing.values():
        unit = mx.StimulationUnit(unit_id)
        unit.power_up(True).connect(True).set_voltage_mode().dac_source(_DAC_CHANNEL)
        mx.send(unit)

    return routing


# ── Recording context manager ──────────────────────────────────────────


@dataclass
class ActiveRecording:
    """Handle yielded by :func:`recording_session`.

    Carries the metadata needed to attach stim events to the manifest
    when the recording ends.  Fields are populated by
    :func:`recording_session` and consumed at exit.
    """

    output_dir: str
    name: str
    wells: list[int]
    kind: str
    started_at: str
    t_start: float
    events: list[dict] = field(default_factory=list)


def _unique_recording_name(directory: str, base: str) -> str:
    candidate = base
    counter = 0
    while os.path.exists(os.path.join(directory, f"{candidate}.raw.h5")):
        counter += 1
        candidate = f"{base}_{counter}"
    return candidate


@contextmanager
def recording_session(
    output_dir: str,
    name: str,
    wells: list[int],
    kind: str = "stim",
) -> Iterator[ActiveRecording]:
    """Open an HDF5 recording, yield a handle, close + write manifest on exit.

    Parameters
    ----------
    output_dir : str
        Directory the recording goes into (must already exist or will
        be created).  The manifest is updated in this directory.
    name : str
        Filename prefix.  Auto-incremented on collision so re-running a
        script never overwrites previous data.
    wells : list[int]
        Wells to record from.
    kind : str
        Experiment kind (one of ``manifest.KIND_CHOICES``); recorded in
        the manifest's top-level ``kind`` field if not already set.
    """
    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)
    final_name = _unique_recording_name(output_dir, name)

    saver = mx.Saving()
    saver.open_directory(output_dir)
    saver.set_legacy_format(False)
    saver.group_delete_all()
    for w in wells:
        saver.group_define(w, "routed", list(range(1024)))
    saver.start_file(final_name)
    saver.start_recording(wells)

    started_at = manifest_mod.now_iso()
    t_start = time.perf_counter()
    rec = ActiveRecording(
        output_dir=output_dir,
        name=final_name,
        wells=list(wells),
        kind=kind,
        started_at=started_at,
        t_start=t_start,
    )

    error: BaseException | None = None
    try:
        yield rec
    except BaseException as exc:
        error = exc
        raise
    finally:
        duration = time.perf_counter() - t_start

        # Stop saving regardless of whether the body raised
        try:
            saver.stop_recording()
            time.sleep(mx.Timing.waitAfterRecording)
            saver.stop_file()
            saver.group_delete_all()
        except Exception:
            pass

        h5_path = os.path.join(output_dir, f"{final_name}.raw.h5")
        if not os.path.exists(h5_path):
            return

        m = manifest_mod.load_or_init(output_dir, kind=kind, wells_requested=wells)
        try:
            wells_info = manifest_mod.extract_wells_from_h5(h5_path)
            mxw_version = manifest_mod.read_mxw_version(h5_path)
        except Exception as exc:
            wells_info = []
            mxw_version = None
            m["errors"].append(
                f"manifest: could not read per-well info from "
                f"{final_name}.raw.h5: {exc}"
            )
        if mxw_version and not m["environment"].get("mxw_version"):
            m["environment"]["mxw_version"] = mxw_version

        entry = {
            "status": "failed" if error is not None else "ok",
            "started_at": started_at,
            "name": final_name,
            "output_file": f"{final_name}.raw.h5",
            "output_bytes": os.path.getsize(h5_path),
            "duration_actual_s": round(duration, 2),
            "wells": wells_info,
            "events": rec.events,
        }
        if error is not None:
            entry["error_type"] = type(error).__name__
            entry["error_message"] = str(error)
            m["errors"].append(
                f"record: {type(error).__name__}: {error}"
            )
        manifest_mod.append_recording(m, entry)
        manifest_mod.write_atomic(m, output_dir)


# ── Pulse firing ───────────────────────────────────────────────────────


def _build_biphasic_pulse(
    seq,
    amplitude_mv: float,
    phase_us: float,
    polarity: str,
) -> None:
    """Append a single biphasic DAC pulse to *seq* on ``_DAC_CHANNEL``."""
    amp_bits = _mv_to_bits(amplitude_mv)
    phase_samples = _us_to_samples(phase_us)
    code_pos = _DAC_MID - amp_bits
    code_neg = _DAC_MID + amp_bits
    if polarity == "positive_first":
        first, second = code_pos, code_neg
    elif polarity == "negative_first":
        first, second = code_neg, code_pos
    else:
        raise ValueError(
            f"polarity must be 'positive_first' or 'negative_first' "
            f"(got {polarity!r})"
        )

    seq.append(mx.DAC(_DAC_CHANNEL, first))
    seq.append(mx.DelaySamples(phase_samples))
    seq.append(mx.DAC(_DAC_CHANNEL, second))
    seq.append(mx.DelaySamples(phase_samples))
    seq.append(mx.DAC(_DAC_CHANNEL, _DAC_MID))
    seq.append(mx.DelaySamples(_TAIL_SAMPLES))


def _coerce_electrode_list(electrodes) -> list[int]:
    if isinstance(electrodes, int):
        return [electrodes]
    return [int(e) for e in electrodes]


def _select_units(routing: dict[int, int], targets: list[int], seq) -> None:
    """Power up *targets* and power down all other stim units in *routing*."""
    target_set = set(targets)
    for elec, unit_id in routing.items():
        unit = mx.StimulationUnit(unit_id)
        if elec in target_set:
            seq.append(
                unit.power_up(True).connect(True).set_voltage_mode()
                    .dac_source(_DAC_CHANNEL)
            )
        else:
            seq.append(unit.power_up(False).connect(False))


def fire_pulse(
    routing: dict[int, int],
    electrodes,
    amplitude_mv: float = STIM_AMPLITUDE_MV,
    phase_us: float = STIM_PHASE_US,
    polarity: str = STIM_POLARITY,
    label: str | None = None,
    recording: ActiveRecording | None = None,
) -> int:
    """Fire one biphasic pulse on the given electrode(s).

    All target electrodes share DAC 0 — they receive the same waveform
    at the same instant.  Stim units for non-target electrodes are
    powered down for the duration of the pulse to avoid stimulating
    them inadvertently.

    Parameters
    ----------
    routing : dict[int, int]
        ``{electrode: unit_id}`` from :func:`connect_stim_electrodes`.
    electrodes : int | list[int]
        Electrode(s) to fire.  Must all be in *routing*.
    amplitude_mv : float
        Pulse amplitude (biphasic, swings to ±amplitude).
    phase_us : float
        Duration of each half-phase.
    polarity : {"positive_first", "negative_first"}
        Pulse polarity ordering.
    label : str | None
        Optional tag stored with the manifest event.
    recording : ActiveRecording | None
        When given, an event describing the pulse is appended to
        ``recording.events`` and will land in ``manifest.json`` when
        the recording ends.

    Returns
    -------
    int
        Unix timestamp (ms) recorded immediately after sending the
        pulse to the server.
    """
    targets = _coerce_electrode_list(electrodes)
    for e in targets:
        if e not in routing:
            raise ValueError(
                f"electrode {e} is not in the stim routing "
                f"({sorted(routing)})"
            )

    seq = mx.Sequence()
    _select_units(routing, targets, seq)
    _build_biphasic_pulse(seq, amplitude_mv, phase_us, polarity)
    seq.send()
    timestamp_ms = int(time.time() * 1000)

    if recording is not None:
        recording.events.append({
            "type": "stim",
            "timestamp_ms": timestamp_ms,
            "electrodes": targets,
            "amplitude_mv": amplitude_mv,
            "phase_us": phase_us,
            "polarity": polarity,
            "label": label,
        })

    return timestamp_ms


def fire_pulse_train(
    routing: dict[int, int],
    electrodes,
    amplitude_mv: float = STIM_AMPLITUDE_MV,
    phase_us: float = STIM_PHASE_US,
    frequency_hz: float = 10.0,
    n_pulses: int = 10,
    polarity: str = STIM_POLARITY,
    label: str | None = None,
    recording: ActiveRecording | None = None,
) -> int:
    """Fire ``n_pulses`` of the same waveform at ``frequency_hz``.

    Implemented as a single hardware-timed maxlab.Sequence for
    sub-millisecond precision (much better than a Python loop with
    ``time.sleep``).  Inter-pulse delays are computed in samples.

    For trains with varying amplitudes or phase widths, call
    :func:`fire_pulse` repeatedly in a Python loop instead — Python-
    level timing is fine for tens-of-milliseconds gaps and gives
    arbitrary per-pulse parameters.

    Returns the Unix timestamp (ms) recorded just after sending the
    full sequence.
    """
    if n_pulses < 1:
        raise ValueError("n_pulses must be >= 1")
    if frequency_hz <= 0:
        raise ValueError("frequency_hz must be positive")

    targets = _coerce_electrode_list(electrodes)
    for e in targets:
        if e not in routing:
            raise ValueError(
                f"electrode {e} is not in the stim routing "
                f"({sorted(routing)})"
            )

    sr = _query_sampling_hz()
    period_samples = max(1, int(round(sr / frequency_hz)))

    # One biphasic pulse occupies ~2*phase_samples + tail.  Refuse
    # configurations where the inter-pulse interval is smaller than
    # the pulse footprint.
    pulse_footprint = 2 * _us_to_samples(phase_us) + _TAIL_SAMPLES
    if period_samples < pulse_footprint:
        raise ValueError(
            f"frequency {frequency_hz} Hz is too high for phase {phase_us} µs "
            f"(pulse occupies {pulse_footprint} samples, period is "
            f"{period_samples} samples)"
        )
    inter_pulse_samples = period_samples - pulse_footprint

    seq = mx.Sequence()
    _select_units(routing, targets, seq)
    for i in range(n_pulses):
        _build_biphasic_pulse(seq, amplitude_mv, phase_us, polarity)
        if i < n_pulses - 1 and inter_pulse_samples > 0:
            seq.append(mx.DelaySamples(inter_pulse_samples))
    seq.send()
    timestamp_ms = int(time.time() * 1000)

    if recording is not None:
        recording.events.append({
            "type": "stim_train",
            "timestamp_ms": timestamp_ms,
            "electrodes": targets,
            "amplitude_mv": amplitude_mv,
            "phase_us": phase_us,
            "frequency_hz": frequency_hz,
            "n_pulses": n_pulses,
            "polarity": polarity,
            "label": label,
        })

    return timestamp_ms
