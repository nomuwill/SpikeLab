"""
Experiment configuration — shared constants for all scripts.

Edit these values before running on a new setup.
"""

# ── Hardware ───────────────────────────────────────────────────────────

# Wells to use (all 6 wells on the MaxTwo 6-well plate)
WELLS = [0, 1, 2, 3, 4, 5]

# Spike detection threshold (multiples of RMS noise)
DETECTION_THRESHOLD = 5.0

# ── Activity scan ──────────────────────────────────────────────────────

# Seconds of recording per electrode block during scanning
SCAN_SECONDS_PER_BLOCK = 30.0

# Maximum electrodes routed per block (hardware limit ~1024 channels)
MAX_ELECTRODES_PER_BLOCK = 1020

# ── Electrode selection ────────────────────────────────────────────────

# Minimum number of regions to place around active electrodes
MIN_REGIONS = 40

# Maximum electrodes in the final configuration
MAX_SELECTED_ELECTRODES = 1020

# ── Recording ──────────────────────────────────────────────────────────

# Duration of the main recording (seconds)
RECORDING_DURATION_SEC = 300.0

# ── Stimulation defaults ───────────────────────────────────────────────
#
# Used by experiment_lib.py helpers when an experiment script does not
# pass explicit values.  Override per call where appropriate.

# Default biphasic pulse amplitude in mV.  The pulse is symmetric and
# swings to ±STIM_AMPLITUDE_MV.
STIM_AMPLITUDE_MV = 200.0

# Default duration of one phase of the biphasic pulse, in microseconds.
# Total pulse footprint on the DAC is ~2 × phase + a 2-sample tail.
STIM_PHASE_US = 100.0

# Default polarity ordering for the biphasic pulse:
#   "positive_first" — positive phase, then negative, then return to zero
#   "negative_first" — negative phase first (reversed)
STIM_POLARITY = "positive_first"

# Filename for the per-electrode stim routing produced by 02_select_electrodes.py
# when --stim-electrodes is given.  Read by experiment_lib.connect_stim_electrodes.
STIM_ROUTING_FILE = "stim_routing.json"

# ── Paths ──────────────────────────────────────────────────────────────

import os as _os

# Where to store scan results, configs, and recordings
# Resolved relative to THIS file's directory, not the working directory
OUTPUT_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "experiment_output")

# Filename for scan results
SCAN_RESULTS_FILE = "scan_results.npz"

# Filename for the generated electrode configuration
GENERATED_CONFIG_FILE = "selected_electrodes.cfg"

# Filename prefix for recordings
RECORDING_PREFIX = "recording"
