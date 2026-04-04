#!/usr/bin/env bash
# Download the Stanford CT Head dataset and convert to a raw volume file.
#
# Source: https://graphics.stanford.edu/data/voldata/
# Original format: 113 individual 256x256 slices, 16-bit big-endian
# Output format: Single raw binary volume, 256x256x113, uint16 little-endian
# Output size: ~14 MB
#
# This is a CT scan of a human head, useful for testing isosurface extraction
# and volume rendering in VisLang.
#
# DSL usage:
#   data = raw_source('data/cthead_256x256x113_uint16.raw',
#                      dimensions=(256, 256, 113),
#                      scalar_type='unsigned_short')
#   show(data, 'cthead', scalar_range=(0, 3272), lut='grayscale')

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="${SCRIPT_DIR}/../data"
OUTPUT_FILE="${DATA_DIR}/cthead_256x256x113_uint16.raw"
URL="https://graphics.stanford.edu/data/voldata/CThead.tar.gz"

mkdir -p "$DATA_DIR"

if [ -f "$OUTPUT_FILE" ]; then
    echo "Dataset already exists: $OUTPUT_FILE"
    echo "Remove it first if you want to re-download."
    exit 0
fi

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "Downloading CThead.tar.gz (~7.3 MB) from Stanford..."
curl -L -o "$TMPDIR/CThead.tar.gz" "$URL"

echo "Extracting slices..."
cd "$TMPDIR"
tar xzf CThead.tar.gz

echo "Concatenating 113 slices into single volume..."
for i in $(seq 1 113); do
    cat "CThead.$i"
done > cthead_be.raw

echo "Converting big-endian to little-endian..."
python3 -c "
import numpy as np
data = np.fromfile('cthead_be.raw', dtype='>u2')
data.astype('<u2').tofile('$OUTPUT_FILE')
print(f'Wrote {data.nbytes} bytes ({data.shape[0]} voxels)')
print(f'Value range: {data.min()} - {data.max()}')
"

echo "Downloaded to: $OUTPUT_FILE"
echo "Dimensions: 256 x 256 x 113"
echo "Scalar type: unsigned_short (uint16, little-endian)"
echo "Done."
