"""Backfill: upload any locally-completed sort folder to S3 derived/ks2SpikeLab/.

A folder is considered complete when it contains sorted_spikedata_curated.pkl.
Idempotent — re-running just re-syncs (aws s3 sync skips unchanged files).

Usage: python upload_existing.py [<recording_name> ...]
       (no args → upload all complete folders)
"""

import subprocess
import sys
import time
from pathlib import Path

S3_ENDPOINT = "https://s3.braingeneers.gi.ucsc.edu"
S3_DERIVED_PREFIX = (
    "s3://braingeneers/ephys/"
    "2026-04-18-e-KOLF21J_MO_control_sch_halo_dopamine_04272026/"
    "derived/ks2SpikeLab"
)
PROJECT_ROOT = Path(__file__).resolve().parent


def upload_folder(local_dir: Path) -> None:
    rec_name = local_dir.name
    s3_dest = f"{S3_DERIVED_PREFIX}/{rec_name}/"
    print(f"[upload] {rec_name} → {s3_dest}")
    t0 = time.time()
    subprocess.run(
        [
            "aws", "--endpoint-url", S3_ENDPOINT,
            "s3", "sync", str(local_dir), s3_dest,
            "--exclude", "inter_*",
            "--exclude", "inter_*/*",
            "--no-progress",
        ],
        check=True,
    )
    print(f"[upload] {rec_name} done in {time.time() - t0:.1f}s")


def main():
    if len(sys.argv) > 1:
        names = sys.argv[1:]
    else:
        names = []
        for d in sorted(PROJECT_ROOT.iterdir()):
            if not d.is_dir() or d.name.startswith("_") or d.name == "__pycache__":
                continue
            if (d / "sorted_spikedata_curated.pkl").exists():
                names.append(d.name)

    if not names:
        print("[upload] Nothing to upload.")
        return

    print(f"[upload] {len(names)} folder(s):")
    for n in names:
        print(f"           {n}")

    failures = []
    for n in names:
        local = PROJECT_ROOT / n
        if not (local / "sorted_spikedata_curated.pkl").exists():
            print(f"[upload] SKIP {n} (no curated.pkl)")
            continue
        try:
            upload_folder(local)
        except subprocess.CalledProcessError as e:
            print(f"[upload] FAIL {n}: {e}")
            failures.append(n)

    print()
    print(f"[upload] Done. {len(names) - len(failures)}/{len(names)} succeeded.")
    if failures:
        print("[upload] Failures:")
        for n in failures:
            print(f"           {n}")
        sys.exit(1)


if __name__ == "__main__":
    main()
