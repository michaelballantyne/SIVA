# VisLang Backlog

## High Priority

- [ ] Auto-screenshot from state-changing tools — `set_pipeline`, `set_camera`, `set_colormap`, `toggle_visibility` etc. should all return a screenshot. Halves round trips during iterative work.
- [ ] Rich `describe_data` with percentiles — include p1/p25/p50/p75/p99 for each field, distribution shape flag, terrain-following detection. Eliminates N follow-up `get_field_summary` calls.
- [ ] Fix silent calculator failures — when `vtkArrayCalculator` fails to produce the named result array, report it loudly instead of silently succeeding.
- [ ] Vector component coloring — `show(..., color_by="field", component=0)`. VTK's mapper supports `SetArrayComponent()`; expose it. Critical for any vector field analysis.
- [ ] `load("file.vts")` convenience — auto-detect reader from file extension. Hide VTK class names from the common case.

## Medium Priority

- [ ] Batch point probing — `sample_points(node, points=[(x,y,z),...], fields=["a","b"])` to replace N individual `sample_point` calls.
- [ ] `sample_line` / line probe — extract a 1D profile between two points. Essential for quantitative analysis.
- [ ] `slab(data, k=0)` convenience — extract a grid layer by index without knowing grid dimensions. Hides VOI boilerplate.
- [ ] Coordinate-based `extract_region` — accept physical bounds instead of grid indices for `vtkExtractGrid`.
- [ ] `compute_vorticity` should return vector + magnitude — currently returns only magnitude, making component analysis impossible.
- [ ] `extract_component` helper — extract a single component from a vector field as a scalar.
- [ ] Snake_case property aliases — accept `contour_by` alongside `ContourBy`, `threshold_range` alongside `ThresholdRange`.
- [ ] Context-aware `get_examples` — filter examples by loaded data type and substitute real field names/ranges.

- [ ] Move server working files out of `.vislang/` — history, screenshots, and logs should be in visible directories (e.g. `history/`, `screenshot.png`, `server.log` directly in the session folder). No hidden directories.

## Low Priority / Ideas

- [ ] Scene annotations — `annotate(position, label)` for labeling features on the rendered image.
- [ ] Multi-panel layouts — side-by-side views showing different fields on the same geometry.
- [ ] Camera orbit / turntable — return multiple frames from different angles.
- [ ] `describe_data` working without active pipeline — load the file directly if given a filename.
- [ ] Color bar positioning — control placement and size of scalar bars to prevent overlap.
- [ ] Diverging colormap with dark midpoint — `cool_to_warm` white midpoint is invisible on light backgrounds.
- [ ] Lighting control — `light(direction, intensity, color)` for surface detail.
- [ ] Session-based workspace — each visualization exploration gets its own folder with history and descriptions.
- [ ] In-plane vector glyphs on slices — `show_vectors()` for flow visualization on cross-sections.
- [ ] Multi-timestep support — discover sibling timesteps, animate, compare.
