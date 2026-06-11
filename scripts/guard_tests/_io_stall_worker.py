"""Worker subprocess for the IOStallWatchdog stress test.

Phases:

1. WRITE — write a 200 MB file in 4 MB chunks with ``fsync`` between
   chunks so the device's write counter climbs visibly. This proves
   the worker is doing real, observable I/O.
2. STALL — close the file and sleep. No more writes from the
   worker; if the system has no other writers on the device the
   ``disk_io_counters`` total stops climbing.

The IOStallWatchdog polls the folder's device's read+write byte
counter every 0.5 s and trips when the total has been stuck for
``stall_s`` seconds. On trip it fires the registered in-process
kill callback (``interrupt_main`` -> ``os._exit(1)`` after grace).

Note: this watchdog observes the **device** counter, not the
process counter. Background activity from anything else writing
to the same disk can prevent the trip — that's a limitation of
the watchdog's design (it's meant to catch a dead device / hung
NFS, not a stuck single process). Document any flakiness as a
test-environment issue, not a watchdog bug.

Usage:
    python _io_stall_worker.py <folder> <stall_s>
"""
import os
import sys
import time
from pathlib import Path

from spikelab.spike_sorting.guards import IOStallWatchdog
from spikelab.spike_sorting.guards._inactivity import (
    make_in_process_kill_callback,
)


def main() -> int:
    folder = Path(sys.argv[1])
    stall_s = float(sys.argv[2])

    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "io_stall_test.dat"
    print(f"[worker pid={os.getpid()}] folder={folder} stall_s={stall_s}",
          flush=True)

    wd = IOStallWatchdog(
        folder=folder,
        stall_s=stall_s,
        poll_interval_s=0.5,
        kill_grace_s=2.0,
    )
    wd.register_kill_callback(
        make_in_process_kill_callback(
            interrupt_grace_s=1.0,
            sorter="io_stall_worker",
        )
    )

    with wd:
        # Phase 1: write 200 MB in 4 MB chunks with fsync between.
        # This guarantees the byte counter climbs observably.
        chunk = b"\x01" * (4 * 1024 * 1024)
        n_chunks = 50
        print(f"[worker] phase 1: writing {n_chunks * 4} MB in 4 MB chunks",
              flush=True)
        with open(target, "wb") as f:
            for i in range(n_chunks):
                f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
                if i % 10 == 0:
                    print(f"[worker]   wrote {(i + 1) * 4} MB", flush=True)
        print(f"[worker] phase 1 done", flush=True)

        # Phase 2: stall — no more I/O from this process. The
        # watchdog should detect the device counter going flat.
        print(f"[worker] phase 2: stalling — sleeping for "
              f"{stall_s + 10:.1f} s; expect a trip after {stall_s:.1f} s "
              f"if the device is quiet otherwise.", flush=True)
        try:
            time.sleep(stall_s + 10)
        except KeyboardInterrupt:
            print(f"[worker] received KeyboardInterrupt after stall — "
                  f"watchdog tripped as expected", flush=True)
            return 1

    print(f"[worker] reached end without trip — device likely had "
          f"background I/O", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
