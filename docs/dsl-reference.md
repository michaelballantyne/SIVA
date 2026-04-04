# VisLang DSL Reference

> Auto-generated from source by `python gen_docs.py`.
> Do not edit by hand — changes will be overwritten.

---

## Overview

A VisLang pipeline file is a plain Python script that uses DSL forms to
describe what you want to visualize. Pipeline files are executed by the
MCP tool `set_pipeline('pipeline.py')`, which builds and renders the scene.

### Compositional structure

```python
# 1. Load data with source()
data = source('vtkXMLStructuredGridReader', FileName='mydata.vts')

# 2. Apply filter forms — each takes input= and returns a node reference
region = threshold(input=data, ThresholdBy='field', ThresholdRange=[lo, hi])
iso    = contour(input=data, ContourBy='field', Isosurfaces=[value])

# 3. Add things to the scene with show()
show(region, 'region', color_by='field', scalar_range=(lo, hi))
show(iso,    'iso',    color_by='field', lut='hot')

# 4. Set up the scene with camera(), background(), or scene_preset()
camera(position=(x,y,z), focal_point=(fx,fy,fz))
scene_preset('dark')
```

All DSL forms are available as module-level functions inside the pipeline file.
You do not need to import anything — `set_pipeline()` injects them automatically.

---

## Contents

- [Data Sources](#data-sources)
- [Filtering & Clipping](#filtering-clipping)
- [Derived Fields](#derived-fields)
- [Flow Visualization](#flow-visualization)
- [Data Conversion](#data-conversion)
- [Display](#display)
- [Generic](#generic)

---

## Data Sources

### `source(vtk_class, props)`

### `raw_source(filename, dimensions = (1, 1, 1), scalar_type = 'unsigned_char', header_size = 0, num_components = 1)`

Load a raw binary volume file via vtkImageReader2.

Args:
    filename: Path to the .raw file.
    dimensions: (nx, ny, nz) tuple of grid dimensions.
    scalar_type: Data type string ("unsigned_char", "unsigned_short",
                 "float", "double", etc.) or a VTK type constant.
    header_size: Number of bytes to skip at the start of the file.
    num_components: Number of scalar components per voxel.

## Filtering & Clipping

### `threshold(input = None, props)`

### `contour(input = None, props)`

### `isosurface(input = None, props)`

Alias for contour() - more intuitive name.

### `slice(input = None, origin = None, normal = None, props)`

### `clip(input = None, origin = None, normal = None, inside_out = False, props)`

Clip data by a plane. Keeps the half on the normal side.

### `clip_box(input = None, bounds = None, inside_out = True, props)`

Clip data by an axis-aligned box. By default keeps inside.

### `clip_sphere(input = None, center = None, radius = 100, inside_out = True, props)`

Clip data by a sphere. By default keeps inside.

### `extract_region(input = None, bounds = None, voi = None, props)`

Extract a sub-region of a structured grid by physical bounds or grid indices.

Exactly one of ``bounds`` or ``voi`` must be provided.

Automatically selects the correct VTK extraction filter based on the
input data type (vtkExtractGrid for vtkStructuredGrid/vtkRectilinearGrid,
vtkExtractVOI for vtkImageData).

Args:
    input: Input structured grid node (vtkStructuredGrid, vtkImageData, etc.).
    bounds: Physical coordinate bounds [xmin, xmax, ymin, ymax, zmin, zmax].
            The region is converted to grid indices internally using the
            input dataset's coordinate system.
    voi: Grid index bounds [imin, imax, jmin, jmax, kmin, kmax].
         Use this when you already know the exact grid indices.
    **props: Additional properties forwarded to the underlying filter (e.g.
             SampleRate=[sx, sy, sz] for subsampling).

Raises:
    ValueError: If both or neither of ``bounds`` and ``voi`` are given.

### `extract_grid(input = None, props)`

### `surface(input = None, props)`

Extract the outer surface of a dataset.

### `smooth(input = None, iterations = 20, props)`

Smooth a polydata surface.

## Derived Fields

### `make_vector(components = ('u', 'v', 'w'), result = 'velocity', input = None)`

Assemble three scalar arrays into a single 3-component vector array.

This is the general primitive for building vector fields from named
scalar components.  ``compute_velocity`` is a thin wrapper around this.

Args:
    components: Tuple/list of three scalar array names (cx, cy, cz).
    result: Name for the resulting vector array.
    input: Input data node containing the scalar arrays.

### `compute_velocity(input = None, components = ('u', 'v', 'w'), result = 'velocity')`

Compute a vector field from scalar components.

Backwards-compatible wrapper around ``make_vector``.

### `compute_magnitude(input = None, components = ('u', 'v', 'w'), result = 'speed')`

Compute the magnitude of scalar components.

### `compute_vorticity(input = None, velocity_input = None, components = ('u', 'v', 'w'), result = 'vorticity_magnitude', vector = False)`

Compute vorticity from velocity components.

Backwards-compatible wrapper.  For new code prefer ``make_vector`` +
``curl`` directly.

If velocity_input is provided, uses it directly. Otherwise computes
velocity from the scalar components first.

Args:
    vector: If True, return the full 3-component vorticity vector
            (result name defaults to 'vorticity'). If False (default),
            return the scalar magnitude.

### `curl(vector_field, result = 'vorticity', vector = True)`

Compute the curl (vorticity) of a vector field.

This is the general curl operator.  ``compute_vorticity`` is a thin
wrapper around this.

Args:
    vector_field: Input node whose active vector array will be used.
    result: Name for the output array.
    vector: If True (default), return the full 3-component curl vector.
            If False, return the scalar magnitude of the curl.

### `gradient(input = None, props)`

### `compute_gradient_magnitude(input = None, field = None, result = None)`

Compute the gradient magnitude of a scalar field.

Useful for finding edges and boundaries in the data.

### `extract_component(input = None, field = None, component = 0, result_name = None)`

Extract a single component from a vector field as a new scalar array.

Args:
    input: Input data node containing the vector field.
    field: Name of the vector field to extract from.
    component: Component index (0, 1, 2) or name ("x", "y", "z").
    result_name: Name for the new scalar array. Defaults to "{field}_{component}".

### `calculator(input = None, props)`

## Flow Visualization

### `stream_tracer(input = None, props)`

### `seeds_near(input = None, field = None, min_val = None, max_val = None, num_seeds = 30, offset_z = 10)`

Create seed points near where a field is in a given range.

Finds the spatial extent of the field range, then creates a line
source through that region.

### `tube(input = None, props)`

### `glyph(input = None, props)`

### `mask_points(input = None, props)`

### `line_probe(input = None, point1 = None, point2 = None, resolution = 100)`

Create a line probe that samples data between two points.

Uses vtkLineSource + vtkProbeFilter to sample the input dataset
along a line from point1 to point2.

Args:
    input: Input data node to sample from.
    point1: (x, y, z) start point of the line.
    point2: (x, y, z) end point of the line.
    resolution: Number of sample points along the line.

## Data Conversion

### `cell_to_point(input = None, props)`

Convert cell data to point data.

### `point_to_cell(input = None, props)`

Convert point data to cell data.

### `probe(input = None, source = None, props)`

Sample source data at input geometry points.

### `resample_to_image(input = None, dimensions = None, props)`

Resample any dataset to a regular image grid.

### `elevation(input = None, low_point = None, high_point = None, props)`

Color by elevation (z-coordinate by default).

### `outline(input = None, props)`

Draw bounding box outline around data.

### `warp_vector(input = None, props)`

### `warp_scalar(input = None, props)`

## Display

### `show(node, name = None, display_props)`

### `camera(position = None, focal_point = None, up = None, zoom = None)`

### `background(r, g, b)`

### `scene_preset(name = 'dark')`

Apply a named scene preset for background and styling.

Presets:
  dark - Dark blue/black background (default)
  light - Light gray background (good for solid objects)
  black - Pure black background
  white - Pure white background (publication-ready)

### `title(text, position = 'top', font_size = 24, color = (1, 1, 1))`

Add a text annotation to the scene.

## Generic

### `filter(vtk_class, input = None, props)`

---

## `show()` Display Properties Reference

The `show()` form accepts these keyword arguments for controlling appearance:

### Surface / Actor Display Props

| Property | Type | Description |
| -------- | ---- | ----------- |
| `color_by` | str | Field name to color by. If omitted, uses VTK default. |
| `scalar_range` | (lo, hi) | Min/max values for colormap mapping. |
| `lut` | str | Colormap preset name (see `list_capabilities()` for options). |
| `opacity` | float | Overall actor opacity (0.0–1.0). |
| `color` | (r,g,b) | Solid color (floats 0–1). Used when `color_by` is not set. |
| `component` | int or str | For vector fields: which component to color by. 0/1/2 or 'x'/'y'/'z'. |
| `representation` | str | 'Surface' (default), 'Wireframe', 'Points', or 'Volume'. |
| `specular` | float | Specular highlight intensity (0–1). |
| `specular_power` | float | Specular highlight sharpness. |
| `line_width` | float | Line width for wireframe / streamlines. |
| `scalar_bar` | bool or str | Show a color legend. Pass True or a title string. |

### Volume Rendering Props (representation='Volume')

| Property | Type | Description |
| -------- | ---- | ----------- |
| `opacity_function` | list or str | Control points `[(value, opacity), ...]` or a preset name like `'fire'`, `'ct_bone'`. |
| `gradient_opacity` | bool or list | Edge-enhanced opacity. True uses a default ramp; list for custom `[(grad, opacity), ...]`. |
| `volume_resolution` | int | Resampling resolution for non-image data (default 256, max 512). |
| `shade` | bool | Enable shading for volume rendering (default True). |
| `sample_distance` | float | Ray casting step size; smaller = higher quality but slower. |
| `clip_planes` | list | List of `{'origin': ..., 'normal': ...}` dicts to clip the volume. |
