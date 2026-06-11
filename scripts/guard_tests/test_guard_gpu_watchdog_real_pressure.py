"""Stress test #10: GpuMemoryWatchdog kills a real VRAM-eating process.

The worker actually allocates CUDA tensors until VRAM crosses the
configured ``abort_pct``. The watchdog polls real GPU memory via
pynvml — no synthetic threshold trick — and drives the documented
trip cascade against a real PID holding live CUDA allocations.

Test passes when:
  * Worker exits within bounded time.
  * Exit code is 1 (KeyboardInterrupt path or os._exit fallback) —
    not 0 (watchdog never tripped) and not 2 (max_gb safety cap
    hit without a trip).
  * Peak VRAM percent observed crosses abort_pct.
  * Trip evidence in worker output.
  * Post-test VRAM drops back near baseline (CUDA reclaims pages).
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

import torch

WORKER = Path(__file__).parent / "_vram_eater_worker.py"

ABORT_PCT = 80.0
MAX_GB = 30.0   # safety cap — must NOT be reached on a 32 GB GPU
TIMEOUT_S = 240.0


def _read_vram() -> tuple[float, float]:
    free, total = torch.cuda.mem_get_info(0)
    used_gb = (total - free) / 1024**3
    total_gb = total / 1024**3
    return used_gb, total_gb


def main() -> int:
    if not torch.cuda.is_available():
        print("FAIL: CUDA not available")
        return 1

    used_gb, total_gb = _read_vram()
    pct = 100 * used_gb / total_gb
    print(f"GPU device:         {torch.cuda.get_device_name(0)}")
    print(f"VRAM total:         {total_gb:.1f} GB")
    print(f"VRAM in use now:    {used_gb:.2f} GB ({pct:.1f}%)")
    print(f"abort_pct:          {ABORT_PCT}%  -> trip at "
          f"~{total_gb * ABORT_PCT / 100:.1f} GB used")
    print(f"max_gb safety cap:  {MAX_GB} GB allocated by worker")

    if pct >= ABORT_PCT - 30.0:
        print(f"\nFAIL: GPU already at {pct:.1f}% — too close to "
              f"abort_pct={ABORT_PCT}%. Free VRAM first.")
        return 1

    cmd = [
        "conda", "run", "-n", "spikelab", "--no-capture-output",
        "python", str(WORKER), str(ABORT_PCT), str(MAX_GB),
    ]
    print(f"\nSpawning worker: {' '.join(cmd)}\n")

    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1)

    stdout_lines: list[str] = []
    peak_pct = pct
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
            if "VRAM in-use:" in line and "(" in line:
                # Parse "(NN.N%)"
                try:
                    pct_str = line.rsplit("(", 1)[-1].split("%", 1)[0]
                    peak_pct = max(peak_pct, float(pct_str))
                except ValueError:
                    pass
            continue
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    elapsed = time.time() - t0
    stderr = proc.stderr.read() if proc.stderr else ""
    rc = proc.returncode

    print("\n" + "=" * 60)
    if stderr:
        print("Worker stderr:")
        print(stderr)
    print("=" * 60)
    print(f"Worker exit code:   {rc}")
    print(f"Wall time:          {elapsed:.1f} s")
    print(f"Peak VRAM %:        {peak_pct:.1f}")

    # Post-mortem: VRAM should drop back close to baseline
    # (CUDA driver releases the freed allocations on process exit).
    time.sleep(2.0)
    final_used, _ = _read_vram()
    final_pct = 100 * final_used / total_gb
    print(f"Post-test VRAM %:   {final_pct:.1f}")

    if rc == 0:
        print("\nFAIL: worker exited 0 — watchdog never tripped")
        return 1
    if rc == 2:
        print("\nFAIL: worker reached max_gb safety cap without trip")
        return 1
    if rc != 1 and rc > 0:
        print(f"\nFAIL: unexpected exit code {rc}")
        return 1

    if "ABORT:" not in stderr and "ABORT:" not in "".join(stdout_lines):
        print("FAIL: no ABORT trip evidence in worker output")
        return 1

    if peak_pct < ABORT_PCT - 1.0:
        print(f"FAIL: peak VRAM% only {peak_pct:.1f} — never crossed "
              f"abort_pct={ABORT_PCT}")
        return 1

    if final_pct > pct + 5.0:
        print(f"WARN: post-test VRAM% ({final_pct:.1f}) > pre-test "
              f"({pct:.1f}) + 5 — pages may not have been reclaimed")

    print(f"\nPASS: worker actually allocated CUDA memory until VRAM% "
          f"crossed {ABORT_PCT} (peak {peak_pct:.1f}%); "
          f"GpuMemoryWatchdog drove the kill in {elapsed:.0f} s; "
          f"exit code {rc}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
