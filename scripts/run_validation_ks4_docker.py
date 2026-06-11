"""Validation: Kilosort4 Docker"""
from spikelab.spike_sorting import sort_recording

recording_file = "/home/sharf-lab/Desktop/Analysis_shared/data/spikesort_test/data.raw.h5"
results_folder = "/home/sharf-lab/Desktop/Analysis_shared/data/spikesort_test/results_ks4_docker"

spike_data_list = sort_recording(
    recording_files=[recording_file],
    sorter="kilosort4",
    results_folders=[results_folder],
    use_docker=True,
    n_jobs=4,
    delete_inter=True,
    hdf5_plugin_path="/home/sharf-lab/MaxLab/so",
)

if spike_data_list:
    sd = spike_data_list[0]
    print(f"\nKS4 Docker: {sd.N} curated units, {sd.length / 1000:.1f} s duration")
else:
    print("KS4 Docker: Sorting did not produce results.")
