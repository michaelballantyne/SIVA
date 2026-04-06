# Interactive Wildfire QA Session — 2026-04-06

Manual QA of the `claude/independent-backlog-work-X2iWZ` branch changes
using the wildfire dataset in interactive mode. Human + Claude pair session.

## What was tested

| Change | Result |
|--------|--------|
| Field validation (typo in ThresholdBy) | Clear error with available field names |
| DSL alias removal (`isosurface`) | Clean NameError with hint to use get_dsl_overview |
| Title overlay clearing on pipeline rebuild | Title appears, disappears on rebuild without title() |
| `get_ground_z` `layers=False` parameter | Returns just "Ground z = X.X" |
| `camera_orbit` (vestigial `node` param removed) | Works, 4-frame orbit smooth |
| 2D overlay separation (scalar bars + annotations + titles) | All coexist correctly |
| VTK whitelist expansion (`vtkStructuredGridGeometryFilter`, `vtkShrinkFilter`) | Both work |
| VTK 9.5 deprecation warnings (`AddActor2D`/`RemoveActor2D`) | No warnings in server.log |
| Legacy global state removal | Everything works (implicit) |
| Auto-populate DSL namespace from PipelineBuilder | Everything works (implicit) |
| Window-closed detection in `list_views` | **Broken** — see below |

## Bug found: window-closed detection doesn't work

In interactive mode, closing OS windows (including all of them) is not
detected by `list_views`. `is_window_closed()` never returns True.
`focus()` and `screenshot()` silently render into dead window buffers,
so the agent believes everything is fine while the user sees nothing.

The `GetMapped()` approach documented in the code may not work as assumed
on macOS/Cocoa — needs investigation. Added to backlog.

## Observations worth acting on

**`color_by` a vector field is a silent gotcha.** `color_by="velocity"`
on a 3-component vector colors by component 0 with an unhelpful auto
range. I had to discover I needed `compute_magnitude` to get speed
coloring. Either auto-detect vectors and compute magnitude, or warn
that the field is a vector and explain how component coloring works.
This cost a full round-trip during the session.

**Field validation errors use internal node names.** The error said
`node_2: ERROR - Field 'temprature' not found...` instead of reflecting
the variable name from the pipeline code. Minor polish but would make
errors easier to map back to the pipeline.

**Per-view data isolation is real friction.** Creating a new view
requires writing a full pipeline with `source()` before any query
tools work. `get_statistics` fails with "No pipeline is active."
This reinforces the `start_session` / session-level data idea already
on the backlog — it was the most annoying friction point during
multi-view exploration.

## What worked well

- The overall workflow (load, query stats, write pipeline, iterate) is
  smooth and fast. Pipeline builds are sub-second for most operations.
- `suggest_isosurface` and field statistics made it easy to pick good
  values on the first try — no blind guessing.
- Volume rendering with custom opacity functions produced genuinely
  impressive results. The fire volume rendering on black background
  looked like a real photograph.
- Streamlines with `seeds_near` + `compute_magnitude` coloring
  revealed clear physical structure (laminar flow disrupted by
  convective updraft).
- Multi-view worked well for exploring different aspects of the data
  (temperature, oxygen, cross-section, volume) simultaneously.
- Annotations (billboard text) stayed readable from all camera angles.
- The expanded VTK whitelist enabled using `vtkStructuredGridGeometryFilter`
  for efficient index-based extraction without going through the DSL's
  `extract_grid` wrapper.
