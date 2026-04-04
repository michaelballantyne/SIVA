#!/usr/bin/env bash
# Download the ctBones dataset (rotational C-arm x-ray scan of a human foot)
# from the TTK-data repository.
#
# Source: https://github.com/topology-tool-kit/ttk-data
# Format: VTK XML ImageData (.vti)
# Size: ~11 MB
#
# This is a CT scan dataset useful for testing isosurface extraction,
# volume rendering, and medical imaging visualization in VisLang.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${SCRIPT_DIR}/../data"
OUTPUT_FILE="${DATA_DIR}/ctBones.vti"
URL="https://raw.githubusercontent.com/topology-tool-kit/ttk-data/dev/ctBones.vti"

mkdir -p "$DATA_DIR"

if [ -f "$OUTPUT_FILE" ]; then
    echo "Dataset already exists: $OUTPUT_FILE"
    echo "Remove it first if you want to re-download."
    exit 0
fi

echo "Downloading ctBones.vti (CT scan of human foot, ~11 MB)..."
curl -L -o "$OUTPUT_FILE" "$URL"
echo "Downloaded to: $OUTPUT_FILE"

# Verify the file is valid XML (VTI files are XML-based)
if head -c 100 "$OUTPUT_FILE" | grep -q "VTKFile"; then
    echo "Verified: file appears to be valid VTK XML ImageData."
else
    echo "WARNING: file may not be valid VTK format."
fi

echo "Done."
