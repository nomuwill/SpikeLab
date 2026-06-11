#!/usr/bin/env python3
"""
Example: paired-pulse stimulation with variable inter-stimulus interval.

Delivers two pulses on the same electrode separated by a configurable
ISI.  Sweeps the ISI to characterise short-term plasticity (paired-
pulse facilitation / depression).

Prerequisites are the same as in :mod:`stim_response_curve` —
``02_select_electrodes.py`` must have been run with
``--stim-electrodes <ELEC>`` for the chosen electrode.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from experiment_lib import (
    connect_stim_electrodes,
    fire_pulse,
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

# Inter-stimulus intervals to test, in milliseconds
ISI_MS_LIST = [10, 25, 50, 100, 250, 500, 1000]
REPEATS_PER_ISI = 5
BETWEEN_PAIRS_SEC = 5.0
BASELINE_SEC = 30.0


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

    with recording_session(OUTPUT_DIR, name="paired_pulse", wells=WELLS,
                           kind="stim") as rec:
        print(f"Baseline: {BASELINE_SEC} s")
        time.sleep(BASELINE_SEC)

        for isi_ms in ISI_MS_LIST:
            print(f"ISI {isi_ms} ms — {REPEATS_PER_ISI} pairs")
            for i in range(REPEATS_PER_ISI):
                # Pulse 1
                fire_pulse(
                    routing,
                    electrodes=STIM_ELECTRODE,
                    amplitude_mv=AMPLITUDE_MV,
                    phase_us=PHASE_US,
                    label=f"isi{isi_ms}_p1_rep{i}",
                    recording=rec,
                )
                time.sleep(isi_ms / 1000.0)
                # Pulse 2
                fire_pulse(
                    routing,
                    electrodes=STIM_ELECTRODE,
                    amplitude_mv=AMPLITUDE_MV,
                    phase_us=PHASE_US,
                    label=f"isi{isi_ms}_p2_rep{i}",
                    recording=rec,
                )
                time.sleep(BETWEEN_PAIRS_SEC)

    print("Done.  See manifest.json for the full event log.")


if __name__ == "__main__":
    main()
