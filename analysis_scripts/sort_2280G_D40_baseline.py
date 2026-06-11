"""Sort 2280G_MO_H9SynGFP_D40_Baseline_02212026 locally with Kilosort2 (Docker)."""

import pickle
from pathlib import Path
from spikelab.spike_sorting import sort_recording

RAW_H5 = (
    "/media/sharf-lab/Extreme Pro/Midbrain_GK/"
    "Baseline_haloperidol_dopamine_SCH_02252026/"
    "2026-02-25-e-H9SynGFP_MO_Baseline_haloperidol_dopamine_SCH/"
    "original/data/2280G_MO_H9SynGFP_D40_Baseline_02212026.raw.h5"
)

OUT_DIR = Path("/home/sharf-lab/Desktop/Analysis_shared/sort_workdir"
               "/2280G_MO_H9SynGFP_D40_Baseline_02212026")
OUT_DIR.mkdir(parents=True, exist_ok=True)

print(f"Sorting: {RAW_H5}")
print(f"Output:  {OUT_DIR}")

results = sort_recording(
    recording_files=[RAW_H5],
    results_folders=[str(OUT_DIR)],
    sorter="kilosort2",
    use_docker=True,
    hdf5_plugin_path="/home/sharf-lab/MaxLab/so/",
    sorter_inactivity_base_s=7200.0,
    sorter_inactivity_per_min_s=300.0,
    recompute_recording=False,
)

sd = results[0]
print(f"\nDone — {sd.N} units, {sd.length/1000:.1f} s")

pkl_path = OUT_DIR / "sorted_spikedata_curated.pkl"
if not pkl_path.exists():
    with open(pkl_path, "wb") as f:
        pickle.dump(sd, f)
    print(f"Saved pkl → {pkl_path}")
else:
    print(f"pkl already present: {pkl_path}")
