from siva.spec_api import *

data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

velocity = make_vector(input=data, components=("u", "v", "w"), result="velocity")
vort = curl_vector(vector_field=velocity, output_field="vorticity")
wx = extract_component(input=vort, field="vorticity", component=2, result_name="omega_z")

plume = contour(input=wx, ContourBy="theta", Isosurfaces=[400])
show(plume, "plume", color_by="omega_z", scalar_range=(-1.0, 1.0),
     lut="blue_to_red", opacity=1.0,
     scalar_bar="Vertical vorticity ω_z (1/s)")

ground = extract_grid(input=data, VOI=[251, 850, 0, 499, 0, 0])
show(ground, "ground", color_by="rhof_1", scalar_range=(0, 0.6),
     lut="terrain", opacity=0.7)

background("dark")
