#!/usr/bin/env bash
# Download the Stanford CT Head dataset and convert to a VTK .vti volume.
#
# Source: https://graphics.stanford.edu/data/voldata/
# Original format: 113 individual 256x256 slices, 16-bit big-endian
# Output format: vtkXMLImageData (.vti), uint16, zlib-compressed
#
# This is a CT scan of a human head, useful for testing isosurface extraction
# and volume rendering in SIVA.
#
# DSL usage:
#   data = load('data/cthead.vti')

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
DATA_DIR="${SCRIPT_DIR}/data"
OUTPUT_FILE="${DATA_DIR}/cthead.vti"
URL="https://graphics.stanford.edu/data/voldata/CThead.tar.gz"

if [ ! -x "$VENV_PYTHON" ]; then
    echo "Error: venv Python not found at $VENV_PYTHON" >&2
    echo "Create it first: python3 -m venv .venv && .venv/bin/pip install -e \".[dev]\"" >&2
    exit 1
fi

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

echo "Converting to .vti (byte-swap + wrap as vtkImageData)..."
"$VENV_PYTHON" - <<PYEOF
import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk

raw = np.fromfile("$TMPDIR/cthead_be.raw", dtype=">u2").astype("<u2")
assert raw.size == 256 * 256 * 113, f"unexpected voxel count: {raw.size}"

img = vtk.vtkImageData()
img.SetDimensions(256, 256, 113)
# Stanford CThead: in-plane 1 mm, slice thickness 2 mm.
img.SetSpacing(1.0, 1.0, 2.0)
img.SetOrigin(0.0, 0.0, 0.0)

arr = numpy_to_vtk(raw, deep=True, array_type=vtk.VTK_UNSIGNED_SHORT)
arr.SetName("scalars")
img.GetPointData().SetScalars(arr)

writer = vtk.vtkXMLImageDataWriter()
writer.SetFileName("$OUTPUT_FILE")
writer.SetCompressorTypeToZLib()
writer.SetInputData(img)
writer.Write()

print(f"Wrote {raw.nbytes} bytes of voxels ({raw.size} voxels)")
print(f"Value range: {raw.min()} - {raw.max()}")
PYEOF

echo "Wrote: $OUTPUT_FILE"
echo "Dimensions: 256 x 256 x 113, spacing 1x1x2 mm, uint16"
echo "Done."
