from siva.spec_api import *

# Correct props on known classes -- matches the class-specific overload.
cone = source("vtkConeSource", Radius=1.5, Center=[0.0, 0.0, 0.0])
sphere = source("vtkSphereSource", Radius=2.0, ThetaResolution=24)

# Fixed-class wrapper verbs: valid passthrough props, including a mode literal
# (VectorMode) and SIVA-level extras (ContourBy / Isosurfaces / GlyphSource).
vol = source("vtkXMLImageDataReader", FileName="scan.vti")
iso = contour(input=vol, ContourBy="temperature", Isosurfaces=[300.0, 600.0])
arrow = source("vtkArrowSource", TipResolution=8)
arrows = glyph(input=iso, GlyphSource=arrow, VectorMode="UseVector", ScaleFactor=2.0)
cut = slice(input=vol, origin=(0, 0, 0), normal=(0, 0, 1))

# CutFunction (dict form) is accepted on clip classes via the direct filter()
# form, matching the runtime special-case the clip*/slice verbs rely on.
clipped = filter("vtkClipDataSet", input=vol,
                 CutFunction={"type": "Plane", "Origin": (0, 0, 0), "Normal": (1, 0, 0)})

# Same CutFunction dict routed through the clip() wrapper verb, whose **props
# forward to vtkClipDataSet -- the real pre-fix false-positive path.
clipped_wrap = clip(input=vol,
                    CutFunction={"type": "Plane", "Origin": (0, 0, 0), "Normal": (1, 0, 0)})

# vtkNrrdReader accepts a scalar-type name string on DataScalarType (derived,
# mirroring vtkImageReader2's sibling override).
nrrd = source("vtkNrrdReader", FileName="vol.nrrd", DataScalarType="unsigned_short")

# vtkCellDerivatives mode families take name-string literals, not ints.
deriv = filter("vtkCellDerivatives", input=vol,
               VectorMode="ComputeVorticity", TensorMode="ComputeGradient")

show(iso, "iso")
