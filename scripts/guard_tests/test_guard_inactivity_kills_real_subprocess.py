"""Stress test #8a: LogInactivityWatchdog kills a real subprocess.

Spawns a long-sleeping ``python -c "time.sleep(60)"`` subprocess,
attaches a ``LogInactivityWatchdog`` to a log file we never modify
(passing the subprocess as ``popen``), and verifies the watchdog:

1. Trips on log inactivity within tolerance.
2. Calls ``popen.terminate()`` (SIGTERM on Unix), which Python's
   bare ``time.sleep`` honours — so the sleeper exits with a
   negative returncode.
3. Falls back to ``popen.kill()`` (SIGKILL) when terminate is
   ignored.

Test #3 wired up the trip mechanism with a stub kill_callback —
no real process died. This test exercises the *kill path*: the
watchdog's job under real KS2-local execution is to ``terminate()``
a runaway sorter subprocess, so we verify it can do that against
a real PID.

Two variants run in sequence: a SIGTERM-honouring sleeper (cleanly
killed by ``terminate``) and a SIGTERM-ignoring sleeper (forced
out by ``kill``).
"""
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from spikelab.spike_sorting.guards import LogInactivityWatchdog

LOG_DIR = Path("/tmp/inactivity_kill_real_subprocess")


def _run_one(label: str, ignore_sigterm: bool) -> bool:
    print("=" * 60)
    print(f"Variant: {label}")
    print("=" * 60)

    if LOG_DIR.exists():
        shutil.rmtree(LOG_DIR)
    LOG_DIR.mkdir(parents=True)
    log_path = LOG_DIR / "sort.log"
    log_path.write_text("starting...\n", encoding="utf-8")

    # Build the sleeper. The SIGTERM-ignoring variant installs
    # SIG_IGN for SIGTERM so the watchdog must escalate to SIGKILL.
    if ignore_sigterm:
        sleeper_code = (
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(60)\n"
        )
    else:
        sleeper_code = "import time; time.sleep(60)"

    sleeper = subprocess.Popen([sys.executable, "-c", sleeper_code])
    print(f"Sleeper PID: {sleeper.pid}  (ignore_sigterm={ignore_sigterm})")

    INACTIVITY_S = 2.0
    KILL_GRACE_S = 0.5  # short grace so the SIGKILL fallback fires fast

    watchdog = LogInactivityWatchdog(
        log_path=log_path,
        popen=sleeper,
        inactivity_s=INACTIVITY_S,
        sorter="kilosort2",
        poll_interval_s=0.25,
        kill_grace_s=KILL_GRACE_S,
    )

    t0 = time.time()
    with watchdog:
        try:
            rc = sleeper.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            print("FAIL: sleeper still alive after 10 s — watchdog never killed it")
            sleeper.kill()
            sleeper.wait()
            return False
    elapsed = time.time() - t0

    print(f"Sleeper exit code: {rc}")
    print(f"Wall time:         {elapsed:.2f} s")
    print(f"watchdog.tripped() = {watchdog.tripped()}")

    if not watchdog.tripped():
        print("FAIL: watchdog did not trip")
        return False

    # Unix: popen returncode is the negative of the signal that
    # killed it. SIGTERM=15, SIGKILL=9.
    expected_signals = (-signal.SIGKILL,) if ignore_sigterm \
        else (-signal.SIGTERM, -signal.SIGKILL)
    if rc not in expected_signals:
        print(f"FAIL: expected returncode in {expected_signals}, got {rc}")
        return False

    rc_name = {
        -signal.SIGTERM: "SIGTERM (graceful)",
        -signal.SIGKILL: "SIGKILL (forced)",
    }.get(rc, str(rc))
    print(f"PASS: sleeper killed by {rc_name} within "
          f"{elapsed:.1f} s of inactivity-watchdog trip")
    return True


def main() -> int:
    n_pass = 0
    if _run_one("subprocess honours SIGTERM (terminate path)",
                ignore_sigterm=False):
        n_pass += 1
    print()
    if _run_one("subprocess ignores SIGTERM (kill fallback path)",
                ignore_sigterm=True):
        n_pass += 1
    print()
    print("=" * 60)
    print(f"Results: {n_pass}/2 variants passed")
    print("=" * 60)
    return 0 if n_pass == 2 else 1


if __name__ == "__main__":
    sys.exit(main())
