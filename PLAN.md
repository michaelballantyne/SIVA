# VisLang Phase 1: Headless MCP Server + DSL

## Context

VisLang is a system for interactively building scientific visualizations
through conversation with an LLM. The full design is in `DESIGN.md`. This
plan covers Phase 1: a minimal but genuinely useful system where the LLM
writes declarative pipeline specs, a headless VTK renderer executes them,
and rich feedback (screenshots, data queries) guides iteration.

The target demo: interactively build a wildfire visualization (terrain +
fire isosurface + wind streamlines) on the `output.30000.vts` dataset
through conversation.

This will be built in a cloud environment (headless, no display). The
1GB data file won't be available there, so we include a synthetic test
data generator.

## File Structure

```
vislang/
├── __init__.py
├── server.py          # MCP server (FastMCP) with all tool definitions
├── dsl.py             # DSL builder functions + interpreter
├── renderer.py        # Headless VTK renderer
├── queries.py         # Query tool implementations
├── filters.py         # VTK filter creation + special-case handling
└── test_data.py       # Synthetic .vts generator for testing
CLAUDE.md              # DSL reference for the LLM (project-level)
.mcp.json              # MCP server configuration for Claude Code
requirements.txt       # vtk, mcp
```

## Implementation Steps

### Step 1: Project skeleton + synthetic test data

Create `requirements.txt`:
```
vtk
mcp
```

Create `vislang/test_data.py` — generates a small synthetic .vts file
(~50x40x20 structured grid) with fields mimicking the wildfire data:
- Terrain-like z-coordinates (a ridge shape)
- `theta` field with a hot spot (simulating fire)
- `u`, `v`, `w` velocity fields (simple flow pattern)
- `rhof_1` fuel density (depleted near the hot spot)

This lets us develop and test without the 1GB real dataset.

### Step 2: Headless VTK renderer (`vislang/renderer.py`)

A class `Renderer` that manages:
- A `vtkRenderWindow` with `SetOffScreenRendering(True)`
- A `vtkRenderer` with configurable background
- Methods: `clear()`, `add_actor(name, actor)`, `remove_actor(name)`,
  `set_camera(position, focal_point, up, zoom)`, `render()`,
  `screenshot(path) -> str`
- Actor tracking by name (dict of name → actor)

No interactor, no event loop. Purely synchronous — render on demand.

### Step 3: Filter creation (`vislang/filters.py`)

A function `create_vtk_filter(vtk_class, input_algorithm, **properties)`
that:
1. Validates `vtk_class` against a whitelist
2. Instantiates the VTK object
3. Connects the input via `SetInputConnection`
4. Applies properties, with special-case handling for:
   - `Isosurfaces=[400.0]` → `SetValue(0, 400.0)`, etc.
   - `ContourBy="theta"` → `SetInputArrayToProcess(0,0,0,0,"theta")`
   - `Vectors="velocity"` → `SetInputArrayToProcess(0,0,0,0,"velocity")` 
   - `ThresholdRange=[350, 800]` → `SetLowerThreshold` + `SetUpperThreshold`
   - `AddScalarArrayName=["u","v","w"]` → calls `AddScalarArrayName` per item
   - `AddVectorArrayName=["Vorticity"]` → calls `AddVectorArrayName` per item
   - `VOI=[0,599,0,499,0,0]` → `SetVOI(*list)`
   - `SampleRate=[10,10,2]` → `SetSampleRate(*list)`
   - Default: `Set{PropertyName}(value)` convention
5. Calls `Update()` on the filter
6. Returns the filter (vtkAlgorithm) and a status report (output cell/point
   count, bounds, warnings)

Whitelisted VTK classes:
- Sources: `vtkXMLStructuredGridReader`
- Filters: `vtkContourFilter`, `vtkArrayCalculator`, `vtkExtractGrid`,
  `vtkThreshold`, `vtkStreamTracer`, `vtkTubeFilter`, `vtkCellDataToPointData`,
  `vtkCellDerivatives`, `vtkGlyph3D`, `vtkWindowedSincPolyDataFilter`

A function `create_show(vtk_algorithm, **display_props)` that:
1. Creates a `vtkDataSetMapper` connected to the filter output
2. Handles `color_by` → `SelectColorArray` + `SetScalarModeToUsePointFieldData`
3. Handles `scalar_range` → `mapper.SetScalarRange`
4. Handles `color` (solid) → `ScalarVisibilityOff` + `actor.GetProperty().SetColor`
5. Handles `opacity`, `specular`, `specular_power`
6. Handles `representation` → `Surface`/`Wireframe`/`Points`
7. Returns a `vtkActor`

### Step 4: DSL interpreter (`vislang/dsl.py`)

The DSL interpreter:
1. Takes a code string
2. Executes it via `exec()` in a restricted namespace containing only
   the builder functions
3. Builder functions register nodes on a `PipelineBuilder` context object
4. After execution, iterates the namespace to find variable bindings to
   node handles (this gives us names)
5. Returns the built pipeline description

Builder functions:
- `source(vtk_class, **props)` → returns a `NodeRef`
- `filter(vtk_class, input=..., **props)` → returns a `NodeRef`
- Convenience wrappers: `contour(input=, **props)`,
  `calculator(input=, **props)`, `threshold(input=, **props)`,
  `extract_grid(input=, **props)`, `stream_tracer(input=, **props)`,
  `tube(input=, **props)`, `glyph(input=, **props)`
- `show(node, **display_props)` → registers display directive
- `camera(position=, focal_point=, up=, zoom=)` → registers camera
- `background(r, g, b)` → registers background color

The `PipelineBuilder` class:
- Stores list of node declarations and show directives
- On `build(renderer)`: walks the node graph in dependency order,
  creates VTK objects via `filters.py`, creates actors via `create_show`,
  adds actors to renderer, sets camera, renders

Phase 1 is tear-down/rebuild: each `set_pipeline` call clears the renderer
and rebuilds everything. VTK's reader caching means re-reading the same
file is fast after the first time.

### Step 5: Query tools (`vislang/queries.py`)

Functions that take VTK data objects and return structured text:

- `get_array_info(data)` → lists arrays with component counts, types,
  and value ranges (min/max for each)
- `get_bounds(data)` → `[xmin, xmax, ymin, ymax, zmin, zmax]`
- `get_statistics(data, field)` → min, max, mean, std
- `get_histogram(data, field, bins=20)` → text histogram with ASCII bars
- `get_spatial_extent(data, field, min_val, max_val)` → bounding box of
  where field is within the given range

These operate on VTK data objects (output of any filter), so they can
query any node in the pipeline by name.

### Step 6: MCP server (`vislang/server.py`)

FastMCP server with tools:

**`set_pipeline(code: str) -> str`**
- Calls DSL interpreter to parse the code
- Clears renderer, rebuilds pipeline
- Renders
- Saves spec + screenshot to `.vislang/history/vNNN/`
- Returns reconciliation report: version number, per-node status
  (output cell/point counts, bounds, available arrays on each output),
  any errors/warnings

**`screenshot() -> Image`**
- Renders current scene
- Returns the image + camera state as text metadata

**`get_array_info(node: str = "") -> str`**
- If node given, reports arrays on that node's output
- Otherwise reports root data source

**`get_bounds(node: str = "") -> str`**

**`get_statistics(node: str, field: str) -> str`**

**`get_histogram(node: str, field: str, bins: int = 20) -> str`**

**`get_spatial_extent(node: str, field: str, min_value: float, max_value: float) -> str`**

**`restore_version(version: int) -> str`**
- Reads saved spec from `.vislang/history/vNNN/pipeline.py`
- Runs it through `set_pipeline`

**`get_pipeline() -> str`**
- Returns the current DSL spec text

The server keeps references to the renderer and the current pipeline state
(built VTK objects by name) so query tools can access any node's output.

### Step 7: MCP configuration + CLAUDE.md

`.mcp.json`:
```json
{
  "mcpServers": {
    "VisLang": {
      "command": "python",
      "args": ["-m", "vislang.server"],
      "cwd": "<project_dir>"
    }
  }
}
```

The `vislang/server.py` should be runnable as `python -m vislang.server`.
It needs `if __name__ == "__main__"` or module-level startup that creates
the FastMCP instance and runs it.

`CLAUDE.md` (project-level): DSL reference including:
- Available builder functions with signatures and examples
- Supported VTK classes with their special properties
- How `show()` properties work
- Available query tools and when to use them
- A complete example spec (the wildfire visualization)
- Instruction to always query data characteristics before guessing
  parameters

### Step 8: Integration testing

1. Run `python -m vislang.test_data` to generate `test_data.vts`
2. Start the MCP server, call `set_pipeline` with a simple spec loading
   the test data and creating a contour
3. Verify screenshot returns an image
4. Verify query tools return correct array info, statistics, etc.
5. Test a multi-filter pipeline (data → calculator → stream_tracer)
6. Test error cases (bad array name, bad filter class, empty output)

## Key Design Decisions

- **exec() in restricted namespace for Phase 1** — no Starlark yet.
  The namespace contains only builder functions, no builtins except
  `range`, `zip`, `enumerate`, `len`, `min`, `max`, `True`, `False`,
  `None`. Safe enough for development.
- **Tear-down/rebuild** — no reconciliation. Each `set_pipeline` clears
  and rebuilds. Simple, correct, sufficient for demo.
- **Variable names as node identity** — after exec(), iterate the
  namespace to find which variables are bound to NodeRef objects. Use
  the variable name as the node's name for queries and reports.
- **Headless only** — `SetOffScreenRendering(True)`. No interactor.
  Adding the native window later is a small change to `renderer.py`.
- **Version history** — save to `.vislang/history/vNNN/` on each
  `set_pipeline` call. Simple incrementing counter.

## Verification

After building, test the full loop:

1. Generate test data: `python -m vislang.test_data`
2. Configure `.mcp.json` to point to the server
3. Start a Claude Code session in the project directory
4. Ask: "Load the test data and show me what arrays are available"
   → LLM should call `get_array_info`
5. Ask: "Show me the terrain colored by fuel density"
   → LLM should call `set_pipeline` with extract_grid + show
6. Ask: "Add a fire isosurface"
   → LLM should call `get_statistics` for theta range, then update spec
7. Ask: "Add wind streamlines near the fire"
   → LLM should call `get_spatial_extent` for theta>400, then update spec
8. Verify screenshots show progressive visualization buildup
9. Test `restore_version` to go back to an earlier state

For the real demo (local, with `output.30000.vts`): same workflow but
on real data, producing the wildfire visualization from DESIGN.md.
