"""Worker subprocess for the VRAM-eater stress test.

Allocates GPU memory in 1 GB chunks via ``torch.full(..., 1,
dtype=torch.uint8, device='cuda')``. The non-zero fill forces the
CUDA driver to actually commit the pages; cuMemAlloc + a kernel
write is unambiguously physical.

A ``GpuMemoryWatchdog`` polls VRAM via pynvml every 0.25 s. When
the watchdog trips it fires ``_thread.interrupt_main`` and falls
back to ``os._exit(1)`` if the main thread (busy in a CUDA C
extension) doesn't yield within the documented grace period.

Usage:
    python _vram_eater_worker.py <abort_pct> <max_gb>
"""
import os
import sys
import time

import torch

from spikelab.spike_sorting.guards import GpuMemoryWatchdog
from spikelab.spike_sorting.guards._inactivity import (
    make_in_process_kill_callback,
)


def _read_vram_gb() -> tuple[float, float]:
    """Return (used_gb, total_gb) for cuda:0."""
    free, total = torch.cuda.mem_get_info(0)
    used_gb = (total - free) / 1024**3
    total_gb = total / 1024**3
    return used_gb, total_gb


def main() -> int:
    abort_pct = float(sys.argv[1])
    max_gb = float(sys.argv[2])

    print(f"[worker pid={os.getpid()}] starting", flush=True)
    print(f"[worker] abort_pct={abort_pct} max_gb={max_gb}", flush=True)
    used_gb, total_gb = _read_vram_gb()
    print(f"[worker] starting VRAM: {used_gb:.2f} / {total_gb:.1f} GB "
          f"({100 * used_gb / total_gb:.1f}%)", flush=True)

    wd = GpuMemoryWatchdog(
        device_index=0,
        warn_pct=max(1.0, abort_pct - 5.0),
        abort_pct=abort_pct,
        poll_interval_s=0.25,
        kill_grace_s=2.0,
        warn_temp_c=None,    # disable thermal trip — we're testing VRAM
        abort_temp_c=None,
        monitor_throttle_reasons=False,
    )
    wd.register_kill_callback(
        make_in_process_kill_callback(
            interrupt_grace_s=1.0,
            sorter="vram_eater",
        )
    )

    CHUNK_GB = 1.0
    chunk_elems = int(CHUNK_GB * 1024**3)  # 1 byte per uint8 element
    allocs: list[torch.Tensor] = []
    allocated_gb = 0.0

    with wd:
        try:
            while allocated_gb < max_gb:
                # uint8 + non-zero fill: 1 GB per chunk, forced commit
                # because torch.full writes the value to every element.
                chunk = torch.full(
                    (chunk_elems,), 1, dtype=torch.uint8, device="cuda"
                )
                # Synchronize so the allocation is observable to pynvml
                # *before* the next loop iteration runs and the watchdog
                # polls VRAM.
                torch.cuda.synchronize()
                allocs.append(chunk)
                allocated_gb += CHUNK_GB
                used_gb, _ = _read_vram_gb()
                print(f"[worker] +{CHUNK_GB:.1f} GB -> "
                      f"{allocated_gb:.1f} GB allocated; "
                      f"VRAM in-use: {used_gb:.1f} GB "
                      f"({100 * used_gb / total_gb:.1f}%)", flush=True)
                time.sleep(0.5)
        except KeyboardInterrupt:
            used_gb, _ = _read_vram_gb()
            print(f"[worker] received KeyboardInterrupt at "
                  f"{allocated_gb:.1f} GB allocated, "
                  f"VRAM in-use: {used_gb:.1f} GB "
                  f"({100 * used_gb / total_gb:.1f}%)", flush=True)
            return 1
    print(f"[worker] reached max_gb={max_gb} without trip — watchdog failed",
          flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
