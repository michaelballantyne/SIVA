#!/bin/bash
# Download hydrogen atom dataset
# Source: http://klacansky.com/open-scivis-datasets/
# Spatial probability distribution of electron in hydrogen atom (128x128x128, uint8)

set -euo pipefail
cd "$(dirname "$0")"

BASE_URL="https://mballantyne.net/visdata/scivis/hydrogen_atom"

mkdir -p data

for f in hydrogen_atom_128x128x128_uint8.raw hydrogen_atom.nhdr.txt; do
    if [ -f "data/$f" ]; then
        echo "Already have $f"
    else
        echo "Downloading $f ..."
        curl -L -o "data/$f" "$BASE_URL/$f"
    fi
done

echo "Done. Files in $(pwd)/data/"
