"""
Spike sort a MaxOne recording using Kilosort2 via Docker.

Uses SpikeLab's sort_with_kilosort2 with use_docker=True, which runs
Kilosort2 inside the spikeinterface/kilosort2-compiled-base Docker
container (compiled MATLAB Runtime — no MATLAB license needed).

Requires: Docker with NVIDIA GPU support.
"""

from spikelab.spike_sorting import sort_with_kilosort2

recording_file = "/home/sharf-lab/Desktop/Analysis_shared/data/spikesort_test/data.raw.h5"
results_folder = "/home/sharf-lab/Desktop/Analysis_shared/data/spikesort_test/results"

spike_data_list = sort_with_kilosort2(
    recording_files=[recording_file],
    results_folders=[results_folder],
    use_docker=True,
    n_jobs=4,
    delete_inter=True,
    hdf5_plugin_path="/home/sharf-lab/MaxLab/so",
)

if spike_data_list:
    sd = spike_data_list[0]
    print(f"\nSorting complete: {sd.N} curated units, {sd.length / 1000:.1f} s duration")
else:
    print("Sorting did not produce results.")
