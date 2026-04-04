# VisLang - Declarative VTK Visualization DSL

## Quick Start

Use the VisLang MCP tools to build scientific visualizations interactively.
Always start by querying the data before choosing visualization parameters.

## Available MCP Tools

- `set_pipeline(code)` — Execute a DSL pipeline spec (clears and rebuilds)
- `screenshot()` — Return current scene as an image
- `get_array_info(node?)` — List arrays, types, ranges on a node
- `get_bounds(node?)` — Spatial bounds of a node's output
- `get_statistics(node, field)` — Min, max, mean, std for a field
- `get_histogram(node, field, bins?)` — Value distribution histogram
- `get_spatial_extent(node, field, min, max)` — Bounding box where field is in range
- `get_pipeline()` — Return current DSL code
- `restore_version(version)` — Restore a previous pipeline version

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

# Display a node
show(node, "display_name",
    color_by="field_name",       # Color by scalar/vector field
    scalar_range=(min, max),     # Map scalars to this range
    color=(r, g, b),             # Solid color (0-1 floats), when not using color_by
    opacity=1.0,                 # 0.0 = transparent, 1.0 = opaque
    specular=0.3,                # Specular lighting
    specular_power=20.0,
    representation="Surface",    # "Surface", "Wireframe", "Points"
    line_width=2.0,
    lut=dict(                    # Custom color lookup table
        hue_range=(0.0, 0.67),
        saturation_range=(0.5, 1.0),
        value_range=(0.3, 1.0)))

# Scene setup
camera(position=(x,y,z), focal_point=(x,y,z), up=(x,y,z), zoom=1.0)
background(r, g, b)
```

### Supported VTK Classes

**Sources:**
- `vtkXMLStructuredGridReader` — Read .vts files (FileName=)
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

## Workflow Guidelines

1. **Always query first** — Use `get_array_info()` to see available fields and ranges before building.
2. **Check statistics** — Use `get_statistics()` to find appropriate threshold/isosurface values.
3. **Use spatial extent** — Use `get_spatial_extent()` to position cameras and seed points near features.
4. **Iterate visually** — After each `set_pipeline()`, check the `screenshot()` to verify.
5. **Build incrementally** — Start simple (terrain), then add features (fire, wind, etc.).

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

## Independent Work Guidance

When working independently (overnight sessions, extended iterations):
- Check the time periodically with `date -u` to track session duration
- Iterate on the plan steps, testing each change with the real dataset
- After completing the core plan, pursue improvements in order of value:
  1. Fix bugs found during testing
  2. Improve visualization quality (better camera angles, color maps, lighting)
  3. Refine MCP tool ergonomics (better error messages, more helpful reports)
  4. Add missing DSL features discovered during testing
  5. Improve CLAUDE.md with lessons learned
  6. Add integration tests
- Commit and push regularly so work isn't lost
- Keep working until the session time limit

## Dataset: output.30000.vts

Wildfire simulation (HIGRAD/FIRETEC). 600x500x61 structured grid, 18.3M points.

**Fields:** u, v, w (wind velocity components), theta (potential temperature — fire shows as high theta >350K), O2, rhowatervapor, rhof_1 (fuel density — 0=burned), convht_1, frhosiesrad_1

**Bounds:** X=[-498, 700], Y=[-500, 498], Z=[0.75, 898.6]

**Key values:**
- theta: ambient ~300K, fire 350-1184K
- rhof_1: 0.0 (burned) to 0.6 (unburned fuel)
- Wind speed (u): -15 to 28 m/s
