"""
hCO-stim-demo_2026-05-13 — Step 7: stimulation + recording.

100 biphasic (negative-first) pulses at 1 Hz starting at t=0.
  Electrode:   9380  (stim unit 22)
  Amplitude:   600 mV
  Pulse width: 200 µs per phase
  Total recording: 300 s (stim t=0–~100 s, post-stim ~200 s recovery)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from experiment_lib import (
    connect_stim_electrodes,
    fire_pulse,
    load_and_route,
    prepare_hardware,
    recording_session,
)

OUTPUT_DIR  = "/home/sharf-lab/Desktop/Research_automation/orchestrator/hCO-stim-demo_2026-05-13/recordings/2026-05-13/144722_scan_well0"
WELLS       = [0]
STIM_ELECTRODE  = 9380
AMPLITUDE_MV    = 600
PHASE_US        = 200
POLARITY        = "negative_first"
N_PULSES        = 100
PULSE_INTERVAL_S = 1.0
TOTAL_DURATION_S = 300

print("Initialising hardware...")
prepare_hardware(WELLS)
load_and_route(f"{OUTPUT_DIR}/selected_electrodes.cfg", WELLS)
routing = connect_stim_electrodes(OUTPUT_DIR)
print(f"Stim routing: {routing}")

print(f"\nStarting recording + {N_PULSES} pulses at {1/PULSE_INTERVAL_S:.1f} Hz...")
with recording_session(OUTPUT_DIR, name="stim", wells=WELLS, kind="stim") as rec:
    t_start = time.time()

    for i in range(N_PULSES):
        # Compute absolute time for this pulse and sleep until then
        t_target = t_start + i * PULSE_INTERVAL_S
        wait = t_target - time.time()
        if wait > 0:
            time.sleep(wait)

        fire_pulse(
            routing,
            electrodes=STIM_ELECTRODE,
            amplitude_mv=AMPLITUDE_MV,
            phase_us=PHASE_US,
            polarity=POLARITY,
            label=f"pulse_{i:03d}",
            recording=rec,
        )

        elapsed = time.time() - t_start
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{N_PULSES} pulses  t={elapsed:.1f}s")

    # Hold recording open for the remaining post-stim window
    elapsed = time.time() - t_start
    remaining = TOTAL_DURATION_S - elapsed
    if remaining > 0:
        print(f"\nStim done ({elapsed:.1f}s). Holding {remaining:.0f}s post-stim...")
        time.sleep(remaining)

print("\nRecording closed. Manifest written.")
