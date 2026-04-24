data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

velocity = make_vector(input=data, components=("u", "v", "w"), result="velocity")
vort = curl(vector_field=velocity, result="vorticity", vector=True)
wy = extract_component(input=vort, field="vorticity", component=1, result_name="omega_y")

slab = slice(input=wy, origin=(0, -10, 400), normal=(0, 1, 0))
show(slab, "wy_slice", color_by="omega_y", scalar_range=(-1.5, 1.5),
     lut="blue_to_red", scalar_bar="Lateral vorticity ω_y (1/s)")

fire = contour(input=data, ContourBy="theta", Isosurfaces=[400])
show(fire, "fire_outline", color=(1.0, 1.0, 1.0), opacity=0.9, line_width=1.5)

terrain_line = slice(input=data, origin=(0, -10, 400), normal=(0, 1, 0))
ground_edge = contour(input=terrain_line, ContourBy="theta", Isosurfaces=[299.9])
# terrain outline via k=0..0 extract, then slice
ground_strip = extract_grid(input=data, VOI=[251, 850, 225, 230, 0, 0])
show(ground_strip, "terrain", color_by="rhof_1", scalar_range=(0, 0.6),
     lut="terrain", opacity=1.0)

scene_preset("dark")
