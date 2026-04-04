# API Reflection — 2026-04-04

Read-only review of the public-facing MCP tools and DSL methods. The user of
this API is an LLM conversing with a human to build scientific visualizations.

---

## 1. Current API Surface

### MCP Tools (38 total)

**Pipeline lifecycle (7):**
set_pipeline, reset_pipeline, get_pipeline, quick_start, list_versions,
restore_version, export_standalone

**Data inspection / query (14):**
describe_data, get_array_info, get_field_summary, get_node_info, get_bounds,
get_statistics, query_stats, get_histogram, get_spatial_extent, sample_point,
sample_points, sample_line, get_ground_z, benchmark_pipeline

**Suggestion / guidance (4):**
suggest_scalar_range, suggest_opacity, suggest_isosurface, suggest_camera

**Scene mutation without rebuild (7):**
set_camera, set_opacity, set_colormap, set_color_range, set_background,
set_window_size, toggle_visibility

**Actor inspection (2):**
list_actors, get_actor_info

**Post-pipeline data manipulation (3):**
extract_component, make_vector, curl

**Discovery / reference (3):**
list_data_files, list_capabilities, get_examples

**Rendering (1):**
screenshot

### DSL Builder Methods (42 in PipelineBuilder, 42 exposed in namespace)

Sources: source, raw_source
Filters (thin wrappers): filter, contour, isosurface, calculator, threshold,
extract_grid, extract_region, stream_tracer, tube, glyph, warp_vector,
warp_scalar, cell_to_point, point_to_cell, outline, elevation, surface,
smooth, mask_points, gradient, clip, clip_sphere, clip_box, probe,
resample_to_image, slice, sample_line, seeds_near
Compute: make_vector, compute_velocity, curl, compute_vorticity,
compute_gradient_magnitude, compute_magnitude, extract_component
Scene: show, camera, background, scene_preset, title

---

## 2. Observations

### What's working well

- **The DSL-as-pipeline-file pattern is strong.** The LLM writes Python to a
  file, calls `set_pipeline()`, and gets back a structured status report plus
  a screenshot. This is a clean separation: the MCP tool is the control plane,
  the DSL is the data plane.

- **`describe_data()` is genuinely good.** Rich percentiles, distribution shape
  flags, volume rendering hints, and quick-start suggestions in a single call.
  This eliminates the multi-call exploration loop that earlier feedback flagged.

- **Auto-screenshots on state-changing tools** reduce round trips nicely.

- **The `suggest_*` family is well-scoped.** Each one answers a specific "what
  value should I use?" question that an LLM would otherwise guess wrong on.

### What's awkward or problematic

**A. Ghost tool in instructions.** The MCP instructions string references
`load("file.vts")` at line 49 and lists `load` in the available tools list at
line 86, but no `load()` tool exists. An LLM reading the instructions will try
to call it and fail. The backlog item "load() convenience" is marked done, but
the implementation appears to be `describe_data(file_path=...)` instead. The
instructions and tool list are out of date.

**B. 38 tools is a lot.** Every tool consumes prompt space in the LLM's
context, and the sheer count makes the tool-selection problem harder. LLMs are
worse at choosing from 38 options than from 15. The current surface has
significant overlap:

  - `describe_data` subsumes `get_array_info`, `get_node_info`, `get_bounds`,
    and most of `get_field_summary`. If `describe_data` already returns
    per-field percentiles, bounds, and dimensions, the four narrower tools
    rarely need to exist independently.

  - `set_colormap` and `set_color_range` overlap: `set_colormap` already
    accepts `scalar_range_min/max`. `set_color_range` is a strict subset.

  - `sample_point` is a strict subset of `sample_points` (one point vs. many).
    The single-point version could be removed.

  - `get_statistics` is subsumed by `describe_data` for initial exploration and
    by `query_stats` for conditional analysis.

  - `quick_start` returns code the LLM has to paste into a file, then call
    `set_pipeline()`. It's an awkward two-step. It could just write the file
    and call `set_pipeline` itself, or be folded into `load()`.

**C. Inconsistent parameter conventions.**

  - `set_camera` takes comma-separated strings: `position="100,-500,400"`.
    Every other tool takes proper typed parameters. This forces the LLM to
    format coordinates as strings, which is error-prone and inconsistent
    with the DSL's `camera(position=(100,-500,400))`.

  - Node references: some tools use `node: str` (query tools), others use
    `node_name: str` (extract_component, make_vector, curl), others use
    `name: str` (set_opacity, toggle_visibility, get_actor_info). The empty
    string `""` convention for "use root source" is also fragile -- an LLM
    might pass `None` or omit the parameter entirely.

  - `suggest_opacity` uses `scalar_range_min`/`scalar_range_max` as two
    separate floats. `set_colormap` also uses this pattern. But `set_color_range`
    uses `min_val`/`max_val`. The naming is inconsistent across the three
    places where a scalar range can be set.

**D. DSL/MCP duplication with semantic drift.** `extract_component`,
`make_vector`, and `curl` exist as both DSL methods and MCP tools, but with
different semantics. The DSL versions operate within the pipeline graph
(returning NodeRefs). The MCP versions operate on already-built VTK objects
post-pipeline. This means:

  - The DSL `make_vector` uses `components=("u","v","w")` (a tuple).
    The MCP `make_vector` uses `cx`, `cy`, `cz` (three separate strings).
  - The DSL `curl` takes `vector_field=node`. The MCP `curl` takes
    `node_name=str` and operates on a pre-existing vector in the dataset.
  - The results from MCP `make_vector` and `curl` are not saved back into
    `_vtk_objects`, so they can't be used by subsequent MCP tools.

An LLM could easily confuse these two levels. The MCP post-hoc versions are
also less useful than doing the same thing in the DSL pipeline, because
pipeline-level operations compose naturally while MCP-level mutations don't.

**E. `get_ground_z` is dataset-specific.** Its docstring says "This grid is
terrain-following" -- that's the wildfire dataset. For a CT scan or any
non-terrain dataset it returns an error. This tool should either be generalized
or clearly scoped as domain-specific.

**F. `list_capabilities` returns a wall of text.** It lists every DSL function,
every VTK class, every colormap. At ~80 lines, it's more context than an LLM
can usefully process. `get_examples` is better-structured but also quite long
(~120 lines). Together they consume significant context without being
individually actionable.

**G. `benchmark_pipeline` is a developer tool.** It's unlikely to be called by
an LLM during a visualization session. It adds to the tool count without
serving the primary user.

---

## 3. Proposed Backlog Items

### Tool count reduction

- **Merge `get_array_info`, `get_node_info`, and `get_bounds` into
  `describe_data`.** `describe_data` already returns all this information.
  Remove the three narrow tools and add a `verbose` flag if anyone needs the
  extra detail that `get_array_info` provides. This removes 3 tools.

- **Remove `sample_point`; keep only `sample_points`.** `sample_points` with a
  single-element list is functionally identical. This removes 1 tool.

- **Merge `set_color_range` into `set_colormap`.** `set_colormap` already
  accepts scalar range parameters. Remove the separate tool. This removes 1
  tool.

- **Remove or hide `benchmark_pipeline`.** It's a developer-facing diagnostic,
  not useful during visualization sessions. If kept, it shouldn't be an MCP
  tool.

- **Consider removing post-pipeline `make_vector` and `curl` MCP tools.**
  Their DSL equivalents are more composable and less confusing. If kept, they
  need to update `_vtk_objects` so downstream tools can use the results.

### Consistency fixes

- **Standardize the node-reference parameter name to `node` everywhere.**
  Currently it's `node`, `node_name`, or `name` depending on the tool. The
  distinction between "pipeline node" and "scene actor" is real but could be
  clarified by documentation rather than naming variance.

- **Make `set_camera` accept proper typed parameters** (lists/tuples) instead
  of comma-separated strings. This aligns with every other tool and with the
  DSL.

- **Standardize scalar range parameter naming.** Pick one convention
  (`scalar_range_min/max` or `min_val/max_val`) and use it consistently.

### Instructions accuracy

- **Remove `load()` from the instructions** or implement it. The instructions
  describe a workflow starting with `load("file.vts")` but the tool doesn't
  exist. This will cause every new session to fail on the first step.

- **Trim the `list_capabilities` output.** Consider splitting it into
  categories (sources, filters, colormaps) so the LLM can ask for what it
  needs, or fold the most common patterns into `get_examples` and drop
  `list_capabilities` entirely.

### Domain specificity

- **Generalize or gate `get_ground_z`.** Either make it a general "sample
  Z-coordinate at (x,y) for the lowest layer of a structured grid" tool
  (useful for any 3D grid, not just terrain), or only expose it when the loaded
  dataset is a structured grid.

### Structural

- **Proceed with `server.py` module split** (already in backlog as partially
  started). Grouping tools into query tools, mutation tools, and lifecycle
  tools would make the code easier to maintain and the tool list easier to
  review.

### Longer term

- **Consider a single `query(node, what, ...)` tool** that dispatches to
  statistics, histogram, spatial extent, etc. based on a `what` parameter.
  This would reduce the 8+ query tools to 1 tool with sub-commands, at the
  cost of slightly more complex parameters. Worth prototyping to see if it
  actually helps LLM tool selection.

- **Consider a single `adjust(actor, property, value)` tool** to replace
  set_opacity, set_colormap, set_color_range, set_background, toggle_visibility.
  These all follow the same pattern: pick an actor, change a property, re-render.
