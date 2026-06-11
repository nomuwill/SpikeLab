#!/bin/bash
# Wrapper that calls SpikeLab's run_kilosort2_docker.sh with our local script.
#
# Usage:
#   bash scripts/run_kilosort2_docker.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SPIKELAB_DIR="${SCRIPT_DIR}/../SpikeLab"
RESULTS_DIR="${SCRIPT_DIR}/../data/spikesort_test/results"

exec bash "${SPIKELAB_DIR}/docker/kilosort2/run.sh" \
    "${SCRIPT_DIR}/run_kilosort2_docker.py" \
    "$RESULTS_DIR"
