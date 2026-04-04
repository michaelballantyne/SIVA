#!/bin/bash
# Generate a synthetic 64x64x64 VTK ImageData dataset.
# Unlike the wildfire dataset this doesn't download anything --
# it creates the file locally using VTK's Python bindings.

set -euo pipefail
cd "$(dirname "$0")"

OUTPUT="data/output.vti"

if [ -f "$OUTPUT" ]; then
    echo "Already have $OUTPUT"
else
    echo "Generating synthetic dataset ..."
    python3 generate.py
fi

echo "Done. Files in $(pwd)/data/"
