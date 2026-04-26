# VisLang MCP Tool Reference

> Auto-generated from source by `python scripts/gen_docs.py`.
> Do not edit by hand — changes will be overwritten.

---

## Overview

MCP tools are interactive operations called by an AI assistant or MCP client.
They query data, execute pipelines, adjust the scene, and return screenshots.

`run_pipeline()` is the bridge between the MCP layer and the DSL layer — it
executes a DSL pipeline file and renders the result. After loading data, you
write a pipeline `.py` file using DSL forms and call `run_pipeline()` to run it.

For DSL form documentation, see [dsl-reference.md](dsl-reference.md).

---

## Contents

- [Query Tools](#query-tools)
- [Mutation Tools](#mutation-tools)
- [Meta / Utility Tools](#meta--utility-tools)

---

## Query Tools

Query tools read data without changing the scene.  They all require an active pipeline (loaded via `run_pipeline()` or `load()`) unless otherwise noted.

### `describe_data(node: str = '', file_path: str = '', field: str = '')`

Get an overview of a dataset or a single field's statistics.

Without field= : returns full overview — dimensions, bounds, all fields with
percentiles (p1, p25, p50, p75, p99), distribution shape, and coordinate info.
Note: load() already returns this — no need to call describe_data() on the root
data after load(). Use describe_data() on derived nodes (after threshold, contour,
etc.) to understand what the filter produced.

With field= : returns rich statistics for that one field only (percentiles,
distribution shape). Use this after filtering or transforming data to understand
a specific field before choosing thresholds, isosurface values, or color ranges.

Can be called in three ways:
- describe_data() -- uses the active pipeline's first node
- describe_data(node="nodename") -- uses a named node in the active pipeline
- describe_data(file_path="myfile.vts") -- reads the file directly, no pipeline needed

When file_path is given it takes precedence over node and the active pipeline.
Supported file extensions: .vts, .vti, .vtp, .vtu, .vtr

Examples:
    describe_data()                          -- full overview of root data
    describe_data(node="fire_threshold")     -- full overview of a filtered node
    describe_data(node="fire", field="theta") -- just theta stats on the fire node

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

### `suggest_isosurface(node: str, field: str, num_values: int = 3)`

Suggest good isosurface values for a field.

Analyzes the field histogram to find transition points that produce
meaningful isosurfaces. Returns values you can use in Isosurfaces=[].

Note:
    For sparse fields where the feature of interest is a small fraction of the
    domain (e.g. a fire plume, a hot-spot, a jet), suggestions on the full dataset
    will be dominated by background gradients and give poor results.  Use
    get_histogram() first: if >60% of histogram mass is in the first or last few
    bins, threshold() to the feature region, then call suggest_isosurface on that
    thresholded node instead.

### `get_camera()`

Get the current camera position, focal point, and up vector.

Returns the current camera state so you can save it, tweak it, or
restore it later with set_camera().

---

## Mutation Tools

Mutation tools change scene state (load data, rebuild pipeline, adjust actors).  Most return an auto-screenshot alongside their text result.

### `load(filename: str)`

Load a VTK data file and make it available for visualization.

Auto-detects the appropriate reader from the file extension.
Writes view-main.py (or the active view's pipeline file) with a source()
call for the loaded file — ready for you to add show() calls and run
run_pipeline(). Returns a describe_data() overview of the loaded dataset.

If the pipeline file already exists, load() will not overwrite it. Delete
or rename it first, then call load() again.

Supported extensions: .vts, .vti, .vtp, .vtu, .vtr, .vtk, .nrrd, .nhdr
For .raw binary files, use raw_source() in a pipeline instead.

Args:
    filename: Path to the VTK file to load (relative to the session directory).

### `run_pipeline()`

Wait for the current view's pipeline file to finish building, and return
the build report plus a screenshot.

The server watches `view-<name>.py` and starts a rebuild automatically
every time the file is saved (debounced ~100ms). You do not need to call
`run_pipeline()` to "kick" a build — saving the file is enough. Call
`run_pipeline()` when you want to **block until the build of the current
file content is done** and see the result. If a matching build has already
finished, this returns immediately (no rebuild).

The pipeline file is plain Python. DSL forms are injected automatically —
no import statements needed. Available forms include:
  source(), filter(), threshold(), contour(), stream_tracer(),
  tube(), glyph(), show(), camera(), background(), scene_preset(), and more.
Call get_dsl_reference('form_name') for detailed docs on any form.
Call get_dsl_overview() for the full list of available DSL forms.

Builds are incremental, keyed on a content hash of each DSL node:
  - Visual-only edits (colormap, opacity, scalar_range, camera) — ~free,
    all cache hits, ~1ms.
  - Mid-pipeline edits (a threshold range, a contour value) — only that
    node and its downstream rebuild; ~10-50ms typical.
  - Changing the data file or source() arguments — full rebuild.

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

    # 2. Save the file (watcher triggers a build automatically)
    # 3. Call run_pipeline() to block on the result and get the screenshot
    run_pipeline()

Notes:
    - Every successful build saves a versioned snapshot to .vislang/history/.
      Use list_versions() / restore_version() to navigate history.
    - Use pipeline_status() for a non-blocking peek (no screenshot, no wait)
      while iterating on the file.
    - The status file view-<name>.status.json (next to the pipeline file)
      is updated after every build for non-MCP consumers (humans, scripts).
    - Empty output warnings usually mean wrong field ranges — use
      describe_data(node=, field=) to check.
    - Each node status dict follows the unified schema defined in
      vislang/diagnostics.py: every dict has "status" (ok/error/skipped/warning),
      "class", and for non-ok statuses also "kind" and "message".

### `set_suggested_camera(style: str = 'overview')`

Apply an automatic camera position based on visible actors and return a screenshot.

The first run_pipeline() call already applies an "overview" camera automatically,
so you only need this tool if you want to reset the view or try a different style.

Styles:
  "overview"  (default) — elevated oblique view of the whole scene
  "top_down"  — bird's eye view looking straight down
  "side"      — side view from the south

Returns a screenshot showing the new camera angle.

### `set_camera(position: list = None, focal_point: list = None, up: list = None, zoom: float = 0)`

Set the camera position without rebuilding the pipeline.

Much faster than modifying camera() in run_pipeline. Pass coordinates
as numeric lists, e.g. position=[100, -500, 400].

Args:
    position: Camera position as [x, y, z].
    focal_point: Camera focal point as [x, y, z].
    up: Camera up vector as [x, y, z] (default [0, 0, 1]).
    zoom: Zoom factor (> 0 to apply, e.g. 1.5 to zoom in).

### `set_window_size(width: int, height: int)`

Set the render window size for higher/lower resolution screenshots.

Default is 1920x1080. Use 3840x2160 for 4K publication quality.

---

## Meta / Utility Tools

Meta tools manage server state, versions, views, and output.

### `screenshot()`

Render the current scene and return the image.

Call this after run_pipeline to see the current visualization.

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

### `list_versions()`

List all saved pipeline versions with timestamps.

Each run_pipeline call creates a new version. Use restore_version(n)
to go back to a previous version.

### `restore_version(version: int)`

Restore a previous pipeline version by number.

Writes the historical pipeline back to `view-<name>.py` and waits for the
rebuild. Because this overwrites the file, the watcher will also see the
change; the coordinator dedupes by content hash so there is no double build.

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
executed by run_pipeline().  They do not need imports — they are injected
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

### `new_view(name: str, camera: str = '')`

Create a new independent render context (view), execute its pipeline, and return a screenshot.

Each view has its own pipeline, camera, and version history.
Write view-<name>.py first, then call this to create the view and render it in one step.
After this call all tools operate on the new view.

Args:
    name: Unique name for the new view (e.g. "temperature", "detail").
          Cannot be an existing view name. The pipeline file must already
          exist at view-<name>.py.
    camera: Optional camera style to apply after rendering. One of
            "overview", "top_down", or "side". Defaults to "overview"
            if not specified.

### `focus(name: str)`

Switch which view all tools target (make a named view current).

After calling this, all tools (run_pipeline, set_camera, screenshot, etc.)
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

Returns view names, pipeline status, version numbers, and whether
each view's OS window has been closed by the user (interactive mode
only).  A "window closed" flag means the view still exists in the
registry but the OS window is gone — the agent can offer to reopen
it (via focus()) or remove it (via close_view()).

### `pipeline_status()`

Non-blocking peek at the current view's latest build status.

Returns the contents of `view-<name>.status.json` as a JSON string —
the same file written after every build, so MCP consumers and external
scripts share one schema. If no build has run yet, returns a JSON object
with status "none". If a build is currently in flight, adds an
"inflight_elapsed_s" key with seconds elapsed.

Use this when iterating on a pipeline file: save the file, then call
pipeline_status to peek without blocking. Typical loop: edit file →
pipeline_status() to confirm the new hash built cleanly → only call
run_pipeline() when you want the screenshot back.

Schema keys: source_hash, status (ok/error/running/none), finished_at,
duration_s, node_count, cache {hits, misses, evictions}, screenshot,
version, error, log.
