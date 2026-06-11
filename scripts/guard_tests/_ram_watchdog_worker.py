"""Worker subprocess for the host-RAM watchdog stress test.

Installs a ``HostMemoryWatchdog`` whose ``abort_pct`` is set just
below the current system memory percentage — so the watchdog
trips on its first poll. Registers an in-process kill callback
that calls ``_thread.interrupt_main`` then ``os._exit(1)`` after
a short grace period.

Sleeps in the main thread; the watchdog kills the process via the
documented cascade. Parent verifies the exit code and timing.

Usage:
    python _ram_watchdog_worker.py <abort_pct>
"""
import sys
import time

from spikelab.spike_sorting.guards import HostMemoryWatchdog
from spikelab.spike_sorting.guards._inactivity import (
    make_in_process_kill_callback,
)


def main() -> int:
    abort_pct = float(sys.argv[1])
    warn_pct = max(0.5, abort_pct - 0.5)

    print(f"[worker] abort_pct={abort_pct} warn_pct={warn_pct}", flush=True)

    wd = HostMemoryWatchdog(
        warn_pct=warn_pct,
        abort_pct=abort_pct,
        poll_interval_s=0.25,
        kill_grace_s=2.0,  # seconds between interrupt_main and os._exit
    )
    wd.register_kill_callback(
        make_in_process_kill_callback(
            interrupt_grace_s=1.0,
            sorter="ram_watchdog_worker",
        )
    )

    with wd:
        try:
            time.sleep(60)
        except KeyboardInterrupt:
            print("[worker] received KeyboardInterrupt from interrupt_main",
                  flush=True)
            # Surface as exit code 1 so the parent sees a clean failure.
            return 1
    print("[worker] watchdog never tripped — exiting normally", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
