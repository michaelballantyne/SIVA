from siva.spec_api import *

# View 3: Vorticity — vertical component of curl(velocity)
# The z-component of vorticity reveals counter-rotating vortex pairs
# that drive lateral fire spread. Positive = counterclockwise, Negative = clockwise
# when viewed from above.

data = source("vtkXMLStructuredGridReader", FileName="datasets/wildfire/data/output.30000.vts")

# Build velocity vector and compute vorticity
velocity = make_vector(input=data, components=("u", "v", "w"), result="velocity")
vort = curl_vector(vector_field=velocity, output_field="vorticity")

# Extract ground level vorticity
near_ground = extract_grid(input=vort, VOI=[0, 599, 0, 499, 0, 0])

# Show vertical vorticity component (z) — reveals counter-rotating vortex pair
show(near_ground, "vort_z", color_by="vorticity", component="z",
     scalar_range=(-2, 2), lut="cool_to_warm",
     scalar_bar="Vertical Vorticity (1/s)")

# Fire outline by temperature on the same ground surface
fire_edge = contour(input=near_ground, ContourBy="theta", Isosurfaces=[310])
show(fire_edge, "fire_outline", color=(1.0, 1.0, 0.0), line_width=3, opacity=1.0)

scene_preset("dark")
camera(position=(88.2, -12.3, 555.1), focal_point=(88.2, -12.3, 98.6), up=(0, 1, 0))
