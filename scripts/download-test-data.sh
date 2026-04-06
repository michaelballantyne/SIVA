#!/bin/bash
# Download all datasets required by the test suite.
#
# Usage: bash scripts/download-test-data.sh

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Downloading test datasets ==="
echo

for dataset in wildfire bonsai synthetic; do
    echo "--- $dataset ---"
    bash "datasets/$dataset/download.sh"
    echo
done

echo "All test datasets ready."
