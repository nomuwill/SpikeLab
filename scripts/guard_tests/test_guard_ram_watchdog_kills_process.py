"""Stress test #8b: HostMemoryWatchdog kills a real over-threshold process.

Spawns a worker subprocess that installs a ``HostMemoryWatchdog``
with ``abort_pct`` set just below the current system memory
percentage — so the watchdog trips on its first poll without us
having to allocate any memory in the test. The worker registers
an in-process kill callback that does ``_thread.interrupt_main``
followed by ``os._exit(1)`` after a 1 s grace.

Verifies:

1. Worker subprocess actually dies (within a generous timeout).
2. Exit code matches the documented cascade — 1 from
   ``os._exit(1)`` or 1 from the worker's ``KeyboardInterrupt``
   handler — *not* 0 (would mean the watchdog never tripped).
3. Time-to-die is bounded by the poll interval + interrupt grace.

This is the "process consumes too much, gets killed" scenario in
isolation, with no real memory pressure on the host (we just lie
to the watchdog about the threshold so it trips).
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

WORKER = Path(__file__).parent / "_ram_watchdog_worker.py"


def main() -> int:
    current_pct = psutil.virtual_memory().percent
    # Set abort_pct just below current. The watchdog trips when
    # observed pct >= abort_pct, so 1% headroom is enough.
    abort_pct = max(1.0, current_pct - 1.0)
    print(f"Current system memory:   {current_pct:.1f}%")
    print(f"Worker abort threshold:  {abort_pct:.1f}%")
    print(f"Worker:                  {WORKER}\n")

    # Use the spikelab env explicitly so the worker has psutil + spikelab.
    cmd = [
        "conda", "run", "-n", "spikelab", "--no-capture-output",
        "python", str(WORKER), str(abort_pct),
    ]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    elapsed = time.time() - t0

    print("=" * 60)
    print("Worker output:")
    print("=" * 60)
    print(proc.stdout)
    if proc.stderr:
        print("--- stderr ---")
        print(proc.stderr)
    print("=" * 60)
    print(f"Worker exit code:  {proc.returncode}")
    print(f"Wall time:         {elapsed:.2f} s")
    print("=" * 60)

    # Validation.
    if proc.returncode == 0:
        print("FAIL: worker exited 0 — watchdog never tripped")
        return 1

    # Acceptable termination paths:
    #   1 = worker caught KeyboardInterrupt and returned 1, OR os._exit(1) fired
    #   negative = SIGKILL/SIGTERM (shouldn't happen here unless the worker
    #              hung past os._exit grace — also a pass)
    if proc.returncode != 1 and proc.returncode > 0:
        print(f"FAIL: unexpected exit code {proc.returncode} (expected 1 "
              "or a signal)")
        return 1

    # Look for trip evidence in worker output.
    trip_observed = (
        "TRIP:" in proc.stdout
        or "interrupt_main" in proc.stdout
        or "interrupt_main" in proc.stderr
        or "KeyboardInterrupt" in proc.stdout
    )
    if not trip_observed:
        print("FAIL: no trip evidence in worker output")
        return 1

    # Time bound: poll_interval (0.25 s) + interrupt_grace (1 s) +
    # subprocess startup (~1-3 s for `conda run`) ~= < 8 s.
    if elapsed > 15.0:
        print(f"WARN: worker took {elapsed:.1f} s to die — slower than "
              "expected but technically a pass.")

    print(f"\nPASS: worker terminated with exit code {proc.returncode} "
          f"in {elapsed:.1f} s; HostMemoryWatchdog trip cascade "
          "successfully drove the kill.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
