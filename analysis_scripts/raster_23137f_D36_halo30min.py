import os, shutil, subprocess, tempfile, zipfile, warnings
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from spikelab.data_loaders import load_spikedata_from_kilosort
from spikelab.spikedata.plot_utils import plot_recording

UUID     = "2025-12-18-e-MO_H9SynGFP_D36_control_baseline_haloperidol"
REC      = "23137f_MO_H9SynGFP_D36_haloperidol_30min_12182025"
S3_URL   = f"s3://braingeneers/ephys/{UUID}/derived/kilosort2/{REC}_phy.zip"
ENDPOINT = "https://s3.braingeneers.gi.ucsc.edu"
OUT      = f"/home/sharf-lab/Desktop/Greg/{REC}_raster_5min.png"
WINDOW_MS = 300_000

tmp = tempfile.mkdtemp()
try:
    zip_path = os.path.join(tmp, "phy.zip")
    print("Downloading ...")
    subprocess.run(
        ["aws", "s3", "cp", S3_URL, zip_path, "--endpoint-url", ENDPOINT],
        check=True,
    )
    phy_dir = os.path.join(tmp, "phy")
    os.makedirs(phy_dir)
    print("Extracting ...")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(phy_dir)

    print("Loading ...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        sd = load_spikedata_from_kilosort(
            phy_dir, fs_Hz=20000,
            cluster_info_tsv="cluster_group.tsv",
            include_noise=False,
        )
    print(f"Units: {sd.N},  Duration: {sd.length / 1000:.1f} s")

    print("Detecting bursts ...")
    tburst, edges, _ = sd.get_bursts(1.5, 200, 0.3, square_width=100, gauss_sigma=100)
    print(f"Bursts in 5 min: {int((tburst < WINDOW_MS).sum())} / {len(tburst)} total")

    print("Plotting ...")
    fig = plot_recording(
        sd,
        show_raster=True,
        show_pop_rate=True,
        burst_times=tburst,
        burst_edges=edges,
        time_range=(0, WINDOW_MS),
        raster_bin_size_ms=1.0,
        pop_rate_params={"square_width": 100, "gauss_sigma": 100},
        figsize=(16, 8),
        font_size=12,
        show=False,
        save_path=None,
    )
    fig.suptitle(
        "23137f  H9SynGFP  —  D40 haloperidol 30 min  —  first 5 min  (100 ms burst detection)",
        fontsize=13, fontweight="bold",
    )
    fig.savefig(OUT, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved -> {OUT}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
