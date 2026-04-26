data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

velocity = make_vector(input=data, components=("u", "v", "w"), result="velocity")
vort = curl_vector(vector_field=velocity, output_field="vorticity")
wx = extract_component(input=vort, field="vorticity", component=0, result_name="omega_x")

slab_full = slice(input=wx, origin=(80, 0, 400), normal=(1, 0, 0))
slab = clip_box(input=slab_full, bounds=(70, 90, -150, 150, 0, 400))
show(slab, "wx_slice", color_by="omega_x", scalar_range=(-1.0, 1.0),
     lut="blue_to_red", scalar_bar="Longitudinal vorticity ω_x (1/s)")

theta_slab_full = slice(input=data, origin=(80, 0, 400), normal=(1, 0, 0))
theta_slab = clip_box(input=theta_slab_full, bounds=(70, 90, -150, 150, 0, 400))
fire_outline = contour(input=theta_slab, ContourBy="theta", Isosurfaces=[310])
show(fire_outline, "fire_outline", color=(1.0, 1.0, 1.0), line_width=2.5)

ground_strip = extract_grid(input=data, VOI=[251, 850, 0, 499, 0, 0])
show(ground_strip, "terrain", color_by="rhof_1", scalar_range=(0, 0.6),
     lut="terrain", opacity=1.0)

scene_preset("dark")
