"""Worker subprocess for the crash-recovery stress test.

Runs a canary against the staging directory in the foreground, then
exits normally if it survives. The parent test process is expected
to SIGKILL it before that happens.

Usage:
    python _canary_worker_for_kill_test.py <inter_path> <staging_path>
"""
import os
import sys

os.environ["HDF5_PLUGIN_PATH"] = "/home/sharf-lab/MaxLab/so"

from spikelab.spike_sorting.canary import run_canary
from spikelab.spike_sorting.config import SortingPipelineConfig


def main() -> int:
    inter_path = sys.argv[1]
    staging_path = sys.argv[2]

    config = SortingPipelineConfig.from_kwargs(
        stream_id="well000",
        hdf5_plugin_path="/home/sharf-lab/MaxLab/so",
        freq_min=300,
        freq_max=3000,
        use_docker=True,
    )
    config.execution.canary_first_n_s = 30.0

    print(f"[worker pid={os.getpid()}] starting canary against {staging_path}")
    print(f"[worker pid={os.getpid()}] inter_path: {inter_path}")
    sys.stdout.flush()

    result = run_canary(
        config,
        recording=None,
        rec_path=staging_path,
        inter_path=inter_path,
        sorter_name="kilosort2",
        rec_name="kill_test",
    )
    print(f"[worker pid={os.getpid()}] canary completed: {result!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
