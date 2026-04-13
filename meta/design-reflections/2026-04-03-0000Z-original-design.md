# VisLang: Declarative VTK Visualization with LLM-Driven Pipeline Control

## Vision

VisLang is a system for interactively building scientific visualizations through
conversation with an LLM. The user describes what they want to see; the LLM
writes a declarative pipeline specification; the system reconciles the running
VTK state to match; and the user sees the result instantly in an interactive 3D
viewer.

The key properties:

- **Declarative** -- the pipeline spec describes desired state, not imperative
  steps. The reconciler diffs and applies minimal changes.
- **Generic over VTK** -- filters are referenced by VTK class name. Property
  metadata from ParaView's XML definitions drives validation, error reporting,
  and parameter mapping. No per-filter implementation code is needed beyond what
  ParaView already provides.
- **Interactive** -- the user can rotate, zoom, and inspect the visualization at
  any time. The LLM can push pipeline changes that take effect immediately.
- **Queryable** -- the LLM can ask for screenshots, array ranges, spatial
  bounds, and point samples to make informed decisions about parameters.

## Architecture

```
 Claude Code (LLM)
     │
     │  MCP protocol
     ▼
 ┌──────────────────────────────┐
 │  MCP Server (Python)         │
 │                              │
 │  Tools:                      │
 │   set_pipeline(dsl_code)     │──► DSL interpreter
 │   screenshot()               │        │
 │   get_array_info(node?)      │        ▼
 │   get_bounds(node?)          │    Reconciler
 │   sample_point(x,y,z,fields) │        │
 │   get_statistics(node,field) │        │  creates / updates / removes
 │                              │        │  VTK objects
 └──────────────────────────────┘        │
                                         ▼
                                 ┌──────────────┐
                                 │  VTK Renderer │
                                 │  + Interactor │
                                 │  (native wnd) │
                                 └──────────────┘
                                    ▲
                                    │ mouse / keyboard
                                    User
```

The MCP server and VTK viewer live in the **same process**. The VTK interactor
owns the main loop. The MCP server runs in a background thread and posts work to
the main thread via a queue drained by a VTK timer callback (~100ms). This keeps
mouse interaction smooth while allowing the LLM to push changes.

Future option: swap the native VTK window for a Trame web-based viewer. This is
a viewer-layer change only; the DSL, reconciler, and MCP tools are unaffected.
Trame would additionally enable the user to tweak parameters via UI sliders
alongside the LLM.

## The DSL

A pipeline spec is a Python script that calls builder functions. It is **not**
imperative VTK code -- the builder functions register nodes in a desired-state
graph rather than creating VTK objects directly.

### Core functions

```python
source(vtk_class, name, **properties) -> NodeRef
filter(vtk_class, name, input=..., **properties) -> NodeRef
show(node, name, **display_properties)
camera(position=..., focal_point=..., up=..., zoom=...)
background(r, g, b)
```

`source` and `filter` declare pipeline nodes. `show` declares that a node's
output should be visible in the scene with given display properties. Nodes that
are not `show`n exist in the pipeline (and can be queried) but produce no actor.

### Property mapping

Filter properties use ParaView-style names resolved via ParaView's server
manager XML definitions. The framework maps these to VTK method calls:

| DSL property | VTK method call | Resolved via |
|---|---|---|
| `Isosurfaces=[450.0]` | `SetValue(0, 450.0)` | XML: repeatable property |
| `ContourBy="theta"` | `SetInputArrayToProcess(0,0,0,0,"theta")` | XML: InputArray domain |
| `BoundarySmoothing=False` | `SetBoundarySmoothing(0)` | XML: boolean property |
| `VectorMode="Vorticity"` | `SetVectorModeToComputeVorticity()` | XML: enum property |
| `Function="u*iHat+..."` | `SetFunction("u*iHat+...")` | XML: string property |

For early development (before the XML metadata system is built), properties fall
back to direct `Set{Name}(value)` calls on the VTK object, which covers the
majority of simple cases.

### Display properties

`show()` handles the mapper/actor/property boilerplate that is always the same
pattern regardless of filter type:

```python
show(node, "terrain",
    color_by="rhof_1",           # SetScalarModeToUsePointFieldData + SelectColorArray
    scalar_range=(0.0, 0.6),     # mapper.SetScalarRange
    lut=dict(                    # vtkLookupTable configuration
        hue_range=(0.1, 0.33),
        saturation_range=(0.4, 0.8),
        value_range=(0.1, 0.4)),
    opacity=1.0,                 # actor.GetProperty().SetOpacity
    color=(1, 0, 0),             # solid color (when color_by is absent)
    specular=0.3,                # actor.GetProperty().SetSpecular
    representation="Surface")    # "Surface", "Wireframe", "Points", "Volume"
```

### Example: wildfire visualization

This example reproduces the pipeline saved in `vis-state.pvsm` -- a volume
rendering of fire, streamlines through the wind field, and vector glyphs:

```python
# --- Data source ---
data = source("vtkXMLStructuredGridReader", "data",
    FileName="output.30000.vts")

# --- Derived fields ---
velocity = filter("vtkArrayCalculator", "velocity", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat",
    ResultArrayName="velocity")

# --- Fire volume (threshold theta, then volume render) ---
fire_region = filter("vtkThreshold", "fire_region", input=data,
    ThresholdRange=[350.0, 1200.0],
    ThresholdBy="theta")

show(fire_region, "fire_volume",
    representation="Volume",
    color_by="theta",
    scalar_range=(350.0, 1200.0))

# --- Sub-region for glyphs ---
sub_velocity = filter("vtkExtractGrid", "sub_velocity", input=velocity,
    VOI=[251, 850, 0, 499, 0, 20],
    SampleRate=[1, 1, 1])

log_vel = filter("vtkArrayCalculator", "log_vel", input=sub_velocity,
    Function="log10(mag(velocity) + 1)",
    ResultArrayName="logvelocitymagnitude")

glyphs = filter("vtkGlyph3D", "glyphs", input=log_vel,
    GlyphSource=source("vtkArrowSource", "arrow",
        TipResolution=8, ShaftResolution=8),
    ScaleArray="logvelocitymagnitude",
    OrientationArray="velocity",
    ScaleFactor=14.38,
    MaximumNumberOfSamplePoints=20000,
    GlyphMode="UniformSpatialDistribution")

show(glyphs, "wind_glyphs",
    color_by="u",
    opacity=0.8)

# --- Streamlines ---
streamlines = filter("vtkStreamTracer", "streamlines", input=velocity,
    Vectors="velocity",
    IntegrationDirection="Both",
    IntegratorType="RungeKutta45",
    MaximumNumberOfSteps=2000,
    MaximumPropagation=1198,
    InitialIntegrationStep=0.2)

show(streamlines, "streamlines",
    color_by="velocity",
    representation="Surface")

# --- Scene ---
camera(
    position=(365.4, 913.1, 943.7),
    focal_point=(199.0, -296.6, -123.3),
    up=(0.045, -0.664, 0.746))
background(0.15, 0.15, 0.2)
```

### Example: iterative exploration session

This shows the back-and-forth of a typical session. The LLM starts simple
and refines based on queries.

**Turn 1 -- LLM loads data and checks what's available:**

Tool call: `get_array_info()`
```
Response:
  Arrays: u(1), v(1), w(1), theta(1), O2(1),
          rhowatervapor(1), rhof_1(1), convht_1(1), frhosiesrad_1(1)
  Bounds: x[0, 202] y[-250, 248] z[1.5, 899.4]
  Cells: 17,940,000
```

**Turn 2 -- LLM writes initial pipeline:**

Tool call: `set_pipeline(code)`
```python
data = source("vtkXMLStructuredGridReader", "data",
    FileName="output.30000.vts")

terrain = filter("vtkExtractGrid", "terrain", input=data,
    VOI=[0, 599, 0, 499, 0, 0])

show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6))

camera(position=(400, -300, 400), focal_point=(100, 0, 100), up=(0, 0, 1))
```

```
Response:
  Reconciled: +data, +terrain, +terrain[show]
  terrain output: 299,000 points, 298,501 cells
  Rendered in 0.8s
```

**Turn 3 -- LLM checks a value range before adding fire:**

Tool call: `get_statistics("data", "theta")`
```
Response:
  theta: min=298.1, max=812.7, mean=300.4
```

**Turn 4 -- LLM adds fire isosurface:**

Tool call: `set_pipeline(code)`
```python
data = source("vtkXMLStructuredGridReader", "data",
    FileName="output.30000.vts")

terrain = filter("vtkExtractGrid", "terrain", input=data,
    VOI=[0, 599, 0, 499, 0, 0])

show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6))

fire = filter("vtkContourFilter", "fire", input=data,
    ContourBy="theta",
    Isosurfaces=[400.0])

show(fire, "fire", color=(1.0, 0.3, 0.05), opacity=0.5)

camera(position=(400, -300, 400), focal_point=(100, 0, 100), up=(0, 0, 1))
```

```
Response:
  Reconciled: data (unchanged), terrain (unchanged),
              +fire, +fire[show]
  fire output: 45,231 points, 44,892 cells
  Rendered in 0.3s
```

The reconciler only created the new fire contour and its actor. The data reader,
terrain extraction, and terrain actor were reused from the previous state.

## The Reconciler

The reconciler maintains two parallel structures:

1. **Desired graph** -- rebuilt each time `set_pipeline` runs the DSL code.
   A dict of `Node(name, vtk_class, params, inputs)` and
   `ShowDirective(node_name, display_name, display_params)`.

2. **Live graph** -- the actual VTK objects currently instantiated.
   A dict of `LiveNode(name, vtk_class, params, inputs, vtk_object)` and
   `LiveShow(display_name, actor, mapper)`.

### Reconciliation algorithm

```
for each node in desired_graph:
    if name not in live_graph:
        CREATE vtk object, set params, connect inputs
    elif vtk_class changed or inputs changed:
        DESTROY old, CREATE new (structural change)
    elif params changed:
        UPDATE params on existing vtk object, call Update()

for each node in live_graph but not in desired_graph:
    DESTROY vtk object, remove actor if shown

for each show directive:
    if display_name not in live_shows:
        CREATE mapper + actor, add to renderer
    elif display_params changed:
        UPDATE mapper/actor properties
    (remove actors for show directives that disappeared)

render()
```

### Timeout and error handling

Every `vtk_object.Update()` call is wrapped with a progress observer:

```python
def safe_update(vtk_filter, timeout=5.0):
    start = time.time()
    def on_progress(obj, event):
        if time.time() - start > timeout:
            obj.SetAbortExecute(True)
    vtk_filter.AddObserver("ProgressEvent", on_progress)
    vtk_filter.Update()
```

After each update, the framework generically checks:
- VTK error/warning observers (attached to every created object)
- Output emptiness (0 cells/points)
- Output bounds and size

The reconciler returns a structured report. Following The Gamma's principle of
dot-driven development, each node's report includes not just what happened but
**what the LLM could do next** — available arrays, valid downstream filters,
and display options. This collapses the act/query cycle: instead of act →
query → think → act, the LLM gets results and next options in a single
response.

```
v003 -- Reconciled in 0.4s (estimated 0.3s):
  data: unchanged
    Arrays: u, v, w, theta[298.1-812.7], O2[0.13-0.21],
            rhof_1[0-0.6], rhowatervapor, convht_1, frhosiesrad_1
  terrain: unchanged
  fire: CREATED (vtkContourFilter)
    VTK warnings: none
    Output: 45,231 points, 44,892 cells
    Bounds: x[52.3, 88.1] y[-15.2, 14.8] z[168.4, 195.2]
    Output arrays: theta[400.0], O2[0.17-0.20], rhof_1[0-0.18], ...
    Valid downstream: smooth, clip, glyph, calculator, ...
    Show options: Surface, Wireframe, Points | color_by: theta, O2, ...
  fire[show]: CREATED (actor added)
```

The standalone query tools (`get_valid_filters`, `get_array_info`, etc.)
remain available for the first turn (before any pipeline exists) and for
targeted exploration. But the enriched reconciliation report means the LLM
rarely needs to make separate query calls during iterative refinement — the
information is right there in the feedback from the action it just took.

### Array name validation

When a property is known (via ParaView XML or the fallback heuristic) to be a
field name reference, the reconciler checks it against the input's available
arrays before passing it to VTK:

```
Error: fire.ContourBy="temperature" -- array not found.
  Available point arrays: u, v, w, theta, O2, rhowatervapor,
                          rhof_1, convht_1, frhosiesrad_1
  Did you mean: theta?
```

## MCP Interface

### Mutation tool

**`set_pipeline(code: str) -> str`**

Executes the DSL code, diffs against current state, reconciles. Returns a
structured report of what changed, output statistics, any errors/warnings, and
a **version number**. Each call auto-saves the pipeline spec and a screenshot
(see Version History below).

### Query tools

**`screenshot() -> Image`**

Returns the current view as an image. The response also includes the current
**camera state** (position, focal point, view up, zoom) so the LLM knows how
the user has oriented their view.

**`get_array_info(node: str = None) -> str`**

Lists available arrays with component counts and value ranges. If `node` is
given, reports arrays on that node's output; otherwise reports the root data
source.

**`get_bounds(node: str = None) -> str`**

Returns spatial bounds `[xmin, xmax, ymin, ymax, zmin, zmax]` for a node's
output or the root source.

**`get_statistics(node: str, field: str) -> str`**

Returns min, max, mean, and standard deviation of a field on a node's output.

**`sample_point(x: float, y: float, z: float, fields: list[str] = None) -> str`**

Probes the root data source at the given coordinates and returns field values.
Useful for understanding local data characteristics before choosing filter
parameters.

**`restore_version(version: int) -> str`**

Restores a previous pipeline spec by version number. Re-runs the saved DSL code
through the reconciler. Returns the same reconciliation report as `set_pipeline`.

**`get_user_marks() -> str`**

Returns any points the user has marked in the viewer (see User Interaction
Capture below), including 3D world coordinates and field values at each mark.
Clears the mark list after reading.

**`get_valid_filters(node: str) -> str`**

Given a pipeline node, returns which VTK filters can accept its output as input.
Uses the node's output data type (e.g. vtkStructuredGrid, vtkPolyData) and
ParaView XML `DataTypeDomain` constraints to filter the catalog. Response is
grouped by category:

```
Node "velocity" outputs vtkStructuredGrid with arrays: u, v, w, velocity(3)

Extraction/subsetting:
  vtkExtractGrid -- extract a VOI or subsample
  vtkThreshold -- extract cells by field value range

Contouring:
  vtkContourFilter -- isosurface of a scalar field

Derivatives:
  vtkCellDerivatives -- compute vorticity, strain, gradient
  vtkGradientFilter -- compute gradient of a scalar/vector field

Streamlines (requires vector field):
  vtkStreamTracer -- trace streamlines through velocity

Glyphs (requires vector or scalar field):
  vtkGlyph3D -- place oriented/scaled glyphs at points
```

**`get_show_options(node: str) -> str`**

Given a pipeline node, returns which visual representations are valid for its
output data type and available arrays. For example:

```
Node "velocity" (vtkStructuredGrid, 18M cells):
  representation: Surface, Wireframe, Points, Outline
  representation: Volume (structured data -- supported)
  color_by: u, v, w (scalar), velocity (vector magnitude or component)
```

A node producing vtkPolyData would not list Volume. A node with no vector arrays
would not suggest streamlines as a downstream filter.

**`estimate_cost(vtk_class: str, node: str, **params) -> str`**

Estimates the computational cost of applying a filter before running it. Uses
the input node's size (cell/point count) and filter-specific complexity model.

```
estimate_cost("vtkStreamTracer", "velocity",
              number_of_seeds=50, max_steps=2000)

Response:
  Input: 18,300,000 cells
  Estimated time: ~3-5s
  Estimated output: ~50,000-200,000 cells
  Recommendation: OK -- moderate cost

estimate_cost("vtkGlyph3D", "velocity",
              max_sample_points=500000)

Response:
  Input: 18,300,000 cells
  Estimated time: ~15-30s
  Estimated output: ~10,000,000 cells (high memory)
  Recommendation: WARN -- consider reducing max_sample_points or
                  subsample input first with vtkExtractGrid
```

## Version History

Every `set_pipeline` call saves the pipeline spec and a screenshot to a
versioned history directory:

```
.vislang/history/
  v001/
    pipeline.py      # the DSL code
    screenshot.png   # render at time of change
  v002/
    pipeline.py
    screenshot.png
  ...
```

The reconciliation report includes the version number:

```
v003 -- Reconciled in 0.4s:
  data: unchanged
  terrain: unchanged
  fire: UPDATED value 400 -> 500
    Output: 23,109 points, 22,844 cells
```

This allows easy rollback ("that looked worse, go back to v002") without the
LLM needing to remember or reconstruct previous states.

## User Interaction Capture

The viewer captures user interactions and makes them available to the LLM,
enabling spatially-aware assistance.

### Camera state

Every `screenshot()` response includes the current camera position, focal point,
view up vector, and zoom level. This tells the LLM what the user is looking at
and from what angle, which is useful for:
- Orienting annotations and labels
- Positioning seed points for streamlines in the visible region
- Setting up `camera()` calls that match the user's current viewpoint

### Point marking

The user can **shift-click** in the viewer to mark points of interest. VTK's
`vtkCellPicker` ray-casts through the scene and records:

```
Mark at (72.3, -5.1, 178.4) on actor "terrain"
  theta=487.2, rhof_1=0.02, u=3.4, v=-1.2, w=0.8
```

Marks accumulate until the LLM reads them via `get_user_marks()`. This enables
workflows like:

> **User:** *shift-clicks on the fire front, shift-clicks on the ridge*
>
> **User:** "Add streamlines seeded between these two points"
>
> **LLM:** *calls `get_user_marks()`, gets the two 3D coordinates, writes
> streamline seed placement accordingly*

The picker reports which actor was hit (by the `show` display name), so the LLM
knows whether the user clicked on the terrain, the fire isosurface, etc. Field
values at the picked point are included so the LLM can understand local data
characteristics without a separate `sample_point` call.

## User Interface

No custom UI code is needed. The interface is three panes:

```
┌─────────────────────────┬──────────────────────┐
│                         │                      │
│   Claude Code terminal  │    VTK render window  │
│                         │                      │
│   (conversation +       │    (rotate, zoom,    │
│    tool responses)      │     shift-click to   │
│                         │     mark points)     │
├─────────────────────────┤                      │
│                         │                      │
│   Text editor           │                      │
│   (pipeline.py)         │                      │
│                         │                      │
└─────────────────────────┴──────────────────────┘
```

- **Claude Code terminal** -- the conversation. The user describes what they
  want; the LLM writes pipeline code and queries data. Reconciliation reports
  and query results appear here.
- **VTK render window** -- the live visualization. The user can freely rotate,
  zoom, and shift-click to mark points of interest. Updates appear instantly
  when the LLM pushes a pipeline change.
- **Text editor open to the pipeline file** -- the user watches the DSL evolve
  as the LLM edits it. Over time they learn the DSL and can make direct edits
  themselves. The pipeline file is the shared artifact -- readable by both the
  human and the LLM, version-controlled, and reproducible.

The pipeline spec doubles as documentation of the visualization. A colleague
can read `pipeline.py` and understand exactly what's being shown and why,
without needing to reverse-engineer ParaView state files or point-and-click
through a GUI.

### The DSL as a communication medium

A key motivation for the DSL (beyond LLM convenience) is **readability for
domain experts who will never write code.** A scientist reviewing a
visualization needs to verify that the data is being shown correctly -- is
that really a contour of theta at 400K, or did the LLM pick the wrong
field? Raw VTK Python is too verbose for this verification:

```python
# Raw VTK: 10 lines to express "isosurface of theta at 400"
contour = vtk.vtkContourFilter()
contour.SetInputConnection(calc.GetOutputPort())
contour.SetInputArrayToProcess(0, 0, 0,
    vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS, "theta")
contour.SetValue(0, 400.0)
mapper = vtk.vtkPolyDataMapper()
mapper.SetInputConnection(contour.GetOutputPort())
mapper.ScalarVisibilityOff()
actor = vtk.vtkActor()
actor.SetMapper(mapper)
actor.GetProperty().SetColor(1.0, 0.3, 0.05)
```

```python
# DSL: the intent is immediately readable
fire = contour(input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, color=(1.0, 0.3, 0.05))
```

The DSL is not just a more convenient way to drive VTK -- it is a
**communication medium between the LLM and the domain expert.** The expert
reads it to verify scientific correctness; the LLM writes it to express
visualization intent. This dual readership is a stronger justification for
the DSL than LLM convenience alone.

### Smart defaults and inference (future)

Following the model of Vega-Lite (which infers scales, color schemes, axes,
and mark types from data characteristics), the DSL could evolve to
automatically infer reasonable defaults:

- `show(fire)` without `color=` picks a sensible color that doesn't
  conflict with existing actors in the scene
- `show(terrain, color_by="rhof_1")` without `scalar_range=` auto-sets
  the range from the data's actual min/max
- `contour(input=data, ContourBy="theta")` without `Isosurfaces=` picks
  a value at an interesting point in the distribution (steepest gradient,
  or a meaningful percentile)
- Camera auto-frames visible actors if no `camera()` is specified

This makes early exploration much faster -- the LLM writes a minimal spec,
the defaults produce something reasonable, then both human and LLM refine.
The spec grows more explicit as the visualization matures.

To preserve readability, **every inferred value must be reported** in the
reconciliation report:

```
fire[show]: CREATED
  color: auto (1.0, 0.3, 0.05) -- distinct from terrain
  scalar_range: auto [0.0, 0.6] -- from data range
```

This ensures the spec is still verifiable even when terse. Inferred values
are always overridable by explicit specification.

Smart defaults are a later-phase feature (after the core DSL and reconciler
are solid), but the DSL should be designed to accommodate them from the
start -- optional parameters with `None` defaults that trigger inference.

### Encoding types (future)

Inspired by Vega-Lite's encoding type system. VTK arrays have data types
(float, int) and component counts (scalar, vector), but no semantic
annotation about what kind of quantity they represent. The DSL could support
encoding types that drive automatic colormap and scale selection:

- **Sequential** (0 to max): fuel density, oxygen concentration → viridis,
  inferno
- **Diverging** (centered on a reference): temperature deviation from
  ambient 300K, signed velocity components → coolwarm, RdBu
- **Categorical** (small cardinality): material ID, region label →
  qualitative palette

The framework could infer encoding type from data characteristics (signed
range → diverging, zero-based → sequential, small integer cardinality →
categorical) or accept it as a DSL annotation:

```python
show(fire, color_by=("theta", "diverging", 300))  # 300K = center
show(terrain, color_by=("rhof_1", "sequential"))   # 0 = min
```

This eliminates a class of aesthetic mistakes where the LLM picks an
inappropriate colormap for the data's semantics.

### Interactive widgets (future, inspired by Vega-Lite selections)

VTK has a rich widget framework (`vtkImplicitPlaneWidget2`,
`vtkSphereWidget2`, `vtkBoxWidget2`, `vtkLineWidget2`) that allows direct
3D manipulation of filter parameters. In raw VTK, wiring a widget to a
filter requires 25-30 lines of boilerplate with manual callback setup.
The DSL can make this declarative:

```python
# Interactive clip plane -- user drags to explore cross-sections
plane = interactive_plane(normal=(0, 0, 1))
clipped = clip(input=fire, plane=plane)
show(clipped, color_by="theta")

# Interactive streamline seed region -- user drags to reposition
seed_region = interactive_sphere(center=(70, 0, 185), radius=30)
streams = stream_tracer(input=velocity, seeds=seed_region,
    num_seeds=50, max_propagation=200)
show(streams, color_by="velocity")

# Interactive threshold range
temp_range = interactive_range(field="theta", initial=[350, 800])
hot_region = threshold(input=data, field="theta", range=temp_range)
show(hot_region, representation="Volume", color_by="theta")
```

The framework handles all widget creation, representation setup, observer
callbacks, and filter re-execution. The DSL wiring is implicit and correct
by construction.

This creates a **new interaction modality**: the LLM builds interactive
instruments (draggable planes, movable seed regions, adjustable thresholds)
and the user manipulates them directly without the LLM in the loop. The LLM
sets up the exploration tools; the user plays with them. This is different
from both the pure-GUI approach (user builds everything) and the pure-LLM
approach (LLM mediates every change).

**Widgets as temporary scaffolding.** Interactive widgets can also serve as
a parameter-picking UI that the LLM deploys on demand and later removes.
When the user says "add streamlines near the fire," the LLM doesn't guess
coordinates -- it places an interactive seed sphere near the fire region.
The user drags it to the right spot. The LLM then reads the widget state
(via a `get_widget_state()` query), replaces the interactive element with
fixed coordinates, and removes the widget:

```python
# Phase 1: LLM deploys positioning widget
seed_region = interactive_sphere(center=(70, 0, 185), radius=30)
streams = stream_tracer(input=velocity, seeds=seed_region, ...)
show(streams, color_by="velocity")
```

```python
# Phase 2: LLM freezes the user's chosen position
streams = stream_tracer(input=velocity,
    seed_center=(82, -12, 191), seed_radius=25, ...)
show(streams, color_by="velocity")
```

This pattern generalizes: the LLM can deploy interactive threshold sliders
to help the user find the right isosurface value, interactive planes to
position a cross-section, or interactive boxes to define a region of
interest -- then freeze the chosen values into the final spec. The widget
is scaffolding that exists during collaborative parameter selection and
compiles away into concrete values. The version history captures both
stages.

### Multi-view composition (future)

Declarative specification of multiple coordinated views, wrapping VTK's
`vtkRenderer.SetViewport()`:

```python
with view("overhead"):
    show(terrain, color_by="rhof_1")
    camera(position=(100, 0, 800), up=(0, 1, 0))

with view("perspective"):
    show(terrain, color_by="rhof_1")
    show(fire, color=(1, 0.3, 0.05))
    show(streams, color_by="velocity")
    camera(position=(400, -300, 400))

layout(["overhead", "perspective"], columns=2)
```

## DSL Implementation: Starlark

The DSL is implemented in Starlark, Google's restricted Python dialect designed
for Bazel build files. Starlark is a separate interpreter (not CPython) with
no path to arbitrary code execution, making it safe for auto-approved MCP tool
calls without user confirmation.

### Why Starlark

The `set_pipeline` MCP tool is auto-approved -- the LLM can call it freely
without the user confirming each change. This means the DSL code executes
without human review. Starlark provides hard safety guarantees:

- Separate interpreter (starlark-rust via starlark-pyo3), not CPython
- No `import`, `exec`, `eval`, `open`, `__import__`
- No `class` definitions, no dunder methods, no metaclasses
- No access to the host filesystem, network, or Python runtime
- Deterministic and hermetic -- no side effects beyond what the host injects
- Guaranteed termination (no `while` loops, no recursion)

The only callable functions are those explicitly injected by the host:
`source`, `filter`, `contour`, `calculator`, `show`, `camera`, `background`,
etc. The worst an LLM can produce is a bad pipeline spec, not arbitrary code
execution.

### Syntax

Starlark syntax is a subset of Python. Variables, `for` loops, `if`/`else`,
list/dict comprehensions, and function definitions all work. LLMs are familiar
with Starlark from Bazel BUILD files in training data, and the restrictions
(no `while`, no `class`, no `import`) are things the LLM knows not to use.

```python
data = source("vtkXMLStructuredGridReader",
    FileName="output.30000.vts")

velocity = calculator(input=data,
    Function="u*iHat + v*jHat + w*kHat",
    ResultArrayName="velocity")

terrain = extract_grid(input=data,
    VOI=[0, 599, 0, 499, 0, 0])

show(terrain, color_by="rhof_1", scalar_range=(0.0, 0.6))

fire = contour(input=data,
    ContourBy="theta", Isosurfaces=[400.0])

show(fire, color=(1.0, 0.3, 0.05), opacity=0.5)

# Comprehensions for repetitive structures
vortex_levels = [
    contour(input=velocity,
        ContourBy="VorticityMagnitude", Isosurfaces=[val])
    for val in [3.0, 5.0]
]

vortex_colors = [(0.2, 0.6, 1.0), (0.9, 0.2, 0.9)]

[show(v, color=c, opacity=0.5)
 for v, c in zip(vortex_levels, vortex_colors)]

camera(
    position=(800, -600, 600),
    focal_point=(100, 0, 100),
    up=(0, 0, 1),
    zoom=1.2)

background(0.15, 0.15, 0.2)
```

### Node identity from variable names

Node names for reconciliation, queries, and version diffs are derived from
the Starlark variable bindings -- not from string arguments. After evaluating
the DSL code, the host calls `module.names()` to enumerate all globals, then
`module[name]` to retrieve each value. Variables bound to node handles become
named pipeline nodes.

```python
fire = contour(...)   # Node is named "fire"
terrain = extract_grid(...)  # Node is named "terrain"
```

This means:
- No redundant string name arguments
- The Python/Starlark variable name *is* the node identity
- Reconciliation matches nodes by variable name across versions
- Query tools use the same names: `get_statistics("fire", "theta")`
- The reconciliation report uses the same names: `fire: UPDATED Isosurfaces [400] -> [500]`

Starlark enforces single assignment at the top level -- reassigning a global
is an error. This gives us the SSA property: each node name is defined exactly
once per spec.

For list-valued bindings (like `vortex_levels` above), the framework
auto-names elements as `vortex_levels[0]`, `vortex_levels[1]`, etc.

### Host integration via starlark-pyo3

We use a fork of starlark-pyo3 that adds `module.names()` (the underlying
Rust `Module` already supports `.names()` -- the fork adds the Python
binding).

The evaluation flow:

1. Host creates a Starlark `Module` and injects builder functions via
   `module.add_callable("source", source_fn)`, etc.
2. Host calls `sl.eval(module, ast, globals)` to execute the DSL code.
3. Builder functions (`source`, `contour`, `show`, etc.) return opaque handle
   objects and internally register node declarations on a collector.
4. After evaluation, the host calls `module.names()` to enumerate globals,
   then `module[name]` for each. Variables bound to node handles are matched
   to their declarations, assigning each node its variable name.
5. The resulting named node graph is passed to the reconciler.

### Convenience wrappers

For common filter types, the host injects short-name wrappers alongside the
generic `filter()` function:

| Wrapper | Equivalent to |
|---|---|
| `contour(...)` | `filter("vtkContourFilter", ...)` |
| `calculator(...)` | `filter("vtkArrayCalculator", ...)` |
| `threshold(...)` | `filter("vtkThreshold", ...)` |
| `extract_grid(...)` | `filter("vtkExtractGrid", ...)` |
| `stream_tracer(...)` | `filter("vtkStreamTracer", ...)` |
| `glyph(...)` | `filter("vtkGlyph3D", ...)` |

The generic `filter("vtkClassName", ...)` remains available for any VTK
filter not covered by a wrapper. Both forms work identically for
reconciliation.

## Property Resolution: ParaView XML Metadata

ParaView's server manager XML files (shipped with ParaView under
`Remoting/Application/Resources/`) define every VTK filter's properties. For
example, `vtkContourFilter` has:

```xml
<SourceProxy name="Contour" class="vtkContourFilter">
  <InputProperty name="Input" .../>
  <IntVectorProperty name="ComputeNormals" default_values="1" .../>
  <StringVectorProperty name="ContourBy"
    command="SetInputArrayToProcess" ...>
    <ArrayListDomain name="array_list" .../>
  </StringVectorProperty>
  <DoubleVectorProperty name="Isosurfaces"
    command="SetValue"
    set_number_command="SetNumberOfContours"
    repeat_command="1" .../>
</SourceProxy>
```

From this, the framework knows:
- `ContourBy` maps to `SetInputArrayToProcess` and expects an array name
- `Isosurfaces` maps to repeated `SetValue(index, val)` calls
- `ComputeNormals` is a boolean (IntVector with 0/1)

**Fallback for early development:** before the XML parsing is implemented,
properties are resolved by simple convention: `PropertyName=value` calls
`obj.SetPropertyName(value)`. This works for the majority of simple VTK
properties (`SetFunction`, `SetResultArrayName`, `SetOpacity`, etc.) and only
fails for the special cases (array selections, indexed values, enums).

## Development Roadmap

### Phase 1: Minimal Interactive Loop

Goal: Claude Code can load data, create a few filter types, see screenshots,
and iterate on a visualization with the user. Limited filter support, no XML
metadata, no reconciliation -- just a command executor.

**Components:**
- VTK viewer with timer-based command queue
- MCP server in background thread with `set_pipeline` and `screenshot` tools
- DSL interpreter that executes builder functions (`source`, `filter`, `show`,
  `camera`, `background`)
- Builder functions create VTK objects directly (no diffing yet -- each
  `set_pipeline` call tears down and rebuilds the full pipeline)
- `show()` handles mapper/actor creation with: `color`, `opacity`, `color_by`,
  `scalar_range`, `representation`
- Property resolution via simple `Set{Name}(value)` convention
- `screenshot` tool captures current view

**Supported VTK classes (hardcoded list):**
- Sources: `vtkXMLStructuredGridReader`
- Filters: `vtkContourFilter`, `vtkArrayCalculator`, `vtkExtractGrid`,
  `vtkThreshold`, `vtkStreamTracer`
- Special handling for: `Isosurfaces` (indexed), `ContourBy` / `Vectors` /
  `ThresholdBy` (array selection), `AddScalarArrayName` /
  `AddVectorArrayName` (accumulating)

**Query tools:** `screenshot` (with camera state), `get_array_info`,
`get_bounds`, `get_statistics`, `get_histogram`, `get_spatial_extent`

**Data exploration tools** (critical for avoiding blind parameter guessing):
- `get_histogram(node, field, bins=50)` -- returns a text-format histogram
  of a field's value distribution. Knowing that 99.5% of theta is ~300K and
  only 0.1% exceeds 400K immediately tells the LLM the right isosurface range.
- `get_spatial_extent(node, field, min_value, max_value)` -- returns the
  bounding box of where a field condition is met. "theta > 350K is
  concentrated in x[52-90], y[-18,16], z[168-200]" eliminates guessing
  at seed positions, slice coordinates, and camera placement.

**Version history:** Each `set_pipeline` call saves the DSL code and a
screenshot to `.vislang/history/vNNN/`. `restore_version` tool to roll back.

**Milestone:** Reproduce the fire + terrain + streamlines visualization from
`vis-state.pvsm` through an interactive Claude Code session.

### Phase 2: User Interaction Capture

Goal: The LLM can see what the user is looking at and where they're pointing.

**Components:**
- Camera state included in `screenshot()` responses
- Shift-click point marking via `vtkCellPicker` -- records 3D coordinates,
  hit actor name, and field values at the picked point
- `get_user_marks()` tool returns accumulated marks and clears the list
- Marks displayed as small spheres in the viewer for visual feedback

**Milestone:** User shift-clicks on the fire front and says "add streamlines
here." The LLM reads the marked coordinates and seeds streamlines at the right
location without guessing.

### Phase 3: Reconciliation

Goal: Pipeline changes are incremental. Changing one parameter doesn't rebuild
the whole pipeline.

**Components:**
- Desired-state graph built by DSL execution
- Live-state graph tracking instantiated VTK objects
- Diff algorithm: create / update / destroy based on name matching
- Structured reconciliation report returned from `set_pipeline`
- Timeout/abort on filter updates via progress observers
- Generic output statistics (cell/point count, bounds) after each update

**Milestone:** Changing an isosurface value from 400 to 500 only re-runs the
contour filter and re-renders; the data reader and other filters are untouched.

### Phase 4: ParaView XML Metadata

Goal: Any VTK filter known to ParaView works in the DSL without custom code.
The LLM can discover what's available and estimate costs before acting.

**Components:**
- Parser for ParaView server manager XML files
- Property resolution: map DSL property names to VTK method calls using XML
  definitions (enum properties, array selections, indexed/repeatable
  properties, boolean toggles)
- Array name validation using XML `ArrayListDomain`
- Auto-generated DSL reference documentation from XML
- `get_dsl_reference(vtk_class)` query tool
- `get_valid_filters(node)` -- uses output data type + XML `DataTypeDomain`
  to list which filters accept a node's output, grouped by category
- `get_show_options(node)` -- lists valid representations and color-by options
  based on output data type and available arrays
- `estimate_cost(vtk_class, node, **params)` -- per-filter complexity model
  (O(n) for contours, O(seeds * steps) for streamlines, etc.) combined with
  input size to give rough time/memory estimates and warnings

**Performance model:** Each filter class has a complexity entry in the registry:

```python
"vtkContourFilter": {"time": "O(n)", "output": "O(n^0.67)"},
"vtkStreamTracer":  {"time": "O(seeds * max_steps)", "output": "O(seeds * max_steps)"},
"vtkGlyph3D":       {"time": "O(sample_points * glyph_faces)", "output": "O(sample_points * glyph_faces)"},
"vtkThreshold":     {"time": "O(n)", "output": "O(n)"},  # worst case
```

Calibrated via a few benchmark runs on the actual hardware, stored as a
constant factor per-machine. Even rough estimates (order of magnitude) prevent
the worst failures like freezing on an 18M-cell volume render.

**Two modes of cost estimation:**

- **Whole-pipeline** (`estimate_cost` with no node arg) -- sums cost across all
  nodes. Useful for one-shot initial pipelines or major restructures. "This
  pipeline will take ~45s to build -- the glyph filter dominates at ~30s.
  Want me to reduce glyph density?"

- **Incremental** -- reported automatically in every `set_pipeline`
  reconciliation report. The reconciler knows which nodes changed, so it
  estimates and reports only the cost of the diff: "v004 -- estimated 2s for
  changes, actual 1.8s." No extra tool call needed. Over time this also
  reveals calibration accuracy, allowing the model to be refined.

**Milestone:** User asks for a filter type not in the Phase 1 hardcoded list.
The LLM queries `get_valid_filters` to discover options, checks
`estimate_cost` to verify feasibility, looks up the DSL reference, writes the
correct declaration, and it works without any framework code changes.

### Phase 5: Polish and Ergonomics

Goal: Robust, pleasant to use.

**Components:**
- Better error messages: "did you mean?" suggestions for misspelled array
  names and property names
- Cost estimation: warn before expensive operations based on input size
- `sample_point` query tool for probing data values at locations
- Color map presets (viridis, coolwarm, etc.) usable by name in `show()`
- Scalar bar / color legend support in `show()`
- Text annotations
- Lighting control

### Phase 6: Trame Integration (Optional)

Goal: Browser-based viewer with user-adjustable parameter sliders alongside
LLM control.

**Components:**
- Replace native VTK window with Trame web app
- Auto-generate UI controls (sliders, dropdowns) for parameters of each
  `show`n node
- Trame state variables bound to pipeline parameters; user slider changes
  feed back into the desired-state graph
- MCP server runs within Trame's server process (no separate thread needed)

**Milestone:** User drags a slider to adjust the isosurface threshold while
the LLM simultaneously adds streamlines to the scene.

## Related Work

### paraview-mcp (LLNL)

https://github.com/llnl/paraview_mcp

MCP server for ParaView by Shusen Liu and Haichao Miao (Lawrence Livermore).
~20 hand-curated imperative tools (`create_isosurface`, `color_by`,
`get_screenshot`, etc.). Connects to `pvserver --multi-clients`. The LLM
issues sequential tool calls to build up a visualization.

Limitations we experienced firsthand: multi-client crashes, single-isosurface
restriction, broken toggle_visibility, no undo, no declarative state
management. Each tool is a narrow wrapper; adding new operations means writing
new Python wrapper functions.

### viznoir

https://github.com/kimimgo/viznoir

The most feature-rich existing tool. 22 MCP tools, headless VTK rendering via
EGL/OSMesa, physics-aware domain auto-detection (CFD, FEA, medical),
animation engine with easing functions and video export. Designed for
automated/batch workflows.

Has a JSON-based declarative pipeline DSL (`PipelineDefinition` compiled to
VTK scripts via `execute_pipeline`), but it's a linear filter chain — not a
DAG, so derived quantities can't reference each other. Stateless: each
`execute_pipeline` call is independent, no persistent renderer, no version
history. Purely headless (no interactive window or human-editable spec file).
See `meta/design-reflections/2026-04-13-viznoir-comparison.md` for detailed
source code analysis.

### Patrick O'Leary's suite

Three complementary tools taking a "help the LLM write correct code" approach:

- **data-mcp** (https://github.com/patrickoleary/data-mcp) -- MCP server for
  scientific data introspection. Tools for querying dataset schemas,
  statistics, and component info. Has `suggest_visualizations` based on data
  characteristics. Uses Trame for interactive 3D viewing. Closest to our
  query tools concept.

- **vtkapi-mcp** (https://github.com/patrickoleary/vtkapi-mcp) -- Validates
  LLM-generated VTK code against a curated index of ~2,900 VTK classes.
  Catches hallucinated methods, wrong imports, incorrect class names. 18 tools
  for lookup and validation. Could complement our system as a reference for
  VTK class metadata or as a validation layer.

- **vtk-python-tests** (https://github.com/patrickoleary/vtk-python-tests) --
  909 VTK Python test files with 672 baseline images, structured for RAG
  integration. Provides grounded examples of working VTK code and expected
  visual output.

### vtk-prompt (Kitware)

https://github.com/Kitware/vtk-prompt

Natural language to VTK Python code via LLM generation and `exec()`. Made by
Kitware (the creators of VTK). RAG-enhanced with a ChromaDB database of VTK
examples. Supports multiple LLM providers. Trame web UI for viewing results.

No pipeline state management -- each prompt generates a fresh standalone
script. No incremental updates or reconciliation.

### What VisLang adds

VizNoir has a declarative pipeline DSL (linear filter chain) but the
following combination is unique to VisLang:
- DAG-based specs where derived quantities reference each other
- Version history with rollback and persistent renderer state
- The pipeline spec as a human-readable, editable, version-controllable
  artifact (not JSON passed through MCP)
- An interactive render window for direct visual inspection
- Concrete parameter suggestion tools (`suggest_isosurface`, etc.)
- Structured semantic diagnostics beyond error messages

### The Gamma (Petricek)

https://thegamma.net/ — a series of projects by Tomas Petricek (Alan Turing
Institute) on programmatic data exploration for non-programmers.

Key ideas and papers:

- **Dot-driven development** (ECOOP 2017) -- type providers generate types
  from actual data so that at every point, code completion shows exactly the
  valid next steps. Users explore data by pressing "." and choosing from
  offered options. The type system encodes row polymorphism, type state, and
  dependent typing into simple member access, so the user never needs to know
  an API.

- **Type providers from data samples** (PLDI 2016) -- F# type providers
  examine actual data and infer types. No schemas to write. The type system
  adapts to the data present, not data someone imagined.

- **Live evaluation with result reuse** (Art Sci Eng Programming, 2020) --
  formalizes an algorithm for incrementally updating previews when code
  changes, reusing previous computation results. Proves correctness of the
  reuse. This is a formalized reconciler for a data exploration calculus.

- **Composable visualization primitives** (JFP 2021) -- Compost builds rich
  visualizations from small functional primitives via composition, rather
  than choosing from fixed chart types.

Ideas we adapt:

- **Data-aware guided construction.** Our `get_valid_filters(node)` and
  `get_array_info()` tools are the query-based version of dot-driven
  development. The valid options at every point in the DSL are determined by
  the actual data and pipeline state. After writing `contour(input=data,
  ContourBy=`, the LLM can know the valid values are the actual array names
  from the loaded data and their ranges. Our query tools make this explicit
  rather than encoding it in a type system, which is appropriate since the
  LLM interacts via MCP tools rather than an IDE completion menu.

- **Formalized incremental evaluation.** Petricek's live evaluation paper
  gives a formal treatment of when you can reuse previous results during
  incremental edits -- essentially our reconciliation problem. His data
  exploration calculus and correctness proofs could inform our reconciler
  and provide a citable formal foundation, extended from tabular data
  exploration to visualization pipelines.

- **The spec as transparency.** The Gamma frames code as a transparency
  tool for data journalism -- readers can inspect exactly what was done.
  Our pipeline spec serves the same role for scientific visualization. A
  visualization published with its VisLang spec is reproducible and
  auditable in a way that a ParaView state file or a screenshot is not.

- **Composability in display configuration.** Compost's approach of
  composing visualizations from functional primitives suggests that our
  `show()` could be more compositional -- layering color mapping onto
  representation onto lighting, rather than a flat bag of properties.

### Typed Holes and ChatLSP (Hazel)

https://arxiv.org/abs/2409.00921 — "Statically Contextualizing Large Language
Models with Typed Holes" by the Hazel team. Integrates LLM code generation
with a language server that provides type context from typed holes (gaps in
code).

Key results: static context (type definitions and function headers) is
essential for LLM completions in low-resource languages, with iterative
error feedback acting as a multiplier on top of good context — together
raising test pass rates from 0% to 76% (GPT-4). (Note: earlier notes in
this repo cited "3x / 4x" multipliers, but these refer to different ablation
dimensions and don't compound as implied.)

They propose ChatLSP, extending LSP with AI-specific methods:
- `aiTutorial()` -- instructional text about the language for LLMs
- `expectedType()` -- what type is expected at the cursor
- `retrieveRelevantTypes()` -- transitively resolves all referenced types
- `retrieveRelevantHeaders()` -- relevant function signatures
- `errorReport()` -- structured errors for correction rounds

Ideas we adapt:

- **Queryable DSL reference over static docs.** Rather than loading the full
  DSL reference into a CLAUDE.md, a `get_dsl_reference(vtk_class)` tool
  returns just the relevant properties and examples when needed. More
  token-efficient, analogous to their `retrieveRelevantTypes()`.

- **Transitive context retrieval.** When the LLM needs to fill in
  `ContourBy=`, retrieve not just "expects a field name" but the actual
  available arrays with types, ranges, and component counts from the
  upstream data. Our enriched reconciliation report already does this
  post-hoc; a `get_context(node, parameter)` tool could provide it
  proactively during construction.

- **Bounded error correction rounds.** The paper shows 2 automatic retries
  on type errors are effective. If `set_pipeline` returns validation errors
  (bad array name, type mismatch), the MCP tool could automatically
  re-prompt with the errors for a bounded number of attempts before
  returning to the LLM. Or we leave retries to the LLM's own judgment --
  either way, the enriched error feedback enables effective correction.

Where we go further: their feedback is purely static (type errors). Ours
combines static validation (array name checking, property type checking)
with **runtime semantic feedback** (output cell counts, bounds, emptiness,
screenshots). "This isosurface produced 0 cells, value may be outside range
[298.1, 812.7]" is a runtime observation, not a type error. The paper's
results suggest static context alone is powerful; runtime feedback on top of
that should be even more effective.

### Structure-Aware RAG for Viz Pipelines (Notre Dame / LLNL)

https://arxiv.org/abs/2603.16057 — "Toward Reliable Scientific Visualization
Pipeline Construction with Structure-Aware Retrieval-Augmented LLMs" (2026).

Encodes pipeline structure, module compatibility, and execution order into
RAG retrieval context for vtk.js code generation. Key insight: pipeline
*topology* matters for retrieval, not just individual API docs. The LLM
needs to know that a contour filter produces polydata which can't be
volume-rendered, not just how to call a contour filter.

Closest existing work to VisLang, but generates imperative vtk.js code
rather than declarative specs. Our declarative spec language encodes
structural constraints inherently — connection types are validated by the
reconciler, so the LLM doesn't need retrieval to know what's compatible.

Idea we adapt: **structure-aware retrieval**. When providing RAG examples,
encode the pipeline topology (what connects to what, data type flow) not
just isolated filter usage.

### ChatVis (LLNL)

https://arxiv.org/abs/2507.23096 — "ChatVis: Large Language Model Agent for
Generating Scientific Visualizations" (2024-2025, SC'24 Workshops).

LLM assistant for ParaView using chain-of-thought prompting, RAG from
ParaView documentation, and iterative error feedback (execute script →
capture error → feed back → retry). Includes benchmark visualization tasks.

A more polished version of the imperative ParaView approach we tried and
found limiting. Same fundamental issues: imperative scripts, no incremental
update, feedback is just error messages. Their benchmark tasks could serve
as evaluation targets for VisLang.

### Compiler-Guided Inference-Time Adaptation (Idris)

https://arxiv.org/abs/2602.11481 — "Compiler-Guided Inference-Time
Adaptation: Improving GPT-5 Programming Performance in Idris" (2026).

GPT-5 goes from 22/56 to 54/56 correct on Idris exercises when given
structured compiler feedback in an iterative loop. Key finding: **local
compilation errors are far more useful than documentation or error
classification guides.** The LLM doesn't need extensive training data on
an unfamiliar language — just good error feedback.

Strongly validates our enriched reconciliation feedback approach. The LLM
doesn't need deep VTK training data; structured feedback from the spec
validator and runtime (array name errors, empty output warnings, output
statistics) is sufficient for effective iteration.

### DSL-Xpert 2.0

"DSL-Xpert 2.0: Enhancing LLM-Driven Code Generation for Domain-Specific
Languages" (MODELS 2024 / Information and Software Technology 2025).

Framework for LLM-driven DSL code generation integrating grammar prompting,
few-shot learning, automatic grammar validation, and iterative correction.
Works across multiple DSLs without per-language fine-tuning.

Idea we adapt: **grammar-aware prompting** reduces syntax errors. Our DSL's
Starlark grammar + builder function signatures could be included in the
system prompt, similar to their approach. The DSL's advantage over raw VTK
code: the grammar is small and regular.

### Imperative vs Declarative for Scene Generation (Brown)

https://arxiv.org/abs/2504.05482 — "Imperative vs. Declarative Programming
Paradigms for Open-Universe Scene Generation" (2025).

Compares declarative (constraints → solver) vs imperative (step-by-step
code) for 3D scene layout. Imperative won 82-94% of the time because
complex spatial relationships are hard to express as constraints.

Cautionary note for our design. For structured filter pipelines this
shouldn't apply — the pipeline DAG is naturally declarative. But **camera
placement, annotation positioning, and seed point placement** involve
spatial reasoning that may be easier to express imperatively. Our DSL
should handle these gracefully — the `camera()` function with explicit
coordinates is already more imperative than declarative.

### SciVisAgentBench (Notre Dame / LLNL)

https://arxiv.org/abs/2603.29139 — "SciVisAgentBench: A Benchmark for
Evaluating Scientific Data Analysis and Visualization Agents" (2026).

108 expert-crafted scientific visualization tasks spanning multiple domains,
data types, and complexity levels. Multimodal evaluation pipeline combining
LLM-based visual judging with deterministic evaluators (image metrics, code
checkers, rule-based verifiers).

We should use this as our evaluation benchmark. Their multimodal evaluation
approach (rendered image assessment + spec correctness checking) could also
inform our feedback — feeding visual quality assessment back to the LLM
alongside structural feedback from the reconciler.

### Shadow Pipelines (SIGMOD DEEM)

https://arxiv.org/abs/2404.19591 — "Shadow Pipelines: Towards Interactively
Improving ML Data Preparation Code" (2024).

Runs hidden variant pipelines alongside the user's pipeline to auto-detect
issues and try alternative transformations. Uses incremental view
maintenance for efficiency.

Less directly applicable than initially expected — this is about exploring
the *design space* of alternative transformations, not previewing changes.
But the concept of parallel pipeline evaluation connects to our
multi-resolution preview brainstorming idea.

### Additional references

- **LIDA** (Microsoft, ACL 2023) -- grammar-agnostic visualization
  generation with LLM self-evaluation feedback. Demonstrates that LLM
  self-evaluation of generated visualizations can drive iterative
  improvement. https://microsoft.github.io/lida

- **VizGenie** (arXiv 2507.21124, 2025) -- self-refining, domain-aware
  workflows for scientific visualization.

- **Andrew Blinn's thesis proposal** "Structured Semantic Context for
  Programming Processes" (Dec 2025, U Michigan) -- extends ChatLSP toward
  surfacing semantic context (types, values, control flow) to both humans
  and LLMs during programming. Directly relevant to DSL tooling design.

### What we can reuse

- **vtk-prompt's RAG system** -- its ChromaDB database of VTK code examples
  could help the LLM write correct DSL specs, especially for less common
  filters. We could use it directly or adapt it to store DSL examples rather
  than raw VTK code.
- **vtk-python-tests baseline images** -- 672 images of known-correct VTK
  output, useful as grounding for visual quality assessment.
- **vtkapi-mcp's class index** -- its 2,900-class VTK API database could
  supplement or replace our planned ParaView XML parsing for property
  metadata. Its validation approach (AST parsing to catch hallucinated
  methods) could be adapted to validate DSL specs.
- **data-mcp's data exploration pattern** -- its query tools
  (`get_statistics`, `suggest_visualizations`) align with our design and
  validate the approach.

## Domain Knowledge

The LLM has broad but shallow domain knowledge about scientific visualization.
For common tasks (isosurfaces for boundaries, streamlines for flow, diverging
colormaps for signed quantities) it does fine. But there are gaps where
domain-specific knowledge matters:

- **Community conventions** -- CFD expects certain color palettes. Medical
  imaging has established windowing ranges (bone vs soft tissue vs lung in CT).
  Geoscience has standard color scales for elevation and seismic data. The LLM
  might pick viridis when the community expects something specific.

- **Meaningful thresholds** -- knowing that theta=400K is the fire boundary in
  wildfire data, or that Q-criterion > 0 identifies vortex cores in CFD, is
  domain knowledge the LLM might approximate but get subtly wrong.

- **Derived quantity recipes** -- von Mises stress from a stress tensor for
  FEA, Q-criterion from velocity gradients for CFD, apparent diffusion
  coefficient from diffusion MRI. These are standard domain recipes that
  require precise formulas.

- **Physical plausibility checks** -- "velocity magnitude of 500 m/s in a
  wildfire sim is probably wrong" requires knowing what's physically reasonable
  for the domain.

The query tools (`get_histogram`, `get_spatial_extent`, `get_statistics`) help
the LLM make informed parameter choices from the data itself, which partially
addresses this. But domain conventions and recipes benefit from an external
reference.

**Approach:** domain-specific knowledge files (not baked into the framework)
that can be loaded as context when working with a particular kind of data. For
example, a `domains/cfd.md` file with standard derived quantities, meaningful
threshold ranges, conventional color maps, and common filter chains. These
could be selected manually ("I'm working with CFD data") or suggested
automatically based on array names and data characteristics detected by the
framework. This is lighter than viznoir's baked-in physics layer but addresses
the same gap -- domain expertise that the LLM alone may not reliably provide.

## Research Framing

The core claim: a declarative DSL with live feedback, data introspection, and
incremental update produces better LLM-driven visualizations than existing
approaches (imperative tool calls, one-shot code generation). This is a systems
and language design contribution, evaluated empirically.

### Ablation study

Compare visualization quality and efficiency across increasingly capable
configurations, holding the LLM and dataset constant:

1. **One-shot code generation** (vtk-prompt style) -- LLM writes a complete
   VTK script from a natural language description. No iteration, no feedback.
   Baseline.

2. **+ Interactive iteration** -- LLM can see screenshots after each change
   and revise the pipeline. Measures the value of multi-turn visual feedback.

3. **+ Data queries** (histograms, spatial extent, statistics, array info) --
   LLM can introspect the data before writing the spec. Measures whether
   knowing value ranges and spatial layout reduces blind parameter guessing.

4. **+ Structured error reporting** (output emptiness checks, array name
   validation, VTK warnings surfaced as clear messages) -- Measures whether
   actionable error feedback reduces failures vs raw VTK tracebacks or silent
   empty output.

5. **+ Cost estimation and timeout** -- Measures whether preventing expensive
   operations (freeze/crash avoidance) improves session reliability.

6. **+ User interaction capture** (marked points, camera state) -- Measures
   whether spatial grounding from the user improves placement of seeds,
   slices, and other spatially-dependent parameters.

### Evaluation metrics

- **Iterations to target** -- number of `set_pipeline` calls to reach a
  visualization that matches a reference (judged by structural similarity
  or manual evaluation).
- **Failure rate** -- fraction of pipeline submissions that produce empty
  output, VTK errors, freezes, or crashes.
- **Time to completion** -- wall clock time from task description to
  acceptable visualization.
- **Final quality** -- visual comparison against expert-authored reference
  visualizations, possibly scored by domain experts.

### Benchmark tasks

A suite of visualization tasks across datasets and domains:
- Wildfire simulation: fire isosurface + terrain + wind streamlines (the
  running example in this project)
- CFD: vortex identification via Q-criterion on turbulence data
- Medical imaging: organ segmentation with tissue windowing
- Climate: multi-field overlay (temperature, pressure, wind vectors)

Each task has a natural language description, a dataset, and a reference
visualization. The ablation runs each configuration against the full suite.

### Motivation from experience

This project's design was directly motivated by a failed session using
paraview-mcp (imperative tool calls). Key failures included:
- Blind parameter guessing for isosurface values and spatial coordinates,
  requiring many wasted iterations (motivates data query tools)
- Silent empty output when filter parameters were wrong, with no diagnostic
  information (motivates structured error reporting)
- Freezing on volume rendering of 18M cells with no way to cancel (motivates
  cost estimation and timeout)
- Inability to position streamline seeds without knowing where the fire was
  in space (motivates spatial extent queries and user point marking)
- Accumulating stale pipeline objects with no way to clean up or roll back
  (motivates declarative state management and version history)

Each feature in the design traces to a concrete failure mode observed in
practice.

## Relationship to DSL + LLM Approaches

### Landscape

There are several established approaches for helping LLMs work with DSLs:

**Constrained decoding** (XGrammar, Outlines, llguidance) filters token
probabilities at each generation step so output conforms to a formal grammar.
This guarantees syntactic validity but not semantic correctness. Requires
logit access, so it's unavailable with commercial models accessed via API.

**LSP-as-MCP bridges** (mcp-language-server, lsp-mcp) expose language server
features -- completion, diagnostics, hover, go-to-definition -- to LLMs via
MCP tools. This gives the LLM access to static semantic analysis.

**Tool-use feedback loops** give the LLM predefined actions with concrete
feedback. The LLM calls tools, observes results, iterates. This shifts DSL
knowledge from prompt context into the tool responses.

**RAG over DSL corpora** retrieves validated examples at inference time for
in-context priming. Fine-tuning over DSL datasets is a heavier variant.

**Spec-driven development** (Spec Kit, AWS Kiro) uses structured specifications
to guide code generation through phases before any code is produced.

### Where VisLang fits

VisLang is primarily a tool-use feedback loop, but with properties that
distinguish it from typical tool-use approaches:

**Runtime semantic feedback, not just static analysis.** An LSP tells you "this
variable doesn't exist." Our system tells you "this isosurface produced 45,231
cells in x[52-88], y[-15,15], z[168-195]." That's runtime feedback dependent
on the data, not the code. For any domain where interesting properties are
data-dependent, runtime feedback is strictly more informative than static
analysis. Static analysis can verify that `ContourBy="theta"` is a valid array
name; only execution can tell you whether the isosurface at 400K produces
meaningful geometry or empty output.

**The reconciler as a semantic layer.** Between the DSL spec and the runtime,
the reconciler diffs, validates, estimates cost, reports changes, and applies
minimal mutations. This is richer than "parse and run" -- it combines
validation, execution, and observation into a single structured response. The
reconciliation report is a form of semantic feedback that doesn't exist in
any of the other approaches.

**The spec as a versioned shared artifact.** In tool-use approaches, the LLM's
actions are ephemeral tool calls with no persistent artifact. In code
generation, the output is a one-shot script. Our DSL spec is a living document
that both human and LLM iterate on, with version history and rollback. The
human can read it, learn from it, and eventually co-author it.

**Declarative over imperative.** The LLM describes desired state, not steps.
It doesn't need to reason about current state, cleanup of previous attempts,
or execution ordering. The reconciler handles all of that. This reduces the
reasoning burden on the LLM and eliminates a class of errors (stale state,
forgotten cleanup, ordering bugs) that plague imperative tool-use approaches.

### What we can use from other approaches

**LSP for the human side.** Our MCP query tools serve the LLM; an LSP built on
the same semantic knowledge could serve the user editing the pipeline file.
Autocompletion for filter names and property names, error highlighting for
invalid array references, hover documentation for filter parameters. Same
knowledge base, two protocols, two consumers.

**RAG for filter selection.** The LLM knows VTK broadly but may not know which
filter is best for a specific task. RAG over VTK examples (vtk-prompt's corpus,
O'Leary's test suite) helps with "which filter?" while our query tools help
with "what parameters?"

**Constrained decoding is unnecessary.** Our DSL is Starlark, which LLMs
already know. Syntax errors are rare; semantic errors are where the real
problems are. Our runtime feedback loop addresses semantic correctness where
grammar constraints cannot.

### Generalizing: the declarative reconciliation pattern

The combination of declarative spec + reconciliation + rich runtime feedback
is a general pattern for LLM collaboration on stateful systems. It applies
wherever:

- There is an **expensive stateful runtime** (renderer, database, cluster,
  simulator) that is costly to tear down and rebuild from scratch
- Configuration is a **DAG of components** with parameters and connections
- **Iteration is valuable** -- the user and LLM want to tweak and observe, not
  generate once
- **Interesting properties are data-dependent** -- runtime feedback is more
  informative than static analysis

The pattern:

```
LLM writes declarative spec
  → Reconciler diffs against live state
  → Applies minimal mutations to runtime
  → Returns rich runtime semantic feedback
  → LLM uses feedback to write next version
  → Version history enables rollback
  → User observes runtime + artifact, provides contextual input
```

Domains beyond visualization where this pattern applies:

| Domain | Runtime | Spec | Runtime feedback |
|---|---|---|---|
| Visualization | VTK renderer | Pipeline of filters + display | Cell counts, bounds, screenshots |
| Infrastructure | Cloud provider | Resource graph (Terraform) | Provision times, costs, endpoint health |
| Data pipelines | Airflow/Spark | DAG of transforms | Row counts, schema, data quality metrics |
| Simulation config | OpenFOAM/FEA solver | Mesh + physics + boundary conditions | Convergence, residuals, probe values |
| Database schema | RDBMS | Tables + indices + constraints | Query plans, row counts, migration time |
| Audio production | DAW / effect chain | Signal processing graph | Waveform, spectrum, loudness |

In each case, the declarative spec lets the LLM describe intent; the
reconciler manages the stateful runtime; and runtime feedback provides the
semantic grounding that static analysis alone cannot.

The key principle: **for data-dependent DSLs targeting stateful runtimes,
the best diagnostics come from actually running the spec against real data
and reporting what happened.** Grammar constraints and static analysis are
useful but insufficient. The reconciliation report -- showing what changed,
what it produced, and whether it makes sense -- is the feedback mechanism
that closes the loop.

## Generalizing Beyond VTK

VisLang is built for VTK visualization pipelines, but many of its ideas apply
to LLM collaboration on any computational task involving data. This section
explores what generalizes cleanly and where domain-specific adaptation is
needed.

### What generalizes

The following components are domain-independent:

- **Starlark as a safe authoring language** -- variable bindings as node
  identity, SSA at top level, guaranteed termination, host-injected functions
- **The MCP interface pattern** -- one mutation tool (`set_pipeline`) plus
  query tools, with structured feedback including next-step guidance
- **Version history and rollback** -- saving each spec + output snapshot,
  restoring previous versions
- **Data-aware query tools** -- histograms, statistics, spatial/structural
  summaries of intermediate results
- **Cost estimation** -- per-operation complexity models, whole-pipeline and
  incremental estimates
- **The enriched feedback loop** -- every action returns results plus
  guidance on what's possible next (The Gamma's dot-driven principle)

These form a **framework for building LLM-collaborative environments for
stateful runtimes**, with a shared infrastructure layer and pluggable
domain-specific components.

### What doesn't generalize: the reconciler

The reconciliation strategy must match the runtime's execution model. There
are (at least) two distinct patterns:

**Lazy / demand-driven runtimes** (VTK, database query planners, Terraform):
The runtime maintains a persistent pipeline graph and has its own internal
change-tracking. The reconciler's job is structural — diff the desired graph
against the live graph and apply minimal mutations (add/remove nodes, change
parameters, rewire connections). The runtime handles incremental
re-execution internally.

```
Reconciler manages structure:  add node, remove node, change param
Runtime manages execution:     VTK's Update() only re-runs modified branches
```

**Eager runtimes** (numpy, pandas, scipy): Computations execute immediately
and produce concrete results. There is no persistent pipeline to mutate.
Incrementalization requires caching intermediate results and replaying only
the changed subexpressions — the approach formalized in Petricek's live
data exploration calculus. The expression structure of the program IS the
dataflow graph; no explicit pipeline DSL is needed.

```
Reconciler manages caching:    cache results at each subexpression
Re-execution on edit:          reuse cached prefix, re-run from change point
```

These two strategies cannot be cleanly unified into one mechanism. Layering
Gamma-style expression caching on top of VTK would cache empty lazy pipeline
objects (useless). Layering structural reconciliation on top of numpy would
require wrapping every operation in an explicit pipeline node (cumbersome).

### The domain-specific plug-in

Each domain provides:

- **Host functions** injected into Starlark (VTK filters, pandas operations,
  scipy solvers, etc.)
- **A reconciler** implementing either the lazy or eager strategy
- **Structured summaries** appropriate for the domain (cell counts and bounds
  for VTK, row counts and column statistics for pandas, convergence metrics
  for solvers)
- **Domain knowledge files** (optional) with conventions, standard recipes,
  and plausibility checks
- **Cost models** per operation

### Potential domains

| Domain | Runtime model | Host functions | Reconciler type |
|---|---|---|---|
| VTK visualization | Lazy (demand-driven pipeline) | source, filter, show | Structural graph diff |
| pandas/data wrangling | Eager | load, filter, groupby, merge | Expression cache + replay |
| scipy/ML fitting | Eager (expensive) | fit, predict, cross_validate | Expression cache + replay |
| SQL analytics | Lazy (query planner) | table, query, join, aggregate | Structural query diff |
| Terraform/infra | Lazy (provider APIs) | resource, data, output | Structural graph diff |

VisLang is the first instance. The framework-level components (Starlark,
MCP, versioning, feedback) would be shared; the domain components would
be separate packages.

## Brainstorming: Future Capabilities

Ideas for additional tools and features that could help the LLM make better
visualizations faster. Not yet scheduled in the roadmap.

### Data probing along lines and planes

Single-point sampling is useful, but a profile is more informative. A
`probe_line(start, end, field, num_samples)` tool returns a 1D profile of a
field along a line through the domain -- e.g., "what does theta look like from
the ridge to the valley?" Returns a compact table or text sparkline. This is
what `extract_yz_slice.py` was doing manually. Could also support
`probe_plane(origin, normal, field)` for 2D cross-sections.

### Reference image comparison

Load a reference image (e.g., a figure from a paper, a colleague's
visualization) and display it alongside the current render. A
`set_reference_image(path)` tool that returns the reference alongside every
subsequent `screenshot()` call. Enables "make it look like Figure 4" workflows
where the LLM can visually compare its progress against a target. Could also
compute simple similarity metrics (color histogram distance, structural
similarity) to quantify how close the current render is to the reference.

### Pipeline suggestions

After data is loaded and arrays are known, the system could suggest meaningful
visualization approaches -- not just "which filters are valid" but "which
combinations are useful for this kind of data." A
`suggest_visualizations(node)` tool that combines filter validity with domain
heuristics:

```
You have:
  - velocity components (u, v, w) -> streamlines, glyphs, vorticity
  - temperature field (theta, range 298-813K) -> isosurface for fire front,
    volume rendering for flame/smoke
  - fuel density (rhof_1, range 0-0.6) -> terrain surface coloring to
    show burn scars

Suggested starting pipeline:
  1. Extract terrain (k=0) colored by rhof_1 for context
  2. Isosurface of theta ~400K for fire boundary
  3. Streamlines through velocity near fire region
```

This could be partly LLM-driven (the system provides the array metadata, the
LLM has domain knowledge about what's interesting) or partly hardcoded for
common data patterns (CFD, medical imaging, geoscience, etc.).

### Occlusion awareness

"From this camera angle, is the fire isosurface hidden behind the terrain?" --
hard to tell from a screenshot alone whether something is invisible because
it's absent vs. because it's occluded. A depth-buffer analysis after rendering
could report: "actor 'fire' is 80% occluded by 'terrain' from current view."
This helps the LLM decide between adjusting camera angle, changing opacity,
or clipping away obscuring geometry.

Could be implemented via VTK's `vtkVisibilitySort` or by rendering each actor
separately to an offscreen buffer and comparing visible pixel counts.

### Color palette previews

Preview what a color map looks like applied to actual data values before
committing to a pipeline change. A `preview_colormap(node, field, colormap)`
tool that returns a small image showing the color mapping on a representative
sample of the data. Useful for aesthetic iteration -- "show me viridis vs
coolwarm vs inferno on the theta range" without modifying the pipeline three
times.

### Version annotations and decision log

Extend the version history with LLM-generated annotations: why each change was
made, what was tried, what worked. Over a long session this builds a decision
log that helps with:
- Explaining the visualization to others ("here's how we arrived at this")
- Resuming a session later ("last time we tried X and it didn't work because Y")
- Understanding which parameters are sensitive ("changing isosurface from 400
  to 450 lost the fire front entirely")

Could be as simple as an optional `note` parameter on `set_pipeline`:
```
set_pipeline(code, note="Lowered isosurface to 350K -- 400 was missing the
                         smoldering region visible in the reference image")
```

### Animated parameter sweeps

Automatically generate a sequence of renders varying one parameter (e.g.,
isosurface value from 300 to 600 in 10 steps) and return them as a filmstrip
or animation. Useful for finding the right parameter value visually rather
than guessing. A `sweep(param_path, values, screenshot=True)` tool that
temporarily modifies one parameter, captures each frame, and restores the
original.

### Multi-resolution preview pipeline

Maintain a subsampled proxy of the input data alongside the full-resolution
data. Run the pipeline on the cheap version first, let the LLM iterate
quickly, then promote to full resolution once the spec stabilizes.

For a structured grid like the wildfire data (600 x 500 x 61 = 18M cells),
`vtkExtractGrid` with `SampleRate=[10, 10, 2]` produces a 60 x 50 x 31
grid (~1/200th the size). The full pipeline runs in milliseconds instead of
seconds.

The reconciler flow becomes:

```
set_pipeline(code)
  → run on subsampled proxy (fast)
  → return preview screenshot + reconciliation report
  → LLM iterates on preview (cheap, many rounds)
  → LLM or user calls promote_to_full_resolution()
  → run on full data (expensive, once)
  → return final report
```

Cost estimation becomes empirical rather than modeled: "preview took 0.3s
on 1/200th data, full resolution estimated ~30-60s."

**Caveats:**
- Not all filters scale linearly with resolution. Streamline tracing
  depends on flow features that might vanish in subsampled data.
- Isosurface topology can change at different resolutions — a fire front
  3 cells wide at full resolution may disappear at 10x subsampling.
- The preview can be misleading. The framework should warn: "preview only —
  features smaller than N cells may not be visible."

Despite these caveats, for the common case — "does this pipeline produce
anything reasonable before I wait 30 seconds?" — this would be very useful.
The LLM could iterate 5 times on the cheap version in the time one
full-resolution run takes.

### Multi-view layouts

Side-by-side views of the same data from different angles or with different
visualizations. "Show me the terrain from above colored by rhof_1 next to a
3D perspective with fire and streamlines." VTK supports multiple renderers in
a single window via viewport splits. A `split_view(layout)` DSL directive
could manage this.
