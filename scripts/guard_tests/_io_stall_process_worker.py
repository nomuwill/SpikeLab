"""Worker subprocess for the *process-mode* IOStallWatchdog test.

Same shape as ``_io_stall_worker.py`` (write 200 MB, then stall),
but the watchdog is constructed in **process mode** with the
worker's own PID. The intent is to verify the watchdog trips on
this worker's I/O stall even when other processes are noisy on
the same device.

Usage:
    python _io_stall_process_worker.py <stall_s>
"""
import os
import sys
import time
from pathlib import Path

from spikelab.spike_sorting.guards import IOStallWatchdog
from spikelab.spike_sorting.guards._inactivity import (
    make_in_process_kill_callback,
)

FOLDER = Path("/tmp/io_stall_process_worker")


def main() -> int:
    stall_s = float(sys.argv[1])
    FOLDER.mkdir(parents=True, exist_ok=True)
    target = FOLDER / "data.bin"
    print(f"[worker pid={os.getpid()}] stall_s={stall_s}", flush=True)

    wd = IOStallWatchdog(
        pids=[os.getpid()],
        include_descendants=True,
        stall_s=stall_s,
        poll_interval_s=0.5,
        kill_grace_s=2.0,
    )
    wd.register_kill_callback(
        make_in_process_kill_callback(
            interrupt_grace_s=1.0,
            sorter="io_stall_process_worker",
        )
    )

    with wd:
        # Phase 1: real, observable I/O
        chunk = b"\x01" * (4 * 1024 * 1024)
        n_chunks = 50
        print(f"[worker] phase 1: writing {n_chunks * 4} MB",
              flush=True)
        with open(target, "wb") as f:
            for i in range(n_chunks):
                f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
                if i % 10 == 0:
                    print(f"[worker]   wrote {(i + 1) * 4} MB", flush=True)
        print(f"[worker] phase 1 done", flush=True)

        # Phase 2: stall — this process is no longer doing I/O.
        # Even with ambient device I/O from a noisy second process,
        # the per-process counters for THIS pid stay flat.
        print(f"[worker] phase 2: stalling — sleeping for "
              f"{stall_s + 10:.1f} s; expect trip after "
              f"{stall_s:.1f} s.", flush=True)
        try:
            time.sleep(stall_s + 10)
        except KeyboardInterrupt:
            print(f"[worker] received KeyboardInterrupt — watchdog "
                  f"tripped as expected", flush=True)
            return 1

    print(f"[worker] reached end without trip — process-mode failed",
          flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
