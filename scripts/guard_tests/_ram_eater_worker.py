"""Worker subprocess for the RAM-eater stress test.

Allocates RAM in 1 GB chunks at ~1 GB/s. Each chunk is a numpy
``np.full(..., 1, dtype=uint8)`` array — explicitly written so the
kernel cannot defer the commit via overcommit / zero-page COW.
The chunks are appended to a list so the GC keeps them alive.

A ``HostMemoryWatchdog`` polls ``psutil.virtual_memory().percent``
every 0.5 s with a configurable ``abort_pct``. When the watchdog
trips it fires ``_thread.interrupt_main``; if the main thread is
still allocating (rather than respecting the KeyboardInterrupt)
the watchdog's grace expires and ``os._exit(1)`` terminates the
process.

Usage:
    python _ram_eater_worker.py <abort_pct> <max_gb>
"""
import os
import sys
import time

import numpy as np
import psutil

from spikelab.spike_sorting.guards import HostMemoryWatchdog
from spikelab.spike_sorting.guards._inactivity import (
    make_in_process_kill_callback,
)


def main() -> int:
    abort_pct = float(sys.argv[1])
    max_gb = float(sys.argv[2])

    print(f"[worker pid={os.getpid()}] starting", flush=True)
    print(f"[worker] abort_pct={abort_pct} max_gb={max_gb}", flush=True)
    start_pct = psutil.virtual_memory().percent
    print(f"[worker] starting system mem%: {start_pct:.1f}", flush=True)

    wd = HostMemoryWatchdog(
        warn_pct=max(1.0, abort_pct - 5.0),
        abort_pct=abort_pct,
        poll_interval_s=0.5,
        kill_grace_s=2.0,
    )
    wd.register_kill_callback(
        make_in_process_kill_callback(
            interrupt_grace_s=1.0,
            sorter="ram_eater",
        )
    )

    CHUNK_GB = 1.0
    chunk_bytes = int(CHUNK_GB * 1024**3)
    allocs: list[np.ndarray] = []
    allocated_gb = 0.0

    with wd:
        try:
            while allocated_gb < max_gb:
                # np.full with a non-zero fill writes every byte ->
                # forces physical commit (no zero-page COW deferral).
                chunk = np.full(chunk_bytes, 1, dtype=np.uint8)
                allocs.append(chunk)
                allocated_gb += CHUNK_GB
                pct = psutil.virtual_memory().percent
                print(f"[worker] +{CHUNK_GB:.1f} GB -> "
                      f"{allocated_gb:.1f} GB allocated; "
                      f"system mem%: {pct:.1f}",
                      flush=True)
                time.sleep(1.0)
        except KeyboardInterrupt:
            elapsed = "(unknown)"
            print(f"[worker] received KeyboardInterrupt at "
                  f"{allocated_gb:.1f} GB allocated, "
                  f"system mem%: {psutil.virtual_memory().percent:.1f}",
                  flush=True)
            return 1
    print(f"[worker] reached max_gb={max_gb} without trip — watchdog failed",
          flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
