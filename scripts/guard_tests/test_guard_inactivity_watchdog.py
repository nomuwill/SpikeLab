"""Stress test #3: sorter-log inactivity watchdog fires on a stale log.

Creates a ``LogInactivityWatchdog`` with a 2 s timeout watching a log
file that we deliberately never modify. The watchdog's polling loop
should observe no mtime/size change, fire the kill callback, and
flip ``tripped()`` → True. ``make_error()`` then produces the
classified ``SorterTimeoutError`` the production code routes back
to the caller.

Doesn't run a real sort — exercises the watchdog mechanism in
isolation. The integration with ks2_runner / Docker is exercised
elsewhere; this test just proves the mechanism works.
"""
import shutil
import sys
import threading
import time
from pathlib import Path

from spikelab.spike_sorting._exceptions import SorterTimeoutError
from spikelab.spike_sorting.guards import LogInactivityWatchdog

LOG_DIR = Path("/tmp/inactivity_watchdog_stress_test")


def main() -> int:
    if LOG_DIR.exists():
        shutil.rmtree(LOG_DIR)
    LOG_DIR.mkdir(parents=True)
    log_path = LOG_DIR / "sort.log"
    log_path.write_text("starting...\n", encoding="utf-8")

    callback_fired = threading.Event()

    def _kill_callback() -> None:
        callback_fired.set()

    INACTIVITY_S = 2.0
    POLL_S = 0.25

    print(f"Log path:    {log_path}")
    print(f"Inactivity:  {INACTIVITY_S} s")
    print(f"Poll:        {POLL_S} s")
    print("Will leave log untouched and wait for the watchdog to trip...\n")

    watchdog = LogInactivityWatchdog(
        log_path=log_path,
        popen=None,
        inactivity_s=INACTIVITY_S,
        sorter="kilosort2",
        poll_interval_s=POLL_S,
        kill_grace_s=0.5,
        kill_callback=_kill_callback,
    )

    t0 = time.time()
    with watchdog:
        # Sleep long enough for the watchdog to trip + invoke callback.
        # 2 s inactivity + 0.5 s kill grace + 0.5 s margin = 3 s.
        if not callback_fired.wait(timeout=8.0):
            print(f"FAIL: kill callback never fired (waited 8 s)")
            return 1
        elapsed = time.time() - t0
        print(f"Kill callback fired after {elapsed:.2f} s")

    if not watchdog.tripped():
        print("FAIL: watchdog.tripped() is False after the callback fired")
        return 1
    print(f"watchdog.tripped() = True")

    err = watchdog.make_error()
    if not isinstance(err, SorterTimeoutError):
        print(f"FAIL: make_error returned {type(err).__name__}, "
              "expected SorterTimeoutError")
        return 1
    print(f"\nClassified error: {type(err).__name__}")
    print(f"  sorter:        {err.sorter}")
    print(f"  inactivity_s:  {err.inactivity_s}")
    print(f"  log_path:      {err.log_path}")
    print(f"  message:       {err}")

    if elapsed < INACTIVITY_S:
        print(f"\nFAIL: trip fired in {elapsed:.2f} s, less than the "
              f"{INACTIVITY_S} s tolerance — the watchdog is over-eager")
        return 1
    if elapsed > INACTIVITY_S + 2.0:
        print(f"\nWARN: trip fired in {elapsed:.2f} s, more than 2 s past the "
              f"{INACTIVITY_S} s tolerance — loose but not wrong")

    print("\nPASS: inactivity watchdog tripped within tolerance and produced "
          "the right classified error.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
