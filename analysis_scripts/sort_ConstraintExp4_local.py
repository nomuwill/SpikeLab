"""
Sort all 5 Maxwell recordings from 2026-05-22-e-ConstraintExp4 locally with
Kilosort2 (Docker) and upload results to S3 derived/spikelabKS2/.

Workflow per recording:
  1. Download .raw.h5 to sort_workdir/<exp_name>/ (skipped if already present)
  2. Sort with sort_recording (KS2 Docker) → results in same workdir
  3. Upload curated results to s3://braingeneers/ephys/<UUID>/derived/spikelabKS2/<exp_name>/
     (excludes .h5, .dat, and inter_*/ intermediate directories)
  4. Delete the entire workdir before proceeding to the next recording
"""

import logging
import shutil
import subprocess
from pathlib import Path

from spikelab.spike_sorting import sort_recording

# ── Config ────────────────────────────────────────────────────────────────────
UUID       = "2026-05-22-e-ConstraintExp4"
S3_BASE    = f"s3://braingeneers/ephys/{UUID}"
S3_RAW     = f"{S3_BASE}/original/data"
S3_OUT     = f"{S3_BASE}/derived/spikelabKS2"
ENDPOINT   = "https://s3.braingeneers.gi.ucsc.edu"
HDF5_PLUGIN = "/home/sharf-lab/MaxLab/so/"
WORKDIR_BASE = Path("/home/sharf-lab/Desktop/Analysis_shared/sort_workdir")

EXPERIMENTS = [
    "24487_22May26",
    "23131_22May26",
    "24478_22May26",
    "23131_19May26",
    "23187_19May26",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def run(cmd: list[str], **kw) -> None:
    """Run a shell command, streaming output."""
    log.info("$ " + " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def aws_cp(src: str, dst: str) -> None:
    run(["aws", "s3", "cp", src, dst, "--endpoint-url", ENDPOINT])


def sort_one(exp_name: str) -> bool:
    workdir = WORKDIR_BASE / exp_name
    local_h5 = workdir / f"{exp_name}.raw.h5"
    s3_raw = f"{S3_RAW}/{exp_name}.raw.h5"
    s3_out = f"{S3_OUT}/{exp_name}"

    try:
        workdir.mkdir(parents=True, exist_ok=True)

        # ── 1. Download ────────────────────────────────────────────────────────
        if local_h5.exists() and local_h5.stat().st_size > 1_000_000:
            log.info(f"[{exp_name}] Raw file already present "
                     f"({local_h5.stat().st_size / 1e9:.2f} GB), skipping download.")
        else:
            log.info(f"[{exp_name}] Downloading {s3_raw} ...")
            aws_cp(s3_raw, str(local_h5))
            log.info(f"[{exp_name}] Downloaded: {local_h5.stat().st_size / 1e9:.2f} GB")

        # ── 2. Sort ────────────────────────────────────────────────────────────
        log.info(f"[{exp_name}] Starting KS2 sort (Docker) ...")
        results = sort_recording(
            recording_files=[str(local_h5)],
            results_folders=[str(workdir)],
            sorter="kilosort2",
            use_docker=True,
            hdf5_plugin_path=HDF5_PLUGIN,
            # Curation (defaults)
            snr_min=5.0,
            fr_min=0.05,
            isi_viol_max=0.01,
            spikes_min_first=30,
            spikes_min_second=50,
            std_norm_max=1.0,
            # Outputs
            compile_to_npz=True,
            create_figures=True,
            save_waveform_files=False,   # templates stored in pkl; skip per-unit npy
            # Timeout tolerances (generous for MaxOne recordings)
            sorter_inactivity_base_s=7200.0,    # 2 h base
            sorter_inactivity_per_min_s=300.0,  # +5 min per recording-minute
        )
        sd = results[0]
        log.info(f"[{exp_name}] Sort complete: {sd.N} curated units, "
                 f"duration {sd.length / 1000:.1f} s")

        # ── 3. Upload results to S3 ────────────────────────────────────────────
        log.info(f"[{exp_name}] Uploading to {s3_out}/ ...")
        skip_suffixes = {".h5", ".dat"}
        uploaded = 0
        for fpath in sorted(workdir.rglob("*")):
            if not fpath.is_file():
                continue
            # Skip raw data and binary intermediate files
            if fpath.suffix.lower() in skip_suffixes:
                continue
            # Skip everything inside inter_*/ intermediate directories
            rel = fpath.relative_to(workdir)
            if rel.parts[0].startswith("inter_"):
                continue
            s3_uri = f"{s3_out}/{rel.as_posix()}"
            aws_cp(str(fpath), s3_uri)
            uploaded += 1
        log.info(f"[{exp_name}] Uploaded {uploaded} files.")

        return True

    except Exception:
        log.exception(f"[{exp_name}] FAILED")
        return False

    finally:
        # ── 4. Cleanup workdir ─────────────────────────────────────────────────
        if workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)
            log.info(f"[{exp_name}] Workdir deleted: {workdir}")


def main() -> int:
    log.info(f"UUID: {UUID}")
    log.info(f"Sorting {len(EXPERIMENTS)} experiments sequentially")
    log.info(f"Results → {S3_OUT}/<exp_name>/\n")

    successes, failures = [], []
    for exp_name in EXPERIMENTS:
        log.info(f"\n{'=' * 60}\n[{exp_name}] START\n{'=' * 60}")
        ok = sort_one(exp_name)
        (successes if ok else failures).append(exp_name)
        log.info(f"[{exp_name}] {'OK' if ok else 'FAILED'}\n")

    log.info(f"\n{'=' * 60}\nSUMMARY\n{'=' * 60}")
    log.info(f"Succeeded ({len(successes)}): {successes}")
    if failures:
        log.info(f"Failed    ({len(failures)}): {failures}")

    return 1 if failures else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
