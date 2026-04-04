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

# Convert to VTI for VisLang compatibility
if [ ! -f "data/hydrogen_atom.vti" ]; then
    echo "Converting to VTI ..."
    python3 -c "
import vtk
r = vtk.vtkNrrdReader()
r.SetFileName('data/hydrogen_atom.nhdr.txt')
r.Update()
d = r.GetOutput()
d.GetPointData().GetArray(0).SetName('probability')
w = vtk.vtkXMLImageDataWriter()
w.SetFileName('data/hydrogen_atom.vti')
w.SetInputData(d)
w.SetDataModeToBinary()
w.Write()
print(f'Wrote hydrogen_atom.vti: {d.GetDimensions()} dims, scalar range {d.GetScalarRange()}')
"
fi

echo "Done. Files in $(pwd)/data/"
