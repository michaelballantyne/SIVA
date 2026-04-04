# VisLang Backlog

## High Priority

- [x] Multiple named views — Implemented `new_view(name)`, `focus(name)`, `close_view(name)`, `list_views()`. `ViewContext` class bundles per-view state (vtk_objects, current_code, version, annotations, renderer). All 39 existing tools use `_current_ctx()` helper to target the active view. Legacy shim in `_current_ctx()` preserves test backward-compat when `_views` is empty. 42 new tests in `test_named_views.py`.
- [x] Per-view pipeline files — Each view now has its own pipeline file named `view-<name>.py` (e.g. `view-main.py`, `view-closeup.py`). `ViewContext.pipeline_file` property returns the filename. `set_pipeline()` defaults to the current view's pipeline file. `restore_version()` writes to the view's pipeline file. 8 new tests added.
- [~] `server.py` module split — at 1,696 lines, `server.py` mixes MCP setup, global state, 37 tool handlers, pipeline execution, and a 125-line examples string. Suggested split: `server_state.py` (globals, `_get_data`, `_auto_screenshot`), `tools_query.py`, `tools_mutate.py`, `tools_meta.py`, with `server.py` as a thin entry point. Attempted but stalled. (code-quality-reflection 3.3)

## Medium Priority

- [ ] Jupyter notebook with trame interactive views — After exploration with multiple views, Claude assembles a summative analysis as a Jupyter notebook. Each cell contains self-contained VisLang pipeline code that renders an interactive 3D trame view. A small API (e.g. `vislang.notebook.show(code)`) interprets pipeline code and displays it in a Jupyter cell. Claude writes the .ipynb directly — no MCP notebook tools needed. Note: even before this is built, Claude can already produce static reports using screenshot paths from `.vislang/history/`.
- [x] Replace Python loops with numpy in `queries.py` — `get_statistics()`, `get_histogram()`, and `get_spatial_extent()` iterate over all tuples in Python; on the wildfire dataset (18M points) this takes seconds per field. `get_rich_field_stats()` already uses `vtk_to_numpy` + numpy for the same work. Converted these three functions to the numpy path for 10-100x speedup. (code-quality-reflection 3.7)
- [x] Fix `get_statistics()` missing cell-data arrays in error messages — When reporting "field not found," it only listed point data arrays even though the function searches both point and cell data. The error hint now lists all available arrays from both. (code-quality-reflection 3.8)
- [x] Consolidate duplicated constants — `_scalar_type_map` is defined independently in `dsl.py` and `filters.py`; `_component_name_map` (`{"x":0,"y":1,"z":2}`) appears in four places (`filters.py:72`, `filters.py:959`, `dsl.py:161`, `server.py:403`). Move each to a single module-level constant and import it everywhere. Also unify the reader-extension maps (`EXT_TO_READER` in `filters.py` vs the local `readers` dict in `server.py:quick_start`, which omits `.vtr`). (code-quality-reflection 3.5, 3.6, 3.14)
- [x] Extract node-lookup boilerplate into a helper — 16 MCP tool functions repeat the same 4-line `_get_data` / error-return pattern. A `@requires_data` decorator or `_get_data_or_error()` helper would eliminate ~64 lines of duplication and ensure consistent error messages. (code-quality-reflection 3.4)
- [x] Standardize API parameter naming — `set_camera` takes comma-separated strings instead of typed lists; node references are `node`, `node_name`, or `name` depending on the tool; scalar range is `scalar_range_min/max` in some tools and `min_val/max_val` in others. Pick one convention per concept and apply it consistently. (api-reflection C)
- [x] Reduce tool count by merging redundant tools — removed `@mcp.tool()` from `sample_point` (use `sample_points` with one point), `set_color_range` (use `set_colormap(name, scalar_range=[min,max])`), and `benchmark_pipeline` (developer diagnostic). Tool count reduced from 38 to 35. Underlying functions preserved so tests still pass. (api-reflection B, G)
- [x] Generalize or gate `get_ground_z` — its docstring is specific to terrain-following grids (wildfire dataset). For a CT scan or any non-terrain dataset it errors. Either generalize it to "sample Z at (x,y) for the lowest layer of any structured grid" or only expose it when a structured grid is loaded. (api-reflection E)

## Low Priority / Ideas

- [x] Automated documentation extraction — `gen_docs.py` imports vislang modules (with mcp/renderer mocked) and writes `docs/reference.md` (MCP tools + DSL methods with signatures and docstrings), `docs/instructions.md` (server instructions string), `docs/examples.md` (get_examples() output), and `README.md` (project overview). Idempotent; run `python gen_docs.py` to regenerate.

- [x] Add server-layer test coverage — 29 tests in `tests/test_server_tools.py` directly invoke the `@mcp.tool` functions via the same mock pattern as `test_auto_screenshot.py`. Covers `load()`, `describe_data()`, `get_statistics()`, `query_stats()` (including all six comparison operators and parse failure), `sample_points()`, `extract_component()`, and `_get_data_or_error()`. (code-quality-reflection 3.15)
- [x] Remove MCP tool versions of `extract_component`, `make_vector`, and `curl` — these duplicated DSL functionality; the DSL versions in `dsl.py`/`filters.py` are preferred. Tests for DSL/filter versions kept; docs regenerated.
- [x] Decompose `_create_volume()` and `PipelineBuilder.build()` — Both were 200+ line functions handling many unrelated concerns. Broken into smaller single-responsibility functions. (code-quality-reflection 3.10, 3.11)
- [x] Fix `_seeds_near` use structured data — was regex-parsing the formatted string from `get_spatial_extent` to extract coordinates; now calls a structured version returning numeric values directly. (code-quality-reflection 3.11)
- [x] Defer module-level side effects in `server.py` — `_parse_args()` and `Renderer` creation run at import time, making `import vislang.server` in tests trigger argparse and VTK window creation. Moved both into `main()` with lazy initialization. (code-quality-reflection 3.12)
- [x] Standardize error return conventions in `queries.py` — query functions now prefix all error returns with "Error: " for consistent detection by callers. (code-quality-reflection 3.9)
- [x] Fix unreachable dead code in `queries.query_stats()` — Line 1266 (`return "\n".join(lines)`) was unreachable after the early return on lines 1252-1264. Removed to avoid confusion. (code-quality-reflection 3.1)
- [x] Trim `list_capabilities` output — reduced from ~80 lines to ~54 lines. (api-reflection F)
- [x] Remove or hide `benchmark_pipeline` from MCP tool list — done as part of tool count reduction above. (api-reflection G)
- [x] Scene annotations — `annotate(x, y, z, label, color, font_size)` and `clear_annotations()` MCP tools added; uses `vtkBillboardTextActor3D` for camera-facing labels; 22 tests in `tests/test_annotations.py`.
- [x] Add a second dataset (bonsai CT scan) — added the Bonsai dataset (256³ uint8, ~16 MB, from klacansky.com/open-scivis-datasets/) as `datasets/bonsai/`. Regular grid (vtkImageData), isotropic spacing, single scalar — structurally different from the wildfire curvilinear grid. 19 verification tests added.
- [ ] Context-aware `get_examples` — filter examples by loaded data type and substitute real field names and ranges from the active pipeline. Reverted — blind field substitution produces misleading examples. Needs a more principled approach that understands field semantics.
- [x] 2D chart rendering — `render_chart(chart_type, node, field, data, title, x_label, y_label)` MCP tool added. Supports "histogram" (field values from pipeline) and "line" (raw JSON x/y data or field vs. index from pipeline). Returns `[description_text, Image(png)]`. Uses matplotlib Agg backend for headless rendering. 26 tests in `tests/test_chart_rendering.py`.
- [ ] Multi-panel layouts — side-by-side views showing different fields on the same geometry. Session 1 identified this as useful for comparison.
- [x] Camera orbit / turntable — `camera_orbit(node, n_frames, elevation)` MCP tool added. Returns alternating text descriptions and Image objects for n_frames evenly-spaced azimuth positions. Saves original camera state and restores it after orbit. 14 tests in `tests/test_camera_orbit.py`.
- [ ] In-plane vector glyphs on slices — `show_vectors()` for flow visualization on cross-sections.
- [ ] Multi-timestep support — discover sibling timesteps, animate, compare.

## Completed

- [x] Auto-screenshot from state-changing tools — `set_pipeline`, `set_camera`, `set_colormap`, `toggle_visibility` etc. now return a screenshot automatically via `_with_screenshot()`.
- [x] Rich `describe_data` with percentiles — include p1/p25/p50/p75/p99 for each field, distribution shape flag, terrain-following detection, and coordinate-to-index mapping.
- [x] Fix silent calculator failures — post-update validation for vtkArrayCalculator; when the named result array is absent after `Update()`, the pipeline reports a warning.
- [x] Vector component coloring — `component` parameter on `show()` colors by a single component of a vector field via `SetArrayComponent()`.
- [x] `extract_component` helper and `compute_vorticity(vector=True)` — numpy/VTK array ops bypassing vtkArrayCalculator; 9 tests in `test_extract_component.py`.
- [x] Generalize `compute_velocity`/`compute_vorticity` into `make_vector` + `curl` — general primitives with `compute_velocity` and `compute_vorticity` as thin wrappers; 26 tests.
- [x] `sample_line` / line probe — extract a 1D profile between two points; implemented in `queries.py` and exposed as MCP tool.
- [x] Conditional / subregion statistics — `query_stats(node, field, condition)` for queries like "mean updraft velocity where theta > 400K."
- [x] Error path tests — 50 tests in `test_error_paths.py` covering invalid expressions, missing fields, out-of-range values, empty datasets, and unsupported extensions.
- [x] Integration tests pass under pytest — fixed Xvfb startup in `conftest.py` and renamed `test` decorator to `_register`; all 46 integration tests pass.
- [x] Batch point probing — `sample_points(node, points, fields)` replaces N individual `sample_point` calls; 29 tests.
- [x] Coordinate-based `extract_region` — accept physical bounds (not grid indices) for `vtkExtractGrid`; coexists with `VOI` parameter.
- [x] `load("file.vts")` convenience (partial) — `describe_data(file_path=...)` reads without a prior `set_pipeline()`; `load_file()` in `filters.py` with 27 tests. Note: `load()` as a standalone MCP tool was not actually implemented despite being marked done — see high-priority bug above.
- [x] Implement the missing `load()` MCP tool — `load(filename)` now exists as a proper `@mcp.tool` decorated function in `server.py`. (commit b067750)
- [x] Fix `restore_version()` bug — `restore_version()` now writes the versioned code to `pipeline.py` before calling `set_pipeline`, fixing the path/content mismatch. (commit b067750)
- [x] `compute_vorticity` returns vector + magnitude — `vector=True` parameter returns full 3-component vorticity vector.
- [x] `describe_data` working without active pipeline — `describe_data(file_path="file.vts")` reads the file directly.

## Dataset Sources

- [Open SciVis Datasets](https://klacansky.com/open-scivis-datasets/) — curated collection of volumetric datasets (CT scans, simulations) in raw format. Good source for structurally diverse test data. Includes bonsai, hydrogen atom, nucleon, skull, foot, and ~30 others ranging from 67 KB to multi-GB.
