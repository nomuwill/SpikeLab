#!/usr/bin/env python3
"""
Hub-induction daily stimulation session — sequential STDP protocol.

Stim pattern (within-hub STDP induction):
  - Fire hub-A electrodes ONE AT A TIME in sequence, --delta-t-ms apart
  - --gap-ms pause (inter-hub gap, network recovery)
  - Fire hub-B electrodes ONE AT A TIME in sequence, --delta-t-ms apart
  - --gap-ms pause
  - Repeat for --stim-duration seconds

Sequential firing places each consecutive electrode pair within the STDP
LTP window (~5–20 ms), potentiating connections along the firing sequence.
Electrode order is the order stored in hub_config.json — greedy expansion
from hub centre outward, so the default pattern is centre → periphery
(divergent).  Cross-hub STDP is avoided by the inter-hub gap.

Three-phase session:
  1. Baseline recording   (no stim, default 10 min)
  2. Sequential STDP stim (default 60 min)
  3. Post-stim recording  (no stim, default 5 min)

Hub electrode assignments loaded from hub_config.json (written by
select_hub_electrodes.py).  Stim routing loaded from stim_routing.json
(written by 02_select_electrodes.py).  Both live in --config-dir.

Usage:
    /home/sharf-lab/MaxLab/python/bin/python3 \\
      /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/hub_stim.py \\
      --config-dir PLAN_DIR/config \\
      --output-dir PLAN_DIR/recordings/YYYY-MM-DD/HHMMSS_stim_w0 \\
      [--baseline-duration 600] [--stim-duration 3600] [--post-duration 300] \\
      [--amplitude-mv 600] [--phase-us 200] [--delta-t-ms 10] [--gap-ms 150]
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from experiment_lib import (
    prepare_hardware,
    load_and_route,
    connect_stim_electrodes,
    recording_session,
    fire_pulse,
)
import manifest as manifest_mod

WELLS         = [0]       # MaxOne — single well
CFG_FILE      = "selected_electrodes.cfg"
HUB_CFG_FILE  = "hub_config.json"


# ── Load hub config ────────────────────────────────────────────────────

def load_hub_config(config_dir):
    """Read hub_config.json; return (hub_a_electrodes, hub_b_electrodes)."""
    path = os.path.join(config_dir, HUB_CFG_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"hub_config.json not found at {path}\n"
            "Run select_hub_electrodes.py first."
        )
    with open(path) as f:
        cfg = json.load(f)

    hub_a = [e["electrode"] for e in cfg["hub_a"]["electrodes"]]
    hub_b = [e["electrode"] for e in cfg["hub_b"]["electrodes"]]

    if not hub_a or not hub_b:
        raise RuntimeError(f"hub_config.json has empty hub — check {path}")

    print(f"  Hub A: {len(hub_a)} electrodes — {hub_a}")
    print(f"  Hub B: {len(hub_b)} electrodes — {hub_b}")
    return hub_a, hub_b


# ── Recording phases ───────────────────────────────────────────────────

def run_baseline(output_dir, duration_s):
    print(f"\n── BASELINE ({duration_s / 60:.0f} min, no stim) ──────────────────────")
    with recording_session(output_dir, name="baseline", wells=WELLS, kind="stim") as rec:
        print(f"  {rec.name}.raw.h5")
        time.sleep(duration_s)
    print("  Done")


def _fire_sequential(routing, electrodes, amplitude_mv, phase_us,
                     delta_t_s, hub_label, cycle, recording):
    """Fire electrodes one at a time, delta_t_s apart (STDP induction).

    Each consecutive pair fires within the LTP window.  Electrode order
    is the order supplied — hub_config.json stores centre-out, so the
    default produces a divergent activation wave from hub centre.
    """
    for i, elec in enumerate(electrodes):
        fire_pulse(
            routing,
            electrodes=[elec],
            amplitude_mv=amplitude_mv,
            phase_us=phase_us,
            polarity="positive_first",
            label=f"{hub_label}_{cycle}_e{i}",
            recording=recording,
        )
        time.sleep(delta_t_s)


def run_sequential_stim(output_dir, routing, hub_a, hub_b,
                        duration_s, amplitude_mv, phase_us,
                        delta_t_s, gap_s):
    """Sequential STDP: fire hub A electrodes in order → gap → hub B in order → gap."""
    hub_sweep_s = len(hub_a) * delta_t_s   # wall-clock for one hub sequence
    cycle_s     = 2 * (hub_sweep_s + gap_s)
    n_est       = int(duration_s / cycle_s)
    hz          = 1.0 / cycle_s

    print(f"\n── SEQUENTIAL STDP STIM ({duration_s / 60:.0f} min) ─────────────────────")
    print(f"  {amplitude_mv} mV, {phase_us} µs")
    print(f"  Δt = {delta_t_s * 1000:.0f} ms between electrodes within hub")
    print(f"  Hub sweep: {hub_sweep_s * 1000:.0f} ms  |  inter-hub gap: {gap_s * 1000:.0f} ms")
    print(f"  Cycle: {cycle_s * 1000:.0f} ms  (~{hz:.2f} Hz per hub)  |  est. cycles: {n_est}")
    print(f"  Hub A order (centre → periphery): {hub_a}")
    print(f"  Hub B order (centre → periphery): {hub_b}")

    with recording_session(output_dir, name="stim", wells=WELLS, kind="stim") as rec:
        print(f"  {rec.name}.raw.h5")
        t_end = time.time() + duration_s
        cycle = 0

        while time.time() < t_end:
            # ── Hub A: sequential sweep ───────────────────────────────
            _fire_sequential(routing, hub_a, amplitude_mv, phase_us,
                             delta_t_s, "A", cycle, rec)
            time.sleep(gap_s)

            if time.time() >= t_end:
                break

            # ── Hub B: sequential sweep ───────────────────────────────
            _fire_sequential(routing, hub_b, amplitude_mv, phase_us,
                             delta_t_s, "B", cycle, rec)
            time.sleep(gap_s)
            cycle += 1

    print(f"  {cycle} full cycles delivered")


def run_post_stim(output_dir, duration_s):
    print(f"\n── POST-STIM ({duration_s / 60:.0f} min, no stim) ─────────────────────")
    with recording_session(output_dir, name="post_stim", wells=WELLS, kind="stim") as rec:
        print(f"  {rec.name}.raw.h5")
        time.sleep(duration_s)
    print("  Done")


# ── Main ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Hub-induction daily co-activation stim session"
    )
    parser.add_argument("--config-dir", required=True,
                        help="Directory with hub_config.json, selected_electrodes.cfg, "
                             "stim_routing.json (output of select_hub_electrodes.py)")
    parser.add_argument("--output-dir", required=True,
                        help="Session output directory (recordings + manifest)")
    parser.add_argument("--baseline-duration", type=float, default=600.0,
                        help="Baseline recording seconds (default: 600 = 10 min)")
    parser.add_argument("--stim-duration", type=float, default=3600.0,
                        help="Total co-activation session seconds (default: 3600 = 1 hr)")
    parser.add_argument("--post-duration", type=float, default=300.0,
                        help="Post-stim recording seconds (default: 300 = 5 min)")
    parser.add_argument("--amplitude-mv", type=float, default=600.0,
                        help="Biphasic pulse amplitude in mV (default: 600)")
    parser.add_argument("--phase-us", type=float, default=200.0,
                        help="Pulse phase width in µs (default: 200)")
    parser.add_argument("--delta-t-ms", type=float, default=10.0,
                        help="Inter-electrode delay within each hub in ms (default: 10 — "
                             "within the STDP LTP window of ~5–20 ms)")
    parser.add_argument("--gap-ms", type=float, default=150.0,
                        help="Recovery gap between hub A and hub B sequences in ms "
                             "(default: 150 — prevents cross-hub STDP)")
    args = parser.parse_args()

    config_dir = os.path.abspath(args.config_dir)
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    delta_t_s = args.delta_t_ms / 1000.0
    gap_s     = args.gap_ms / 1000.0

    # ── Load hub electrode lists ───────────────────────────────────────
    print("Loading hub config...")
    hub_a, hub_b = load_hub_config(config_dir)

    # ── Hardware init ──────────────────────────────────────────────────
    cfg_path = os.path.join(config_dir, CFG_FILE)
    if not os.path.exists(cfg_path):
        raise FileNotFoundError(
            f"selected_electrodes.cfg not found at {cfg_path}"
        )
    print(f"\nInitialising hardware...")
    prepare_hardware(WELLS)
    load_and_route(cfg_path, WELLS)
    routing = connect_stim_electrodes(config_dir)
    print(f"  Routing: {len(routing)} stim electrodes wired")

    t0 = time.perf_counter()

    # ── Three-phase session ────────────────────────────────────────────
    run_baseline(output_dir, args.baseline_duration)
    run_sequential_stim(output_dir, routing, hub_a, hub_b,
                        args.stim_duration, args.amplitude_mv,
                        args.phase_us, delta_t_s, gap_s)
    run_post_stim(output_dir, args.post_duration)

    total = time.perf_counter() - t0
    print(f"\nSession complete — {total / 60:.1f} min total")
    print(f"Output: {output_dir}")
    print(f"  baseline.raw.h5    — intrinsic activity pre-stim")
    print(f"  stim.raw.h5        — co-activation session (stim events in manifest)")
    print(f"  post_stim.raw.h5   — intrinsic activity post-stim")


if __name__ == "__main__":
    main()
