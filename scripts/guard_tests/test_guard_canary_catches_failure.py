"""Stress test #1: canary catches a deliberately broken environment.

The canary's contract is to surface classified failures BEFORE the
full sort burns hours. We patch ``docker_utils.get_docker_image`` to
return a nonexistent image tag so the canary's KS2 launch must fail
when Docker tries to pull it. A classified ``DockerEnvironmentError``
(or any subclass of ``EnvironmentSortFailure``) returned from the
canary proves it is doing its job.

Bypasses the outer preflight (which would catch the same problem at
``_check_docker_sorter``) by calling ``run_canary`` directly — that
way we exercise the canary's own classified-error routing.
"""
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["HDF5_PLUGIN_PATH"] = "/home/sharf-lab/MaxLab/so"

from spikelab.spike_sorting import docker_utils as _docker_utils
from spikelab.spike_sorting._exceptions import (
    EnvironmentSortFailure,
    SpikeSortingClassifiedError,
)
from spikelab.spike_sorting.canary import run_canary
from spikelab.spike_sorting.config import SortingPipelineConfig

STAGING = Path(
    "/home/sharf-lab/Desktop/Analysis_shared/data/spikesort_test/"
    "maxtwo_concat_test/_concat_input_baseline_halo"
)
INTER = Path("/tmp/canary_failure_test_inter")

BOGUS_IMAGE = "spikeinterface/kilosort2-compiled-base:NONEXISTENT_GUARD_STRESS_TEST"


def main() -> int:
    if INTER.exists():
        shutil.rmtree(INTER)
    INTER.mkdir(parents=True)

    # Replace the image lookup so KS2 docker launch points at a tag
    # that the registry will refuse to serve.
    original_get_image = _docker_utils.get_docker_image
    _docker_utils.get_docker_image = lambda sorter, cuda_tag=None: BOGUS_IMAGE
    print(f"Patched get_docker_image -> {BOGUS_IMAGE}")

    try:
        config = SortingPipelineConfig.from_kwargs(
            stream_id="well000",
            hdf5_plugin_path="/home/sharf-lab/MaxLab/so",
            freq_min=300,
            freq_max=3000,
            use_docker=True,
        )
        config.execution.canary_first_n_s = 30.0

        print(f"Staging:  {STAGING}")
        print(f"Inter:    {INTER}\n")

        t0 = time.time()
        result = run_canary(
            config,
            recording=None,
            rec_path=str(STAGING),
            inter_path=str(INTER),
            sorter_name="kilosort2",
            rec_name="well000_failure_canary",
        )
        elapsed = time.time() - t0
    finally:
        _docker_utils.get_docker_image = original_get_image

    print(f"\n{'=' * 60}")
    print(f"Canary returned in {elapsed:.1f} s")
    print(f"Result: {result!r}")
    print(f"{'=' * 60}")

    if result is None:
        print("UNEXPECTED: canary returned None — the broken image did not")
        print("surface as a classified failure. Either the canary swallowed")
        print("the error as 'non-classified' (smoke-test-not-hard-gate), or")
        print("Docker still managed to pull / cache it.")
        # Look for the canary's tee log to inspect what really happened.
        for log in INTER.rglob("*.log"):
            print(f"  log: {log}")
        return 1

    if isinstance(result, EnvironmentSortFailure):
        print(f"PASS: canary surfaced an EnvironmentSortFailure subclass: "
              f"{type(result).__name__}")
        print(f"      message: {result}")
        return 0

    if isinstance(result, SpikeSortingClassifiedError):
        print(f"PARTIAL: canary surfaced a classified failure but not the "
              f"expected EnvironmentSortFailure family: "
              f"{type(result).__name__}")
        print(f"         message: {result}")
        # Still a useful signal — canary did its job, just routed
        # through a different classification.
        return 0

    print(f"UNEXPECTED: canary returned {type(result).__name__} "
          "(not a SpikeSortingClassifiedError)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
