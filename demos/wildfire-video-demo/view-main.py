from siva.spec_api import *

data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

ground = extract_grid(input=data, VOI=[251, 850, 0, 499, 0, 0])
show(ground, "ground", color_by="rhof_1", scalar_range=(0, 0.6),
     lut="terrain", scalar_bar="Fuel density (rhof_1)")

fire = contour(input=data, ContourBy="theta", Isosurfaces=[400, 600, 800])
show(fire, "fire", color_by="theta", scalar_range=(400, 900),
     lut="fire", opacity=0.55, scalar_bar="Potential temperature (K)")

wind_layer = extract_grid(input=data, VOI=[251, 850, 0, 499, 4, 4])
sparse = mask_points(input=wind_layer, OnRatio=400, RandomMode=True)
vel = make_vector(input=sparse, components=("u", "v", "w"), result="velocity")
speed = compute_magnitude(input=vel, components=("u", "v", "w"), result="speed")
arrow = source("vtkArrowSource", TipResolution=8, ShaftResolution=8)
arrows = glyph(input=speed, GlyphSource=arrow,
               OrientationArray="velocity",
               ScaleArray="speed", ScaleFactor=5.0)
show(arrows, "wind", color_by="speed", scalar_range=(0, 25),
     lut="wind", scalar_bar="Wind speed (m/s)")

background("dark")
