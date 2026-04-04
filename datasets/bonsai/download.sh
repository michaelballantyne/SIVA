#!/bin/bash
# Download bonsai CT scan dataset
# Source: http://klacansky.com/open-scivis-datasets/
# CT scan of a bonsai tree (256x256x256, uint8)

set -euo pipefail
cd "$(dirname "$0")"

BASE_URL="https://mballantyne.net/visdata/scivis/bonsai"

mkdir -p data

for f in bonsai_256x256x256_uint8.raw bonsai.nhdr.txt; do
    if [ -f "data/$f" ]; then
        echo "Already have $f"
    else
        echo "Downloading $f ..."
        curl -L -o "data/$f" "$BASE_URL/$f"
    fi
done

echo "Done. Files in $(pwd)/data/"
