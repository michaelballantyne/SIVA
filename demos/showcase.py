"""Showcase demo: generates multiple visualization renders from the wildfire dataset.

Run from project root: python demos/showcase.py
Produces PNG files in demos/output/
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.renderer import Renderer
from siva.run import interpret

OUTPUT_DIR = "demos/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "datasets", "wildfire", "data", "output.30000.vts")

if not os.path.exists(DATA):
    print(f"ERROR: {DATA} not found.")
    print("Run: bash datasets/wildfire/download.sh")
    sys.exit(1)


def render_demo(name, code, width=1920, height=1080):
    """Render a demo and save to output directory."""
    t0 = time.time()
    renderer = Renderer(width, height)
    vtk_objs, node_statuses, show_statuses, scene = interpret(code, renderer)

    errors = [s for s in node_statuses.values() if "error" in s]
    if errors:
        print(f"  WARN: {name} has errors: {[e['error'] for e in errors]}")

    path = os.path.join(OUTPUT_DIR, f"{name}.png")
    renderer.screenshot(path)
    dt = time.time() - t0
    total_pts = sum(s.get("num_points", 0) for s in node_statuses.values())
    print(f"  {name}: {dt:.1f}s, {total_pts:,} total pts -> {path}")
    return vtk_objs


print("=" * 60)
print("SIVA Showcase: Wildfire Visualization Suite")
print("=" * 60)
print()

# 1. Overview: Terrain + Fire + Streamlines
print("1. Overview visualization...")
render_demo("01_overview", f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta", scalar_range=(350.0, 1200.0), lut="fire", specular=0.5)
fire_core = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[600.0])
show(fire_core, "fire_core", color_by="theta", scalar_range=(400.0, 1200.0), lut="fire", specular=0.7)
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat", ResultArrayName="velocity")
seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200, num_seeds=25, offset_z=10)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=seeds, Vectors="velocity", IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=600, InitialIntegrationStep=0.3)
tubes = filter("vtkTubeFilter", input=streams, Radius=1.5, NumberOfSides=8)
show(tubes, "wind", color_by="u", scalar_range=(-5, 25), lut="wind", opacity=0.8, specular=0.3,
    scalar_bar="Wind Speed (m/s)")
camera(position=(80, -600, 500), focal_point=(80, -10, 160), up=(0, 0, 1))
background(0.05, 0.05, 0.12)
''')

# 2. Top-down view
print("2. Top-down view...")
render_demo("02_topdown", f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta", scalar_range=(350.0, 1200.0), lut="fire", specular=0.5)
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat", ResultArrayName="velocity")
seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200, num_seeds=25, offset_z=10)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=seeds, Vectors="velocity", IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=600, InitialIntegrationStep=0.3)
tubes = filter("vtkTubeFilter", input=streams, Radius=1.5, NumberOfSides=8)
show(tubes, "wind", color_by="u", scalar_range=(-5, 25), lut="wind", opacity=0.8)
camera(position=(80, -10, 900), focal_point=(80, -10, 160), up=(0, 1, 0))
background(0.05, 0.05, 0.12)
''')

# 3. Fire close-up
print("3. Fire close-up...")
render_demo("03_fire_closeup", f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[350.0, 400.0, 600.0])
show(fire, "fire", color_by="theta", scalar_range=(300.0, 1200.0), lut="fire", specular=0.5,
    scalar_bar="Temperature (K)")
camera(position=(100, -60, 210), focal_point=(80, -10, 170), up=(0, 0, 1))
background(0.05, 0.05, 0.12)
''')

# 4. Oxygen depletion
print("4. Oxygen depletion...")
render_demo("04_oxygen", f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="O2", scalar_range=(0.086, 0.23), lut="oxygen",
    scalar_bar="O2 Concentration")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color=(1.0, 0.3, 0.0), opacity=0.6, specular=0.5)
camera(position=(80, -600, 500), focal_point=(80, -10, 160), up=(0, 0, 1))
background(0.05, 0.05, 0.12)
''')

# 5. Threshold hot region
print("5. Hot region threshold...")
render_demo("05_hot_region", f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain")
hot = filter("vtkThreshold", input=data, ThresholdBy="theta", ThresholdRange=[350.0, 1200.0])
show(hot, "hot_volume", color_by="theta", scalar_range=(350.0, 1200.0), lut="fire", opacity=0.5,
    scalar_bar="Temperature (K)")
camera(position=(80, -400, 350), focal_point=(80, -10, 170), up=(0, 0, 1))
background(0.05, 0.05, 0.12)
''')

# 6. Vorticity + Fire
print("6. Vorticity analysis...")
render_demo("06_vorticity", f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA}")
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat", ResultArrayName="velocity")
vorticity = filter("vtkCellDerivatives", input=velocity,
    VectorMode="ComputeVorticity", TensorMode="PassTensors")
vort_pts = filter("vtkCellDataToPointData", input=vorticity)
vort_mag = filter("vtkArrayCalculator", input=vort_pts,
    AddVectorArrayName=["Vorticity"],
    Function="mag(Vorticity)", ResultArrayName="vorticity_magnitude")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta", scalar_range=(350.0, 1200.0), lut="fire", specular=0.5, opacity=0.7)
vort_iso = filter("vtkContourFilter", input=vort_mag,
    ContourBy="vorticity_magnitude", Isosurfaces=[3.5])
show(vort_iso, "vortex", color=(0.3, 0.5, 1.0), opacity=0.4, specular=0.3)
camera(position=(80, -500, 400), focal_point=(80, -10, 170), up=(0, 0, 1))
background(0.05, 0.05, 0.12)
''')

# 7. Streamlines + Vorticity combined
print("7. Flow analysis (streamlines + vorticity)...")
render_demo("07_flow_analysis", f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA}")
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat", ResultArrayName="velocity")
vorticity = filter("vtkCellDerivatives", input=velocity,
    VectorMode="ComputeVorticity", TensorMode="PassTensors")
vort_pts = filter("vtkCellDataToPointData", input=vorticity)
vort_mag = filter("vtkArrayCalculator", input=vort_pts,
    AddVectorArrayName=["Vorticity"],
    Function="mag(Vorticity)", ResultArrayName="vorticity_magnitude")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta", scalar_range=(350.0, 1200.0), lut="fire", opacity=0.6)
vort_iso = filter("vtkContourFilter", input=vort_mag,
    ContourBy="vorticity_magnitude", Isosurfaces=[3.5])
show(vort_iso, "vortex", color=(0.3, 0.5, 1.0), opacity=0.3)
seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200, num_seeds=25, offset_z=10)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=seeds, Vectors="velocity", IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=600, InitialIntegrationStep=0.3)
tubes = filter("vtkTubeFilter", input=streams, Radius=1.2, NumberOfSides=6)
show(tubes, "streamlines", color_by="u", scalar_range=(-5, 25), lut="wind", opacity=0.7)
camera(position=(80, -500, 400), focal_point=(80, -10, 170), up=(0, 0, 1))
background(0.05, 0.05, 0.12)
''')

# 8. Radiative heat
print("8. Radiative heat transfer...")
render_demo("08_radiative_heat", f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain",
    color_by="frhosiesrad_1",
    scalar_range=(-50000, 50000),
    lut="heat",
    scalar_bar="Radiative Heat Flux")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color=(1.0, 0.5, 0.0), opacity=0.4)
camera(position=(80, -600, 500), focal_point=(80, -10, 160), up=(0, 0, 1))
background(0.05, 0.05, 0.12)
''')

# 9. Cross-section through fire (using slice)
print("9. Cross-section through fire...")
render_demo("09_cross_section", f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain", opacity=0.3)
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta", scalar_range=(350.0, 1200.0), lut="fire", opacity=0.4, specular=0.5)
cross = slice(input=data, origin=(80, -10, 175), normal=(0, 1, 0))
show(cross, "cross_section", color_by="theta", scalar_range=(290.0, 1200.0), lut="fire",
    scalar_bar="Temperature (K)")
camera(position=(80, -500, 400), focal_point=(80, -10, 175), up=(0, 0, 1))
background(0.05, 0.05, 0.12)
''')

# 10. Publication composite with scalar bars
print("10. Publication composite...")
render_demo("10_publication", f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta", scalar_range=(350.0, 1200.0), lut="fire", specular=0.5, opacity=0.7,
    scalar_bar="Temperature (K)")
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat", ResultArrayName="velocity")
vorticity = filter("vtkCellDerivatives", input=velocity,
    VectorMode="ComputeVorticity", TensorMode="PassTensors")
vort_pts = filter("vtkCellDataToPointData", input=vorticity)
vort_mag = filter("vtkArrayCalculator", input=vort_pts,
    AddVectorArrayName=["Vorticity"],
    Function="mag(Vorticity)", ResultArrayName="vorticity_magnitude")
vort_iso = filter("vtkContourFilter", input=vort_mag,
    ContourBy="vorticity_magnitude", Isosurfaces=[3.5])
show(vort_iso, "vortex", color=(0.3, 0.5, 1.0), opacity=0.3, specular=0.3)
seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200, num_seeds=30, offset_z=10)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=seeds, Vectors="velocity", IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=600, InitialIntegrationStep=0.3)
tubes = filter("vtkTubeFilter", input=streams, Radius=1.2, NumberOfSides=8)
show(tubes, "wind", color_by="u", scalar_range=(-5, 25), lut="wind", opacity=0.8, specular=0.3,
    scalar_bar="Wind Speed (m/s)")
camera(position=(80, -600, 500), focal_point=(80, -10, 160), up=(0, 0, 1))
background(0.05, 0.05, 0.12)
''')

# 11. Auto-seeded streamlines colored by vertical velocity
print("11. Auto-seeded streamlines (vertical velocity)...")
render_demo("11_vertical_wind", f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color=(1.0, 0.3, 0.0), opacity=0.4)
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat", ResultArrayName="velocity")
seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200, num_seeds=30, offset_z=10)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=seeds, Vectors="velocity", IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=600, InitialIntegrationStep=0.3)
tubes = filter("vtkTubeFilter", input=streams, Radius=1.5, NumberOfSides=8)
show(tubes, "vertical_wind", color_by="w", scalar_range=(-10, 15), lut="wind",
    scalar_bar="Vertical Velocity (m/s)")
camera(position=(80, -500, 400), focal_point=(80, -10, 170), up=(0, 0, 1))
background(0.05, 0.05, 0.12)
''')

print()
print(f"All demos saved to {OUTPUT_DIR}/")
print("=" * 60)
