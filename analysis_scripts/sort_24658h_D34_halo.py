"""Sort 24658h D34 Dopamine+Haloperidol — single recording."""
import os, sys, subprocess, pickle
from pathlib import Path
from spikelab.spike_sorting import sort_recording

ENDPOINT = "https://s3.braingeneers.gi.ucsc.edu"
S3_ROOT  = "s3://braingeneers/ephys"
UUID     = "2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH"
STEM     = "24658h_MO_H9SynGFP_D34_Dopamine_haloperidol_02242026"
WORKDIR  = Path(f"/home/sharf-lab/Desktop/Analysis_shared/sort_workdir/{STEM}")
WORKDIR.mkdir(parents=True, exist_ok=True)
LOCAL_H5 = WORKDIR / f"{STEM}.raw.h5"

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0: raise RuntimeError(r.stderr.strip())

print(f"Downloading {STEM} ...")
run(["aws","s3","cp",f"{S3_ROOT}/{UUID}/original/data/{STEM}.raw.h5",str(LOCAL_H5),"--endpoint-url",ENDPOINT])
print(f"Downloaded: {LOCAL_H5.stat().st_size/1e9:.2f} GB")

print("Sorting with KS2 ...")
results = sort_recording(
    recording_files=[str(LOCAL_H5)], results_folders=[str(WORKDIR)],
    sorter="kilosort2", use_docker=True,
    hdf5_plugin_path="/home/sharf-lab/MaxLab/so/",
    sorter_inactivity_base_s=7200.0, sorter_inactivity_per_min_s=300.0,
    recompute_recording=False,
)
sd = results[0]
print(f"Sorted: {sd.N} units, {sd.length/1000:.1f} s")

pkl_path = WORKDIR / "sorted_spikedata_curated.pkl"
if not pkl_path.exists():
    with open(pkl_path, "wb") as f: pickle.dump(sd, f)

s3_prefix = f"{S3_ROOT}/{UUID}/derived/ks2SpikeLab/{STEM}"
uploaded = 0
for path in sorted(WORKDIR.rglob("*")):
    if not path.is_file(): continue
    if path.suffix.lower() in {".h5", ".dat"}: continue
    rel = path.relative_to(WORKDIR).as_posix()
    r = subprocess.run(["aws","s3","cp",str(path),f"{s3_prefix}/{rel}","--endpoint-url",ENDPOINT],
                       capture_output=True, text=True)
    if r.returncode == 0: uploaded += 1
print(f"Uploaded {uploaded} files to {s3_prefix}/")

if LOCAL_H5.exists():
    LOCAL_H5.unlink()
    print("Raw h5 cleaned up.")
print("Done.")
