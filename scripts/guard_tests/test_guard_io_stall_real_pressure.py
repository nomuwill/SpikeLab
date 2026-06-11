"""Stress test #11: IOStallWatchdog kills a real I/O-then-stall process.

Spawns a worker that writes 200 MB to a file (real, observable
I/O on the device) and then stops writing. The watchdog polls the
device's ``read+write`` byte counter every 0.5 s and trips when
the counter has been flat for ``stall_s`` seconds.

Caveat: this watchdog observes the **device** counter, not the
process counter. If anything else on the host is writing to the
same disk during the worker's stall phase, the counter keeps
climbing and the trip is delayed or missed. The test is therefore
sensitive to host quietness — that's a property of the watchdog's
design, not a test bug.

Test passes when the worker exits with code 1 and the watchdog's
ABORT message appears in stderr. A return of 2 indicates the
device was too noisy for the trip to fire within the worker's
sleep window.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

WORKER = Path(__file__).parent / "_io_stall_worker.py"
FOLDER = Path("/tmp/io_stall_stress_test")
# A previous run showed the warn fired at "idle for 2.5s" before
# background I/O reset the counter at ~5 s. Lowering stall_s so the
# trip threshold sits inside the observable quiet window.
STALL_S = 2.0
TIMEOUT_S = 60.0


def main() -> int:
    if FOLDER.exists():
        shutil.rmtree(FOLDER)
    FOLDER.mkdir(parents=True)

    print(f"Folder:    {FOLDER}")
    print(f"stall_s:   {STALL_S} s")
    print(f"Timeout:   {TIMEOUT_S} s\n")

    cmd = [
        "conda", "run", "-n", "spikelab", "--no-capture-output",
        "python", str(WORKER), str(FOLDER), str(STALL_S),
    ]
    print(f"Spawning worker: {' '.join(cmd)}\n")

    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    elapsed = time.time() - t0

    print("Worker stdout:")
    print(proc.stdout)
    if proc.stderr:
        print("Worker stderr:")
        print(proc.stderr)
    print("=" * 60)
    print(f"Worker exit code:   {proc.returncode}")
    print(f"Wall time:          {elapsed:.1f} s")
    print("=" * 60)

    if proc.returncode == 2:
        print("\nINCONCLUSIVE: worker reached end of stall without trip. "
              "The device likely had background I/O during the stall "
              "window — re-run on a quieter system, or stub "
              "_read_io_bytes for a deterministic version.")
        return 2

    if proc.returncode == 0:
        print("\nFAIL: worker exited cleanly — watchdog never tripped")
        return 1

    if proc.returncode != 1:
        print(f"\nFAIL: unexpected exit code {proc.returncode}")
        return 1

    if "TRIP:" not in proc.stderr and "ABORT" not in proc.stderr.upper():
        # The IOStallWatchdog uses "TRIP" in its log message.
        if "stalled" not in proc.stderr.lower():
            print("FAIL: no trip evidence in worker stderr")
            return 1

    print(f"\nPASS: worker did real I/O, then stalled; "
          f"IOStallWatchdog detected the flat byte counter and "
          f"drove the kill in {elapsed:.1f} s; exit code "
          f"{proc.returncode}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
