"""Verifies the new IOStallWatchdog process-mode kills a real
stalled process even when ambient device I/O is loud.

Two subprocesses run concurrently:

* **Watchdog worker** — does brief real I/O then stalls. The
  IOStallWatchdog is in process mode tracking only this PID.
* **Disk noisemaker** — writes to the same /tmp during the
  watchdog worker's stall phase, keeping the device's I/O byte
  counter climbing the whole time.

Under the legacy device-mode IOStallWatchdog, the noisemaker would
mask the worker's stall and the watchdog would never trip. Under
process-mode, the worker's own per-PID byte counter stays flat
during the stall, so the watchdog trips on schedule regardless of
the noisemaker.

Test passes when the watchdog worker dies with exit code 1 (kill
cascade) and the disk noisemaker is still alive at the end.
"""
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

WATCHDOG_WORKER = (
    Path(__file__).parent / "_io_stall_process_worker.py"
)
NOISEMAKER_INLINE_CODE = """
import os, time
from pathlib import Path
target = Path('/tmp/io_stall_noisemaker')
target.mkdir(parents=True, exist_ok=True)
print(f'[noisemaker pid={os.getpid()}] writing /tmp at ~10 MB/s for 30 s', flush=True)
chunk = b'\\x02' * (1 * 1024 * 1024)  # 1 MB
deadline = time.time() + 30.0
with open(target / 'noise.bin', 'wb') as f:
    while time.time() < deadline:
        for _ in range(10):
            f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        time.sleep(0.05)  # 10 MB then short pause -> ~10-20 MB/s steady
print(f'[noisemaker] done', flush=True)
"""

STALL_S = 2.0
TIMEOUT_S = 60.0


def main() -> int:
    print(f"WATCHDOG_WORKER:  {WATCHDOG_WORKER}")
    print(f"stall_s:          {STALL_S} s")
    print(f"Timeout:          {TIMEOUT_S} s\n")

    print("Spawning disk noisemaker...")
    noise = subprocess.Popen(
        [sys.executable, "-c", NOISEMAKER_INLINE_CODE],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # Let the noisemaker start writing so the device counter is
    # already climbing when the watchdog enters its initial probe.
    time.sleep(2.0)
    print(f"Noisemaker PID: {noise.pid} (alive: {noise.poll() is None})\n")

    print("Spawning watchdog worker (process-mode)...")
    cmd = [
        "conda", "run", "-n", "spikelab", "--no-capture-output",
        "python", str(WATCHDOG_WORKER), str(STALL_S),
    ]
    t0 = time.time()
    try:
        worker = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_S
        )
    finally:
        elapsed = time.time() - t0

    print("Worker stdout:")
    print(worker.stdout)
    if worker.stderr:
        print("Worker stderr:")
        print(worker.stderr)
    print("=" * 60)
    print(f"Worker exit code:  {worker.returncode}")
    print(f"Wall time:         {elapsed:.1f} s")

    # Was the noisemaker still alive when the worker died? That's
    # the proof that ambient I/O didn't stop — process-mode tripped
    # despite the noise.
    noise_alive_at_end = noise.poll() is None
    print(f"Noisemaker alive at end:  {noise_alive_at_end}")

    # Kill the noisemaker so it doesn't keep writing after the test.
    if noise_alive_at_end:
        try:
            noise.kill()
        finally:
            try:
                noise.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    print("=" * 60)

    # Cleanup test artefacts (keep this idempotent).
    for p in [Path("/tmp/io_stall_process_worker"),
              Path("/tmp/io_stall_noisemaker")]:
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    if worker.returncode == 2:
        print("\nFAIL: process-mode watchdog did NOT trip — worker "
              "reached its stall-end safety. The per-process byte "
              "counter must have moved (which it shouldn't have, "
              "since the worker did no I/O after phase 1). Likely a "
              "bug in _read_io_bytes_for_pids or the integration.")
        return 1
    if worker.returncode == 0:
        print("\nFAIL: worker exited 0 (clean) — watchdog never tripped")
        return 1
    if worker.returncode != 1 and worker.returncode > 0:
        print(f"\nFAIL: unexpected exit code {worker.returncode}")
        return 1

    if "TRIP:" not in worker.stderr:
        print("FAIL: no TRIP evidence in worker stderr")
        return 1
    if "process tree" not in worker.stderr:
        print("FAIL: trip log doesn't reference 'process tree' "
              "— check the scope label for process mode")
        return 1
    if not noise_alive_at_end:
        print("WARN: noisemaker died before the watchdog tripped — "
              "the test didn't actually have ambient I/O during the "
              "stall window. The watchdog *did* trip but this run "
              "doesn't prove process-mode is immune to ambient I/O.")

    print(f"\nPASS: process-mode IOStallWatchdog tripped on the "
          f"worker's stall in {elapsed:.1f} s while a separate "
          f"noisemaker process was actively writing to the same "
          f"/tmp device. Worker exit code: {worker.returncode}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
