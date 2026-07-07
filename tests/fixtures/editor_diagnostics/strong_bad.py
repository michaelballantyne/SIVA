from siva.spec_api import *

vol = source("vtkXMLImageDataReader", FileName="scan.vti")

# Misspelled passthrough prop on a fixed-class wrapper verb -- the static
# mirror of siva.filters' runtime property-typo validator.
iso = contour(input=vol, ContourBy="t", Isosurfaces=[1.0], Bogusss=9)

# Invalid mode-setter literal on a fixed-class wrapper verb.
arrows = glyph(input=vol, VectorMode="Sideways")
