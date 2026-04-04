#!/bin/bash
# Download wildfire simulation dataset and related papers
# Source: HIGRAD/FIRETEC coupled fire-atmosphere simulation
# Context: 2022 IEEE SciVis Contest

set -euo pipefail
cd "$(dirname "$0")"

BASE_URL="https://mballantyne.net/visdata"

mkdir -p data

for f in output.30000.vts \
         scivis-report_8947f.pdf \
         2022_IEEE_Scientific_Visualization_Contest_Winner_Multifield_Analysis_of_Vorticity-Driven_Lateral_Spread_in_Wildfire_Ensembles.pdf; do
    if [ -f "data/$f" ]; then
        echo "Already have $f"
    else
        echo "Downloading $f ..."
        curl -L -o "data/$f" "$BASE_URL/$f"
    fi
done

echo "Done. Files in $(pwd)/data/"
