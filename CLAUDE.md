# VisLang - Declarative VTK Visualization DSL

## Quick Start

Use the VisLang MCP tools to build scientific visualizations interactively.
Always start by querying the data before choosing visualization parameters.

## Server Launch Modes

The MCP server defaults to **interactive mode** which opens a VTK window the
user can watch and rotate/zoom while building visualizations.

```bash
# Interactive (default) — opens a live VTK window
python -m vislang.server

# Off-screen — headless rendering, returns screenshots only
python -m vislang.server --offscreen
```

**For development and testing (CI, subagents, automated work), always use
`--offscreen`.** The interactive window requires a display and will block in
headless environments. When configuring the MCP server in `claude_desktop_config.json`
or similar, add `--offscreen` if running without a user watching.

## Available MCP Tools

- `set_pipeline(code)` — Execute a DSL pipeline spec (clears and rebuilds)
- `screenshot()` — Return current scene as an image
- `get_array_info(node?)` — List arrays, types, ranges on a node
- `get_bounds(node?)` — Spatial bounds of a node's output
- `get_statistics(node, field)` — Min, max, mean, std for a field
- `get_histogram(node, field, bins?)` — Value distribution histogram
- `get_spatial_extent(node, field, min, max)` — Bounding box where field is in range
- `list_capabilities()` — List available VTK classes, colormaps, and DSL functions
- `list_data_files()` — List available data files in current dir and data/
- `list_actors()` — List all actors/volumes in scene with visibility and type
- `sample_point(node, x, y, z)` — Sample all field values at nearest grid point
- `get_ground_z(node, x, y)` — Find ground z-coordinate at x,y (terrain-following grids)
- `suggest_opacity(node, field, scalar_range_min?, scalar_range_max?, max_opacity?)` — Suggest opacity transfer function for volume rendering
- `suggest_isosurface(node, field, num_values?)` — Suggest good isosurface/contour values from histogram analysis
- `suggest_camera(style?)` — Suggest camera position for "overview", "closeup", "top_down", or "side" views
- `suggest_scalar_range(node, field, percentile_low?, percentile_high?)` — Percentile-based scalar range
- `describe_data(node?)` — Comprehensive dataset overview (dimensions, bounds, all fields)
- `get_field_summary(node, field)` — Combined stats + ranges + opacity suggestion in one call
- `get_examples()` — Copy-pasteable pipeline patterns for common visualizations
- `get_pipeline()` — Return current DSL code
- `restore_version(version)` — Restore a previous pipeline version
- `set_camera(position?, focal_point?, up?, zoom?)` — Update camera without rebuilding pipeline
- `set_opacity(name, opacity)` — Adjust actor/volume transparency without rebuild
- `set_background(r, g, b)` — Change background color without rebuild
- `toggle_visibility(name)` — Show/hide a named layer without rebuild
- `reset_pipeline()` — Clear scene and start fresh
- `list_versions()` — Show all saved pipeline versions
- `export_standalone(path?)` — Export current pipeline as a standalone Python script
- `set_window_size(width, height)` — Change render resolution (default 1920x1080)

## DSL Reference

### Builder Functions

```python
# Load data
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

# Create filters (input= connects to upstream node)
subset = filter("vtkExtractGrid", input=data, VOI=[0,599,0,499,0,0], SampleRate=[1,1,1])
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat",
    ResultArrayName="velocity")
iso = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
hot = filter("vtkThreshold", input=data, ThresholdBy="theta", ThresholdRange=[350.0, 1200.0])
streams = filter("vtkStreamTracer", input=velocity, Vectors="velocity",
    IntegrationDirection="Both", MaximumNumberOfSteps=2000,
    MaximumPropagation=1198, InitialIntegrationStep=0.2)
tubes = filter("vtkTubeFilter", input=streams, Radius=2.0, NumberOfSides=8)

# Convenience wrappers (same as filter() with class pre-filled)
contour(input=data, ContourBy="theta", Isosurfaces=[400.0])
calculator(input=data, ...)
threshold(input=data, ...)
extract_grid(input=data, ...)
stream_tracer(input=velocity, ...)
tube(input=streams, ...)
glyph(input=data, ...)
slice(input=, origin=(x,y,z), normal=(nx,ny,nz))
seeds_near(input=, field="theta", min_val=400, max_val=1200, num_seeds=30, offset_z=10)
compute_velocity(input=, components=("u","v","w"), result="velocity")
compute_magnitude(input=, components=("u","v","w"), result="speed")
compute_vorticity(input=, result="vorticity_magnitude")
clip(input=, origin=(x,y,z), normal=(nx,ny,nz), inside_out=False)
clip_sphere(input=, center=(x,y,z), radius=100, inside_out=True)
clip_box(input=, bounds=(xmin,xmax,ymin,ymax,zmin,zmax), inside_out=True)
probe(input=, source=node_ref)
resample_to_image(input=, dimensions=(nx,ny,nz))
raw_source(filename, dimensions=(nx,ny,nz), scalar_type="unsigned_char", header_size=0)

# Display a node
show(node, "display_name",
    color_by="field_name",       # Color by scalar/vector field
    scalar_range=(min, max),     # Map scalars to this range
    color=(r, g, b),             # Solid color (0-1 floats), when not using color_by
    opacity=1.0,                 # 0.0 = transparent, 1.0 = opaque
    specular=0.3,                # Specular lighting
    specular_power=20.0,
    representation="Surface",    # "Surface", "Wireframe", "Points", "Volume"
    line_width=2.0,
    lut=dict(                    # Custom color lookup table
        hue_range=(0.0, 0.67),
        saturation_range=(0.5, 1.0),
        value_range=(0.3, 1.0)),
    # Volume rendering options (only when representation="Volume"):
    opacity_function=[(val, opacity), ...],  # Transfer function control points
    volume_resolution=256)       # Resampling resolution for structured grids

# Scene setup
camera(position=(x,y,z), focal_point=(x,y,z), up=(x,y,z), zoom=1.0)
background(r, g, b)
scene_preset("dark")               # "dark", "light", "black", "white"
title("My Visualization", position="top", font_size=24)
```

### Field-Specific Defaults

When `color_by` matches a known field and no `lut` or `scalar_range` is provided,
sensible defaults are applied automatically:

| Field | Default Colormap | Default Range |
|---|---|---|
| `theta` | fire | (298, 1200) |
| `rhof_1` | terrain | (0.0, 0.6) |
| `O2` | oxygen | (0.1, 0.23) |
| `u` | wind | (-15, 28) |
| `v` | wind | (-15, 19) |
| `w` | cool_to_warm | (-15, 21) |
| `vorticity_magnitude` | cool_to_warm | (0.5, 5.0) |
| `speed` | wind | (0, 20) |

This means `show(terrain, "terrain", color_by="rhof_1")` works without explicit lut/scalar_range.

### Supported VTK Classes

**Sources/Readers:**
- `vtkXMLStructuredGridReader` — Read .vts files (FileName=)
- `vtkXMLImageDataReader` — Read .vti files (FileName=)
- `vtkXMLPolyDataReader` — Read .vtp files (FileName=)
- `vtkXMLUnstructuredGridReader` — Read .vtu files (FileName=)
- `vtkXMLRectilinearGridReader` — Read .vtr files (FileName=)
- `vtkImageReader2` — Read raw binary volumes (use `raw_source()` convenience)

### Loading Raw Binary Volumes

For datasets from klacansky.com and similar sources:

```python
# Load raw binary with known dimensions and data type
data = raw_source("data/cthead.raw", dimensions=(256, 256, 113),
    scalar_type="unsigned_short", header_size=0)
show(data, "vol", representation="Volume", opacity_function="ct_bone")
```

Supported scalar types: `"unsigned_char"`, `"unsigned_short"`, `"short"`, `"float"`, `"double"`, `"int"`, `"unsigned_int"`, `"char"`
- `vtkArrowSource` — Arrow glyph source
- `vtkLineSource` — Line seed for streamlines (Point1=, Point2=)
- `vtkPointSource` — Point cloud seed (Center=, Radius=, NumberOfPoints=)

**Filters:**
- `vtkContourFilter` — Isosurfaces (ContourBy=, Isosurfaces=[])
- `vtkArrayCalculator` — Derived fields (AddScalarArrayName=[], Function=, ResultArrayName=)
- `vtkExtractGrid` — Subsample structured grid (VOI=[], SampleRate=[])
- `vtkThreshold` — Threshold by field value (ThresholdBy=, ThresholdRange=[])
- `vtkStreamTracer` — Streamlines (Vectors=, IntegrationDirection=, MaximumNumberOfSteps=, MaximumPropagation=, InitialIntegrationStep=)
- `vtkTubeFilter` — Thicken lines into tubes (Radius=, NumberOfSides=)
- `vtkCellDataToPointData` — Convert cell data to point data
- `vtkGlyph3D` — Place glyphs at points
- `vtkGeometryFilter` — Extract surface geometry
- `vtkWarpScalar` — Warp geometry by scalar field
- `vtkResampleToImage` — Resample any dataset to regular image grid (used internally for volume rendering)

### Special Property Mappings

| Property | Effect |
|---|---|
| `Isosurfaces=[v1, v2, ...]` | Set contour values |
| `ContourBy="field"` | Select field for contouring |
| `Vectors="field"` | Select vector field for streamlines |
| `ThresholdRange=[lo, hi]` | Set threshold bounds |
| `ThresholdBy="field"` | Select field for thresholding |
| `AddScalarArrayName=["a","b"]` | Register scalar arrays in calculator |
| `VOI=[x0,x1,y0,y1,z0,z1]` | Volume of Interest for extract grid |
| `SampleRate=[sx,sy,sz]` | Subsampling rate |
| `IntegrationDirection="Both"` | "Forward", "Backward", or "Both" |
| `IntegratorType="RungeKutta45"` | "RungeKutta2", "RungeKutta4", "RungeKutta45" |
| `SeedSource=node_ref` | Seed source for stream tracer (line/point source) |
| `VectorMode="ComputeVorticity"` | For vtkCellDerivatives: "PassVectors", "ComputeGradient", "ComputeVorticity" |
| `TensorMode="PassTensors"` | For vtkCellDerivatives: "PassTensors", "ComputeGradient", "ComputeStrain" |
| `OrientationArray="velocity"` | Orient glyphs by this vector field |
| `ScaleArray="speed"` | Scale glyphs by this scalar field |
| `GlyphSource=node_ref` | Source geometry for glyphs (arrow, etc.) |
| `AddVectorArrayName=["vel"]` | Register vector arrays in calculator |
| `CutFunction=dict(type="Plane", Origin=..., Normal=...)` | Set cutting plane for vtkCutter |

## Workflow Guidelines

1. **Start with describe_data()** — Get a complete overview of dimensions, fields, and ranges.
2. **Check statistics** — Use `get_field_summary()` for detailed field analysis (combines stats + range + opacity suggestion).
3. **Build incrementally** — Start simple (terrain), then add features (fire, wind, etc.).
4. **Use field defaults** — Known fields (theta, rhof_1, O2, u) auto-apply colormap + range.
5. **Use convenience functions** — `compute_velocity()`, `compute_vorticity()`, `seeds_near()` save many lines.
6. **For volume rendering** — Use `suggest_opacity()` for transfer functions, or preset names like `"fire"`, `"ct_bone"`.
7. **Iterate visually** — After each `set_pipeline()`, check `screenshot()`. Use `set_camera()`, `toggle_visibility()`, `set_opacity()` for quick adjustments without rebuilding.
8. **Check ground z** — Use `get_ground_z()` before placing streamline seeds (terrain-following grid).

## Color Map Presets

Use preset names as the `lut` parameter in `show()`:
- `"terrain"` — Brown (burned) to green (vegetated)
- `"fire"` — Black → red → orange → yellow → white
- `"wind"` — Dark blue (slow/reverse) → green → yellow → orange (fast)
- `"cool_to_warm"` — Blue → white → red (diverging)
- `"blue_to_red"` — Blue → cyan → green → yellow → red (rainbow)
- `"grayscale"` — Black to white

You can also use HSV-based dicts: `lut=dict(hue_range=(0,1), saturation_range=(0.5,1), value_range=(0.3,1))`

## Important: Terrain-Following Grid

The wildfire data uses a terrain-following coordinate system. The z-coordinates at ground level vary with x,y position (z ranges from ~0.75 to ~196 at ground level). This means:
- Seed points for streamlines must be placed at appropriate z-coordinates (use `get_ground_z()`)
- The z=0 plane does NOT correspond to the ground surface
- Use `get_spatial_extent()` to find where features are in 3D space

## Example: Wildfire Visualization

```python
# Load the wildfire simulation data
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

# Terrain surface (bottom slice, colored by fuel density)
terrain = filter("vtkExtractGrid", input=data, VOI=[0,599,0,499,0,0])
show(terrain, "terrain",
    color_by="rhof_1",
    scalar_range=(0.0, 0.6),
    lut=dict(hue_range=(0.1, 0.33), saturation_range=(0.4, 0.8), value_range=(0.3, 0.8)))

# Fire isosurface
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta", scalar_range=(350.0, 1200.0),
    lut=dict(hue_range=(0.0, 0.12), saturation_range=(0.8, 1.0), value_range=(0.5, 1.0)))

# Wind velocity field
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat",
    ResultArrayName="velocity")

# Streamlines through fire region
streams = filter("vtkStreamTracer", input=velocity,
    Vectors="velocity",
    IntegrationDirection="Both",
    MaximumNumberOfSteps=2000,
    MaximumPropagation=500,
    InitialIntegrationStep=0.5)
tubes = filter("vtkTubeFilter", input=streams, Radius=3.0, NumberOfSides=8)
show(tubes, "wind", color_by="u", scalar_range=(-5, 15), opacity=0.7)

# Scene
camera(position=(100, -800, 600), focal_point=(100, 0, 50), up=(0, 0, 1))
background(0.15, 0.15, 0.2)
```

## Example: Minimal Volume Rendered Fire (with convenience features)

```python
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

# Auto-defaults for rhof_1: terrain colormap + (0, 0.6) range
terrain = filter("vtkExtractGrid", input=data, VOI=[0,599,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1")

# Volume render fire - "fire" opacity preset + auto colormap
hot = filter("vtkThreshold", input=data, ThresholdBy="theta", ThresholdRange=[340, 1200])
show(hot, "fire_vol", representation="Volume", color_by="theta",
    opacity_function="fire", volume_resolution=200)

scene_preset("dark")
```

## Example: Full Volume Rendered Fire (with explicit control)

```python
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

# Terrain surface for context
terrain = filter("vtkExtractGrid", input=data, VOI=[0,599,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain")

# Volume render the fire plume (threshold first to reduce data)
hot = filter("vtkThreshold", input=data, ThresholdBy="theta", ThresholdRange=[350.0, 1200.0])
show(hot, "fire_volume",
    representation="Volume",
    color_by="theta",
    scalar_range=(350.0, 1200.0),
    lut="fire",
    opacity_function=[(350, 0.0), (400, 0.02), (500, 0.1), (700, 0.3), (1000, 0.6), (1200, 0.8)],
    volume_resolution=200)

camera(position=(100, -800, 600), focal_point=(100, 0, 50), up=(0, 0, 1))
background(0.05, 0.05, 0.1)
```

### When to Use Volume Rendering vs Isosurfaces

| Use Case | Approach |
|---|---|
| Sharp boundaries (fire front, bone) | Isosurface (`contour()`) |
| Diffuse fields (temperature, density) | Volume rendering |
| See internal structure | Volume rendering with clipping |
| Multiple threshold levels | Multiple isosurfaces or volume with opacity ramp |
| CT scan visualization | Volume rendering with gradient_opacity + isosurface |

### Volume Rendering Options

- `representation="Volume"` enables volume rendering instead of surface
- `opacity_function` controls transparency per data value:
  - List of `(value, opacity)` control points: `[(300, 0.0), (400, 0.1), (1200, 0.8)]`
  - Preset string: `"ramp_up"`, `"gaussian"`, `"step"`, `"fire"`, `"vorticity"`,
    `"o2_depletion"`, `"ct_bone"`, `"ct_tissue"`
  - `None` for a linear ramp (default)
- `opacity` scales all opacity values (0.0-1.0)
- `volume_resolution` controls resampling grid size (default 256); higher = better quality, slower
- `gradient_opacity=True` enables gradient-based edge enhancement (or custom list of control points)
- `shade=True/False` controls volume shading (default True)
- `sample_distance=0.5` controls ray marching step size (lower = better quality, slower)
- `clip_planes=[{"origin": (x,y,z), "normal": (nx,ny,nz)}]` crops the volume
- `ambient`, `diffuse`, `specular`, `specular_power` control material properties
- `lut` works the same as surface rendering (preset names or HSV dicts)
- Structured grids are automatically resampled to vtkImageData for the volume mapper
- Works best with thresholded data to focus on regions of interest
- Auto-opacity: when no opacity_function is given, histogram-guided control points
  are auto-generated (common values become transparent, rare values opaque)

## Example: CT Scan Volume Rendering

```python
data = source("vtkXMLImageDataReader", FileName="data/ctBones.vti")

# Volume render the CT scan (no resampling needed for vtkImageData)
show(data, "ct_volume", representation="Volume",
    color_by="Scalars_",
    scalar_range=(0, 255),
    lut="grayscale",
    opacity_function=[(0, 0.0), (30, 0.0), (80, 0.01), (120, 0.05), (180, 0.2), (255, 0.6)])

# Bone isosurface for reference
bone = filter("vtkContourFilter", input=data, ContourBy="Scalars_", Isosurfaces=[140.0])
show(bone, "bone", color=(0.9, 0.85, 0.7), opacity=0.3, specular=0.4)

camera(position=(400, -200, 300), focal_point=(128, 128, 128), up=(0, 0, 1))
background(0.0, 0.0, 0.05)
```

## Example: Vorticity Visualization (VLS Analysis)

```python
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

# Compute velocity vector
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat",
    ResultArrayName="velocity")

# Compute vorticity (curl of velocity)
vorticity = filter("vtkCellDerivatives", input=velocity,
    VectorMode="ComputeVorticity", TensorMode="PassTensors")
vort_pts = filter("vtkCellDataToPointData", input=vorticity)

# Compute vorticity magnitude
vort_mag = filter("vtkArrayCalculator", input=vort_pts,
    AddVectorArrayName=["Vorticity"],
    Function="mag(Vorticity)",
    ResultArrayName="vorticity_magnitude")

# Strong vortex isosurface
vort_iso = filter("vtkContourFilter", input=vort_mag,
    ContourBy="vorticity_magnitude", Isosurfaces=[3.5])
show(vort_iso, "vortex_tubes", color=(0.3, 0.5, 1.0), opacity=0.5)
```

## Example: Cross-Section Slices

```python
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

# Y-Z cross-section through the fire at x=80
yz_cut = slice(input=data, origin=(80, 0, 0), normal=(1, 0, 0))
show(yz_cut, "yz_section", color_by="theta", scalar_range=(298, 600), lut="fire")

# Horizontal slice at fire level
horiz = slice(input=data, origin=(0, 0, 175), normal=(0, 0, 1))
show(horiz, "horizontal", color_by="theta", scalar_range=(298, 500))

# Fire isosurface for context
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color=(1.0, 0.3, 0.0), opacity=0.5)

camera(position=(300, -300, 350), focal_point=(80, -10, 170), up=(0, 0, 1))
background(0.03, 0.03, 0.08)
```

## Example: Oxygen Depletion

```python
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

# O2 depletion: volume render depleted region (below ambient 0.23)
o2_depleted = filter("vtkThreshold", input=data, ThresholdBy="O2", ThresholdRange=[0.086, 0.22])
show(o2_depleted, "o2_depletion", representation="Volume", color_by="O2",
    scalar_range=(0.086, 0.22), lut="oxygen",
    opacity_function=[(0.086, 0.6), (0.15, 0.3), (0.20, 0.1), (0.22, 0.02)],
    volume_resolution=150, gradient_opacity=True)

# O2 on a horizontal slice through fire level
o2_slice = slice(input=data, origin=(80, -10, 175), normal=(0, 0, 1))
show(o2_slice, "o2_section", color_by="O2", scalar_range=(0.15, 0.23), lut="oxygen", opacity=0.5)
```

## Example: Radiative Heat Transfer

```python
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

# Volume render positive radiative heat (fire heating surroundings)
rad_heat = filter("vtkThreshold", input=data, ThresholdBy="frhosiesrad_1",
    ThresholdRange=[100, 100000])
show(rad_heat, "heating", representation="Volume", color_by="frhosiesrad_1",
    scalar_range=(100, 50000), lut="fire",
    opacity_function=[(100, 0.01), (1000, 0.05), (5000, 0.15), (20000, 0.4), (50000, 0.7)],
    volume_resolution=150)

# Volume render radiative cooling (blue)
rad_cool = filter("vtkThreshold", input=data, ThresholdBy="frhosiesrad_1",
    ThresholdRange=[-400000, -100])
show(rad_cool, "cooling", representation="Volume", color_by="frhosiesrad_1",
    scalar_range=(-100000, -100), lut="cool_to_warm",
    opacity_function=[(-100000, 0.5), (-10000, 0.2), (-1000, 0.05), (-100, 0.01)],
    volume_resolution=100, opacity=0.5)
```

## Example: Wind Glyphs

```python
# After computing velocity...
speed = filter("vtkArrayCalculator", input=velocity,
    AddScalarArrayName=["u", "v", "w"],
    Function="sqrt(u*u + v*v + w*w)",
    ResultArrayName="speed")

# Subsample for glyphs
sub = filter("vtkExtractGrid", input=speed,
    VOI=[220,380,200,300,0,12], SampleRate=[8,8,2])

arrow = source("vtkArrowSource", TipResolution=6, ShaftResolution=6)
glyphs = filter("vtkGlyph3D", input=sub,
    GlyphSource=arrow, OrientationArray="velocity",
    ScaleArray="speed", ScaleFactor=5.0)
show(glyphs, "wind_glyphs", color_by="speed", scalar_range=(0, 20), lut="wind")
```

## Example: Complete VLS Analysis (Contest Winner Style)

```python
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

# Layer 1: Terrain with fuel density (fire footprint visible as burned areas)
terrain = filter("vtkExtractGrid", input=data, VOI=[0,599,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1")

# Layer 2: Volume rendered fire plume
hot = filter("vtkThreshold", input=data, ThresholdBy="theta", ThresholdRange=[340, 1200])
show(hot, "fire", representation="Volume", color_by="theta",
    opacity_function="fire", volume_resolution=200, gradient_opacity=True)

# Layer 3: Volume rendered vorticity (VLS core analysis)
vort = compute_vorticity(input=data)
show(vort, "vorticity", representation="Volume", color_by="vorticity_magnitude",
    opacity_function="vorticity", volume_resolution=150, opacity=0.5)

# Layer 4: Streamlines through fire region
vel = compute_velocity(input=data)
seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200, num_seeds=40)
streams = filter("vtkStreamTracer", input=vel,
    SeedSource=seeds, Vectors="velocity", IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=600)
tubes = tube(input=streams, Radius=1.5, NumberOfSides=8)
show(tubes, "wind", color_by="u", opacity=0.5)

# Layer 5: O2 depletion cross-section
o2_cut = slice(input=data, origin=(80, -10, 0), normal=(1, 0, 0))
show(o2_cut, "o2", color_by="O2", opacity=0.4)

scene_preset("dark")
camera(position=(80, -700, 550), focal_point=(80, -10, 150), up=(0, 0, 1))
title("Wildfire VLS Analysis")
```

## Development Goals

The SciVis contest challenge description (scivis-report_8947f.pdf) and winning report
(contest_winner.pdf) contain many visualization ideas and existing renderings to reproduce.
After the initial wildfire demo works well, pick progressively harder visualization
challenges from these documents and implement them. The end goal is to efficiently
create or reproduce any of these scientific visualizations through conversation.

### Wildfire dataset targets (single timestep output.30000.vts):
1. Basic wildfire demo (terrain + fire isosurface + streamlines) ✓
2. Wind vector glyphs (arrows showing wind direction/magnitude) ✓
3. Vorticity visualization (vortex tubes near fire for VLS analysis) ✓
4. Oxygen depletion visualization (O2 field on terrain/slices) ✓
5. Combined multi-layer visualization matching contest winner figures ✓
6. Radiative heat transfer visualization (frhosiesrad_1) ✓
7. Cross-section slices through the fire plume ✓
8. Volume rendered fire plume (theta with transfer functions) ✓
9. Volume rendered vorticity field ✓

### Cross-domain generalization:
After the wildfire tools are mature, test with datasets from other domains
to verify generality. Source: http://klacansky.com/open-scivis-datasets/

Download one dataset at a time (be polite to their hosting). Don't check
data files into git - only check in download scripts and documentation.

Good candidates for testing different VTK capabilities:
- CT scan datasets (medical imaging: isosurfaces, volume rendering)
- Simulation datasets (CFD: streamlines, vector fields)
- Geoscience datasets (structured grids, terrain)

For each new domain, note what DSL features or VTK classes are missing.

## Challenge Documentation

When developing visualizations, reflect on what requires lots of iteration or
is otherwise challenging. Document these in `CHALLENGES.md` with specific
examples. For each challenge, propose how the project could address it through:
- Better interaction tools (new MCP tools, query helpers)
- Static checks (validation before running filters)
- Better defaults (smart camera, colormap selection)
- Improved error messages (diagnostic hints)

This feedback loop drives project improvement priorities.

## Independent Work Guidance

**Self-management for long sessions:**
- **Read `.vislang/work_state.md` first** when starting or resuming work.
  It tracks what's done, what's in progress, and what's next.
- **Update `.vislang/work_state.md`** after each significant milestone.
- **CRITICAL: After completing ANY task, run `date -u` and check if the
  allotted time has passed. If not, pick the next most valuable thing to
  work on and KEEP GOING. Do NOT stop early. Do NOT write a "summary" and
  stop — summaries are not a stopping signal. The only stopping signal is
  the clock.**
- **Delegate ALL implementation work to subagents.** The top-level agent
  should only manage: check time, decide what to work on, launch subagent,
  wait for result, review, commit, repeat. This keeps context lean.
- **Subagent isolation rule**: Either use `isolation: "worktree"` for
  parallel execution, or run subagents sequentially. Never run multiple
  non-isolated subagents in parallel — they share the working directory.
- Check the time periodically with `date -u` to track session duration
- Iterate on the plan steps, testing each change with the real dataset
- After completing the core plan, pursue improvements in order of value:
  1. Fix bugs found during testing
  2. Improve visualization quality (better camera angles, color maps, lighting)
  3. Refine MCP tool ergonomics (better error messages, more helpful reports)
  4. Add missing DSL features discovered during testing
  5. Improve CLAUDE.md with lessons learned
  6. Add integration tests
  7. Try reproducing contest winner figures from the PDFs
  8. Add new VTK filter classes to the whitelist
  9. Improve the DSL with convenience features
- Commit and push regularly so work isn't lost
- Keep working until the session time limit
- If running low on context, consider what can be delegated to subagents

## Dataset: output.30000.vts

Wildfire simulation (HIGRAD/FIRETEC). 600x500x61 structured grid, 18.3M points.

**Fields:** u, v, w (wind velocity components), theta (potential temperature — fire shows as high theta >350K), O2, rhowatervapor, rhof_1 (fuel density — 0=burned), convht_1, frhosiesrad_1

**Bounds:** X=[-498, 700], Y=[-500, 498], Z=[0.75, 898.6]

**Key values:**
- theta: ambient ~300K, fire 350-1184K
- rhof_1: 0.0 (burned) to 0.6 (unburned fuel)
- Wind speed (u): -15 to 28 m/s
