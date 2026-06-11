"""
Batch KS2 sort for 4 unsorted 2280G D38/D39 recordings.
UUID: 2026-02-21-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH
Runs sequentially (single GPU). Cleans up raw h5 after each sort.
"""

import pickle, sys
from pathlib import Path
import subprocess

from spikelab.spike_sorting import sort_recording

UUID     = "2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH"
S3_ROOT  = "s3://braingeneers/ephys"
ENDPOINT = "https://s3.braingeneers.gi.ucsc.edu"
WORKDIR  = Path("/home/sharf-lab/Desktop/Analysis_shared/sort_workdir")

RECS = [
    "2280G_MO_H9SynGFP_D38_Baseline_02192026",
    "2280G_MO_H9SynGFP_D38_haloperidol_02192026",
    "2280G_MO_H9SynGFP_D39_haloperidol_12hr_02202026",
    "2280G_MO_H9SynGFP_D39_haloperidol_24hr_02202026",
]

ok, failed = [], []

for stem in RECS:
    print(f"\n{'='*65}")
    print(f"Sorting: {stem}")

    rec_dir  = WORKDIR / stem
    local_h5 = rec_dir / f"{stem}.raw.h5"
    pkl_path = rec_dir / "sorted_spikedata_curated.pkl"
    rec_dir.mkdir(parents=True, exist_ok=True)

    if pkl_path.exists():
        print("  Already sorted — skipping.")
        ok.append(stem)
        continue

    try:
        # Download raw h5 from S3
        s3_h5 = f"{S3_ROOT}/{UUID}/original/data/{stem}.raw.h5"
        if local_h5.exists() and local_h5.stat().st_size > 1_000_000:
            print(f"  Raw already present ({local_h5.stat().st_size/1e9:.2f} GB)")
        else:
            print("  Downloading raw h5 from S3 ...")
            r = subprocess.run(
                ["aws", "s3", "cp", s3_h5, str(local_h5), "--endpoint-url", ENDPOINT],
                check=True,
            )
            print(f"  Downloaded: {local_h5.stat().st_size/1e9:.2f} GB")

        print("  Sorting with KS2 (Docker) ...")
        results = sort_recording(
            recording_files=[str(local_h5)],
            results_folders=[str(rec_dir)],
            sorter="kilosort2",
            use_docker=True,
            hdf5_plugin_path="/home/sharf-lab/MaxLab/so/",
            sorter_inactivity_base_s=7200.0,
            sorter_inactivity_per_min_s=300.0,
            recompute_recording=False,
        )
        sd = results[0]
        print(f"  Done: N={sd.N} units, {sd.length/1000:.1f} s")

        if not pkl_path.exists():
            with open(pkl_path, "wb") as f:
                pickle.dump(sd, f)
        ok.append(stem)

    except Exception as exc:
        print(f"  FAILED: {exc}", file=sys.stderr)
        failed.append((stem, str(exc)))

    finally:
        if local_h5.exists():
            print(f"  Removing raw h5 ({local_h5.stat().st_size/1e9:.2f} GB) ...")
            local_h5.unlink()

print(f"\n{'='*65}")
print(f"Done — {len(ok)} succeeded, {len(failed)} failed")
for s, e in failed:
    print(f"  FAILED: {s}: {e}")
