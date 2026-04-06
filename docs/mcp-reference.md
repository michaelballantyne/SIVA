# VisLang MCP Tool Reference

> Auto-generated from source by `python scripts/gen_docs.py`.
> Do not edit by hand — changes will be overwritten.

---

## Overview

MCP tools are interactive operations called by an AI assistant or MCP client.
They query data, execute pipelines, adjust the scene, and return screenshots.

`set_pipeline()` is the bridge between the MCP layer and the DSL layer — it
executes a DSL pipeline file and renders the result. After loading data, you
write a pipeline `.py` file using DSL forms and call `set_pipeline()` to run it.

For DSL form documentation, see [dsl-reference.md](dsl-reference.md).

---

## Contents

- [Query Tools](#query-tools)
- [Mutation Tools](#mutation-tools)
- [Meta / Utility Tools](#meta--utility-tools)

---

## Query Tools

Query tools read data without changing the scene.  They all require an active pipeline (loaded via `set_pipeline()` or `load()`) unless otherwise noted.

### `describe_data(node: str = '', file_path: str = '')`

Get a comprehensive overview of a dataset: dimensions, bounds, all fields with statistics.

This is the recommended first call after loading data. Returns everything
you need to start building a visualization, including per-field percentiles
(p1, p25, p50, p75, p99), distribution shape, and coordinate info.
No follow-up calls needed for basic exploration.

Can be called in three ways:
- describe_data() -- uses the active pipeline's first node
- describe_data(node="nodename") -- uses a named node in the active pipeline
- describe_data(file_path="myfile.vts") -- reads the file directly, no pipeline needed

When file_path is given it takes precedence over node and the active pipeline.
Supported file extensions: .vts, .vti, .vtp, .vtu, .vtr

### `get_array_info(node: str = '')`

List all arrays on a node's output (or root data source if node is empty).

Returns array names, types, component counts, and value ranges.
Use this first to understand what fields are available before building visualizations.

### `get_field_summary(node: str, field: str)`

Get comprehensive summary of a field: stats, percentiles, and opacity suggestion.

Combines get_statistics + suggest_scalar_range + suggest_opacity in one call.
Use this when exploring a field before visualization.

### `get_node_info(node: str)`

Get detailed information about a specific pipeline node's output.

Shows point count, cell count, bounds, and all arrays with ranges.
More detailed than get_array_info for a specific node.

### `get_bounds(node: str = '')`

Get spatial bounds of a node's output data.

### `get_statistics(node: str, field: str)`

Get min, max, mean, std for a field on a node's output.

Use this to understand value ranges before setting thresholds, isosurface values,
or color map ranges.

### `query_stats(node: str, field: str, condition: str)`

Compute statistics for a field filtered by a condition on another field.

Answers questions like:
  - "mean updraft velocity where theta > 400"
  - "min/max oxygen where fuel_density > 0.1"
  - "volume (count) where temperature >= 500"

The *condition* string must be in the form "<field> <op> <value>" where
op is one of: >, <, >=, <=, ==, !=

Examples:
    query_stats("", "w", "theta > 400")
    query_stats("thresh1", "O2", "fuel_density >= 0.1")
    query_stats("", "temperature", "temperature != 0")

Returns count of matching points plus mean, min, max, std, and percentiles
(p1, p25, p50, p75, p99) of the target field within the matching region.

Args:
    node: Pipeline node to query (empty string for root source).
    field: Scalar field to compute statistics on.
    condition: Condition string like "theta > 400" (field op value).

### `get_histogram(node: str, field: str, bins: int = 20)`

Get a text histogram of a field's value distribution.

Useful for understanding data distribution before choosing visualization parameters.

### `get_spatial_extent(node: str, field: str, min_value: float, max_value: float)`

Find the bounding box where a field is within a given range.

Useful for positioning seed points for streamlines, focusing cameras,
or understanding where features are located in 3D space.

### `sample_points(node: str, points: list, fields: list = None)`

Sample field values at multiple (x, y, z) locations in one call.

Probes all requested points efficiently using a single spatial index,
avoiding the round-trip cost of calling sample_point N times.

Returns a structured text report: one block per input point showing
the nearest grid point, field values (scalar or vector), and whether
the query coordinate was outside the dataset bounds.

Args:
    node: Pipeline node to sample from (empty string for root source).
    points: List of [x, y, z] coordinates to probe.
    fields: Optional list of field names to return. If omitted, all
            point-data fields are returned.

Example:
    sample_points("", [[0,0,0],[1,1,1]], fields=["temperature","density"])

### `profile(node: str, point1: list, point2: list, fields: list, resolution: int = 100)`

Extract a 1-D profile of field values along a line between two points.

Samples the dataset at evenly spaced points along the line from point1
to point2 using a probe filter. Returns a table of values with distance
along the line, plus summary statistics (min, max, mean, trend) for each field.

Great for extracting profiles like "temperature vs. height through the plume
center" or "density along a horizontal transect."

Args:
    node: Name of the pipeline node to sample from (empty string for root source).
    point1: Start point [x, y, z].
    point2: End point [x, y, z].
    fields: List of field names to extract (e.g. ["temperature", "density"]).
    resolution: Number of sample points along the line (default 100).

### `get_ground_z(node: str, x: float, y: float, layers: bool = True)`

Return the Z coordinate at (x, y) for the lowest layer of a structured grid.

Useful for any 3D structured grid where the Z coordinate of the bottom
layer varies with position — for example terrain-following grids or
curvilinear meshes. Use this before placing seed points for streamlines
to ensure they are inside the grid.

The response always leads with "Ground z = X.X" so the value is easy to
extract. When layers=True (the default) the z-values at the first 10
vertical layers are also included. Pass layers=False when you only need
the ground z value.

Returns an error message if the data is not a structured grid.

### `suggest_scalar_range(node: str, field: str, percentile_low: float = 1.0, percentile_high: float = 99.0)`

Suggest a useful scalar range for a field based on its value distribution.

Returns percentile-based ranges that avoid extreme outliers compressing
the colormap. Useful before setting scalar_range in show().

### `suggest_opacity(node: str, field: str, scalar_range: list = None, max_opacity: float = 0.8)`

Suggest opacity transfer function control points for volume rendering.

Analyzes the field histogram to make common values transparent and rare
values opaque. Returns control points you can paste into show()'s
opacity_function parameter.

Args:
    node: Pipeline node to query (empty string for root source).
    field: Scalar field to analyze.
    scalar_range: Optional [min, max] range to restrict analysis. If omitted,
                  uses the full data range.
    max_opacity: Maximum opacity value in the returned transfer function (default 0.8).

### `suggest_isosurface(node: str, field: str, num_values: int = 3)`

Suggest good isosurface values for a field.

Analyzes the field histogram to find transition points that produce
meaningful isosurfaces. Returns values you can use in Isosurfaces=[].

### `suggest_camera(style: str = 'overview')`

Suggest a camera position based on visible actors.

Styles: "overview" (default), "closeup", "top_down", "side"

Returns camera parameters you can paste into set_pipeline's camera() call.

### `get_camera()`

Get the current camera position, focal point, and up vector.

Returns the current camera state so you can save it, tweak it, or
restore it later with set_camera() or camera() in the pipeline.

---

## Mutation Tools

Mutation tools change scene state (load data, rebuild pipeline, adjust actors).  Most return an auto-screenshot alongside their text result.

### `load(filename: str)`

Load a VTK data file and make it available for visualization.

Auto-detects the appropriate reader from the file extension.
Stores the data in the pipeline under the node name "data" so
other tools can access it immediately. Returns a describe_data()
overview of the loaded dataset.

Supported extensions: .vts, .vti, .vtp, .vtu, .vtr, .vtk, .nrrd, .nhdr
For .raw binary files, use raw_source() in a pipeline instead.

Args:
    filename: Path to the VTK file to load (relative to the session directory).

### `set_pipeline(file: str = '')`

Execute a VisLang DSL pipeline file. Clears the scene and rebuilds from scratch.

This is the bridge between the MCP layer and the DSL layer.  You write a
pipeline `.py` file using DSL forms (source, filter, show, camera, etc.),
then call this tool to execute it.

The pipeline file is plain Python.  DSL forms are injected automatically —
you do not need any import statements.  Available forms include:
  source(), filter(), threshold(), contour(), stream_tracer(),
  tube(), glyph(), show(), camera(), background(), scene_preset(), and more.
Call get_dsl_reference('form_name') for detailed docs on any form.
Call get_dsl_overview() for the full list of available DSL forms.

After execution the tool returns:
- A status report listing every pipeline node with point/cell counts
- Warnings for empty nodes (with diagnostic hints)
- An auto-captured screenshot of the rendered scene

Args:
    file: Path to the DSL pipeline .py file.  Defaults to the current view's
          per-view file (e.g. ``view-main.py``, ``view-closeup.py``).

Example workflow::

    # 1. Write a pipeline file
    # view-main.py:
    #   data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
    #   region = threshold(input=data, ThresholdBy="temperature",
    #                      ThresholdRange=[500, 2000])
    #   show(region, "fire", color_by="temperature",
    #        scalar_range=(500, 2000), lut="fire",
    #        scalar_bar="Temperature (K)")
    #   scene_preset("dark")

    # 2. Execute it
    set_pipeline("view-main.py")

Notes:
    - Every call to set_pipeline() saves a versioned snapshot to .vislang/history/.
      Use restore_version() or list_versions() to navigate history.
    - Empty output warnings usually mean wrong field ranges — use
      get_statistics() to check.
    - State-changing tools that adjust the camera or actors (set_camera,
      set_colormap, etc.) do not require a set_pipeline() re-run.

### `reset_pipeline()`

Clear the entire scene and reset to empty state.

Use this to start fresh without restarting the server.

### `set_camera(position: list = None, focal_point: list = None, up: list = None, zoom: float = 0)`

Set the camera position without rebuilding the pipeline.

Much faster than modifying camera() in set_pipeline. Pass coordinates
as numeric lists, e.g. position=[100, -500, 400].

Args:
    position: Camera position as [x, y, z].
    focal_point: Camera focal point as [x, y, z].
    up: Camera up vector as [x, y, z] (default [0, 0, 1]).
    zoom: Zoom factor (> 0 to apply, e.g. 1.5 to zoom in).

### `set_opacity(name: str, opacity: float)`

Set the opacity of a named actor in the scene (0.0 = invisible, 1.0 = opaque).

Fast way to adjust transparency without rebuilding the pipeline.

### `set_colormap(name: str, lut: str = '', scalar_range: list = None)`

Change the colormap of a named actor without rebuilding.

Accepts preset names: "fire", "terrain", "wind", "cool_to_warm",
"blue_to_red", "grayscale", "oxygen", "heat".
Optionally update scalar range at the same time.

Args:
    name: Name of the actor to update.
    lut: Colormap preset name (e.g. "fire", "cool_to_warm").
    scalar_range: Optional [min, max] to set the scalar range at the same time.

### `set_background(r: float, g: float, b: float)`

Set the scene background color without rebuilding the pipeline.

Values are 0.0-1.0 RGB. Common presets: dark=(0.02,0.02,0.06),
light=(0.85,0.85,0.9), black=(0,0,0), white=(1,1,1).

### `set_window_size(width: int, height: int)`

Set the render window size for higher/lower resolution screenshots.

Default is 1920x1080. Use 3840x2160 for 4K publication quality.

### `toggle_visibility(name: str)`

Toggle visibility of a named actor/volume in the scene.

Use this to show/hide specific layers without rebuilding the pipeline.

### `annotate(x: float, y: float, z: float, label: str, color: str = 'white', font_size: int = 14)`

Add a text annotation label at a 3D position in the scene.

Uses billboard text that always faces the camera, so it remains readable
from any viewing angle. Annotations persist across camera changes and
accumulate until clear_annotations() is called.

If an annotation with the same label already exists it is replaced.

Args:
    x: World-space X coordinate for the label.
    y: World-space Y coordinate for the label.
    z: World-space Z coordinate for the label.
    label: Text to display. Also used as the unique key for this annotation.
    color: Text color — named CSS color ("white", "red", "yellow", …) or
           hex string ("#ff8800").  Defaults to "white".
    font_size: Font size in points.  Defaults to 14.

### `clear_annotations()`

Remove all text annotations from the scene.

Annotations are added with annotate(). This removes every label that
was placed since the last clear.

---

## Meta / Utility Tools

Meta tools manage server state, versions, views, and output.

### `screenshot()`

Render the current scene and return the image.

Call this after set_pipeline to see the current visualization.

### `camera_orbit(n_frames: int = 8, elevation: float = 30.0)`

Orbit the camera around the scene and return a series of screenshots.

Captures views evenly spaced around the focal point at the given elevation
angle, giving a turntable-style tour of the 3D scene.  Useful for
understanding spatial structure that is hard to read from a single angle.

The original camera state is restored after all frames are captured.

Args:
    n_frames: Number of views to capture (default 8, clamped to 1–16).
    elevation: Camera elevation angle in degrees above the focal plane
               (default 30.0, clamped to -89–89).

Returns:
    A flat list alternating text descriptions and Image objects:
    [description_0, Image_0, description_1, Image_1, ...]

### `quick_start(filename: str)`

Generate a starting pipeline for a data file.

Returns DSL code you can paste into set_pipeline() to get a basic
visualization quickly, which you can then modify.

### `list_actors()`

List all actors/volumes in the current scene with their visibility and type.

Useful for knowing what layers exist for toggle_visibility/set_opacity.

### `get_actor_info(name: str)`

Get information about a specific actor/volume in the scene.

Shows type, visibility, bounds, scalar range, and opacity.

### `list_versions()`

List all saved pipeline versions with timestamps.

Each set_pipeline call creates a new version. Use restore_version(n)
to go back to a previous version.

### `get_pipeline()`

Return the current DSL pipeline spec text.

Use this to see the current pipeline and modify it incrementally.

### `restore_version(version: int)`

Restore a previous pipeline version by number.

Use this to go back to an earlier visualization state.

### `export_standalone(path: str = 'visualization.py')`

Export the current pipeline as a standalone Python script.

The exported script can run independently without the MCP server.

### `get_dsl_overview()`

Get a complete overview of the VisLang DSL: workflow patterns, all forms, VTK classes, and colormaps.

Returns everything you need before writing your first pipeline:

- **Architecture overview** and typical workflow
- **4 key patterns** (surface coloring, isosurface, volume rendering, streamlines)
- **Full DSL form index** organized by category with one-line descriptions
- **VTK Sources/Readers and Filters** usable with source() and filter()
- **Colormap presets** for the lut= parameter of show()

This is your single entry point for DSL discovery. Call this first, then use
get_dsl_reference('form_name') for detailed parameter docs on any specific form.

### `list_data_files()`

List available data files in the current directory.

Finds files with supported extensions: .vts, .vti, .vtp, .vtu, .vtr,
.vtk, .nrrd, .nhdr, .raw

Searches the current directory and all subdirectories.
Call this first to see what datasets are available to visualize.

### `get_dsl_reference(form: str)`

Get detailed documentation for a DSL pipeline form.

Returns the full docstring, signature, a concrete usage example, and
links to related forms.  This is the primary reference for understanding
what parameters any DSL form accepts and how to use it.

DSL forms are plain Python functions available inside pipeline .py files
executed by set_pipeline().  They do not need imports — they are injected
automatically when the pipeline is run.

Call get_dsl_overview() first to see all available form names with descriptions.
Common forms to look up:
- "show" — add a node to the scene with all display options
- "source" — load data or create a geometric shape
- "filter" — apply any whitelisted VTK filter directly
- "threshold" — keep cells in a field value range
- "contour" — extract isosurfaces
- "stream_tracer" — trace streamlines through a vector field
- "glyph" — place oriented/scaled glyphs at grid points
- "volume" — (use show() with representation="Volume")

Args:
    form: DSL form name string, e.g. "show", "threshold", "contour",
          "stream_tracer", "glyph", "extract_component", etc.
          Case-insensitive.

### `new_view(name: str)`

Create a new independent render context (view) and make it current.

Each view has its own pipeline, camera, version history, and annotations.
All existing tools (set_pipeline, set_camera, etc.) operate on the current
view after calling this.

Args:
    name: Unique name for the new view (e.g. "temperature", "detail").
          Cannot be an existing view name.

### `focus(name: str)`

Switch which view all tools target (make a named view current).

After calling this, all tools (set_pipeline, set_camera, screenshot, etc.)
will operate on the named view. Returns a screenshot of the focused view.

Args:
    name: Name of the view to switch to.

### `close_view(name: str)`

Close and remove a named view.

Cannot close the last remaining view. Clears all VTK resources for that view.
If the closed view was current, focus switches to the first remaining view.

Args:
    name: Name of the view to close.

### `list_views()`

List all named views and which one is currently active.

Returns view names, pipeline status, and version numbers.

### `render_chart(chart_type: str, node: str = '', field: str = '', data: str = '', title: str = '', x_label: str = '', y_label: str = '')`

Render a 2D chart (histogram or line plot) and return it as an image.

This tool produces a PNG chart from field data in the pipeline or from
raw x/y values, and returns the image alongside a text description.

Chart types:
  "histogram" -- histogram of a scalar field's values. Requires ``node``
                 and ``field``. Uses the pipeline to fetch the data.
  "line"      -- line plot. Either:
                   (a) pass ``data`` as a JSON string ``{"x": [...], "y": [...]}``
                       for arbitrary x/y series, or
                   (b) pass ``node`` and ``field`` to plot field values vs.
                       point index along a line probe output.

Args:
    chart_type: One of "histogram" or "line".
    node: Pipeline node name to read field data from (empty = root source).
          Used by histogram and line (option b).
    field: Scalar field name to read from the node. Used by histogram and
           line (option b).
    data: JSON string containing ``{"x": [...], "y": [...]}`` arrays for a
          line plot (option a). Ignored for histogram.
    title: Optional chart title.
    x_label: Optional x-axis label.
    y_label: Optional y-axis label.

Returns:
    A list of [description_text, Image(png)] on success, or an error string
    on failure.
