# Bonsai CT Scan Session Feedback

Session: ff60f74a (2026-04-04), human + Claude Opus, VisLang MCP interactive mode.
Dataset: bonsai 256³ uint8 volume (ImageFile scalar field).
Goal: volume rendering and isosurface exploration of a bonsai CT scan.

## What worked well

- `load()` -> `describe_data()` flow worked smoothly for initial orientation.
- `suggest_opacity()` and `get_statistics()` gave useful data when Claude
  remembered to call them proactively. Histogram-guided opacity functions
  directly informed good pipeline parameters.
- Pipeline build result messages ("Pipeline v24 built successfully. Nodes:
  data: vtkNrrdReader -> 16777216 pts...") were informative and appropriately
  concise.
- Incremental pipeline refinement worked once the initial bugs were resolved.

## Server bugs encountered

### Return type validation error (~55 wasted tool calls)

`set_pipeline` was annotated `-> str` but `_with_screenshot()` returns
`[str, Image]`, causing a Pydantic validation error:
`"Input should be a valid string [type=string_type]"`.

Claude spent ~55 tool calls debugging this — spawning an Explore agent,
reading MCP SDK internals, tracing through `_convert_to_content` and
`FuncMetadata` — instead of just fixing the return type annotation. The
user interrupted and guided the fix. `restore_version` hit the same bug
later, indicating the fix wasn't applied consistently.

This is moot now that auto-screenshots are being removed, but it highlights
that tool return types should be tested.

### MCP connection crash

The server crashed or disconnected mid-session. After reconnection, Claude
tried to call tools but got "No such tool available" because MCP tools
weren't re-registered. The user had to restart manually. No clear cause
from the log.

## Missing tools and DSL features

### Scalar color bar / legend

The user asked "Can you put a scale on so I can understand what color maps
to what density number?" There's no scalar bar annotation tool. This is a
basic VTK feature that should exist.

### Spatial-region statistics

The user wanted to understand density ranges in specific regions (above
soil vs. below soil) to choose thresholding values. `get_statistics` only
operates on the whole dataset. A variant that accepts a spatial bounding
box or a pipeline node (post-threshold/clip) would have shortened the
density exploration from 20+ rounds to a few.

### Isosurface + volume rendering composite

The user wanted to reproduce a reference showing "leaves visualized by
direct volume rendering whereas the trunk and branches are visualized
using an isosurface rendering." This multi-representation overlay is a
natural CT visualization pattern but there was no clear DSL pattern for it.

### Interactive parameter adjustment

The user wished for "two sliders for the upper and lower density limits."
Volume rendering exploration is inherently iterative. Even without full
widgets, a way to parameterize and live-update values would reduce the
edit-set_pipeline-screenshot cycle.

## Human + agent interaction friction

### Manual edit handoff

The user repeatedly edited `view-main.py` in their text editor then asked
Claude to "set pipeline" or "set pipeline again." Claude complied but
didn't read the file to understand what changed. The user asked "You can't
see the file?" — Claude wasn't proactively reading the pipeline file
before/after setting it, so it couldn't offer feedback on the user's edits.

This is a Claude behavior / server instructions issue. The instructions
should tell Claude: when the user asks to set a pipeline they've manually
edited, read the file first to understand the changes.

### Write + set_pipeline requires two round-trips

The user explicitly asked "Can you send the write tool call and the set
pipeline tool call in a single round?" Every edit required two sequential
round-trips. The server instructions should guide Claude to batch these.
(This is addressed in the screenshot-removal backlog item.)

Alternatively, `set_pipeline` could accept inline code directly rather
than requiring a file write first.

### Spatial reasoning failure

Claude misinterpreted a clip plane result, calling the top of the plant
"roots." The user corrected: "You're opus, you should be smarter than
this! It seems pretty clear to me that what the clip plane was actually
doing was just cutting to show only the *top* of the plant." Better
integration of spatial context (bounds, clip position) in tool results
might help, but this is partly a Claude reasoning issue.

## Tool output issues

### Pydantic errors are opaque

Validation errors like `"1 validation error for set_pipelineOutput\nresult\n
Input should be a valid string"` leak internal implementation details rather
than actionable information. The actual pipeline error was buried inside.
Error responses should surface the pipeline-level message, not the
serialization failure.

### Screenshots accumulate causing 20MB limit

Already identified and addressed as a high-priority backlog item. 49
screenshots accumulated to ~65MB, eventually hitting the API request limit.

## Efficiency observations

- The return type bug consumed the most wasted rounds by far (~55 calls).
- Density exploration / thresholding took 20+ rounds, mostly because there
  was no way to get statistics for a spatial subregion.
- PDF reading failed repeatedly (wrong filename, then request too large),
  wasting several rounds when trying to reference a paper.
- Claude didn't always check `get_statistics` before choosing threshold
  values, leading to trial-and-error that the data could have prevented.
