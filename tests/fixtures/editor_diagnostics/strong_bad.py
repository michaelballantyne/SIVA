from siva.spec_api import *

vol = source("vtkXMLImageDataReader", FileName="scan.vti")

# Misspelled passthrough prop on a fixed-class wrapper verb -- the static
# mirror of siva.filters' runtime property-typo validator.
iso = contour(input=vol, ContourBy="t", Isosurfaces=[1.0], Bogusss=9)

# Invalid mode-setter literal on a fixed-class wrapper verb.
arrows = glyph(input=vol, VectorMode="Sideways")

# --- source()/filter()-form negatives (no escape-hatch overload) ---

# Misspelled prop on the source() form: with the catch-all overload gone, the
# per-class TypedDict overload for vtkConeSource flags the typo.
badcone = source("vtkConeSource", Radiuss=1.5)

# Wrong-typed value on the filter() form: ContourBy is typed str, not int.
badcontour = filter("vtkContourFilter", input=vol, ContourBy=999)

# Non-whitelisted class name: matches no source() overload at all.
bogus = source("vtkNotARealSource", whatever=3)
