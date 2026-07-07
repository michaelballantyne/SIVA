from siva.spec_api import *

data = source("vtkXMLImageDataReader", FileName="scan.vti")

# Bad colormap name.
show(data, "a", lut="chartreuse_swirl")

# Bad representation.
show(data, "b", representation="Hologram")

# Bad background preset.
background("chartreuse_swirl")

# Bad raw_source scalar type.
ct = raw_source("scan.raw", scalar_type="float64_nope")
