from siva.spec_api import *

data = source("vtkXMLImageDataReader", FileName="scan.vti")

# Valid closed-enum display props on show().
show(data, "a", lut="fire", representation="Surface", opacity=0.5)

# background() accepts a preset name or an explicit (r, g, b) triple.
background("dark")
background(0.05, 0.05, 0.1)

# raw_source(scalar_type=) accepts the documented type names (and raw ints).
ct = raw_source("scan.raw", dimensions=(64, 64, 64), scalar_type="unsigned_short")
