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
| **Test total (22 files)** | **~534** |

Lines: 8,288 source, 7,628 test. Test-to-source ratio near 1:1.

Since the 2026-04-04 reflection, source grew from 5,218 to 8,288 lines (+59%)
and tests from 315 to ~534 (+70%). The test growth slightly outpaced source
growth, which is healthy.

---

## 2. Strengths

**Prior concerns addressed systematically.** Comparing against the 2026-04-04
reflection, most high-priority issues have been resolved:
- `load()` MCP tool implemented (server.py:384-429).
- `restore_version()` bug fixed -- now writes code to the view's pipeline
  file and calls `set_pipeline()` with the file path (server.py:1596-1600).
- `_get_data_or_error()` helper eliminates the 16-copy node-lookup boilerplate
  (server.py:318-333).
- `SCALAR_TYPE_MAP` and `COMPONENT_NAME_MAP` consolidated as module-level
  constants in `filters.py` (lines 20-35), imported by `dsl.py`.
- `_create_volume()` decomposed into five small helpers: `_volume_prepare_data`,
  `_volume_build_mapper`, `_volume_build_color_function`,
  `_volume_build_opacity_function`, `_volume_build_property` (filters.py:705-981).
- `PipelineBuilder.build()` refactored into `build_pipeline()` (compute) and
  `apply_to_renderer()` (scene update), with per-special-class methods
  (`_build_extract_region_node`, `_build_seeds_near_node`, etc.).
- Module-level side effects deferred: `_args` and `_renderer` are `None` until
  `main()` runs (server.py:39-43). The `_LegacyCtx` shim in `_current_ctx()`
  preserves backward compatibility for tests.
- `get_statistics()` now uses numpy (queries.py:347).
- `EXT_TO_READER` consolidated in `filters.py` and imported by server.py.
- Server-layer test coverage added: `test_server_tools.py` with 34 tests,
  plus `test_named_views.py` (50 tests), `test_camera_orbit.py` (14 tests),
  `test_chart_rendering.py` (26 tests).

**Two-phase pipeline execution.** The split into `interpret_build()` (runs on
MCP thread, does not touch the renderer) and `apply_to_renderer()` (runs on
main thread) in dsl.py:2006-2017 is a clean separation that keeps the
interactive event loop responsive during expensive pipeline rebuilds.

**Comprehensive DSL docstrings.** Every `PipelineBuilder` method has a
thorough docstring with Args, Returns, Example, and Notes sections. The
`get_dsl_reference()` tool (server.py:2338-2988) provides per-form
documentation with hand-written examples and cross-references. This is
excellent API discoverability for the AI agent consumer.

**Diagnostic richness.** `create_vtk_filter()` in filters.py:418-462
produces specific warnings for empty filter output, distinguishing contour
out-of-range, threshold non-overlap, and streamline seed placement failures.
`describe_data()` in server.py:836-938 detects terrain-following grids and
warns against extracting ground by z-bounds.

---

## 3. Concerns

### 3.1 `server.py` has grown to 3,048 lines -- urgently needs splitting

`server.py` is now 3,048 lines and contains five distinct responsibilities:
(a) MCP server setup and argument parsing (lines 1-200),
(b) per-view state management and global state (lines 202-382),
(c) 35+ tool handler functions (lines 384-2988),
(d) pipeline execution and version management (lines 492-620),
(e) a 430-line `get_dsl_reference()` function that is mostly static data
    (lines 2338-2988).

The `get_dsl_overview()` function (lines 1718-1888) and `get_dsl_reference()`
(lines 2338-2988) together account for ~800 lines of inline string literals
and example dictionaries. These are documentation data, not server logic, and
should live in their own module.

The backlog already flags this as `server.py module split`, but the file has
nearly doubled since the last reflection (1,696 -> 3,048) and will only get
worse as tools are added or documentation is expanded.

### 3.2 Legacy global state and `_LegacyCtx` shim create dual-path maintenance burden

`server.py` maintains two parallel state systems:
- Module-level globals `_renderer`, `_vtk_objects`, `_current_code`,
  `_annotations` (lines 231-234).
- The `ViewContext` / `_views` registry (lines 207-228, 236-237).

Every state mutation must update both: see `load()` at line 427-428,
`_set_pipeline_impl()` at lines 512-515, and `reset_pipeline()` at
lines 706-712, all of which contain `global _vtk_objects, _current_code`
statements to "keep legacy globals in sync for tests."

The `_LegacyCtx` class (lines 252-292) is an elaborate shim that proxies
module-level globals as properties, creating a virtual `ViewContext` for tests
that poke `srv._renderer` directly. This dual-path state management is fragile:
any new piece of per-view state must be added to both `ViewContext` and
`_LegacyCtx`, and any mutation must update both the context and the globals.

Five test files (`test_named_views.py`, `test_server_tools.py`,
`test_annotations.py`, `test_camera_orbit.py`, `test_chart_rendering.py`)
use `srv._renderer` / `srv._vtk_objects` directly. Migrating these tests to
use `main()` initialization (or a test-oriented init helper) would allow
removing the legacy globals and the `_LegacyCtx` shim entirely.

### 3.3 Scalar bar construction duplicated across two code paths

`filters.py` contains two nearly identical scalar bar construction sequences:
- `_volume_build_scalar_bar()` at lines 900-926 (for volume rendering).
- Inline in `create_show()` at lines 1097-1109 (for surface actors).

Both set the same properties: `SetNumberOfLabels(5)`, `SetWidth(0.08)`,
`SetHeight(0.4)`, `SetPosition(0.88, 0.3)`, same font sizes and colors.
This should be extracted into a shared `_build_scalar_bar(lut, title)` helper.

### 3.4 Python loops remain in four query functions despite numpy being available

The 2026-04-04 reflection noted Python-loop performance in `queries.py`. While
`get_statistics()` and `get_histogram()` were converted to numpy, several
functions still iterate tuple-by-tuple:

- `suggest_scalar_range()` lines 686-688: Python loop + manual sort for
  percentile computation. Should use `vtk_to_numpy` + `np.percentile`.
- `suggest_opacity_function()` lines 776-783: Python loop to build histogram.
  Should use `np.histogram`.
- `suggest_isosurface()` lines 856-861: Python loop to build histogram, then
  lines 897-899: Python loop + sort for percentiles. Same numpy opportunity.
- `_auto_opacity()` in `filters.py` lines 678-684: Python loop for histogram
  computation.

These functions share a common pattern: build-histogram-from-sampled-values.
A shared `_sampled_histogram(arr, scalar_range, n_bins, max_sample)` helper
using numpy would eliminate the duplication and improve performance by 10-100x.

### 3.5 `_auto_opacity` in `filters.py` duplicates logic from `suggest_opacity_function` in `queries.py`

Both `filters._auto_opacity()` (lines 666-702) and
`queries.suggest_opacity_function()` (lines 744-827) compute a
histogram-guided opacity transfer function using the same algorithm: bin the
values, find the ambient peak, make common values transparent and rare values
opaque. The only differences are the number of bins (50 vs 100) and the output
format (list of tuples vs formatted string). This should be a single function
with a `format` parameter.

### 3.6 Dead code: `sample_point` and `set_color_range` in `server.py`

- `sample_point()` at server.py:1107-1117 is a plain function (no `@mcp.tool()`
  decorator). It is not registered as an MCP tool and is not called from
  anywhere in the codebase. Its docstring says "Use sample_points() with a
  single point instead." It should be removed.
- `set_color_range()` at server.py:1422-1451 is similarly undecorated.
  Its docstring says "Prefer set_colormap(...)". It returns
  `_with_screenshot(...)` but is not called from anywhere. Should be removed.
- `benchmark_pipeline()` at server.py:1616-1666 is also undecorated. Its
  docstring says "Developer diagnostic tool -- not exposed as an MCP tool."
  It references the old `interpret()` API signature (4-tuple return) which
  no longer matches the current `interpret()` return signature. This function
  would crash if called. Should be either updated or removed.

### 3.7 `_parse_color` is defined inside `annotate` as a closure

The `_parse_color()` function (server.py:2061-2089) is defined as a nested
function inside the `annotate()` tool handler. It is a pure utility function
with no closure over `annotate`'s variables. It should be a module-level or
utility function so it can be reused (e.g., by a future `title()` tool that
accepts color strings) and tested independently.

### 3.8 `get_dsl_reference()` contains ~430 lines of static data inline

The `_EXAMPLES` dict (server.py:2369-2799), `_RELATED` dict (lines 2859-2902),
and `_CROSS_REFS` dict (lines 2906-2947) are defined as local variables inside
`get_dsl_reference()`. These are static data that never change at runtime.
Defining them as function-local means they are reconstructed on every call.
More importantly, they make the function 650 lines long, obscuring the actual
logic (lines 2800-2988 -- about 50 lines of real code).

These dictionaries should be module-level constants, ideally in a separate
`dsl_docs.py` module.

### 3.9 `dsl.py` has grown to 2,151 lines -- primarily from docstrings

`dsl.py` grew from 715 lines (2026-04-04) to 2,151 lines. The
`PipelineBuilder` class alone contains ~1,800 lines. Most of this growth is
docstrings (which is good for API discoverability), but the file mixes three
concerns:
- The `NodeRef` and `PipelineBuilder` classes (pipeline graph construction).
- The build/execution logic (`build_pipeline`, `apply_to_renderer`, the
  `_build_*` helpers).
- The `interpret()` / `interpret_build()` entry points and `_make_namespace()`.

Splitting `PipelineBuilder` methods into functional groups (sources/filters
vs. derived fields vs. geometry vs. display) would improve navigability without
changing the public API.

### 3.10 Inconsistent return types from mutation tools

Mutation tools have inconsistent return types:
- `set_pipeline()` returns `list[str | Image]` via `_with_screenshot()`.
- `load()` returns `str` (no screenshot).
- `set_camera()`, `set_opacity()`, `set_colormap()`, `set_background()`,
  `set_window_size()`, `toggle_visibility()` return `list[str | Image]`.
- `annotate()`, `clear_annotations()` return `list[str | Image]`.
- `reset_pipeline()` returns `list[str | Image]`.
- `restore_version()` returns `list[str | Image]` (via `set_pipeline`).

The backlog notes that auto-screenshots from mutation tools should be removed
("Remove auto-screenshots from state-changing tools to fix context bloat").
When that happens, all mutation tools should return `str` consistently.

### 3.11 No test coverage for `make_vector` and `curl` as MCP tools

The `make_vector` and `curl` tools are listed in `MUTATION_TOOLS`
(server.py:82-83) but there are no corresponding `@mcp.tool()` decorated
functions in `server.py`. They appear to be vestigial entries in the tool list
from before they were demoted to DSL-only forms. The `_ALL_TOOLS` list and
the MCP instructions reference them, but FastMCP will not find them. This
could cause confusion or errors if a client enumerates available tools from
the instructions string.

### 3.12 `describe_data()` calls `GetDimensions()` three times

In `describe_data()` (server.py:836-938), `data.GetDimensions(dims)` is called
at lines 871-873, again at lines 883-886, and again at lines 898-899. Each
call overwrites the same `dims` list but the values are identical. The
dimensions should be read once and reused.

### 3.13 `_make_namespace` in `dsl.py` manually maps 40+ builder methods

The namespace dict in `_make_namespace()` (dsl.py:2034-2101) manually lists
every `PipelineBuilder` method. Adding a new DSL form requires updating both
the class and this mapping. If one is missed, the form silently fails at
runtime with a `NameError`. This should use `inspect.getmembers()` or a
class-level registry decorator to auto-populate the namespace from all
public methods on `PipelineBuilder`.

---

## 4. Proposed Backlog Items

### High Priority

- **Split `server.py` into modules.** Extract tool handlers into
  `tools_query.py`, `tools_mutate.py`, and `tools_meta.py`. Move
  `get_dsl_overview()` and `get_dsl_reference()` (with their static data
  dicts) into `dsl_docs.py`. Keep `server.py` as a thin entry point with
  MCP setup, `main()`, and the logging decorator. Target: server.py under 400
  lines.

- **Remove legacy global state and `_LegacyCtx` shim.** Migrate the 5 test
  files that use `srv._renderer` / `srv._vtk_objects` to use a proper test
  initialization path (e.g., a `_init_for_test(mode)` helper that creates
  a `ViewContext` in `_views`). Then delete `_renderer`, `_vtk_objects`,
  `_current_code`, `_annotations` module-level globals and the `_LegacyCtx`
  class. This eliminates the dual-state maintenance burden and 5 `global`
  statements in mutation functions.

- **Remove dead tool entries from `MUTATION_TOOLS`.** `make_vector` and `curl`
  are listed in `MUTATION_TOOLS` (server.py:82-83) but have no `@mcp.tool()`
  implementations. Remove them from the list, or the MCP instructions will
  advertise tools that do not exist.

### Medium Priority

- **Remove dead code: `sample_point`, `set_color_range`, `benchmark_pipeline`.**
  These three functions in `server.py` (lines 1107-1117, 1422-1451, 1616-1666)
  are undecorated, uncalled, and in the case of `benchmark_pipeline`, broken
  (wrong return signature from `interpret()`). Delete them.

- **Extract shared scalar bar builder.** The 11-line scalar bar construction
  sequence is duplicated at `filters.py:900-926` and `filters.py:1097-1109`.
  Extract into a `_build_scalar_bar(lut, title, scalar_range)` function.

- **Unify histogram-guided opacity logic.** `filters._auto_opacity()` and
  `queries.suggest_opacity_function()` implement the same algorithm with
  different bin counts and output formats. Consolidate into a single function
  in `queries.py` (or a shared module) with parameters for bin count and
  output format.

- **Convert remaining Python loops to numpy in `queries.py`.** Functions
  `suggest_scalar_range()`, `suggest_opacity_function()`, and
  `suggest_isosurface()` still use Python loops for histogram computation
  and percentile estimation. Replace with `vtk_to_numpy` + `np.histogram`
  + `np.percentile`.

- **Auto-populate DSL namespace from `PipelineBuilder` methods.** Replace the
  manual 40-entry dict in `_make_namespace()` with reflection over
  `PipelineBuilder`'s public methods. This prevents silent omission of new
  DSL forms.

### Low Priority

- **Move `_parse_color` to module level.** Extract from the `annotate()`
  closure to a module-level utility so it can be reused and tested
  independently.

- **Deduplicate `data.GetDimensions()` calls in `describe_data()`.** Read
  dimensions once at the top of the function and reuse the value in all
  three places.

- **Split `dsl.py` docstring bulk from logic.** Consider moving the lengthy
  docstrings into a docstring data module or using a decorator-based approach
  that attaches docstrings from an external source, keeping the method bodies
  terse and navigable.

- **Add MCP protocol-level smoke tests.** The backlog already calls for this.
  A single test that starts the MCP server and calls every tool through the
  JSON-RPC interface with minimal valid inputs would catch tool registration
  errors (like the `make_vector`/`curl` phantom entries) and serialization
  issues.
