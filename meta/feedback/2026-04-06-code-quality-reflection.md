# Code Quality Reflection -- 2026-04-06

## 1. Codebase Snapshot

| File | Lines |
|---|---|
| `vislang/server.py` | 3,048 |
| `vislang/dsl.py` | 2,151 |
| `vislang/queries.py` | 1,336 |
| `vislang/filters.py` | 1,110 |
| `vislang/renderer.py` | 322 |
| `vislang/colormaps.py` | 244 |
| `vislang/run.py` | 71 |
| `vislang/__init__.py` | 2 |
| `vislang/__main__.py` | 4 |
| **Source total** | **8,288** |

| Test file | Tests |
|---|---|
| `tests/test_named_views.py` | 50 |
| `tests/test_error_paths.py` | 50 |
| `tests/test_integration.py` | 46 |
| `tests/test_server_tools.py` | 34 |
| `tests/test_sample_points.py` | 29 |
| `tests/test_query_stats.py` | 29 |
| `tests/test_load_convenience.py` | 27 |
| `tests/test_make_vector_curl.py` | 26 |
| `tests/test_chart_rendering.py` | 26 |
| `tests/test_line_probe.py` | 25 |
| `tests/test_coordinate_extract.py` | 24 |
| `tests/test_annotations.py` | 22 |
| `tests/test_get_ground_z.py` | 20 |
| `tests/test_extract_component.py` | 19 |
| `tests/test_bonsai_dataset.py` | 19 |
| `tests/test_describe_data.py` | 16 |
| `tests/test_camera_orbit.py` | 14 |
| `tests/test_component_coloring.py` | 11 |
| `tests/test_describe_data_no_pipeline.py` | 10 |
| `tests/test_load_tool.py` | 8 |
| `tests/test_auto_screenshot.py` | 5 |
| `tests/test_headless_interactive.py` | 4 |
| **Test total (22 files)** | **514** |

Lines: 8,288 source, 7,628 test. Test-to-source ratio near 1:1.

Since the 2026-04-04 reflection, source grew from 5,218 to 8,288 lines (+59%)
and tests from 315 to 514 (+63%). Growth was concentrated in `dsl.py`
(+1,436 lines, mostly docstrings) and `server.py` (+1,352 lines, tool handlers
and documentation data). Recent velocity (git log) shows multi-view support,
terrain detection, close_view fixes, and documentation consolidation.

---

## 2. Strengths

**Prior concerns addressed.** Comparing against the 2026-04-04 reflection:
- `load()` MCP tool implemented (server.py:384-429).
- `_get_data_or_error()` helper eliminates the 16-copy node-lookup boilerplate
  (server.py:318-333). Every query tool now uses a consistent 2-line pattern.
- `SCALAR_TYPE_MAP`, `COMPONENT_NAME_MAP`, `EXT_TO_READER` consolidated as
  module-level constants in `filters.py` (lines 20-35, 468-477), imported
  where needed.
- `_create_volume()` decomposed into five focused helpers:
  `_volume_prepare_data`, `_volume_build_mapper`, `_volume_build_color_function`,
  `_volume_build_opacity_function`, `_volume_build_property` (filters.py:705-981).
- `PipelineBuilder.build()` refactored into `build_pipeline()` (compute) and
  `apply_to_renderer()` (scene update), with per-special-class build methods.
- Module-level side effects deferred: `_args` and `_renderer` are `None` until
  `main()` runs (server.py:39-43).

**Two-phase pipeline execution.** The split into `interpret_build()` (runs on
the MCP thread, does not touch the renderer) and `apply_to_renderer()` (runs on
the main thread) in dsl.py:2006-2017 is a clean separation that keeps the
interactive event loop responsive during expensive pipeline rebuilds.

**Comprehensive DSL docstrings.** Every `PipelineBuilder` method has a
thorough docstring with Args, Returns, Example, and Notes sections. The
`get_dsl_reference()` tool (server.py:2338-2988) provides per-form
documentation with hand-written examples and cross-references. This is
excellent API discoverability for AI agent consumers.

**Diagnostic richness.** `create_vtk_filter()` in filters.py:418-462
produces specific warnings for empty filter output, distinguishing contour
out-of-range, threshold non-overlap, and streamline seed placement failures.
`describe_data()` in server.py:836-938 detects terrain-following grids and
warns against extracting ground by z-bounds.

**Reader caching.** `filters.py:7-11, 297-322` caches VTK readers to avoid
re-reading large files on pipeline rebuild. The cache key is
`(class_name, filename)`, which is simple and correct.

**Test quality.** 514 tests across 22 files exercise real VTK objects.
`conftest.py` auto-starts Xvfb, generates synthetic test data, and
conditionally skips integration tests when datasets are absent. Tests
use `_make_vti_with_fields()` and similar helpers that create real VTK
datasets, which catches actual integration failures.

**Error convention in queries.py.** The module docstring (lines 1-14) documents
a clear error convention: string results use the `"Error: "` prefix, structured
results use an `"error"` dict key. This is consistently followed across all
15+ query functions.

---

## 3. Concerns

### 3.1 `server.py` has grown to 3,048 lines -- urgently needs splitting

`server.py` is the largest source file and contains five distinct
responsibilities:
(a) MCP server setup, argument parsing, and logging (lines 1-200),
(b) Per-view state management (`ViewContext`, `_LegacyCtx`) (lines 202-382),
(c) 35+ tool handler functions (lines 384-2333),
(d) Pipeline execution, version management, and export (lines 492-1716),
(e) `get_dsl_overview()` (lines 1718-1888) and `get_dsl_reference()` (lines
    2338-2988) -- 800+ lines of static documentation data.

The backlog flags this as `server.py module split`, but the file has nearly
doubled since the last reflection (1,696 -> 3,048). Every new tool or
documentation improvement makes it worse.

### 3.2 Legacy global state and `_LegacyCtx` shim create dual-path maintenance

`server.py` maintains two parallel state systems:
- Module-level globals `_renderer`, `_vtk_objects`, `_current_code`,
  `_annotations` (lines 231-234).
- The `ViewContext` / `_views` registry (lines 207-228, 236-237).

Every state mutation must update both. There are 10 `global` statements
(lines 265, 272, 427, 513, 706, 1951, 1974, 1996, 2992) synchronizing
the two systems. The `_LegacyCtx` class (lines 252-292) has 8 properties
with getters and setters that proxy module-level globals, solely for test
backward compatibility.

The fix is straightforward: add a `_init_for_test(mode=OFFSCREEN)` helper that
creates a proper `ViewContext` in `_views`, update the 5 test files that
poke globals directly, then delete the legacy globals and `_LegacyCtx`.

### 3.3 Phantom tool entries in `MUTATION_TOOLS`

`make_vector` and `curl` are listed in `MUTATION_TOOLS` (server.py:82-83) and
therefore appear in `_ALL_TOOLS` (line 106) which is embedded in the MCP
instructions string (line 162). However, there are no corresponding
`@mcp.tool()` decorated functions in `server.py` -- these were demoted to
DSL-only forms. FastMCP will not register them, so clients that enumerate
tools from the instructions will see names that do not exist. This is a
functional correctness issue.

### 3.4 Three dead functions in `server.py`

- `sample_point()` at line 1107: not decorated, not called. Docstring says
  "Use sample_points() instead."
- `set_color_range()` at line 1422: not decorated, not called. Docstring says
  "Prefer set_colormap(...)".
- `benchmark_pipeline()` at line 1616: not decorated, not called. References
  the old `interpret()` 4-tuple return which no longer matches the current
  `interpret()` signature. This function would crash if called.

### 3.5 Scalar bar construction duplicated across two code paths

`filters.py` contains two nearly identical scalar bar construction sequences:
- `_volume_build_scalar_bar()` at lines 900-926 (for volume rendering).
- Inline in `create_show()` at lines 1097-1109 (for surface actors).

Both set identical properties: `SetNumberOfLabels(5)`, `SetWidth(0.08)`,
`SetHeight(0.4)`, `SetPosition(0.88, 0.3)`, same font sizes and colors.
A shared `_build_scalar_bar(lut, title, scalar_range)` helper would
eliminate this.

### 3.6 Six Python sampling loops in `queries.py` despite numpy availability

Six functions in `queries.py` use the pattern `for i in range(0, n, step):
v = arr.GetValue(i)` to manually subsample VTK arrays:
- `sample_point()` lines 487-494 (brute-force fallback)
- `sample_points()` lines 589-596 (brute-force fallback)
- `suggest_scalar_range()` lines 686-688
- `suggest_opacity_function()` lines 776-783
- `suggest_isosurface()` lines 856-861
- `suggest_isosurface()` lines 897-899

`vtk_to_numpy` + `np.percentile` / `np.histogram` would replace these with
vectorized operations. The first two are fallback paths (acceptable), but
`suggest_scalar_range`, `suggest_opacity_function`, and `suggest_isosurface`
are primary code paths called in normal workflows.

### 3.7 `_auto_opacity` in `filters.py` duplicates `suggest_opacity_function` logic

`filters._auto_opacity()` (lines 666-702) and
`queries.suggest_opacity_function()` (lines 744-827) implement the same
algorithm: bin field values, find the ambient peak, make common values
transparent and rare values opaque. Differences: bin count (50 vs 100),
number of control points (8 vs 6), and output format. These should be a
single function.

### 3.8 `_parse_color` defined as closure inside `annotate()`

`_parse_color()` (server.py:2061-2089) is a pure utility function nested
inside `annotate()`. It has no closure variables. It should be module-level
so it can be reused (e.g., by `title()` if it ever accepts color strings)
and tested independently.

### 3.9 `get_dsl_reference()` is 650 lines, of which 600 are static data

The `_EXAMPLES` dict (server.py:2369-2799), `_RELATED` dict (lines 2859-2902),
and `_CROSS_REFS` dict (lines 2906-2947) are local variables inside
`get_dsl_reference()`. They are reconstructed on every call. They make the
function 650 lines, obscuring the 50 lines of actual logic. These should be
module-level constants in a separate `dsl_docs.py` module.

### 3.10 `_make_namespace` manually maps 40+ builder methods

The namespace dict in `_make_namespace()` (dsl.py:2034-2101) manually lists
every `PipelineBuilder` method. Adding a new DSL form requires updating both
the class and this mapping. If one is missed, the form silently fails with a
`NameError` at pipeline runtime. Auto-populating from `PipelineBuilder`'s
public methods via `inspect.getmembers()` would eliminate this sync risk.

### 3.11 `describe_data()` calls `GetDimensions()` three times

In `describe_data()` (server.py:836-938), `data.GetDimensions(dims)` is called
at lines 871-873, again at 883-886, and again at 898-899. Each call
overwrites the same `dims` list with identical values. Should be read once
and reused.

### 3.12 Inconsistent return types across mutation tools

Mutation tools have mixed return types:
- `load()` returns `str` (no screenshot).
- `set_pipeline()`, `reset_pipeline()`, `set_camera()`, `set_opacity()`,
  `set_colormap()`, `set_background()`, `set_window_size()`,
  `toggle_visibility()`, `annotate()`, `clear_annotations()` all return
  `list[str | Image]` via `_with_screenshot()`.
- `restore_version()` delegates to `set_pipeline()`, returning the same.

The backlog notes this inconsistency should be resolved by removing
auto-screenshots from mutation tools. When that happens, all mutation tools
should return `str`.

### 3.13 `run.py` references outdated `interpret()` return signature

`run.py:57` unpacks the return value of `interpret()` as
`vtk_objects, node_names, node_statuses, show_statuses`. The actual return
of `interpret()` (dsl.py:2104-2126) is
`(vtk_objects_by_name, node_statuses, show_statuses, builder)`. The variable
`node_names` would silently receive `node_statuses`, and `node_statuses`
would receive `show_statuses`. The script would appear to work but print
incorrect status information. This is a latent bug.

### 3.14 `_with_screenshot` used by 12 tool functions, all requiring `structured_output=False`

Every tool that uses `_with_screenshot()` must be decorated with
`@mcp.tool(structured_output=False)` to allow mixed `str | Image` returns.
This is a fragile pairing: forgetting the decorator flag on a new tool would
cause a runtime serialization error. When the auto-screenshot pattern is
removed per the backlog, 13 tools will need their decorator updated back to
the default. Consider centralizing this pattern so it is applied
consistently.

### 3.15 `vtk` imported inline 7 times in `server.py`

`server.py` has `import vtk` in 6 different function bodies (lines 1353,
1391, 1432, 1459, 1543, 2059) plus one `from vtk.util.numpy_support` at
line 2186. A single top-level import would be cleaner and avoid the
repeated import overhead, even though Python caches modules.

---

## 4. Proposed Backlog Items

### High Priority

- **Split `server.py` into modules.** Extract tool handlers into
  `tools_query.py`, `tools_mutate.py`, and `tools_meta.py`. Move
  `get_dsl_overview()` and `get_dsl_reference()` (with their static data
  dicts) into `dsl_docs.py`. Keep `server.py` as a thin entry point with
  MCP setup, `main()`, and the logging decorator. Target: server.py under
  400 lines.

- **Remove legacy global state and `_LegacyCtx` shim.** Add a
  `_init_for_test(mode)` helper that creates a proper `ViewContext` in
  `_views`. Migrate the 5 test files that poke `srv._renderer` /
  `srv._vtk_objects` directly. Then delete the legacy module-level globals
  and the `_LegacyCtx` class, eliminating the 10 `global` statements and
  dual-state maintenance burden.

- **Remove phantom tool entries.** Remove `make_vector` and `curl` from
  `MUTATION_TOOLS` (server.py:82-83). These have no `@mcp.tool()`
  implementations and cause the MCP instructions to advertise nonexistent
  tools.

- **Fix `run.py` return-value unpacking.** `run.py:57` unpacks `interpret()`
  with the wrong variable names. Update to match the current 4-tuple return
  signature: `(vtk_objects_by_name, node_statuses, show_statuses, builder)`.

### Medium Priority

- **Remove dead code.** Delete `sample_point()` (server.py:1107),
  `set_color_range()` (server.py:1422), and `benchmark_pipeline()`
  (server.py:1616). All three are undecorated, uncalled, and in the case
  of `benchmark_pipeline`, broken.

- **Extract shared scalar bar builder.** The 11-line scalar bar
  construction sequence is duplicated at `filters.py:900-926` and
  `filters.py:1097-1109`. Extract into `_build_scalar_bar()`.

- **Unify histogram-guided opacity logic.** Consolidate
  `filters._auto_opacity()` and `queries.suggest_opacity_function()` into
  a single function with parameters for bin count and output format.

- **Convert remaining Python loops to numpy.** Replace the 4 primary-path
  Python loops in `suggest_scalar_range()`, `suggest_opacity_function()`,
  and `suggest_isosurface()` with `vtk_to_numpy` + `np.histogram` +
  `np.percentile`.

- **Auto-populate DSL namespace.** Replace the manual 40-entry dict in
  `_make_namespace()` with reflection over `PipelineBuilder`'s public
  methods. Prevents silent omission of new DSL forms.

### Low Priority

- **Move `_parse_color` to module level.** Extract from the `annotate()`
  closure to a module-level utility for reuse and independent testing.

- **Deduplicate `GetDimensions()` calls in `describe_data()`.** Read
  dimensions once at the function top.

- **Add top-level `import vtk` in `server.py`.** Replace 7 inline imports
  with a single module-level import.

- **Add MCP protocol-level smoke tests.** A single test that calls every
  registered tool through the actual JSON-RPC protocol would catch phantom
  tool entries and serialization issues.
