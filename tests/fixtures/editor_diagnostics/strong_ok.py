from siva.spec_api import *

# Correct props on known classes -- matches the class-specific overload.
cone = source("vtkConeSource", Radius=1.5, Center=[0.0, 0.0, 0.0])
sphere = source("vtkSphereSource", Radius=2.0, ThetaResolution=24)

# Unknown class name falls through to the str escape-hatch overload.
mystery = source("vtkNotARealSource", whatever=3, another="ok")

# Fixed-class wrapper verbs: valid passthrough props, including a mode literal
# (VectorMode) and SIVA-level extras (ContourBy / Isosurfaces / GlyphSource).
vol = source("vtkXMLImageDataReader", FileName="scan.vti")
iso = contour(input=vol, ContourBy="temperature", Isosurfaces=[300.0, 600.0])
arrow = source("vtkArrowSource", TipResolution=8)
arrows = glyph(input=iso, GlyphSource=arrow, VectorMode="UseVector", ScaleFactor=2.0)
cut = slice(input=vol, origin=(0, 0, 0), normal=(0, 0, 1))
show(iso, "iso")
