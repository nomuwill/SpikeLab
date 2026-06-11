#!/bin/bash
# Run all three validation jobs sequentially (they share the GPU).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SPIKELAB_DIR="${SCRIPT_DIR}/../SpikeLab"
BASE="${SCRIPT_DIR}/../data/spikesort_test"
RUNNER="${SPIKELAB_DIR}/docker/kilosort2/run.sh"

# Clean all result dirs
for dir in results_ks2_docker results_ks4 results_ks4_docker; do
    rm -rf "${BASE}/${dir}"
    mkdir -p "${BASE}/${dir}"
done

echo "========================================="
echo "  Running 3 validation jobs sequentially"
echo "========================================="
echo ""

echo ">>> [1/3] Kilosort2 Docker"
bash "$RUNNER" "${SCRIPT_DIR}/run_validation_ks2_docker.py" "${BASE}/results_ks2_docker"
echo ""

echo ">>> [2/3] Kilosort4 local"
bash "$RUNNER" "${SCRIPT_DIR}/run_validation_ks4.py" "${BASE}/results_ks4"
echo ""

echo ">>> [3/3] Kilosort4 Docker"
bash "$RUNNER" "${SCRIPT_DIR}/run_validation_ks4_docker.py" "${BASE}/results_ks4_docker"
echo ""

echo "========================================="
echo "  All validation runs complete"
echo "========================================="
