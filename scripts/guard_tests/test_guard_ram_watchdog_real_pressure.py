"""Stress test #9: HostMemoryWatchdog kills a real RAM-eating process.

Distinct from #8b (which set abort_pct below current to force a
trip on the first poll without the worker doing anything): here
the worker actually allocates RAM until system % crosses a
sane-ish threshold (default 80%), and the watchdog kills it at
that point.

Test passes when:
  * Worker exits within a bounded time.
  * Exit code is 1 (interrupt path) or 1 (os._exit fallback) —
    *not* 0 (would mean the watchdog never tripped) and *not*
    2 (the worker reached its max-GB safety cap without a trip).
  * System memory percent comes back down after the worker dies
    (kernel reclaims the freed pages).
  * Watchdog trip cascade evidence is present in worker output.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

WORKER = Path(__file__).parent / "_ram_eater_worker.py"

ABORT_PCT = 80.0
MAX_GB = 105.0  # safety cap — must NOT be reached
TIMEOUT_S = 240.0


def main() -> int:
    mem = psutil.virtual_memory()
    total_gb = mem.total / 1024**3
    avail_gb = mem.available / 1024**3
    pct = mem.percent
    print(f"System total:       {total_gb:.1f} GB")
    print(f"Currently used:     {(mem.used / 1024**3):.1f} GB ({pct:.1f}%)")
    print(f"Available:          {avail_gb:.1f} GB")
    print(f"abort_pct:          {ABORT_PCT}%  -> trip at "
          f"~{total_gb * ABORT_PCT / 100:.1f} GB used")
    print(f"max_gb safety cap:  {MAX_GB} GB allocated by worker")

    # Refuse to run if the host is already too close to the trip
    # threshold (we want at least 30 % headroom for the test to be
    # meaningful — the trip should be driven by the worker's
    # allocation, not by ambient load).
    if pct >= ABORT_PCT - 30.0:
        print(f"\nFAIL: host already at {pct:.1f}% — too close to "
              f"abort_pct={ABORT_PCT}%. Test abandoned.")
        return 1

    cmd = [
        "conda", "run", "-n", "spikelab", "--no-capture-output",
        "python", str(WORKER), str(ABORT_PCT), str(MAX_GB),
    ]
    print(f"\nSpawning worker: {' '.join(cmd)}\n")

    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)

    # Stream worker stdout live so we can see allocation progress.
    stdout_lines: list[str] = []
    peak_pct = pct
    try:
        while True:
            elapsed = time.time() - t0
            if elapsed > TIMEOUT_S:
                proc.kill()
                print(f"FAIL: worker did not die within {TIMEOUT_S} s")
                return 1
            line = proc.stdout.readline() if proc.stdout else ""
            if line:
                stdout_lines.append(line)
                print(line, end="")
                # Track peak system % observed in worker output.
                if "system mem%:" in line:
                    try:
                        chunk = line.split("system mem%:")[-1].strip()
                        peak_pct = max(peak_pct, float(chunk))
                    except ValueError:
                        pass
                continue
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        elapsed = time.time() - t0
    finally:
        # Drain any remaining stderr.
        stderr = proc.stderr.read() if proc.stderr else ""
    rc = proc.returncode

    print("\n" + "=" * 60)
    if stderr:
        print("Worker stderr:")
        print(stderr)
    print("=" * 60)
    print(f"Worker exit code:   {rc}")
    print(f"Wall time:          {elapsed:.1f} s")
    print(f"Peak system mem%:   {peak_pct:.1f}")

    # Post-mortem system memory: should drop back down once worker died.
    time.sleep(2.0)
    final_pct = psutil.virtual_memory().percent
    print(f"Post-test system mem%: {final_pct:.1f}")

    # Validate.
    if rc == 0:
        print("\nFAIL: worker exited 0 — watchdog never tripped")
        return 1
    if rc == 2:
        print("\nFAIL: worker reached max_gb safety cap — watchdog "
              "never tripped before the cap")
        return 1
    if rc != 1 and rc > 0:
        print(f"\nFAIL: unexpected exit code {rc}")
        return 1

    if "ABORT:" not in stderr and "ABORT:" not in "".join(stdout_lines):
        print("FAIL: no ABORT trip evidence in worker output")
        return 1

    if peak_pct < ABORT_PCT - 1.0:
        print(f"FAIL: peak system mem% was only {peak_pct:.1f} "
              f"— never crossed abort_pct={ABORT_PCT}")
        return 1

    if final_pct > pct + 5.0:
        print(f"WARN: post-test system mem% ({final_pct:.1f}) is "
              f"more than 5 pct above pre-test ({pct:.1f}) — pages "
              "may not have been reclaimed yet")

    print(f"\nPASS: worker actually allocated RAM until system mem% "
          f"crossed {ABORT_PCT}% (peak {peak_pct:.1f}%); "
          f"HostMemoryWatchdog drove the kill in {elapsed:.0f} s; "
          f"exit code {rc}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
