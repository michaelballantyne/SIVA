# View 4: Lateral wind (v-component) on the ground surface
# The v-component is the cross-wind (lateral) velocity.
# In vorticity-driven lateral spread, counter-rotating vortices
# create outward-directed lateral winds at the fire flanks,
# pushing the fire sideways beyond what pure wind-driven spread would produce.

data = source("vtkXMLStructuredGridReader", FileName="datasets/wildfire/data/output.30000.vts")

# Extract ground layer
ground = extract_grid(input=data, VOI=[0, 599, 0, 499, 0, 0])

# Show lateral velocity (v) — positive = spread to +y, negative = spread to -y
# A dipole pattern (positive on one flank, negative on the other) indicates
# vortex-driven outward push
show(ground, "v_wind", color_by="v",
     scalar_range=(-5, 5), lut="cool_to_warm",
     scalar_bar="Lateral Wind v (m/s)")

# Fire outline by temperature
fire_edge = contour(input=ground, ContourBy="theta", Isosurfaces=[310])
show(fire_edge, "fire_outline", color=(1.0, 1.0, 0.0), line_width=3, opacity=1.0)

scene_preset("dark")
camera(position=(81, -9.5, 534.1), focal_point=(81, -9.5, 85.2), up=(0, 1, 0))
