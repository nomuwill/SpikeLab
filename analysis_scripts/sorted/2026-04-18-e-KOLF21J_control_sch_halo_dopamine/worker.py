"""Sort a single Maxwell recording with KS2 (Docker), with a reduced
per-container mem_limit so 3 of these can run in parallel on a 125 GB host.

Usage: python worker.py <recording_filename> <s3_prefix> <raw_dir> <results_root>
"""

import os
import sys
import shutil
import subprocess
import time
from pathlib import Path

# Sequential mode: keep library default mem_limit_frac=0.8 (no monkey-patch).
from spikelab.spike_sorting import sort_recording  # noqa: E402


S3_ENDPOINT = "https://s3.braingeneers.gi.ucsc.edu"
S3_DERIVED_PREFIX = (
    "s3://braingeneers/ephys/"
    "2026-04-18-e-KOLF21J_MO_control_sch_halo_dopamine_04272026/"
    "derived/ks2SpikeLab"
)


def s3_upload(local_dir: Path, s3_dest: str) -> None:
    """aws s3 sync local_dir → s3_dest, skipping any leftover inter_* dirs."""
    print(f"[worker] Uploading {local_dir} → {s3_dest}")
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
    dt = time.time() - t0
    print(f"[worker] Upload completed in {dt:.1f}s")


def s3_download(s3_uri: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 0:
        print(f"[worker] Already present: {dst} ({dst.stat().st_size} bytes); skipping download.")
        return
    print(f"[worker] Downloading {s3_uri} → {dst}")
    t0 = time.time()
    subprocess.run(
        ["aws", "--endpoint-url", S3_ENDPOINT, "s3", "cp",
         s3_uri, str(dst), "--no-progress"],
        check=True,
    )
    dt = time.time() - t0
    sz_gb = dst.stat().st_size / 1e9
    print(f"[worker] Downloaded {sz_gb:.2f} GB in {dt:.1f}s ({sz_gb / dt:.2f} GB/s)")


def main():
    rec_filename = sys.argv[1]
    s3_prefix = sys.argv[2].rstrip("/")
    raw_dir = Path(sys.argv[3])
    results_root = Path(sys.argv[4])

    rec_name = rec_filename.replace(".raw.h5", "")
    raw_path = raw_dir / rec_filename
    results_folder = results_root / rec_name
    results_folder.mkdir(parents=True, exist_ok=True)

    print(f"[worker] Recording: {rec_name}")
    print(f"[worker] Raw path: {raw_path}")
    print(f"[worker] Results: {results_folder}")
    print(f"[worker] HDF5_PLUGIN_PATH: {os.environ.get('HDF5_PLUGIN_PATH', '<unset>')}")

    s3_uri = f"{s3_prefix}/{rec_filename}"
    s3_download(s3_uri, raw_path)

    t_sort_start = time.time()
    try:
        results = sort_recording(
            recording_files=[str(raw_path)],
            results_folders=[str(results_folder)],
            sorter="kilosort2",
            use_docker=True,
            # All curation defaults
            snr_min=5.0,
            fr_min=0.05,
            isi_viol_max=0.01,
            spikes_min_first=30,
            spikes_min_second=50,
            std_norm_max=1.0,
            # Output
            compile_to_npz=True,
            save_raw_pkl=True,
            create_figures=True,
            create_unit_figures=True,
            # Sequential: full default n_jobs
            n_jobs=8,
            delete_inter=True,
        )
        sd = results[0] if results else None
        if sd is None:
            print(f"[worker] FAILED: no SpikeData returned for {rec_name}")
            sys.exit(2)
        print(
            f"[worker] SUCCESS: {rec_name} — {sd.N} curated units, "
            f"{sd.length / 1000:.1f}s, {time.time() - t_sort_start:.0f}s wall."
        )
    except Exception as e:
        print(f"[worker] EXCEPTION for {rec_name}: {type(e).__name__}: {e}")
        # Leave raw on disk for retry/inspection; cleanup left to orchestrator
        raise

    # Upload sort artifacts to S3 derived/ks2SpikeLab/<recording_name>/
    s3_upload(results_folder, f"{S3_DERIVED_PREFIX}/{rec_name}/")

    # Cleanup raw on success only
    try:
        raw_path.unlink()
        print(f"[worker] Removed raw file {raw_path}")
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    main()
