"""
Sort well001 using Kilosort2 (Docker) pinned to GPU 1.

Patches ContainerClient.__init__ before spikelab imports so that
CUDA_VISIBLE_DEVICES=1 is injected into the Docker container environment.
This means the MATLAB Runtime inside the container only sees GPU 1.
"""

import os
import time

# Pin GPU before any CUDA-touching imports
GPU_ID = "1"
os.environ["CUDA_VISIBLE_DEVICES"] = GPU_ID

# Patch ContainerClient so the Docker container inherits GPU_ID.
from spikeinterface.sorters.container_tools import ContainerClient

_base_init = ContainerClient.__init__


def _gpu_init(self, mode, container_image, volumes, py_user_base, extra_kwargs):
    if mode == "docker":
        extra_kwargs.setdefault("environment", {})
        extra_kwargs["environment"]["CUDA_VISIBLE_DEVICES"] = GPU_ID
    _base_init(self, mode, container_image, volumes, py_user_base, extra_kwargs)


ContainerClient.__init__ = _gpu_init

# ── Config ────────────────────────────────────────────────────────────────────

RECORDING_FILE = (
    "/home/sharf-lab/Desktop/Analysis_shared/data/spikesort_test/"
    "maxtwo_concat_test/baseline/M07653_Control_Baseline_2_19_2026.raw.h5"
)
RESULTS_DIR = (
    "/home/sharf-lab/Desktop/Analysis_shared/data/spikesort_test/"
    "parallel_gpu_test/gpu1_well001"
)
HDF5_PLUGIN = "/home/sharf-lab/MaxLab/so"

# ── Sort ──────────────────────────────────────────────────────────────────────

from spikelab.spike_sorting import sort_recording

print(f"[GPU {GPU_ID}] Starting Kilosort2 Docker sort of well001")
t0 = time.time()

results = sort_recording(
    recording_files=[RECORDING_FILE],
    results_folders=[RESULTS_DIR],
    sorter="kilosort2",
    use_docker=True,
    stream_id="well001",
    hdf5_plugin_path=HDF5_PLUGIN,
    freq_max=4500,
    snr_min=5.0,
    fr_min=0.05,
    isi_viol_max=1.0,
    spikes_min_first=30,
    spikes_min_second=50,
    compile_to_npz=True,
    create_figures=True,
)

elapsed = time.time() - t0
sd = results[0] if results else None

if sd is not None:
    print(
        f"[GPU {GPU_ID}] Done in {elapsed/60:.1f} min — "
        f"{sd.N} curated units, {sd.length/1000:.1f} s recording"
    )
else:
    print(f"[GPU {GPU_ID}] Sorting returned no results after {elapsed/60:.1f} min")
