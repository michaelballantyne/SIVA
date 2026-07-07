# Wildfire Simulation Domain Guide (HIGRAD/FIRETEC)

This document contains domain-specific knowledge for visualizing wildfire
simulation data from the HIGRAD/FIRETEC coupled fire-atmosphere model.

Use this as a reference when working with wildfire `.vts` datasets. The
SIVA MCP tools are domain-neutral; this file provides the interpretation
context that turns generic visualization into meaningful wildfire analysis.

## Dataset: output.30000.vts

600x500x61 structured grid, 18.3M points.

**Bounds:** X=[-498, 700], Y=[-500, 498], Z=[0.75, 898.6]

## Fields and Interpretation

| Field | Meaning | Typical Range | Suggested Colormap | Suggested Scalar Range |
|---|---|---|---|---|
| `theta` | Potential temperature (K) | 298-1184 | `fire` | (298, 1200) |
| `rhof_1` | Fuel density (kg/m3) | 0.0-0.6 | `terrain` | (0.0, 0.6) |
| `O2` | Oxygen mass fraction | 0.086-0.23 | `oxygen` | (0.1, 0.23) |
| `u` | Wind velocity x-component (m/s) | -15 to 28 | `wind` | (-15, 28) |
| `v` | Wind velocity y-component (m/s) | -15 to 19 | `wind` | (-15, 19) |
| `w` | Wind velocity z-component (m/s) | -15 to 21 | `cool_to_warm` | (-15, 21) |
| `rhowatervapor` | Water vapor density | 0.0-0.05 | `cool_to_warm` | (0.0, 0.05) |
| `frhosiesrad_1` | Radiative heat transfer | -400k to 100k | `cool_to_warm` | (-50000, 50000) |
| `convht_1` | Convective heat transfer | varies | `cool_to_warm` | (-50000, 50000) |

### Key Thresholds

- **Ambient temperature:** ~298-300 K (theta)
- **Fire ignition region:** theta > ~340 K
- **Active combustion:** theta 400-1200 K
- **Burned fuel:** rhof_1 = 0.0
- **Unburned fuel:** rhof_1 ~ 0.6
- **Ambient oxygen:** O2 ~ 0.23
- **Depleted oxygen (in fire):** O2 < 0.15

## Terrain-Following Grid

This dataset uses a terrain-following coordinate system. The z-coordinates at
ground level vary with (x, y) position — z ranges from ~0.75 to ~196 at
ground level. This means:

- z=0 does NOT correspond to the ground surface
- Seed points for streamlines must use appropriate z values (use `get_ground_z()`)
- Use `get_spatial_extent()` to find where features are in 3D space

## Opacity Presets for Volume Rendering

### Fire (temperature field)
Tuned for theta: transparent below 340K (ambient), ramping through combustion range.
```
[(298, 0.0), (340, 0.0), (400, 0.03), (500, 0.08), (700, 0.2), (1000, 0.5), (1200, 0.7)]
```

### Vorticity
For computed vorticity_magnitude field:
```
[(0.0, 0.0), (0.5, 0.0), (1.0, 0.005), (2.0, 0.02), (3.5, 0.1), (5.0, 0.3)]
```

### Oxygen Depletion
For O2 field — high opacity where oxygen is most depleted:
```
[(0.086, 0.6), (0.15, 0.3), (0.20, 0.1), (0.22, 0.02)]
```

## Common Visualization Patterns

### Terrain + Fire (basic)
```python
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")
terrain = filter("vtkExtractGrid", input=data, VOI=[0,599,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1",
    scalar_range=(0.0, 0.6), lut="terrain")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta",
    scalar_range=(298, 1200), lut="fire")
camera(position=(80, -600, 500), focal_point=(80, -10, 160), up=(0, 0, 1))
```

### Fire Region Extraction
Threshold on potential temperature to isolate the fire plume:
```python
hot = filter("vtkThreshold", input=data, ThresholdBy="theta", ThresholdRange=[340, 1200])
```

### Volume Rendered Fire
```python
hot = filter("vtkThreshold", input=data, ThresholdBy="theta", ThresholdRange=[350.0, 1200.0])
show(hot, "fire_vol", representation="Volume", color_by="theta",
    scalar_range=(350.0, 1200.0), lut="fire",
    opacity_function=[(350, 0.0), (400, 0.02), (500, 0.1), (700, 0.3), (1000, 0.6), (1200, 0.8)],
    volume_resolution=200)
```

### Streamlines Through Fire
```python
velocity = compute_velocity(input=data, components=("u", "v", "w"), result="velocity")
seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200, num_seeds=40)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=seeds, Vectors="velocity", IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=600)
tubes = tube(input=streams, Radius=1.5, NumberOfSides=8)
show(tubes, "wind", color_by="u", scalar_range=(-10, 25), lut="wind", opacity=0.7)
```

### Vorticity Analysis (VLS - Vorticity-driven Lateral Spread)
```python
vort = compute_vorticity(input=data)
vort_iso = filter("vtkContourFilter", input=vort,
    ContourBy="vorticity_magnitude", Isosurfaces=[3.5])
show(vort_iso, "vortex", color=(0.3, 0.5, 1.0), opacity=0.4)
```

### Cross-Section Slice
```python
yz_cut = slice(input=data, origin=(80, 0, 0), normal=(1, 0, 0))
show(yz_cut, "section", color_by="theta", scalar_range=(298, 600), lut="fire", opacity=0.5)
```

### Oxygen Depletion
```python
o2_depleted = filter("vtkThreshold", input=data, ThresholdBy="O2", ThresholdRange=[0.086, 0.22])
show(o2_depleted, "o2_depletion", representation="Volume", color_by="O2",
    scalar_range=(0.086, 0.22), lut="oxygen",
    opacity_function=[(0.086, 0.6), (0.15, 0.3), (0.20, 0.1), (0.22, 0.02)],
    volume_resolution=150, gradient_opacity=True)
```

### Radiative Heat Transfer
```python
# Positive = fire heating surroundings
rad_heat = filter("vtkThreshold", input=data, ThresholdBy="frhosiesrad_1",
    ThresholdRange=[100, 100000])
show(rad_heat, "heating", representation="Volume", color_by="frhosiesrad_1",
    scalar_range=(100, 50000), lut="fire",
    opacity_function=[(100, 0.01), (1000, 0.05), (5000, 0.15), (20000, 0.4), (50000, 0.7)],
    volume_resolution=150)
```

### Wind Glyphs
```python
velocity = compute_velocity(input=data, components=("u", "v", "w"), result="velocity")
speed = compute_magnitude(input=data, components=("u", "v", "w"), result="speed")
sub = filter("vtkExtractGrid", input=speed, VOI=[220,380,200,300,0,12], SampleRate=[8,8,2])
arrow = source("vtkArrowSource", TipResolution=6, ShaftResolution=6)
glyphs = filter("vtkGlyph3D", input=sub,
    GlyphSource=arrow, OrientationArray="velocity",
    ScaleArray="speed", ScaleFactor=6.0)
show(glyphs, "arrows", color_by="speed", scalar_range=(0, 20), lut="wind")
```

### Multiple Temperature Isosurfaces
```python
for temp in [350, 500, 700, 1000]:
    iso = contour(input=data, ContourBy="theta", Isosurfaces=float(temp))
    show(iso, f"iso_{temp}", color_by="theta",
        scalar_range=(298, 1200), lut="fire",
        opacity=0.1 + (temp-350)/1000)
```

## Good Camera Positions

- **Overview:** position=(80, -700, 550), focal_point=(80, -10, 150), up=(0,0,1)
- **Close-up fire:** position=(80, -200, 250), focal_point=(80, -10, 170), up=(0,0,1)
- **Top-down:** position=(80, -10, 900), focal_point=(80, -10, 0), up=(0,1,0)
- **Side view:** position=(-500, -10, 200), focal_point=(80, -10, 150), up=(0,0,1)

## Scientific Context

The HIGRAD/FIRETEC model simulates coupled fire-atmosphere interactions. Key
phenomena to visualize:

- **VLS (Vorticity-driven Lateral Spread):** Fire-generated vortices that cause
  lateral fire spread. Visible as vortex tubes near the fire front — use
  vorticity isosurfaces or volume rendering.
- **Fire plume structure:** The vertical column of hot gas above active
  combustion. Best visualized with theta volume rendering or isosurfaces.
- **Fuel consumption patterns:** Spatial pattern of burned vs unburned fuel
  (rhof_1 on terrain surface).
- **Oxygen depletion zones:** Regions where combustion has consumed oxygen,
  creating hazardous conditions.
- **Wind-fire coupling:** How the fire modifies local wind patterns and how
  wind drives fire spread. Streamlines through the fire region show this.
