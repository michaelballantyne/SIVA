# VisLang Backlog

## High Priority

- [x] Auto-screenshot from state-changing tools — `set_pipeline`, `set_camera`, `set_colormap`, `toggle_visibility` etc. now return a screenshot automatically via `_with_screenshot()`. Covers: set_pipeline, reset_pipeline, set_camera, set_opacity, set_colormap, set_color_range, toggle_visibility, set_window_size, set_background. Query tools are unaffected.
- [ ] Rich `describe_data` with percentiles — include p1/p25/p50/p75/p99 for each field, distribution shape flag (uniform/skewed/bimodal/sparse), terrain-following detection, and coordinate-to-index mapping. Eliminates 3-6 follow-up `get_field_summary` calls per session. Sessions-1-and-2 identified this as the single biggest exploration bottleneck.
- [x] Fix silent calculator failures — `61d803f` added post-update validation for vtkArrayCalculator: when the named result array is absent after `Update()`, the pipeline now reports a warning rather than silently succeeding. Audit other field-name paths (show(), threshold, contour) for same class of silent failure to ensure coverage is complete.
- [ ] Add a second dataset (bonsai CT scan) — add the Bonsai dataset (256³ uint8, ~16MB, from klacansky.com/open-scivis-datasets/) as `datasets/bonsai/`. Regular grid (vtkImageData), isotropic spacing, single scalar — structurally different from the wildfire curvilinear grid. Will reveal which parts of the system are wildfire-specific.
- [ ] Vector component coloring — add a `component` parameter to `show()` for coloring by a single component of a vector field (`SetArrayComponent()` on the mapper). This is the highest-impact vector fix: sessions-2 called it "the single biggest obstacle" and Option C (component on show()) the most impactful approach. VTK already supports it.

## Medium Priority

- [ ] `extract_component` helper and `compute_vorticity(vector=True)` — add `extract_component(input, field, component, result)` using numpy/VTK array ops (bypassing the unreliable vtkArrayCalculator). Update `compute_vorticity` to return a 3-vector when `vector=True`. Unblocks component-level analysis without calculator fragility.
- [ ] `sample_line` / line probe — extract a 1D profile between two points and return field values along it. Most-requested capability gap across both feedback sessions (Session 1: "temperature vs. height through the plume center"). Bridges "I can see it" to "I can quantify it."
- [ ] Conditional / subregion statistics — `query_stats(node, field, condition)` for queries like "mean updraft velocity where theta > 400K" or "volume where O2 < 0.20." Avoids building a full pipeline for statistical questions. Identified in Session 1.
- [ ] Error path tests and pytest migration — add tests for failure modes identified in feedback: invalid calculator functions, missing field names, out-of-range threshold/contour values, empty-output diagnostics. These tests require no data download. Migrate the hand-rolled test decorator to pytest to get parameterization, fixtures, and CI integration. Process reflection flags this as concrete and immediately valuable.
- [ ] Batch point probing — `sample_points(node, points=[(x,y,z),...], fields=["a","b"])` to replace N individual `sample_point` calls. Reduces round trips for quantitative analysis.
- [ ] Coordinate-based `extract_region` — accept physical bounds (not grid indices) for `vtkExtractGrid`. Session 2 documented two wasted iterations because index guessing is opaque. Can coexist with current `VOI` parameter.
- [ ] `server.py` module split — at 1,277 lines, server.py mixes tool definitions, MCP protocol handling, pipeline execution, file management, and session state. Split tool handlers into a separate module. Design reflection flags this as a growing concern as more tools are added.
- [ ] `load("file.vts")` convenience — auto-detect reader from file extension. Reduces errors at the "set pipeline" step by hiding VTK class names from the common case.
- [ ] `compute_vorticity` should return vector + magnitude — currently returns only magnitude, making component analysis impossible. (Addressed together with `extract_component` item above; listed separately for tracking.)
- [ ] `describe_data` working without active pipeline — `describe_data("file.vts")` should work directly without a prior `quick_start()`. Session 2 documented this as a friction point.

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
