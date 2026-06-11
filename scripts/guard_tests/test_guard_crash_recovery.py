"""Stress test #7: crash + recovery via SIGKILL.

Spawns a real KS2 canary in a subprocess, waits for it to begin
writing the sort artefacts (lock file + KS2 results dir), SIGKILLs
the subprocess mid-flight, then verifies recovery on a fresh
``acquire_sort_lock`` against the same intermediate folder. The
sequence we're proving:

1. SIGKILL leaves a stale lock file in the canary's intermediate
   folder. The lock contains the killed PID; ``kill -0`` against
   that PID after the kill returns ENOENT/ESRCH (process is dead).
2. Sorter tempfiles created in /tmp persist after the kill — they
   are kept for postmortem inspection by design.
3. A fresh ``acquire_sort_lock`` against the killed sort's folder
   reclaims the stale lock (because the holder PID is dead on the
   same host) and succeeds without raising ``ConcurrentSortError``.

The realistic operator scenario this models: workstation reboot,
OOM-killer, ctrl-C-then-kill, or simply ``kill -9`` on a runaway
sort. Each of those should leave the next sort recoverable.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from spikelab.spike_sorting._exceptions import ConcurrentSortError
from spikelab.spike_sorting.guards import acquire_sort_lock

INTER = Path("/tmp/crash_recovery_test_inter")
WORKER = Path(__file__).parent / "_canary_worker_for_kill_test.py"
STAGING = Path(
    "/home/sharf-lab/Desktop/Analysis_shared/data/spikesort_test/"
    "maxtwo_concat_test/_concat_input_baseline_halo"
)
SPAWN_TIMEOUT_S = 90.0  # binary-write phase typically starts within ~5–15 s


def _pid_alive(pid: int) -> bool:
    """Return True if the PID is alive on this host."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours
    return True


def _find_lock_file() -> Path | None:
    """Return the first ``.spikelab_sort.lock`` under INTER, or None."""
    if not INTER.exists():
        return None
    for p in INTER.rglob(".spikelab_sort.lock"):
        return p
    return None


def _list_marker_tempfiles() -> set[Path]:
    """Marker tempfiles in /tmp matching the sweep markers."""
    from spikelab.spike_sorting.guards._tempfile_cleanup import (
        _list_marker_files,
    )
    return _list_marker_files(Path(tempfile.gettempdir()))


def main() -> int:
    if INTER.exists():
        shutil.rmtree(INTER)
    INTER.mkdir(parents=True)

    print(f"INTER:    {INTER}")
    print(f"WORKER:   {WORKER}")
    print(f"STAGING:  {STAGING}\n")

    pre_existing_tempfiles = _list_marker_tempfiles()
    print(f"Pre-existing marker tempfiles in /tmp: {len(pre_existing_tempfiles)}\n")

    # Phase 1: spawn worker, wait for the sort to start, SIGKILL.
    print("=" * 60)
    print("Phase 1: spawn worker, wait for sort artefacts, SIGKILL")
    print("=" * 60)
    worker = subprocess.Popen(
        ["conda", "run", "-n", "spikelab", "--no-capture-output",
         "python", str(WORKER), str(INTER), str(STAGING)],
        start_new_session=True,  # so os.killpg works on the whole tree
    )
    worker_pid = worker.pid
    print(f"Spawned worker (conda wrapper) PID {worker_pid}")

    # Poll for sort progress: a lock file appears once the canary's
    # process_recording acquires it; a kilosort2_results dir appears
    # once the binary writer starts. Either is enough.
    deadline = time.time() + SPAWN_TIMEOUT_S
    lock_path: Path | None = None
    while time.time() < deadline:
        lock_path = _find_lock_file()
        ks2_dirs = list(INTER.rglob("kilosort2_results")) if INTER.exists() else []
        if lock_path is not None and ks2_dirs:
            print(f"Sort started: lock at {lock_path}")
            print(f"              KS2 dir at {ks2_dirs[0]}")
            break
        if worker.poll() is not None:
            print(f"FAIL: worker exited prematurely with code {worker.returncode}")
            return 1
        time.sleep(0.5)
    else:
        print(f"FAIL: no lock + KS2 dir within {SPAWN_TIMEOUT_S} s")
        try:
            worker.kill()
        finally:
            worker.wait(timeout=10)
        return 1

    # Read the holder PID from the lock file before killing.
    import json
    lock_info = json.loads(lock_path.read_text(encoding="utf-8"))
    holder_pid = int(lock_info["pid"])
    print(f"Lock-file PID: {holder_pid}")

    # SIGKILL the entire subprocess group so any forked spikeinterface
    # / docker stub processes also die. ``conda run`` is the parent
    # of the actual python child holding the lock; killing the conda
    # wrapper alone might leave the python child alive. Use
    # process-group kill to be safe.
    print(f"\nSIGKILLing worker process tree (parent PID {worker_pid}, "
          f"lock holder PID {holder_pid})")
    try:
        os.killpg(os.getpgid(worker_pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # Fallback: kill the wrapper and the holder directly
        try:
            os.kill(worker_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.kill(holder_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    worker.wait(timeout=15)
    print(f"Worker reaped (returncode={worker.returncode})")

    # Wait briefly for the OS to report the lock holder as dead.
    for _ in range(20):
        if not _pid_alive(holder_pid):
            break
        time.sleep(0.25)

    if _pid_alive(holder_pid):
        print(f"FAIL: lock-holder PID {holder_pid} still alive after SIGKILL")
        return 1
    print(f"PASS: lock-holder PID {holder_pid} confirmed dead")

    # Verify the lock file is still there with the dead PID.
    if not lock_path.exists():
        print(f"FAIL: lock file {lock_path} was cleaned up — unexpected for SIGKILL")
        return 1
    leftover = json.loads(lock_path.read_text(encoding="utf-8"))
    if int(leftover["pid"]) != holder_pid:
        print(f"FAIL: lock file PID {leftover['pid']!r} doesn't match killed "
              f"holder PID {holder_pid}")
        return 1
    print(f"PASS: stale lock file present at {lock_path} with dead PID {holder_pid}")

    # Verify tempfile observability — sorter tempfiles are kept for
    # inspection after a crash.
    post_kill_tempfiles = _list_marker_tempfiles()
    new_tempfiles = post_kill_tempfiles - pre_existing_tempfiles
    print(f"\nNew marker tempfiles in /tmp after crash: {len(new_tempfiles)}")
    if new_tempfiles:
        for p in sorted(new_tempfiles)[:5]:
            print(f"  {p}")
    # No assertion here — tempfile creation is racy and depends on
    # how far KS2 got. Observability is the value, not a fixed count.

    # Phase 2: recovery.
    print("\n" + "=" * 60)
    print("Phase 2: fresh acquire_sort_lock should reclaim the stale lock")
    print("=" * 60)
    target = lock_path.parent
    print(f"Acquiring on {target}")

    t0 = time.time()
    try:
        with acquire_sort_lock(target):
            elapsed = time.time() - t0
            new_info = json.loads(lock_path.read_text(encoding="utf-8"))
            print(f"PASS: stale-lock reclaim succeeded in {elapsed * 1000:.1f} ms")
            print(f"      new lock PID: {new_info['pid']} (parent test PID: "
                  f"{os.getpid()})")
            if int(new_info["pid"]) != os.getpid():
                print(f"FAIL: reclaimed lock PID {new_info['pid']} doesn't match "
                      f"this process {os.getpid()}")
                return 1
    except ConcurrentSortError as exc:
        print(f"FAIL: ConcurrentSortError on stale-lock reclaim: {exc}")
        return 1

    # Lock should be cleaned up after the context manager exits.
    if lock_path.exists():
        print(f"FAIL: lock file {lock_path} not removed after clean exit")
        return 1
    print("PASS: lock cleanly removed after recovery context exit")

    return 0


if __name__ == "__main__":
    sys.exit(main())
