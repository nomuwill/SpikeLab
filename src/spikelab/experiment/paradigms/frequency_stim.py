#!/usr/bin/env python3
"""
Example: continuous-frequency pulse train.

Delivers an evenly spaced train of identical pulses at a fixed
frequency.  All inter-pulse timing is hardware-scheduled inside a
single maxlab.Sequence, so jitter is at the 50 µs sample boundary
rather than the millisecond level you'd get from a Python sleep loop.

Use this for tetanus stimulation, sustained drive, or LTP induction
protocols.

Prerequisites are the same as in :mod:`stim_response_curve`.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from experiment_lib import (
    connect_stim_electrodes,
    fire_pulse_train,
    load_and_route,
    prepare_hardware,
    recording_session,
)


# ── Experiment parameters ─────────────────────────────────────────────

OUTPUT_DIR = "/path/to/output"          # <-- EDIT
WELLS = [2]                              # <-- EDIT
STIM_ELECTRODE = 5280                    # <-- EDIT

AMPLITUDE_MV = 200
PHASE_US = 100
FREQUENCY_HZ = 20
PULSES_IN_TRAIN = 100                    # 100 pulses @ 20 Hz = 5 s of stim

BASELINE_SEC = 60.0                      # pre-train recording
POST_STIM_SEC = 120.0                    # post-train recording


# ── Run ───────────────────────────────────────────────────────────────


def main():
    cfg_path = f"{OUTPUT_DIR}/selected_electrodes.cfg"

    prepare_hardware(WELLS)
    load_and_route(cfg_path, WELLS)
    routing = connect_stim_electrodes(OUTPUT_DIR)

    if STIM_ELECTRODE not in routing:
        raise RuntimeError(
            f"electrode {STIM_ELECTRODE} not in stim routing {sorted(routing)}"
        )

    with recording_session(OUTPUT_DIR, name="frequency_stim", wells=WELLS,
                           kind="stim") as rec:
        print(f"Baseline: {BASELINE_SEC} s")
        time.sleep(BASELINE_SEC)

        print(f"Train: {PULSES_IN_TRAIN} pulses @ {FREQUENCY_HZ} Hz "
              f"on electrode {STIM_ELECTRODE}")
        fire_pulse_train(
            routing,
            electrodes=STIM_ELECTRODE,
            amplitude_mv=AMPLITUDE_MV,
            phase_us=PHASE_US,
            frequency_hz=FREQUENCY_HZ,
            n_pulses=PULSES_IN_TRAIN,
            label=f"{FREQUENCY_HZ}Hz_x{PULSES_IN_TRAIN}",
            recording=rec,
        )

        # Wait for the train to finish playing on the hardware before
        # entering the post-stim recording window.  The Python
        # fire_pulse_train returned when the sequence was sent, not
        # when it finished executing.
        train_duration_sec = PULSES_IN_TRAIN / FREQUENCY_HZ
        time.sleep(train_duration_sec + 1.0)

        print(f"Post-stim recording: {POST_STIM_SEC} s")
        time.sleep(POST_STIM_SEC)

    print("Done.  See manifest.json for the full event log.")


if __name__ == "__main__":
    main()
