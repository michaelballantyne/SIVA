#!/usr/bin/env python3
"""Publication-quality composite render: terrain + fire + streamlines + vorticity + slice."""
import sys
sys.path.insert(0, "/home/user/VisLang")

from vislang.renderer import Renderer
from vislang.dsl import interpret

renderer = Renderer(1920, 1080)
code = '''
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

# Terrain
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain")

# Fire isosurfaces
fire_outer = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[350.0])
show(fire_outer, "smoke", color=(0.5, 0.4, 0.3), opacity=0.1)

fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta", scalar_range=(350.0, 1200.0), lut="fire", specular=0.5, opacity=0.85)

fire_core = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[700.0])
show(fire_core, "fire_core", color=(1.0, 0.95, 0.7), specular=0.8)

# Velocity
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat", ResultArrayName="velocity")

# Vorticity
vorticity = filter("vtkCellDerivatives", input=velocity,
    VectorMode="ComputeVorticity", TensorMode="PassTensors")
vort_pts = filter("vtkCellDataToPointData", input=vorticity)
vort_mag = filter("vtkArrayCalculator", input=vort_pts,
    AddVectorArrayName=["Vorticity"],
    Function="mag(Vorticity)", ResultArrayName="vorticity_magnitude")
vort_iso = filter("vtkContourFilter", input=vort_mag,
    ContourBy="vorticity_magnitude", Isosurfaces=[3.5])
show(vort_iso, "vortex_tubes", color=(0.2, 0.4, 0.9), opacity=0.25, specular=0.2)

# Auto-seeded streamlines
auto_seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200, num_seeds=35, offset_z=15)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=auto_seeds, Vectors="velocity", IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=700, InitialIntegrationStep=0.2)
tubes = filter("vtkTubeFilter", input=streams, Radius=1.0, NumberOfSides=8)
show(tubes, "wind", color_by="w", scalar_range=(-10, 15), lut="cool_to_warm", opacity=0.65, specular=0.3)

# Cross-section slice through fire
yz_cut = slice(input=data, origin=(80, 0, 0), normal=(1, 0, 0))
show(yz_cut, "cross_section", color_by="theta", scalar_range=(298, 600), lut="fire", opacity=0.4)

camera(position=(60, -550, 480), focal_point=(80, -10, 165), up=(0, 0, 1))
background(0.02, 0.02, 0.06)
'''

vtk_objs, node_statuses, show_statuses, builder = interpret(code, renderer)
for nid, s in node_statuses.items():
    name = s.get('name', f'node_{nid}')
    pts = s.get("num_points", 0)
    print(f'  {name}: {pts:,} pts')
renderer.screenshot('demos/output/13_publication_composite.png')
print("Saved 13_publication_composite.png")

# Second angle - top down: replace camera line in the full pipeline
code2 = code.replace(
    'camera(position=(60, -550, 480), focal_point=(80, -10, 165), up=(0, 0, 1))',
    'camera(position=(80, -10, 800), focal_point=(80, -10, 170), up=(0, 1, 0))'
)
renderer2 = Renderer(1920, 1080)
vtk_objs2, node_statuses2, show_statuses2, builder2 = interpret(code2, renderer2)
renderer2.screenshot('demos/output/14_publication_topdown.png')
print("Saved 14_publication_topdown.png")
