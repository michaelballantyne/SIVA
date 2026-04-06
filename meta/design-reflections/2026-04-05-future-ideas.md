# Design Ideas: April 5, 2026

Ideas from the original DESIGN.md that are worth remembering but aren't
part of the current vision or backlog. These emerged during the initial
design phase and are preserved here for future reference.

## Enriched build reports (dot-driven feedback)

After each pipeline build, the report could include not just what happened
but what the LLM could do next — available arrays on each node, valid
downstream filters, valid display options. This collapses the act/query
cycle: instead of build → query → think → build, the LLM gets results and
next options in one response. Inspired by The Gamma's dot-driven
development.

Example format:
```
fire: CREATED (vtkContourFilter)
  Output: 45,231 points, 44,892 cells
  Bounds: x[52.3, 88.1] y[-15.2, 14.8] z[168.4, 195.2]
  Output arrays: theta[400.0], O2[0.17-0.20], rhof_1[0-0.18], ...
  Valid downstream: smooth, clip, glyph, calculator, ...
  Show options: Surface, Wireframe, Points | color_by: theta, O2, ...
```

## Filter and display discovery tools

`get_valid_filters(node)` — given a pipeline node, return which VTK filters
can accept its output, grouped by category (extraction, contouring,
derivatives, streamlines, glyphs). Uses the node's output data type to
filter the catalog.

`get_show_options(node)` — given a pipeline node, return which visual
representations are valid and which arrays are available for coloring.
A node producing polydata wouldn't list Volume; a node with no vector
arrays wouldn't suggest streamlines downstream.

## Cost estimation

`estimate_cost(vtk_class, node, **params)` — estimate computational cost
before running a filter. Uses input size and per-filter complexity models
(O(n) for contours, O(seeds * steps) for streamlines). Even rough
order-of-magnitude estimates prevent freezes on large data. Could also
report incrementally: "v004 — estimated 2s, actual 1.8s."

## ParaView XML metadata

Use ParaView's server manager XML definitions to auto-resolve filter
properties, validate array selections, and support any VTK filter known
to ParaView without custom wrapper code. The XML files define every
property's type, mapping to VTK methods, and valid domains. This would
make the DSL truly generic over VTK rather than requiring a wrapper per
filter type.

## Reference image comparison

Load a target image (a figure from a paper, a colleague's visualization)
and display it alongside the current render. A `set_reference_image(path)`
tool that returns the reference with every subsequent screenshot. Enables
"make it look like Figure 4" workflows. Could also compute similarity
metrics (color histogram distance, structural similarity). Directly
relevant — the bonsai session user was trying to reproduce a paper figure.

## Occlusion awareness

"From this camera angle, is the fire isosurface hidden behind the terrain?"
A depth-buffer analysis after rendering could report: "actor 'fire' is 80%
occluded by 'terrain' from current view." Helps the LLM decide between
adjusting camera, changing opacity, or clipping away obscuring geometry.

## Color palette previews

Preview what a colormap looks like applied to actual data values before
committing to a pipeline change. "Show me viridis vs coolwarm vs inferno
on the theta range" without modifying the pipeline three times.

## Version annotations and decision log

Extend version history with LLM-generated annotations: why each change was
made, what was tried, what worked. Optional `note` parameter on
set_pipeline. Over a long session this builds a decision log useful for
explaining the visualization to others or resuming later.

## Animated parameter sweeps

Generate a sequence of renders varying one parameter (e.g., isosurface
value from 300 to 600 in 10 steps) and return them as a filmstrip. Useful
for finding the right parameter value visually rather than guessing.

## Pipeline suggestions

`suggest_visualizations(node)` — after data is loaded, suggest meaningful
visualization approaches combining filter validity with domain heuristics.
Not just "which filters are valid" but "which combinations are useful for
this kind of data." Could be partly LLM-driven (system provides metadata,
LLM has domain knowledge) or partly template-driven for common data
patterns.
