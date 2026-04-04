# VisLang Backlog

## High Priority

- [x] Auto-screenshot from state-changing tools — `set_pipeline`, `set_camera`, `set_colormap`, `toggle_visibility` etc. now return a screenshot automatically via `_with_screenshot()`. Covers: set_pipeline, reset_pipeline, set_camera, set_opacity, set_colormap, set_color_range, toggle_visibility, set_window_size, set_background. Query tools are unaffected.
- [x] Rich `describe_data` with percentiles — include p1/p25/p50/p75/p99 for each field, distribution shape flag (uniform/skewed/bimodal/sparse), terrain-following detection, and coordinate-to-index mapping. Eliminates 3-6 follow-up `get_field_summary` calls per session. Sessions-1-and-2 identified this as the single biggest exploration bottleneck.
- [x] Fix silent calculator failures — `61d803f` added post-update validation for vtkArrayCalculator: when the named result array is absent after `Update()`, the pipeline now reports a warning rather than silently succeeding. Audit other field-name paths (show(), threshold, contour) for same class of silent failure to ensure coverage is complete.
- [ ] Add a second dataset (bonsai CT scan) — add the Bonsai dataset (256³ uint8, ~16MB, from klacansky.com/open-scivis-datasets/) as `datasets/bonsai/`. Regular grid (vtkImageData), isotropic spacing, single scalar — structurally different from the wildfire curvilinear grid. Will reveal which parts of the system are wildfire-specific.
- [x] Vector component coloring — `component` parameter on `show()` colors by a single component of a vector field (`SetArrayComponent()` on the mapper). Supports 0/1/2 or "x"/"y"/"z". Documented in MCP instructions and `get_examples()`.

## Medium Priority

- [x] `extract_component` helper and `compute_vorticity(vector=True)` — `extract_component(input, field, component, result)` uses numpy/VTK array ops (bypassing the unreliable vtkArrayCalculator). `compute_vorticity` now returns a 3-vector when `vector=True` (result defaults to "vorticity"). Implemented in `vislang/filters.py` and `vislang/dsl.py` with 9 tests in `tests/test_extract_component.py`.
- [ ] Generalize `compute_velocity`/`compute_vorticity` into `make_vector` + `curl` — `compute_velocity` is really just "assemble scalars into a vector" and `compute_vorticity` is `curl(velocity)`. Refactor into two general primitives: `make_vector(components=("a","b","c"))` and `curl(vector_field)`. Vorticity becomes a one-liner. Opens the door to `gradient`, `divergence`, etc. as a family of differential operators.
- [x] `sample_line` / line probe — extract a 1D profile between two points and return field values along it. Implemented in `queries.py` and exposed as MCP tool `sample_line()`. Comprehensive tests in `tests/test_line_probe.py`.
- [x] Conditional / subregion statistics — `query_stats(node, field, condition)` for queries like "mean updraft velocity where theta > 400K" or "volume where O2 < 0.20." Avoids building a full pipeline for statistical questions. Implemented in `queries.py` and exposed as MCP tool `query_stats()`. Tests in `tests/test_query_stats.py`.
- [x] Error path tests — added `tests/test_error_paths.py` with 50 tests covering: invalid calculator expressions, missing field names in get_statistics/get_histogram/query_stats, out-of-range threshold and contour values, operations on None data (no active pipeline), sample_point outside bounds, query_stats with bad condition strings, load_file with unsupported extensions, load_file with non-existent files. Also fixed a ZeroDivisionError in `queries.get_statistics` when called on an empty dataset (0 tuples after threshold removes all points).
- [x] Batch point probing — `sample_points(node, points=[(x,y,z),...], fields=["a","b"])` to replace N individual `sample_point` calls. Reduces round trips for quantitative analysis. Implemented in `queries.sample_points()` + `format_sample_points()` and exposed as MCP tool `sample_points()` in `server.py`. Builds a single vtkPointLocator for all points; detects out-of-bounds queries; returns structured list of dicts. 29 tests in `tests/test_sample_points.py`.
- [x] Coordinate-based `extract_region` — accept physical bounds (not grid indices) for `vtkExtractGrid`. Implemented in `filters.py` as `_physical_bounds_to_voi()`. Session 2 documented two wasted iterations because index guessing is opaque. Coexists with current `VOI` parameter. Tests in `tests/test_coordinate_extract.py`.
- [~] `server.py` module split — at 1,536 lines, server.py still mixes tool definitions, MCP protocol handling, pipeline execution, file management, and session state. Split tool handlers into a separate module. Design reflection flags this as a growing concern as more tools are added. Not yet started.
- [x] `load("file.vts")` convenience — auto-detect reader from file extension. Reduces errors at the "set pipeline" step by hiding VTK class names from the common case. Implemented as MCP tool in `server.py` with 27 tests in `tests/test_load_convenience.py`. Supports .vts, .vti, .vtp, .vtu, .vtk, .pvd, .nrrd, .nhdr. Returns describe_data() output immediately after loading.
- [x] `compute_vorticity` should return vector + magnitude — `vector=True` parameter returns the full 3-component vorticity vector; `vector=False` (default) returns magnitude only. Addressed together with the `extract_component` item above.
- [x] `describe_data` working without active pipeline — `describe_data(file_path="file.vts")` reads the file directly without a prior `quick_start()` or `set_pipeline()`. Session 2 documented this as a friction point. Implemented with `load_file()` in `filters.py` and tests in `tests/test_describe_data_no_pipeline.py`.

## Low Priority / Ideas

- [ ] Scene annotations — `annotate(position, label)` for labeling features on the rendered image. Session 1 identified this as a top-two gap for communicating scientific context.
- [ ] 2D chart rendering — render histogram or line-plot images from field data or probe results. Complements line probes by making the output visual rather than tabular.
- [ ] Multi-panel layouts — side-by-side views showing different fields on the same geometry. Session 1 identified this as useful for comparison (theta, O2, w, fuel on one cross-section).
- [ ] Camera orbit / turntable — return multiple frames from different angles. Helps human readers grasp 3D structure.
- [ ] Move server working files out of hidden directories — history, screenshots, logs should be in visible directories directly in the session folder, not under `.vislang/`.
- [ ] Context-aware `get_examples` — filter examples by loaded data type and substitute real field names and ranges from the active pipeline.
- [ ] Snake_case property aliases — accept `contour_by` alongside `ContourBy`, `threshold_range` alongside `ThresholdRange`. Polish; lower priority than capability gaps.
- [ ] Color bar positioning — control placement and size of scalar bars to prevent overlap.
- [ ] Diverging colormap with dark midpoint — `cool_to_warm` white midpoint is invisible on light backgrounds.
- [ ] Lighting control — `light(direction, intensity, color)` for surface detail.
- [ ] In-plane vector glyphs on slices — `show_vectors()` for flow visualization on cross-sections.
- [ ] Multi-timestep support — discover sibling timesteps, animate, compare.

## Dataset Sources

- [Open SciVis Datasets](https://klacansky.com/open-scivis-datasets/) — curated collection of volumetric datasets (CT scans, simulations) in raw format. Good source for structurally diverse test data. Includes bonsai, hydrogen atom, nucleon, skull, foot, and ~30 others ranging from 67KB to multi-GB.
