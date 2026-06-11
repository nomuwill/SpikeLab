#!/usr/bin/env python3
"""
Example: stim-response curve.

Sweeps stimulation amplitude across a series of values on one
electrode, repeating each amplitude N times with a fixed inter-stim
interval.  Useful for finding the activation threshold of an
electrode's neighbouring neurons.

Prerequisites
-------------
1. Run an activity scan and electrode selection on the target well,
   passing ``--stim-electrodes`` to step 2 so the stim electrode is
   wired into the .cfg::

       python 01_activity_scan.py --kind stim --wells 2 \
           --output-dir $OUT
       python 02_select_electrodes.py --kind stim --wells 2 \
           --output-dir $OUT \
           --stim-electrodes 5280

2. Edit the constants below for your specific experiment.

3. Run with the MaxLab-bundled Python:
       /home/sharf-lab/MaxLab/python/bin/python3 \
           examples/stim_response_curve.py
"""

import sys
import time
from pathlib import Path

# Make experiment_lib importable when running from the examples/ subdir.
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
WELLS = [2]                              # <-- EDIT (single well)
STIM_ELECTRODE = 5280                    # <-- EDIT (must be in stim_routing.json)

AMPLITUDES_MV = [50, 100, 150, 200, 250]
PHASE_US = 100
REPEATS_PER_AMPLITUDE = 10
INTER_STIM_SEC = 1.0                    # gap between consecutive stims
INTER_AMPLITUDE_SEC = 10.0              # pause when changing amplitude
BASELINE_SEC = 60.0                     # pre-stim recording


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

    with recording_session(OUTPUT_DIR, name="stim_response", wells=WELLS,
                           kind="stim") as rec:
        print(f"Baseline: {BASELINE_SEC} s")
        time.sleep(BASELINE_SEC)

        for amp in AMPLITUDES_MV:
            print(f"Amplitude {amp} mV — {REPEATS_PER_AMPLITUDE} stims")
            for i in range(REPEATS_PER_AMPLITUDE):
                fire_pulse(
                    routing,
                    electrodes=STIM_ELECTRODE,
                    amplitude_mv=amp,
                    phase_us=PHASE_US,
                    label=f"{amp}mV_rep{i}",
                    recording=rec,
                )
                time.sleep(INTER_STIM_SEC)
            time.sleep(INTER_AMPLITUDE_SEC)

    print("Done.  See manifest.json for the full event log.")


if __name__ == "__main__":
    main()
