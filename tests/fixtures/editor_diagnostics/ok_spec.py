"""Fixture spec exercising several DSL verbs, including builtin-shadowing ones.

Used by tests/test_editor_diagnostics.py to assert pyright reports zero
errors for a spec that uses `filter` and `slice` -- names that intentionally
shadow Python builtins in the DSL namespace (see siva/sandbox.py).
"""

from siva.spec_api import *

data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
iso = contour(input=data, ContourBy="temperature", Isosurfaces=[500.0])
show(iso, "iso", color_by="temperature", scalar_range=(300.0, 800.0), lut="fire")

trimmed = filter("vtkThreshold", input=data, ThresholdBy="temperature",
                 ThresholdRange=[300.0, 800.0])
cross = slice(input=trimmed, origin=(0, 0, 0), normal=(1, 0, 0))
show(cross, "cross", color_by="temperature")

camera(position=(100, 100, 100), focal_point=(0, 0, 0))
background("dark")

x = math.sqrt(4.0)
