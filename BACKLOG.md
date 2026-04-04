# VisLang Backlog

## High Priority

- [ ] Implement the missing `load()` MCP tool — The MCP instructions and tool list reference `load("file.vts")` but no `@mcp.tool` decorated `load` function exists. Every new session following the instructions will fail on the first step. Approximately 30 lines plus tests; can delegate to or wrap `describe_data(file_path=...)`. (code-quality-reflection 3.2, api-reflection A)
- [ ] Fix `restore_version()` bug — `server.py:1321` passes pipeline code content to `set_pipeline()` which expects a file path. The fix is to write the code to a temp file first, or refactor `set_pipeline` to accept raw code as well as a path. (code-quality-reflection 3.13)
- [ ] Fix unreachable dead code in `queries.query_stats()` — Line 1266 (`return "\n".join(lines)`) is unreachable after the early return on lines 1252-1264. Delete it to avoid confusion. (code-quality-reflection 3.1)
- [~] `server.py` module split — at 1,696 lines, `server.py` mixes MCP setup, global state, 37 tool handlers, pipeline execution, and a 125-line examples string. Suggested split: `server_state.py` (globals, `_get_data`, `_auto_screenshot`), `tools_query.py`, `tools_mutate.py`, `tools_meta.py`, with `server.py` as a thin entry point. Attempted but stalled. (code-quality-reflection 3.3)

## Medium Priority

- [ ] Replace Python loops with numpy in `queries.py` — `get_statistics()`, `get_histogram()`, and `get_spatial_extent()` iterate over all tuples in Python; on the wildfire dataset (18M points) this takes seconds per field. `get_rich_field_stats()` already uses `vtk_to_numpy` + numpy for the same work. Convert these three functions to the numpy path for 10-100x speedup. (code-quality-reflection 3.7)
- [x] Consolidate duplicated constants — `_scalar_type_map` is defined independently in `dsl.py` and `filters.py`; `_component_name_map` (`{"x":0,"y":1,"z":2}`) appears in four places (`filters.py:72`, `filters.py:959`, `dsl.py:161`, `server.py:403`). Move each to a single module-level constant and import it everywhere. Also unify the reader-extension maps (`EXT_TO_READER` in `filters.py` vs the local `readers` dict in `server.py:quick_start`, which omits `.vtr`). (code-quality-reflection 3.5, 3.6, 3.14)
- [x] Extract node-lookup boilerplate into a helper — 16 MCP tool functions repeat the same 4-line `_get_data` / error-return pattern. A `@requires_data` decorator or `_get_data_or_error()` helper would eliminate ~64 lines of duplication and ensure consistent error messages. (code-quality-reflection 3.4)
- [ ] Standardize API parameter naming — `set_camera` takes comma-separated strings instead of typed lists; node references are `node`, `node_name`, or `name` depending on the tool; scalar range is `scalar_range_min/max` in some tools and `min_val/max_val` in others. Pick one convention per concept and apply it consistently. (api-reflection C)
- [ ] Reduce tool count by merging redundant tools — `sample_point` is a strict subset of `sample_points`; `set_color_range` is a strict subset of `set_colormap`; `get_statistics` is subsumed by `describe_data` for initial exploration. Removing or aliasing these three reduces the tool list from 38 toward 35 and makes tool selection easier for LLMs. (api-reflection B)
- [ ] Fix `get_statistics()` missing cell-data arrays in error messages — When reporting "field not found," it only lists point data arrays even though the function searches both point and cell data. The error hint should list all available arrays from both. (code-quality-reflection 3.8)
- [ ] Add a second dataset (bonsai CT scan) — add the Bonsai dataset (256³ uint8, ~16 MB, from klacansky.com/open-scivis-datasets/) as `datasets/bonsai/`. Regular grid (vtkImageData), isotropic spacing, single scalar — structurally different from the wildfire curvilinear grid. Will reveal which parts of the system are wildfire-specific.

## Low Priority / Ideas

- [ ] Add server-layer test coverage — 315 tests exercise `queries.py`, `filters.py`, and `dsl.py` directly, but zero tests invoke `@mcp.tool` functions. The missing `load()` tool would have been caught by a basic smoke test. At minimum, test condition-parsing in `query_stats` and the node-lookup boilerplate. (code-quality-reflection 3.15)
- [ ] Decompose `_create_volume()` and `PipelineBuilder.build()` — Both are 200+ line functions handling many unrelated concerns. Break into smaller single-responsibility functions. (code-quality-reflection 3.10, 3.11)
- [ ] Make `_seeds_near` use structured data — currently regex-parses the formatted string from `get_spatial_extent` to extract coordinates; this would silently break if the output format changes. Have it call a structured version returning numeric values directly. (code-quality-reflection 3.11)
- [ ] Defer module-level side effects in `server.py` — `_parse_args()` and `Renderer` creation run at import time, making `import vislang.server` in tests trigger argparse and VTK window creation. Move both into `main()` with lazy initialization. (code-quality-reflection 3.12)
- [ ] Standardize error return conventions in `queries.py` — query functions return error strings that callers can't distinguish from legitimate text results. Raise exceptions for errors and return strings for success, or adopt the `(result, error)` tuple pattern that `load_file()` already uses. (code-quality-reflection 3.9)
- [ ] Generalize or gate `get_ground_z` — its docstring is specific to terrain-following grids (wildfire dataset). For a CT scan or any non-terrain dataset it errors. Either generalize it to "sample Z at (x,y) for the lowest layer of any structured grid" or only expose it when a structured grid is loaded. (api-reflection E)
- [ ] Trim `list_capabilities` output — currently ~80 lines that consume significant context. Consider splitting by category (sources, filters, colormaps) or folding common patterns into `get_examples` and removing `list_capabilities`. (api-reflection F)
- [ ] Remove or hide `benchmark_pipeline` from MCP tool list — it's a developer diagnostic, unlikely to be called during a visualization session, and adds to the tool count without serving the primary user. (api-reflection G)
- [ ] Scene annotations — `annotate(position, label)` for labeling features on the rendered image. Sessions 1 and 2 identified this as a top-two gap for communicating scientific context.
- [ ] 2D chart rendering — render histogram or line-plot images from field data or probe results. Complements line probes by making the output visual rather than tabular.
- [ ] Multi-panel layouts — side-by-side views showing different fields on the same geometry. Session 1 identified this as useful for comparison.
- [ ] Camera orbit / turntable — return multiple frames from different angles to help readers grasp 3D structure.
- [ ] Context-aware `get_examples` — filter examples by loaded data type and substitute real field names and ranges from the active pipeline.
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
- [x] `compute_vorticity` returns vector + magnitude — `vector=True` parameter returns full 3-component vorticity vector.
- [x] `describe_data` working without active pipeline — `describe_data(file_path="file.vts")` reads the file directly.

## Dataset Sources

- [Open SciVis Datasets](https://klacansky.com/open-scivis-datasets/) — curated collection of volumetric datasets (CT scans, simulations) in raw format. Good source for structurally diverse test data. Includes bonsai, hydrogen atom, nucleon, skull, foot, and ~30 others ranging from 67 KB to multi-GB.
