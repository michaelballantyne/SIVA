from siva.spec_api import *

# View 2: Fire plume — temperature isosurface colored by vertical velocity
# Shows the 3D structure of the fire plume and updraft/downdraft patterns

data = source("vtkXMLStructuredGridReader", FileName="datasets/wildfire/data/output.30000.vts")

# Extract ground for terrain context
ground = extract_grid(input=data, VOI=[0, 599, 0, 499, 0, 0])
show(ground, "terrain", color_by="rhof_1",
     scalar_range=(0, 0.6), lut="terrain", opacity=0.6)

# Temperature isosurface at 310K — captures the warm plume envelope
iso = contour(input=data, ContourBy="theta", Isosurfaces=[310])
show(iso, "plume", color_by="w",
     scalar_range=(-5, 10), lut="cool_to_warm",
     scalar_bar="Vertical Velocity (m/s)",
     specular=0.3, specular_power=20)

scene_preset("dark")
camera(position=(-352.0, -524.0, 657.1), focal_point=(160.9, 0.9, 216.8), up=(0.522, 0.187, 0.832))
