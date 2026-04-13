# View 1: Combined overview — fire scar + plume + lateral wind arrows
# Shows terrain fuel density, fire plume isosurface, and ground-level
# lateral wind to tie together the story of vorticity-driven lateral spread.

data = source("vtkXMLStructuredGridReader", FileName="datasets/wildfire/data/output.30000.vts")

# Ground terrain colored by fuel density (burn scar)
ground = extract_grid(input=data, VOI=[0, 599, 0, 499, 0, 0])
show(ground, "fuel", color_by="rhof_1",
     scalar_range=(0, 0.6), lut="terrain",
     scalar_bar="Fuel Density (kg/m³)")

# Fire plume — temperature isosurface colored by lateral velocity
# This directly shows how the plume structure relates to lateral wind
iso = contour(input=data, ContourBy="theta", Isosurfaces=[310])
show(iso, "plume", color_by="v",
     scalar_range=(-5, 5), lut="cool_to_warm",
     opacity=1.0, specular=0.3,
     scalar_bar="Lateral Velocity v (m/s)")

# Sparse velocity arrows on an elevated slice (~50m above ground)
elevated = extract_grid(input=data, VOI=[0, 599, 0, 499, 5, 5])
sparse = mask_points(input=elevated, OnRatio=500, RandomMode=False)
vel = make_vector(input=sparse, components=("u", "v", "w"), result="velocity")
speed = compute_magnitude(input=vel, components=("u", "v", "w"), result="speed")
arrow = source("vtkArrowSource", TipResolution=12, ShaftResolution=12)
arrows = glyph(input=speed, GlyphSource=arrow,
               OrientationArray="velocity",
               ScaleArray="speed", ScaleFactor=5.0)
show(arrows, "wind_arrows", color_by="speed",
     scalar_range=(0, 20), lut="wind", opacity=0.9,
     specular=0.6, specular_power=30)

scene_preset("dark")
camera(position=(-77.3, -707.3, 484.1), focal_point=(104.5, -11.4, 159.8), up=(0.107, 0.397, 0.912))
