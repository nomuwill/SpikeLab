"""Sort all 15 control recordings of chip 23206i from UUID
2026-04-18-e-KOLF21J_MO_control_sch_halo_dopamine_04272026, with
up to 3 worker processes running concurrently.

Each worker downloads one recording, runs KS2 (Docker) with reduced
per-container mem_limit, and deletes the raw file on success.
"""

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

S3_PREFIX = (
    "s3://braingeneers/ephys/"
    "2026-04-18-e-KOLF21J_MO_control_sch_halo_dopamine_04272026/original/data"
)

PROJECT_ROOT = Path(__file__).resolve().parent
RAW_DIR = Path(
    "/home/sharf-lab/Desktop/Analysis_shared/data/"
    "2026-04-18-e-KOLF21J_control_sch_halo_dopamine/raw"
)
RESULTS_ROOT = PROJECT_ROOT
LOG_DIR = PROJECT_ROOT / "_logs"
WORKER = PROJECT_ROOT / "worker.py"

# 15 recordings: 14 with "control" in name + D30 (added by user as 16th
# but excluding one D40 duplicate ⇒ 15 total).
RECORDINGS = [
    "23206i_KOLF21J_MO_D30_04132026.raw.h5",
    "23206i_KOLF21J_MO_D31_control_04142026.raw.h5",
    "23206i_KOLF21J_MO_D32_control_04152026.raw.h5",
    "23206i_KOLF21J_MO_D33_control_connectedconfig_04162026.raw.h5",
    "23206i_KOLF21J_MO_D33_control_newconfig_04162026.raw.h5",
    "23206i_KOLF21J_MO_D34_control_connectedconfig_04172026.raw.h5",
    "23206i_KOLF21J_MO_D34_control_newconfig_04172026.raw.h5",
    "23206i_KOLF21J_MO_D35_control_connectedconfig_04182026.raw.h5",
    "23206i_KOLF21J_MO_D35_control_newconfig_04182026.raw.h5",
    "23206i_KOLF21J_MO_D36_control_04192026.raw.h5",
    "23206i_KOLF21J_MO_D37_control_04202026.raw.h5",
    "23206i_KOLF21J_MO_D38_control_04212026.raw.h5",
    "23206i_KOLF21J_MO_D39_control_04222026.raw.h5",
    "23206i_KOLF21J_MO_D40_control_24hr_connectedconfig_04232026.raw.h5",
    "23206i_KOLF21J_MO_D40_control_24hr_newconfig_04232026.raw.h5",
]

MAX_PARALLEL = 1  # n=3 caused CUDA_ERROR_ILLEGAL_ADDRESS (KS2 cannot share GPU)


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # Skip recordings that already produced a curated pickle
    queue = []
    skipped = []
    for rec in RECORDINGS:
        rec_name = rec.replace(".raw.h5", "")
        out = RESULTS_ROOT / rec_name / "sorted_spikedata_curated.pkl"
        if out.exists():
            skipped.append(rec_name)
        else:
            queue.append(rec)

    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[orch] Started {started}")
    print(f"[orch] {len(queue)} to sort, {len(skipped)} already done.")
    if skipped:
        print("[orch] Skipping (already curated):")
        for s in skipped:
            print(f"         {s}")

    in_flight: list[tuple[str, subprocess.Popen, object]] = []  # (rec, proc, log_fh)
    results_log: list[tuple[str, int, float]] = []
    t0 = time.time()

    def launch(rec: str):
        rec_name = rec.replace(".raw.h5", "")
        log_path = LOG_DIR / f"{rec_name}.orchlog"
        log_fh = open(log_path, "w", buffering=1)
        log_fh.write(f"# Started {datetime.now().isoformat()}\n")
        log_fh.flush()
        cmd = [
            sys.executable,
            "-u",
            str(WORKER),
            rec,
            S3_PREFIX,
            str(RAW_DIR),
            str(RESULTS_ROOT),
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
        )
        print(f"[orch] LAUNCH pid={proc.pid} {rec_name} → {log_path}")
        in_flight.append((rec, proc, log_fh))

    # Prime the queue
    while queue and len(in_flight) < MAX_PARALLEL:
        launch(queue.pop(0))

    while in_flight:
        time.sleep(5)
        still: list[tuple[str, subprocess.Popen, object]] = []
        for rec, proc, log_fh in in_flight:
            rc = proc.poll()
            if rc is None:
                still.append((rec, proc, log_fh))
            else:
                rec_name = rec.replace(".raw.h5", "")
                elapsed = time.time() - t0
                tag = "OK " if rc == 0 else f"FAIL({rc})"
                print(f"[orch] {tag} {rec_name} (rc={rc}, total elapsed={elapsed:.0f}s)")
                results_log.append((rec_name, rc, elapsed))
                log_fh.close()
                if queue:
                    launch(queue.pop(0))
        in_flight = still

    print()
    print("[orch] === FINAL SUMMARY ===")
    ok = sum(1 for _, rc, _ in results_log if rc == 0)
    fail = sum(1 for _, rc, _ in results_log if rc != 0)
    print(f"[orch] Succeeded: {ok}/{len(results_log)}")
    print(f"[orch] Failed:    {fail}/{len(results_log)}")
    for rec_name, rc, elapsed in results_log:
        print(f"[orch]   {rec_name:80s} rc={rc} t={elapsed:.0f}s")
    print(f"[orch] Total wall: {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
