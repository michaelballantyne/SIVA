#!/usr/bin/env bash
# Download a small CT scan dataset (bonsai, 256x256x256, uint8, ~16MB raw)
# from the Open SciVis Datasets collection by Pavol Klacansky.
#
# After downloading, convert the raw binary to VTI format for use with VTK.
#
# Usage:
#   bash demos/download_ct.sh
#
# The output VTI file will be at /tmp/bonsai.vti

set -euo pipefail

RAW_FILE="/tmp/bonsai_256x256x256_uint8.raw"
VTI_FILE="/tmp/bonsai.vti"

echo "Downloading bonsai CT dataset..."
curl -o "$RAW_FILE" "http://klacansky.com/open-scivis-datasets/bonsai/bonsai_256x256x256_uint8.raw"
echo "Downloaded: $RAW_FILE ($(du -h "$RAW_FILE" | cut -f1))"

echo "Converting raw to VTI..."
python3 - <<'PYEOF'
import vtk

reader = vtk.vtkImageReader2()
reader.SetFileName("/tmp/bonsai_256x256x256_uint8.raw")
reader.SetDataExtent(0, 255, 0, 255, 0, 255)
reader.SetDataScalarTypeToUnsignedChar()
reader.SetNumberOfScalarComponents(1)
reader.SetDataByteOrderToLittleEndian()
reader.SetFileDimensionality(3)
reader.Update()

output = reader.GetOutput()
print(f"  Points: {output.GetNumberOfPoints():,}")
print(f"  Bounds: {output.GetBounds()}")
print(f"  Scalar range: {output.GetScalarRange()}")

writer = vtk.vtkXMLImageDataWriter()
writer.SetFileName("/tmp/bonsai.vti")
writer.SetInputConnection(reader.GetOutputPort())
writer.SetCompressorTypeToZLib()
writer.Write()
print(f"  Written: /tmp/bonsai.vti")
PYEOF

echo "Done. VTI file ready at $VTI_FILE"
