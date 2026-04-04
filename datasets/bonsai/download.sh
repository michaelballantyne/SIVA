#!/bin/bash
# Download bonsai CT scan dataset
# Source: http://klacansky.com/open-scivis-datasets/
# CT scan of a bonsai tree (256x256x256, uint8)

set -euo pipefail
cd "$(dirname "$0")"

BASE_URL="https://mballantyne.net/visdata/scivis/bonsai"

mkdir -p data

for f in bonsai_256x256x256_uint8.raw bonsai.nhdr; do
    if [ -f "data/$f" ]; then
        echo "Already have $f"
    else
        echo "Downloading $f ..."
        curl -L -o "data/$f" "$BASE_URL/$f"
    fi
done

# Convert to VTI for VisLang compatibility
if [ ! -f "data/bonsai.vti" ]; then
    echo "Converting to VTI ..."
    python3 -c "
import vtk
r = vtk.vtkNrrdReader()
r.SetFileName('data/bonsai.nhdr')
r.Update()
d = r.GetOutput()
d.GetPointData().GetArray(0).SetName('density')
w = vtk.vtkXMLImageDataWriter()
w.SetFileName('data/bonsai.vti')
w.SetInputData(d)
w.SetDataModeToBinary()
w.Write()
print(f'Wrote bonsai.vti: {d.GetDimensions()} dims, scalar range {d.GetScalarRange()}')
"
fi

echo "Done. Files in $(pwd)/data/"
