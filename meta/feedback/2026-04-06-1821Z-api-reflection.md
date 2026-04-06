# API Reflection -- 2026-04-06

Read-only review of the public-facing MCP tools and DSL methods. The user of
this API is an LLM conversing with a human to build scientific visualizations.
A smaller, more general, consistent tool set is better than a sprawling
collection of specific tools.

---

## 1. Current API Surface

### MCP Tools

The `_ALL_TOOLS` list declares 47 tool names across three categories:

**Query tools (17):** describe_data, get_array_info, get_field_summary,
get_node_info, get_bounds, get_statistics, query_stats, get_histogram,
get_spatial_extent, sample_points, profile, get_ground_z,
suggest_scalar_range, suggest_opacity, suggest_isosurface, suggest_camera,
get_camera

**Mutation tools (13):** load, set_pipeline, reset_pipeline, set_camera,
set_opacity, set_colormap, set_background, set_window_size,
toggle_visibility, make_vector, curl, annotate, clear_annotations

**Meta tools (17):** screenshot, camera_orbit, quick_start, list_actors,
get_actor_info, list_versions, get_pipeline, restore_version,
export_standalone, get_dsl_overview, list_data_files, get_dsl_reference,
new_view, focus, close_view, list_views, render_chart

However, only **45** `@mcp.tool()` decorators exist in `server.py`.
Two entries -- `make_vector` and `curl` -- are phantom entries in
`MUTATION_TOOLS` with no corresponding tool implementation. They are DSL-only
forms. The instructions string advertises them as available tools, which will
confuse an LLM that tries to call them.

Additionally, `sample_point` and `set_color_range` are defined as plain
functions (no `@mcp.tool()` decorator) and are not in `_ALL_TOOLS`. They
appear to be intentional de-registrations from a prior tool count reduction.

**Effective MCP tool count: 45.**

### DSL Builder Methods (PipelineBuilder)

34 public methods:

- Sources (2): source, raw_source
- Filters / wrappers (20): filter, contour, isosurface, calculator,
  threshold, extract_grid, extract_region, stream_tracer, tube, glyph,
  warp_vector, warp_scalar, cell_to_point, point_to_cell, outline,
  elevation, surface, smooth, mask_points, gradient
- Clipping (3): clip, clip_box, clip_sphere
- Probing (3): probe, line_probe, seeds_near
- Derived fields (7): make_vector, compute_velocity, curl,
  compute_vorticity, compute_magnitude, compute_gradient_magnitude,
  extract_component
- Resampling (1): resample_to_image
- Slicing (1): slice
- Display (5): show, camera, background, scene_preset, title

### Query-side functions (queries.py)

13 public functions: get_rich_field_stats, format_rich_field_stats,
get_array_info, get_bounds, get_statistics, get_histogram,
get_spatial_extent_dict, get_spatial_extent, sample_point, sample_points,
format_sample_points, suggest_scalar_range, suggest_opacity_function,
suggest_isosurface, sample_line, get_line_probe_data, query_stats,
get_ground_z

---

## 2. Observations

### What is working well

**The two-layer architecture (MCP tools / DSL pipeline files) is sound.**
The separation of concerns -- MCP tools for interactive queries and scene
control, DSL forms for declarative pipeline construction -- is the project's
core design strength. Session feedback consistently shows that the pattern of
write-file-then-`set_pipeline()` works well for incremental construction.

**`describe_data()` is excellent.** It returns dimensions, bounds, spacing,
terrain-following grid detection, per-field percentiles with distribution
shape classification, volume rendering readiness notes, and quick-start
suggestions. It is the single most useful tool in the system and has
improved substantially from earlier feedback.

**The `suggest_*` family is well-scoped and heavily used.** Session logs
show `suggest_isosurface`, `suggest_opacity`, and `suggest_camera` being
called proactively before pipeline construction. These tools directly
prevent the most common LLM failure mode (guessing parameter values).

**Terrain-following grid detection works end-to-end.** This was broken in
the 04-06 wildfire session, fixed based on feedback, and verified working
in both subsequent sessions. A clean feedback-to-fix loop.

**Multi-view management is clean.** `new_view`, `focus`, `close_view`,
`list_views` work without confusion. Session logs show 4-6 simultaneous
views managed correctly.

**DSL convenience wrappers have good coverage.** The common patterns
(threshold, contour, stream_tracer + tube, clip variants, extract_region)
cover the workflows that sessions actually use.

### What is awkward or problematic

**A. 45 tools is too many.** Every tool the LLM has to choose from is
cognitive load. The tool surface has been reduced from 38 (April 4 review)
but the list has actually grown to 45 effective tools. For comparison, a
well-designed MCP server for a domain like this should aim for 15-25 tools.
The LLM has to read and reason about all 45 tool descriptions at every
turn, even when most are irrelevant.

**B. Phantom tools in the tool list.** `make_vector` and `curl` appear in
`MUTATION_TOOLS` and in the instructions string's available-tools list, but
have no `@mcp.tool()` implementation. An LLM that reads the instructions
and tries to call `make_vector()` will get "No such tool" errors. This is
a straightforward bug.

**C. Significant query tool overlap.** Multiple tools return subsets of the
same information:

- `describe_data` already returns everything that `get_array_info`,
  `get_node_info`, and `get_bounds` return. These three narrow tools are
  rarely useful independently -- session logs show them called only when
  `describe_data` was not called first.

- `get_field_summary` combines `get_statistics` + `suggest_scalar_range` +
  `suggest_opacity`. It overlaps heavily with both `describe_data` (which
  already provides rich per-field stats) and the individual suggest tools.

- `get_statistics` is subsumed by `describe_data` for initial exploration.
  Its only unique value is querying a *named pipeline node* (e.g. after
  thresholding), which `describe_data(node=...)` also supports. The backlog
  already tracks merging these.

This cluster of overlapping tools (`describe_data`, `get_array_info`,
`get_field_summary`, `get_node_info`, `get_bounds`, `get_statistics`)
constitutes 6 tools that could be 1-2.

**D. Mutation tools that should be pipeline edits.** The backlog already
flags this: `set_colormap`, `set_opacity`, `toggle_visibility`,
`set_background`, `annotate`, `clear_annotations` all mutate VTK state
without updating the pipeline file, causing the file to diverge from the
rendered state. This is a known design problem. The practical consequence
is that `get_pipeline()` lies after any mutation tool is used. These 6
tools exist for "quick tweaks" but create state management headaches.

**E. Instructions string is partially stale.** The instructions say:
"4. State-changing tools ... automatically return a screenshot -- no separate
screenshot() call needed." This is only true for tools with
`structured_output=False`. But the backlog's highest-priority item is to
*remove* auto-screenshots to fix context bloat. The instructions describe a
behavior that is both inconsistently implemented and scheduled for removal.

The instructions also list `make_vector` and `curl` as available tools
(they appear in `_ALL_TOOLS`), which they are not.

**F. Inconsistent naming between "pipeline node" and "scene actor" parameters.**

- Query tools use `node: str` for pipeline node references.
- `set_opacity`, `toggle_visibility`, `set_colormap`, `get_actor_info` use
  `name: str` for scene actor references.
- This distinction is real (nodes are pipeline graph nodes; actors are
  rendered scene objects). But from the LLM's perspective, both refer to
  "a thing with a name" and the name usually matches (it comes from the
  `show()` call). The LLM regularly uses them interchangeably.

**G. `camera_orbit` has a vestigial `node` parameter.** The parameter's
docstring says "Unused -- kept for API consistency. Leave empty." This is
wasted cognitive load. An unused parameter should be removed, not documented
as unused.

**H. `quick_start` returns code that requires a second step.** It generates
pipeline code as a string, which the LLM must then paste into a file and
call `set_pipeline()`. This two-step dance (generate-then-execute) is
unnecessary friction. It could write the file and execute it directly, or
be folded into `load()` as an optional auto-pipeline.

**I. DSL alias proliferation.** The DSL has several aliases that add no
capability:

- `isosurface()` is a pure alias for `contour()`
- `compute_velocity()` is a pure alias for `make_vector()`
- `compute_vorticity()` is a legacy wrapper around `make_vector()` + `curl()`

These aliases appear in `get_dsl_overview()`, doubling the apparent
complexity of derived-field operations. The backlog already has a "remove
alias DSL operations" item, but it remains unresolved.

**J. `render_chart` is an outlier.** It uses matplotlib to produce 2D
charts from pipeline data. This overlaps with `get_histogram` (which
returns text histograms) but produces images. It also accepts a `data`
parameter as a JSON string, which is an unusual interface compared to the
rest of the API. It is useful for presentation but sits oddly in a VTK
visualization server.

---

## 3. Proposed Backlog Items

These are ordered by estimated impact on the LLM user experience, from
highest to lowest.

- **Remove phantom tools from MUTATION_TOOLS list.** `make_vector` and
  `curl` are listed in `MUTATION_TOOLS` and appear in the instructions
  string's available-tools list, but have no `@mcp.tool()` implementation.
  Remove them from the list. This is a one-line fix that prevents confusing
  errors.

- **Merge `get_array_info`, `get_node_info`, and `get_bounds` into
  `describe_data`.** `describe_data` already returns all their information.
  Remove the three narrow tools. If fine-grained output is occasionally
  needed, add an optional `detail` parameter to `describe_data`. This
  removes 3 tools.

- **Merge `get_statistics` and `get_field_summary` into `describe_data` with
  a `field` parameter.** `describe_data(field="temperature")` should return
  rich single-field stats (percentiles, histogram shape, opacity suggestion).
  Remove `get_statistics` and `get_field_summary` as separate tools.
  `query_stats` remains separate because its conditional-filtering semantics
  are distinct. This removes 2 tools.

- **Remove `quick_start` or fold it into `load`.** Currently `load()` returns
  `describe_data()` output, and `quick_start()` returns pipeline code the LLM
  must manually paste. The more useful behavior is `load()` optionally writing
  and executing a starter pipeline. If `quick_start` is kept, it should write
  and execute the file rather than returning code as a string.

- **Remove the `node` parameter from `camera_orbit`.** It is documented as
  unused. Removing it cleans up the interface.

- **Update instructions string to match reality.** Remove references to
  `make_vector` and `curl` as callable tools. Clarify which tools return
  auto-screenshots and which do not (or update after the auto-screenshot
  removal lands). Ensure the workflow section accurately describes the
  current `load()` -> `describe_data()` -> `set_pipeline()` flow.

- **Remove DSL aliases per the existing backlog item.** Delete `isosurface()`
  (use `contour()`), `compute_velocity()` (use `make_vector()`), and
  consider collapsing `compute_vorticity()` into a documented
  `make_vector()` + `curl()` pattern. Each alias doubles the apparent
  surface area in `get_dsl_overview()` without adding capability. Update
  examples and reference docs to use the canonical forms.

- **Evaluate merging the 6 mutation tools into pipeline-file edits.** The
  backlog already proposes this. The key decision is whether `set_opacity`,
  `set_colormap`, `toggle_visibility`, `set_background`, `annotate`, and
  `clear_annotations` should remain as tools (fast tweaks, but cause
  file/state divergence) or be removed in favor of pipeline file edits
  (consistent, but slower). If kept, their state changes should be written
  back into the pipeline file to prevent divergence. If removed, the tool
  count drops by 6 and the file becomes the single source of truth.

- **Consider a unified `query` tool with a `what` parameter.** The remaining
  query tools after the merges above would be: `describe_data`, `query_stats`,
  `get_histogram`, `get_spatial_extent`, `sample_points`, `profile`,
  `get_ground_z`, `suggest_scalar_range`, `suggest_opacity`,
  `suggest_isosurface`, `suggest_camera`, `get_camera`. That is still 12
  query tools. A `query(node, what="histogram", field="temperature")`
  dispatch tool could collapse `get_histogram`, `get_spatial_extent`,
  `suggest_scalar_range`, `suggest_opacity`, `suggest_isosurface`, and
  `suggest_camera` into a single tool with sub-commands. This is a more
  radical reduction and should be prototyped to see if it helps or hinders
  LLM tool selection.

- **Standardize the `name`/`node` parameter convention.** Use `node` for
  pipeline graph node references and `actor` for scene actor references.
  Document the distinction clearly in the instructions string. Currently the
  mixed use of `name` and `node` obscures the real difference.

---

## Summary

The API has matured significantly since the April 4 reflection. The core
workflow (load, query, write pipeline, iterate) is solid. The main remaining
issues are surface area (45 tools is 2x what an LLM can comfortably reason
about) and information overlap (6 query tools that return subsets of
`describe_data`'s output). The phantom `make_vector`/`curl` entries in the
tool list are a concrete bug. The mutation-vs-pipeline-file divergence is a
known design tension. The highest-impact changes are tool merges and removals
that reduce the effective tool count toward 25-30 without losing capability.
