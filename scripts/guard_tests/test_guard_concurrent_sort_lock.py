"""Stress test #2: per-recording sort lock prevents concurrent sorts.

Forks a child that holds the lock for a few seconds, then has the
parent attempt to acquire the same lock. The parent should hit
``ConcurrentSortError`` immediately (no waiting / no retry storm).

Uses ``acquire_sort_lock`` directly rather than running two real
``sort_recording`` calls so the test stays under a second; the
production-path semantics are identical because both go through the
same context manager.
"""
import os
import shutil
import sys
import time
from multiprocessing import Process, Event
from pathlib import Path

from spikelab.spike_sorting._exceptions import ConcurrentSortError
from spikelab.spike_sorting.guards import acquire_sort_lock

INTER = Path("/tmp/sort_lock_stress_test_inter")


def _holder(folder: str, ready: Event, release: Event) -> None:
    """Acquire the lock, signal ``ready``, then wait for release."""
    with acquire_sort_lock(Path(folder)):
        ready.set()
        release.wait(timeout=10.0)


def main() -> int:
    if INTER.exists():
        shutil.rmtree(INTER)
    INTER.mkdir(parents=True)

    ready = Event()
    release = Event()
    holder = Process(target=_holder, args=(str(INTER), ready, release))
    holder.start()
    try:
        if not ready.wait(timeout=5.0):
            print("FAIL: holder process never signalled ready")
            holder.terminate()
            return 1
        print(f"Holder PID {holder.pid} acquired lock at {INTER}/.spikelab_sort.lock")

        # Now attempt to acquire from this process; expect immediate
        # ConcurrentSortError because the holder PID is alive on the
        # same host.
        t0 = time.time()
        try:
            with acquire_sort_lock(INTER):
                elapsed = time.time() - t0
                print(f"FAIL: parent unexpectedly acquired the lock after "
                      f"{elapsed:.3f} s")
                return 1
        except ConcurrentSortError as exc:
            elapsed = time.time() - t0
            print(f"PASS: ConcurrentSortError raised in {elapsed * 1000:.1f} ms")
            print(f"      message: {exc}")
        except Exception as exc:
            print(f"FAIL: expected ConcurrentSortError, got "
                  f"{type(exc).__name__}: {exc}")
            return 1

        # Bonus check: stale-lock reclaim. Kill the holder, leave its
        # lock file behind, and verify a new acquisition succeeds.
        release.set()
        holder.join(timeout=5.0)
        if holder.is_alive():
            holder.kill()
            holder.join()

        # The holder normally cleans up its own lock on exit; force a
        # stale state by re-creating a lock pointing at an obviously
        # dead PID.
        lock_path = INTER / ".spikelab_sort.lock"
        if lock_path.exists():
            print(f"\nLock cleaned up by holder: {not lock_path.exists()}")
        # Synthesise a stale lock from a PID that does not exist.
        dead_pid = 999999  # unlikely-to-be-alive PID
        import socket
        lock_path.write_text(
            '{"pid": %d, "hostname": "%s", "started_at": "1970-01-01T00:00:00"}\n'
            % (dead_pid, socket.gethostname()),
            encoding="utf-8",
        )
        print(f"Synthesised stale lock from dead PID {dead_pid}")

        t0 = time.time()
        try:
            with acquire_sort_lock(INTER) as _:
                elapsed = time.time() - t0
                print(f"PASS: stale-lock reclaim succeeded in "
                      f"{elapsed * 1000:.1f} ms")
        except ConcurrentSortError as exc:
            print(f"FAIL: stale lock not reclaimed: {exc}")
            return 1

        return 0
    finally:
        if holder.is_alive():
            release.set()
            holder.join(timeout=2.0)
            if holder.is_alive():
                holder.kill()


if __name__ == "__main__":
    sys.exit(main())
