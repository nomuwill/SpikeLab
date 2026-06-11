"""
Batch local KS2 sort for unsorted recordings across two UUIDs.
Processes one recording at a time; cleans up raw h5 after each sort.
Uploads sorted_spikedata_curated.pkl + npz to derived/ks2SpikeLab/{stem}/.
"""

import os, sys, subprocess, pickle, shutil
from pathlib import Path

from spikelab.spike_sorting import sort_recording

ENDPOINT  = "https://s3.braingeneers.gi.ucsc.edu"
S3_ROOT   = "s3://braingeneers/ephys"
WORKDIR   = Path("/home/sharf-lab/Desktop/Analysis_shared/sort_workdir")
WORKDIR.mkdir(parents=True, exist_ok=True)

# (UUID, recording_stem)
MISSING = [
    # ── 2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH ──────────
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "21956G_MO_H9SynGFP_D40_SCH_02212026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "21965G_MO_H9SynGFP_D38_Baseline_02192026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "21965G_MO_H9SynGFP_D38_haloperidol_02192026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "21965G_MO_H9SynGFP_D39_baseline_02202026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "21965G_MO_H9SynGFP_D39_dopamine_02202026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "21965G_MO_H9SynGFP_D39_haloperidol_12hr_02202026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "21965G_MO_H9SynGFP_D39_haloperidol_24hr_02202026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "21965G_MO_H9SynGFP_D40_Baseline_02212026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "21965G_MO_H9SynGFP_D40_Dopmanine_24hr_02212026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "2280G_MO_H9SynGFP_D39_baseline_02202026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "2280G_MO_H9SynGFP_D40_SCH_02212026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "23125h_MO_H9SynGFP_D35_Dopamine_haloperidol_24hr_02252026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "23125h_MO_H9SynGFP_D35_Dopamine_haloperidol_24hr_connectedconfig_02252026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "23126h_MO_H9SynGFP_D33_Control_02232026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "23126h_MO_H9SynGFP_D34_Control_02242026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "23126h_MO_H9SynGFP_D35_Control_02252026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "23126h_MO_H9SynGFP_D35_Control_connectedconfig_02252026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "23215h_MO_H9SynGFP_D34_Dopamine_02242026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24478G_MO_H9SynGFP_D38_Baseline_02192026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24478G_MO_H9SynGFP_D38_haloperidol_02192026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24478G_MO_H9SynGFP_D39_baseline_02202026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24478G_MO_H9SynGFP_D39_dopamine_02202026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24478G_MO_H9SynGFP_D39_haloperidol_12hr_02202026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24478G_MO_H9SynGFP_D39_haloperidol_24hr_02202026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24478G_MO_H9SynGFP_D40_Baseline_02212026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24478G_MO_H9SynGFP_D40_SCH_02212026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24478G_MO_H9SynGFP_D43_02242026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24478G_MO_H9SynGFP_D43_Dopamine_haloperidol_02242026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24478G_MO_H9SynGFP_D44_Dopamine_haloperidol_24hr_02252026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24478G_MO_H9SynGFP_D44_Dopamine_haloperidol_24hr_connectedconfig_02252026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24487h_MO_H9SynGFP_D34_02242026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24487h_MO_H9SynGFP_D34_Dopamine_02242026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24487h_MO_H9SynGFP_D34_Dopamine_haloperidol_02242026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24487h_MO_H9SynGFP_D35_Dopamine_haloperidol_24hr_02252026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24487h_MO_H9SynGFP_D35_Dopamine_haloperidol_24hr_connectedconfig_02252026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24658h_MO_H9SynGFP_D34_02242026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24658h_MO_H9SynGFP_D34_Dopamine_02242026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24658h_MO_H9SynGFP_D34_Dopamine_haloperidol_02242026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24658h_MO_H9SynGFP_D35_Dopamine_haloperidol_24hr_02252026"),
    ("2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH", "24658h_MO_H9SynGFP_D35_Dopamine_haloperidol_24hr_connectedconfig_02252026"),
    # ── 2026-02-25-e-24478G_MO_H9SynGFP_D43_Dopamine_02242026 ───────────────
    ("2026-02-25-e-24478G_MO_H9SynGFP_D43_Dopamine_02242026", "24478G_MO_H9SynGFP_D43_Dopamine_02242026"),
]


def s3_pkl_exists(uuid, stem):
    r = subprocess.run(
        ["aws", "s3", "ls",
         f"{S3_ROOT}/{uuid}/derived/ks2SpikeLab/{stem}/sorted_spikedata_curated.pkl",
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


def upload_results(uuid, stem, workdir):
    s3_prefix = f"{S3_ROOT}/{uuid}/derived/ks2SpikeLab/{stem}"
    uploaded = 0
    for path in sorted(Path(workdir).rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".h5", ".dat"}:
            continue
        rel = path.relative_to(workdir).as_posix()
        r = subprocess.run(
            ["aws", "s3", "cp", str(path), f"{s3_prefix}/{rel}", "--endpoint-url", ENDPOINT],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"    upload warning: {rel}: {r.stderr.strip()}", file=sys.stderr)
        else:
            uploaded += 1
    print(f"  uploaded {uploaded} files to {s3_prefix}/")


ok, failed = [], []

for i, (uuid, stem) in enumerate(MISSING):
    print(f"\n{'='*65}")
    print(f"[{i+1}/{len(MISSING)}]  {stem}")
    print(f"  UUID: {uuid}")

    if s3_pkl_exists(uuid, stem):
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
            download(f"{S3_ROOT}/{uuid}/original/data/{stem}.raw.h5", local_h5)
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

        upload_results(uuid, stem, rec_workdir)
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
