"""
Local KS2 sort + curation for 24500f D37 baseline.
UUID: 2025-12-20-e-H9SynGFP_Midbrain_control_baseline_haloperidol_rotenone
Uploads result to: derived/ks2SpikeLab/24500f_MO_H9SynGFP_D37_12192025/
"""

import os, subprocess, pickle
from pathlib import Path

from spikelab.spike_sorting import sort_recording

UUID      = "2025-12-20-e-H9SynGFP_Midbrain_control_baseline_haloperidol_rotenone"
REC       = "24500f_MO_H9SynGFP_D37_12192025"
S3_RAW    = f"s3://braingeneers/ephys/{UUID}/original/data/{REC}.raw.h5"
S3_OUT    = f"s3://braingeneers/ephys/{UUID}/derived/ks2SpikeLab/{REC}"
ENDPOINT  = "https://s3.braingeneers.gi.ucsc.edu"

WORKDIR   = Path(f"/home/sharf-lab/Desktop/Analysis_shared/sort_workdir/{REC}")
LOCAL_H5  = WORKDIR / f"{REC}.raw.h5"
WORKDIR.mkdir(parents=True, exist_ok=True)


def run(cmd, **kw):
    print(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, **kw)


# ── 1. Download raw recording ─────────────────────────────────────────────────
if LOCAL_H5.exists() and LOCAL_H5.stat().st_size > 1_000_000:
    print(f"Raw file already present: {LOCAL_H5} ({LOCAL_H5.stat().st_size / 1e9:.2f} GB)")
else:
    print("Downloading raw recording from S3 ...")
    run(["aws", "s3", "cp", S3_RAW, str(LOCAL_H5), "--endpoint-url", ENDPOINT])
    print(f"Downloaded: {LOCAL_H5.stat().st_size / 1e9:.2f} GB")

# ── 2. Sort + curate ──────────────────────────────────────────────────────────
print("\nRunning KS2 sort + curation (Docker) ...")
results = sort_recording(
    recording_files=[str(LOCAL_H5)],
    results_folders=[str(WORKDIR)],
    sorter="kilosort2",
    use_docker=True,
    hdf5_plugin_path="/home/sharf-lab/MaxLab/so/",
    sorter_inactivity_base_s=7200.0,     # 2-hour base (default 600s too short for 1000ch)
    sorter_inactivity_per_min_s=300.0,   # 5 min per recording-min (default 30s)
    recompute_recording=False,           # reuse existing temp_wh.dat
)
sd = results[0]
print(f"\nSort complete: {sd.N} units, duration {sd.length / 1000:.1f} s")

# ── 3. Verify pkl was saved ───────────────────────────────────────────────────
pkl_path = WORKDIR / "sorted_spikedata_curated.pkl"
if not pkl_path.exists():
    print("pkl not found in workdir — saving manually ...")
    with open(pkl_path, "wb") as f:
        pickle.dump(sd, f)
print(f"Curated pkl: {pkl_path} ({pkl_path.stat().st_size / 1e6:.1f} MB)")

# ── 4. Upload outputs to S3 ───────────────────────────────────────────────────
print("\nUploading to S3 ...")
for path in sorted(WORKDIR.rglob("*")):
    if not path.is_file():
        continue
    if path.suffix.lower() in {".h5", ".dat"}:
        continue
    rel = path.relative_to(WORKDIR).as_posix()
    s3_uri = f"{S3_OUT}/{rel}"
    run(["aws", "s3", "cp", str(path), s3_uri, "--endpoint-url", ENDPOINT])

print(f"\nDone. Uploaded to {S3_OUT}/")
print(f"Units: {sd.N},  Duration: {sd.length / 1000:.1f} s")
