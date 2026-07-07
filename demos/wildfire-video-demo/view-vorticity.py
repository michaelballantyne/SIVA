from siva.spec_api import *

data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

velocity = make_vector(input=data, components=("u", "v", "w"), result="velocity")
vort = curl_vector(vector_field=velocity, output_field="vorticity")
wz = extract_component(input=vort, field="vorticity", component=2, result_name="omega_z")

slab = extract_grid(input=wz, VOI=[251, 850, 0, 499, 1, 1])
show(slab, "wz_slice", color_by="omega_z", scalar_range=(-2.0, 2.0),
     lut="blue_to_red", scalar_bar="Vertical vorticity ω_z (1/s)")

ground_slab = extract_grid(input=data, VOI=[251, 850, 0, 499, 1, 1])
fire_outline = contour(input=ground_slab, ContourBy="theta", Isosurfaces=[400])
show(fire_outline, "fire_outline", color=(1.0, 1.0, 1.0), line_width=2.5)

ground = extract_grid(input=data, VOI=[251, 850, 0, 499, 0, 0])
show(ground, "ground", color_by="rhof_1", scalar_range=(0, 0.6),
     lut="terrain", opacity=0.6)

background("dark")
