"""
Spike sort the M07653 MaxTwo chip with baseline + haloperidol recordings
concatenated per well, using Kilosort2 via Docker.

Strategy
--------
SpikeLab's sort_recording concatenates all .raw.h5 files inside a directory
when the directory is passed as the recording. We don't have both files in
one directory, so we stage symlinks into a single concat-input directory
with numeric prefixes that natsort to baseline-first, halo-second.

We then loop over the 6 MaxTwo wells (well000..well005), calling
sort_recording once per stream. The sorter concatenates the two recordings
for that well, sorts the joined trace, and splits the result back into
per-recording SpikeData via sd.split_epochs() (chunk0=baseline, chunk1=halo).

Outputs
-------
data/spikesort_test/maxtwo_concat_test/sorted/
  well000/
    sorted_spikedata_curated.pkl   <-- concatenated curated SpikeData
    sorted_spikedata.pkl           <-- pre-curation (save_raw_pkl=True)
    chunk0/  chunk1/               <-- per-epoch compiled outputs
    figures/                       <-- QC figures
    sorting_report.md
    recording_report.json
  well001/  ...  well005/

After sorting, load with:
    import pickle
    sd = pickle.load(open(".../well000/sorted_spikedata_curated.pkl","rb"))
    baseline_sd, halo_sd = sd.split_epochs()
"""

import os
import sys
import time
from pathlib import Path

# Maxwell HDF5 decompression plugin (verified present)
HDF5_PLUGIN_PATH = "/home/sharf-lab/MaxLab/so"
os.environ["HDF5_PLUGIN_PATH"] = HDF5_PLUGIN_PATH

from spikelab.spike_sorting import sort_recording


PROJECT = Path("/home/sharf-lab/Desktop/Analysis_shared")
RAW_ROOT = PROJECT / "data" / "spikesort_test" / "maxtwo_concat_test"
BASELINE_H5 = RAW_ROOT / "baseline" / "M07653_Control_Baseline_2_19_2026.raw.h5"
HALO_H5 = RAW_ROOT / "haloperidol" / "M07653_Control_Haloperidol_T1_2_19_2026.raw.h5"

STAGING_DIR = RAW_ROOT / "_concat_input_baseline_halo"
SORTED_ROOT = RAW_ROOT / "sorted"

WELLS = ["well000", "well001", "well002", "well003", "well004", "well005"]


def stage_symlinks() -> Path:
    """Create a directory with two symlinks ordered baseline -> halo."""
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    # Numeric prefix guarantees natsort order regardless of original names.
    # SpikeLab uses natsorted(p.name endswith '.raw.h5') to determine concat order.
    targets = [
        ("01_baseline.raw.h5", BASELINE_H5),
        ("02_haloperidol.raw.h5", HALO_H5),
    ]
    for link_name, src in targets:
        link = STAGING_DIR / link_name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(src)
    print(f"Staged concat input at {STAGING_DIR}:")
    for p in sorted(STAGING_DIR.iterdir()):
        print(f"  {p.name} -> {os.readlink(p)}")
    return STAGING_DIR


def sort_one_well(stream_id: str, staging_dir: Path) -> dict:
    """Run sort_recording for one well, return a small status dict."""
    well_results = SORTED_ROOT / stream_id
    well_results.mkdir(parents=True, exist_ok=True)
    well_inter = SORTED_ROOT / f"_inter_{stream_id}"

    print("\n" + "=" * 70)
    print(f"Sorting {stream_id} (baseline + haloperidol concatenated)")
    print(f"  results -> {well_results}")
    print("=" * 70)

    t0 = time.time()
    try:
        results = sort_recording(
            recording_files=[str(staging_dir)],
            results_folders=[str(well_results)],
            intermediate_folders=[str(well_inter)],
            sorter="kilosort2",
            use_docker=True,
            stream_id=stream_id,
            hdf5_plugin_path=HDF5_PLUGIN_PATH,
            # MaxTwo records at 10 kHz (Nyquist=5 kHz); SpikeLab's default
            # freq_max=6000 exceeds Nyquist. 300-3000 Hz is the standard
            # KS2 spike band.
            freq_min=300,
            freq_max=3000,
            # curation defaults: snr_min=5.0, fr_min=0.05, isi_viol_max=0.01,
            #   spikes_min_first=30, spikes_min_second=50, std_norm_max=1.0
            create_figures=True,
            save_raw_pkl=True,
            compile_to_npz=True,
            # 30s canary catches Docker/MEX/preprocessing failures in
            # seconds rather than ~15 min into the full sort. Now safe
            # to use with directory concatenation thanks to the
            # REC_CHUNKS_FROM_CONCAT flag in load_recording (and the
            # snapshot/restore in run_canary that prevents the
            # canary's narrowed config from leaking into the full
            # sort).
            canary_first_n_s=30.0,
        )
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  FAILED after {elapsed:.0f}s: {type(e).__name__}: {e}")
        return {"stream_id": stream_id, "status": "failed",
                "error": f"{type(e).__name__}: {e}",
                "elapsed_s": elapsed}

    elapsed = time.time() - t0
    # sort_recording catches per-recording errors internally and returns an
    # empty list rather than raising. Treat that as a failure.
    if not results or len(results) < 2:
        msg = (f"sort_recording returned {len(results) if results else 0} "
               "SpikeData (expected 2 epochs). See sorting_*.log in results dir.")
        print(f"  FAILED after {elapsed:.0f}s: {msg}")
        return {"stream_id": stream_id, "status": "failed",
                "error": msg, "elapsed_s": elapsed}

    # results is list[SpikeData] split per input recording (len == 2 here)
    summary = {"stream_id": stream_id, "status": "ok",
               "elapsed_s": elapsed, "epochs": []}
    for i, sd in enumerate(results):
        label = "baseline" if i == 0 else "haloperidol"
        summary["epochs"].append({
            "epoch": label,
            "n_units": int(sd.N),
            "duration_s": float(sd.length / 1000.0),
        })
        print(f"  {label}: {sd.N} curated units, {sd.length/1000:.1f}s")
    print(f"  total elapsed: {elapsed/60:.1f} min")
    return summary


def main():
    print(f"HDF5_PLUGIN_PATH = {HDF5_PLUGIN_PATH}")
    print(f"Baseline file:     {BASELINE_H5}  ({BASELINE_H5.stat().st_size/1e9:.1f} GB)")
    print(f"Haloperidol file:  {HALO_H5}  ({HALO_H5.stat().st_size/1e9:.1f} GB)")
    print(f"Sorted output:     {SORTED_ROOT}")

    staging_dir = stage_symlinks()
    SORTED_ROOT.mkdir(parents=True, exist_ok=True)

    summaries = []
    for sid in WELLS:
        summaries.append(sort_one_well(sid, staging_dir))

    print("\n" + "=" * 70)
    print("RUN SUMMARY")
    print("=" * 70)
    for s in summaries:
        if s["status"] == "ok":
            ep = s["epochs"]
            print(f"  {s['stream_id']}: OK  "
                  f"baseline={ep[0]['n_units']}u  halo={ep[1]['n_units']}u  "
                  f"({s['elapsed_s']/60:.1f} min)")
        else:
            print(f"  {s['stream_id']}: FAILED  {s['error']}")

    n_ok = sum(1 for s in summaries if s["status"] == "ok")
    print(f"\n{n_ok}/{len(summaries)} wells sorted successfully.")
    return 0 if n_ok == len(summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
