"""
Local KS2 sort for 23126h Rotenone recordings.
UUID: 2026-02-27-e-H9SYNGFP_Rotenone_D0-D3
Uploads results to: derived/ks2SpikeLab/{stem}/
"""

import os, sys, subprocess, pickle, shutil
from pathlib import Path

from spikelab.spike_sorting import sort_recording

ENDPOINT = "https://s3.braingeneers.gi.ucsc.edu"
S3_ROOT  = "s3://braingeneers/ephys"
UUID     = "2026-02-27-e-H9SYNGFP_Rotenone_D0-D3"
WORKDIR  = Path("/home/sharf-lab/Desktop/Analysis_shared/sort_workdir")
WORKDIR.mkdir(parents=True, exist_ok=True)

STEMS = [
    "23126h_baseline_control_02272026",
    "23126h_rotet124hr_02282026",
    "23126h_control_48hr_03012026",
    "23126h_control_72hr_03022026",
]


def s3_pkl_exists(stem):
    r = subprocess.run(
        ["aws", "s3", "ls",
         f"{S3_ROOT}/{UUID}/derived/ks2SpikeLab/{stem}/sorted_spikedata_curated.pkl",
         "--endpoint-url", ENDPOINT],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and len(r.stdout.strip()) > 0


def download(s3_url, local_path):
    r = subprocess.run(
        ["aws", "s3", "cp", s3_url, str(local_path), "--endpoint-url", ENDPOINT],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())


def upload_results(stem, workdir):
    s3_prefix = f"{S3_ROOT}/{UUID}/derived/ks2SpikeLab/{stem}"
    uploaded = 0
    for path in sorted(Path(workdir).rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".h5", ".dat"}:
            continue
        rel = path.relative_to(workdir).as_posix()
        r = subprocess.run(
            ["aws", "s3", "cp", str(path), f"{s3_prefix}/{rel}",
             "--endpoint-url", ENDPOINT],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"    upload warning: {rel}: {r.stderr.strip()}", file=sys.stderr)
        else:
            uploaded += 1
    print(f"  uploaded {uploaded} files to {s3_prefix}/")


ok, failed = [], []

for i, stem in enumerate(STEMS):
    print(f"\n{'='*65}")
    print(f"[{i+1}/{len(STEMS)}]  {stem}")

    if s3_pkl_exists(stem):
        print("  already sorted on S3, skipping.")
        ok.append(stem)
        continue

    rec_workdir = WORKDIR / stem
    rec_workdir.mkdir(parents=True, exist_ok=True)
    local_h5 = rec_workdir / f"{stem}.raw.h5"

    try:
        if local_h5.exists() and local_h5.stat().st_size > 1_000_000:
            print(f"  raw already present ({local_h5.stat().st_size / 1e9:.2f} GB)")
        else:
            print("  downloading raw recording ...")
            download(f"{S3_ROOT}/{UUID}/original/data/{stem}.raw.h5", local_h5)
            print(f"  downloaded: {local_h5.stat().st_size / 1e9:.2f} GB")

        print("  sorting with KS2 (Docker) ...")
        results = sort_recording(
            recording_files=[str(local_h5)],
            results_folders=[str(rec_workdir)],
            sorter="kilosort2",
            use_docker=True,
            hdf5_plugin_path="/home/sharf-lab/MaxLab/so/",
            sorter_inactivity_base_s=7200.0,
            sorter_inactivity_per_min_s=300.0,
            recompute_recording=False,
        )
        sd = results[0]
        print(f"  sorted: {sd.N} units, {sd.length/1000:.1f} s")

        pkl_path = rec_workdir / "sorted_spikedata_curated.pkl"
        if not pkl_path.exists():
            with open(pkl_path, "wb") as f:
                pickle.dump(sd, f)

        upload_results(stem, rec_workdir)
        ok.append(stem)

    except Exception as exc:
        print(f"  FAILED: {exc}", file=sys.stderr)
        failed.append((stem, str(exc)))
    finally:
        if local_h5.exists():
            print(f"  cleaning up raw h5 ({local_h5.stat().st_size / 1e9:.2f} GB) ...")
            local_h5.unlink()

print(f"\n{'='*65}")
print(f"DONE — {len(ok)} succeeded, {len(failed)} failed")
if failed:
    print("Failed:")
    for stem, err in failed:
        print(f"  {stem}: {err}")
