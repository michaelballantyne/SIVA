data = source("vtkXMLStructuredGridReader", FileName="datasets/wildfire/data/output.30000.vts")

# Compute vorticity vector
vel = make_vector(input=data, components=("u", "v", "w"), result="velocity")
vort = curl(vector_field=vel, result="vorticity", vector=True)

# Terrain
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.8), lut="grayscale",
     opacity=1.0, specular=0.05, specular_power=5)

# Fire — multiple isosurfaces for layered depth
fire_outer = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[350.0])
show(fire_outer, "fire_glow", color=(0.6, 0.1, 0.0), opacity=0.15)

fire_mid = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[450.0])
show(fire_mid, "fire_mid", color_by="theta", scalar_range=(350.0, 1200.0), lut="fire",
     opacity=0.5, specular=0.4, specular_power=20)

fire_core = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[700.0])
show(fire_core, "fire_core", color=(1.0, 0.9, 0.5), opacity=0.85,
     specular=0.7, specular_power=40)

# Cross-section: wider and taller clip to show vorticity contrast with surroundings
clipped = clip_box(input=vort, bounds=[79, 81, -100, 80, 140, 350])
cross = slice(input=clipped, origin=(80, -10, 175), normal=(1, 0, 0))
show(cross, "streamwise_vorticity", color_by="vorticity", component="x",
     scalar_range=(-4, 4), lut="cool_to_warm", opacity=1.0,
     scalar_bar="Streamwise Vorticity (1/s)")

# Slightly angled view
camera(position=(-250, -200, 300), focal_point=(80, -10, 190), up=(0, 0, 1))
title("Vorticity-Driven Lateral Spread")
scene_preset("black")
