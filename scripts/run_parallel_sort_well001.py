"""
Coordinator: run sort_gpu0_well001.py and sort_gpu1_well001.py in parallel,
polling nvidia-smi every 10 s for per-GPU temperature and memory usage.

Outputs:
  data/spikesort_test/parallel_gpu_test/gpu_temp_log.csv  — timestamped GPU stats
  stdout — live temperature table + final wall-time summary
"""

import csv
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPTS_DIR = Path(__file__).parent
LOG_DIR = Path(
    "/home/sharf-lab/Desktop/Analysis_shared/data/spikesort_test/parallel_gpu_test"
)
LOG_DIR.mkdir(parents=True, exist_ok=True)
TEMP_LOG = LOG_DIR / "gpu_temp_log.csv"

GPU0_SCRIPT = SCRIPTS_DIR / "sort_gpu0_well001.py"
GPU1_SCRIPT = SCRIPTS_DIR / "sort_gpu1_well001.py"
GPU0_LOG = LOG_DIR / "gpu0_sort.log"
GPU1_LOG = LOG_DIR / "gpu1_sort.log"

POLL_INTERVAL_S = 10  # seconds between nvidia-smi queries

# ── Temperature polling ────────────────────────────────────────────────────────

_stop_polling = threading.Event()


def _poll_gpu_temps(csv_path: Path) -> None:
    """Poll nvidia-smi and append rows to csv_path until _stop_polling is set."""
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["timestamp", "gpu_index", "name", "temp_C", "mem_used_MiB", "mem_total_MiB"]
        )
        f.flush()

        while not _stop_polling.is_set():
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,temperature.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
            )
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rows = []
            for line in result.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) == 5:
                    idx, name, temp, mem_used, mem_total = parts
                    writer.writerow([ts, idx, name, temp, mem_used, mem_total])
                    rows.append((idx, name, temp, mem_used, mem_total))
            f.flush()

            if rows:
                header = f"[{ts}] GPU temps:"
                cols = "  ".join(
                    f"GPU{r[0]} {r[2]}°C  {r[3]}/{r[4]} MiB" for r in rows
                )
                print(f"{header}  {cols}", flush=True)

            _stop_polling.wait(POLL_INTERVAL_S)


# ── Launch sort jobs ───────────────────────────────────────────────────────────

def _launch(script: Path, log_path: Path) -> subprocess.Popen:
    """Launch a sorting script as a subprocess, teeing stdout+stderr to log_path."""
    log_fh = open(log_path, "w")
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    def _tee():
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log_fh.write(line)
            log_fh.flush()
        log_fh.close()

    t = threading.Thread(target=_tee, daemon=True)
    t.start()
    return proc


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 70)
    print("Parallel Kilosort2 (Docker) — well001 on GPU 0 and GPU 1")
    print(f"GPU temp log  : {TEMP_LOG}")
    print(f"GPU 0 job log : {GPU0_LOG}")
    print(f"GPU 1 job log : {GPU1_LOG}")
    print(f"Poll interval : {POLL_INTERVAL_S} s")
    print("=" * 70)

    # Start temperature poller in background
    poll_thread = threading.Thread(target=_poll_gpu_temps, args=(TEMP_LOG,), daemon=True)
    poll_thread.start()

    wall_start = time.time()

    print("\nLaunching GPU 0 job...")
    proc0 = _launch(GPU0_SCRIPT, GPU0_LOG)
    time.sleep(2)  # slight stagger so log headers don't interleave
    print("Launching GPU 1 job...")
    proc1 = _launch(GPU1_SCRIPT, GPU1_LOG)

    # Wait for both jobs
    rc0 = proc0.wait()
    rc1 = proc1.wait()

    wall_elapsed = time.time() - wall_start

    # Stop poller after one final sample
    _stop_polling.set()
    poll_thread.join(timeout=POLL_INTERVAL_S + 2)

    # ── Summary ───────────────────────────────────────────────────────────────

    print("\n" + "=" * 70)
    print("PARALLEL SORT COMPLETE")
    print("=" * 70)
    m, s = divmod(int(wall_elapsed), 60)
    print(f"Total wall time : {m} min {s} s")
    print(f"GPU 0 exit code : {rc0}  ({'OK' if rc0 == 0 else 'FAILED'})")
    print(f"GPU 1 exit code : {rc1}  ({'OK' if rc1 == 0 else 'FAILED'})")
    print(f"GPU temp log    : {TEMP_LOG}")

    # Quick temperature summary from CSV
    if TEMP_LOG.exists():
        import csv as _csv
        temps: dict[str, list[float]] = {}
        with open(TEMP_LOG) as f:
            reader = _csv.DictReader(f)
            for row in reader:
                idx = row["gpu_index"]
                try:
                    temps.setdefault(idx, []).append(float(row["temp_C"]))
                except ValueError:
                    pass
        print("\nGPU temperature summary (°C):")
        for idx, vals in sorted(temps.items()):
            print(
                f"  GPU {idx}: min={min(vals):.0f}  max={max(vals):.0f}  "
                f"mean={sum(vals)/len(vals):.0f}  samples={len(vals)}"
            )

    print("\nNext step: run compare_sort_results_well001.py to compare outputs.")
    if rc0 != 0 or rc1 != 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
