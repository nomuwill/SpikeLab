"""
Raster plots (first 5 min) with 100 ms population burst detection.
UUID: 2025-12-20-e-H9SynGFP_Midbrain_control_baseline_haloperidol_rotenone
Animals: 23198f, 23206f, 23215f — D37, D37_haloperidol, D38_haloperidol24hr_baseline
Output: /home/sharf-lab/Desktop/Greg/
"""

import os
import sys
import shutil
import subprocess
import tempfile
import zipfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from spikelab.data_loaders import load_spikedata_from_kilosort
from spikelab.spikedata.plot_utils import plot_recording

UUID = "2025-12-20-e-H9SynGFP_Midbrain_control_baseline_haloperidol_rotenone"
S3_BASE = f"s3://braingeneers/ephys/{UUID}/derived/kilosort2"
S3_ENDPOINT = "https://s3.braingeneers.gi.ucsc.edu"

FS_HZ = 20000.0
WINDOW_MS = 300_000      # 5 minutes
BURST_SQUARE_WIDTH = 100  # ms — population rate smoothing window
BURST_GAUSS_SIGMA = 100   # ms

OUT_DIR = "/home/sharf-lab/Desktop/Greg"
os.makedirs(OUT_DIR, exist_ok=True)

RECORDINGS = [
    "23198f_MO_H9SynGFP_D37_12192025",
    "23198f_MO_H9SynGFP_D37_haloperidol_12192025",
    "23198f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025",
    "23206f_MO_H9SynGFP_D37_12192025",
    "23206f_MO_H9SynGFP_D37_haloperidol_12192025",
    "23206f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025",
    "23215f_MO_H9SynGFP_D37_12192025",
    "23215f_MO_H9SynGFP_D37_haloperidol_12192025",
    "23215f_MO_H9SynGFP_D38_haloperidol24hr_baseline_12202025",
]


def download_s3(s3_url, local_path):
    result = subprocess.run(
        ["aws", "s3", "cp", s3_url, local_path,
         "--endpoint-url", S3_ENDPOINT],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"Download failed:\n{result.stderr}")


for rec in RECORDINGS:
    print(f"\n{'='*60}")
    print(f"Processing: {rec}")
    tmp_dir = tempfile.mkdtemp()

    try:
        zip_path = os.path.join(tmp_dir, f"{rec}_phy.zip")
        s3_url = f"{S3_BASE}/{rec}_phy.zip"

        print(f"  Downloading phy.zip ...")
        download_s3(s3_url, zip_path)

        extract_dir = os.path.join(tmp_dir, "phy")
        os.makedirs(extract_dir)
        print(f"  Extracting ...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        print(f"  Loading spike data ...")
        sd = load_spikedata_from_kilosort(
            extract_dir,
            fs_Hz=FS_HZ,
            cluster_info_tsv="cluster_group.tsv",
            include_noise=False,
        )
        print(f"  Units: {sd.N},  Duration: {sd.length / 1000:.1f} s")

        print(f"  Detecting bursts (100 ms window) ...")
        tburst, edges, _ = sd.get_bursts(
            thr_burst=1.5,
            min_burst_diff=200,
            burst_edge_mult_thresh=0.3,
            square_width=BURST_SQUARE_WIDTH,
            gauss_sigma=BURST_GAUSS_SIGMA,
        )
        n_bursts_5min = int(((tburst * 1.0) < WINDOW_MS).sum())
        print(f"  Bursts in first 5 min: {n_bursts_5min} / {len(tburst)} total")

        save_path = os.path.join(OUT_DIR, f"{rec}_raster_5min.png")
        print(f"  Plotting -> {os.path.basename(save_path)} ...")

        fig = plot_recording(
            sd,
            show_raster=True,
            show_pop_rate=True,
            burst_times=tburst,
            burst_edges=edges,
            time_range=(0, WINDOW_MS),
            raster_bin_size_ms=1.0,
            pop_rate_params={
                "square_width": BURST_SQUARE_WIDTH,
                "gauss_sigma": BURST_GAUSS_SIGMA,
            },
            figsize=(16, 8),
            font_size=12,
            show=False,
            save_path=save_path,
        )
        plt.close(fig)
        print(f"  Saved.")

    except Exception as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

print(f"\nDone. Figures saved to {OUT_DIR}/")
