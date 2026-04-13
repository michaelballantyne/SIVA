# View 5: 3D streamlines + transparent fire volume
# Streamlines seeded on a y-line upwind show lateral deflection at fire flanks
# Volume rendering of temperature shows the fire plume context

data = source("vtkXMLStructuredGridReader", FileName="datasets/wildfire/data/output.30000.vts")

# Build velocity vector
velocity = make_vector(input=data, components=("u", "v", "w"), result="velocity")

# Seed line near ground level, upwind of fire
seeds = source("vtkLineSource",
               Point1=(-50, -200, 185),
               Point2=(-50, 200, 185),
               Resolution=80)

streams = stream_tracer(input=velocity, SeedSource=seeds, Vectors="velocity",
                        IntegrationDirection="Forward",
                        MaximumNumberOfSteps=4000,
                        MaximumPropagation=800)
tubes = tube(input=streams, Radius=0.4, NumberOfSides=6)
show(tubes, "flow", color_by="w",
     scalar_range=(-5, 10), lut="cool_to_warm",
     scalar_bar="Vertical Velocity (m/s)",
     specular=0.4, specular_power=20)

# Transparent volume render of fire (theta)
fire_region = threshold(input=data, ThresholdBy="theta", ThresholdRange=[305, 1200])
show(fire_region, "fire_vol", representation="Volume",
     color_by="theta", scalar_range=(305, 500), lut="fire",
     opacity_function=[(305, 0.0), (310, 0.0), (320, 0.02), (350, 0.06), (400, 0.1), (500, 0.15)],
     gradient_opacity=True, volume_resolution=200)

# Ground terrain with fuel for context
ground = extract_grid(input=data, VOI=[0, 599, 0, 499, 0, 0])
show(ground, "terrain", color_by="rhof_1",
     scalar_range=(0, 0.6), lut="terrain", opacity=0.5)

scene_preset("dark")
camera(position=(-264.0, -161.8, 441.4), focal_point=(73.8, -9.1, 183.6), up=(0.581, 0.083, 0.810))
