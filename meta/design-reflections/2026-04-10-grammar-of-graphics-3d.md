# A Grammar of Graphics for 3D Scientific Visualization

*Design exploration — April 10, 2026*

## The Question

Could VisLang make a significant improvement over PyVista by designing its
DSL with grammar-of-graphics (GoG) principles adapted to 3D? In what
dimensions? How would it fit into the project?

## Context

VisLang has explored two approaches so far:

1. **The main DSL** (`vislang/dsl.py`) — a custom declarative DSL built
   directly on VTK. Pipeline files call builder functions (`source`,
   `threshold`, `contour`, `show`) that register nodes in a desired-state
   graph, which is then executed against VTK.

2. **The tracked-execution experiment** (`experiments/tracked-execution/`) —
   a content-addressed caching layer on PyVista. Pipeline scripts are plain
   Python calling PyVista methods through a tracking proxy that hashes
   operations and caches results.

Both are rough prototypes exploring the design space. The question now is
whether GoG principles could inform a third approach — or reshape one of
these — into something with genuine advantages over the current landscape.

---

## Part 1: The Grammar of Graphics and Why It Matters

### Core Abstractions (Wilkinson 2005, Wickham 2010)

The grammar of graphics decomposes any visualization into orthogonal
components:

| Component | What it specifies |
|---|---|
| **Data** | The dataset being visualized |
| **Aesthetics / Encodings** | Mappings from data variables to visual channels (position, color, size, shape, opacity) |
| **Geometry / Marks** | The visual representation type (point, line, area, bar) |
| **Scales** | Transformations from data units to visual units (linear, log, color ramps) |
| **Statistics** | Data transformations applied before rendering (binning, smoothing, density estimation) |
| **Coordinates** | The coordinate system (Cartesian, polar, map projections) |
| **Facets** | Small-multiple decomposition by a categorical variable |

The fundamental insight: a graphic is not a named type ("bar chart",
"scatter plot") but a *composition of independent decisions*. This
decomposition is what gives the grammar its generative power — it can
describe visualizations that have no name, because the space of valid
compositions far exceeds the space of named chart types.

### What Made ggplot2 Successful

Wickham's ggplot2 operationalized Wilkinson with key practical choices:

- **Layers** as the organizing unit. Each layer has its own data +
  aesthetic mapping + stat + geom + position adjustment.
- **Sensible defaults.** The common case is concise; the full power is
  available when needed.
- **Additive composition via `+`.** Layers stack; scales, coordinates,
  and facets apply globally.
- **Separation of encoding from geometry.** `aes(x=temp, color=species)`
  is an independent, reusable mapping object.

The result: ggplot2 is both a practical tool for everyday graphics and a
framework that can express an enormous range of visualizations through
composition rather than enumeration.

### The 3D Gap

Extension of GoG to 3D has been attempted in narrow domains but never
for the full breadth of scientific visualization:

- **ggrgl** (R, 2020) — adds `z` aesthetic and 3D geoms to ggplot2.
  Targets statistical 3D charts (scatter, bars with depth), not scientific
  visualization.
- **Plotly** — declarative 3D traces (`Isosurface`, `Volume`,
  `Streamtube`, `Cone`) specified via JSON-like dicts. Not formalized as
  a grammar; traces are chosen by type rather than composed from
  orthogonal primitives.
- **Shih, Rozhon & Ma (IEEE TVCG 2019)** — "A Declarative Grammar of
  Flexible Volume Visualization Pipelines." The most relevant academic
  work: a grammar specifically for volume rendering with combinators for
  multi-variable transfer functions and shading effects.
- **DIVA** (Wu et al., IEEE LDAV 2020) — declarative reactive language
  for in-situ visualization with signal-based dataflow semantics.

No system provides a unified, compositional grammar covering the full
range of 3D scientific visualization techniques (volume rendering,
isosurfaces, streamlines, glyphs, slicing) within a single coherent
framework. This is the open research gap.

---

## Part 2: What's Wrong With the Current Landscape

### PyVista's Design and Its Limits

PyVista wraps VTK with a Pythonic, imperative, plotter-centric API.
You create a `Plotter`, add meshes with visual properties as keyword
arguments, and call `show()`. Filters chain as methods:
`mesh.threshold(500).extract_surface().smooth()`.

**Strengths worth acknowledging.** PyVista dramatically lowers the barrier
to VTK. NumPy integration is seamless. The simple case — one mesh, one
colormap — is trivially easy. Filter chaining for data processing is
genuinely composable. Installation is trivial. These are real achievements.

**But the design breaks down for complex scientific visualization:**

1. **Parameter explosion.** `add_mesh()` accepts dozens of keyword
   arguments — color, scalars, cmap, opacity, lighting, ambient, roughness,
   metallic, show_scalar_bar, scalar_bar_args, style, silhouette, etc.
   This is the classic symptom of bundling data binding, visual encoding,
   and rendering configuration into a single imperative call. There is no
   compositional structure.

2. **Tight coupling of data and visual encoding.** The mapping from data
   to visual representation happens inside `add_mesh()`. You cannot define
   "color by temperature using viridis with range [300, 800]" as a reusable
   object and apply it to multiple meshes. You repeat the keyword arguments
   each time.

3. **Imperative state accumulation.** The Plotter accumulates state through
   sequential method calls. Ordering dependencies, hard to reason about
   the final scene without executing, hot-reload is risky. Multiple
   documented issues (point picking failing with multiple actors, cell
   picking only working on the last-added actor) trace back to stateful
   mutation.

4. **Volume rendering is painful.** Transfer function specification is
   a particular sore point. Multiple PyVista GitHub issues document
   confusion about opacity transfer functions: miscalibrated defaults,
   custom opacity maps causing errors, poor documentation of the
   interaction between `n_colors`, opacity arrays, and colormaps.

5. **VTK leaks through.** Complex visualizations often require mixing
   PyVista convenience methods with raw VTK calls, creating a jarring
   split in abstraction levels. VTK's silent failure mode (empty output,
   no error) compounds this — when something goes wrong at the VTK layer,
   there is no diagnostic path through PyVista's API.

6. **Scene composition is ad-hoc.** No concept of grouping, layering,
   or compositional operators. No way to express "overlay these two
   representations of the same data" or "facet this visualization by a
   categorical variable" as structured operations.

### VisLang's Current DSL: Already Partway There

The existing VisLang DSL (`vislang/dsl.py`) already embodies some GoG
principles without naming them:

**What it gets right:**

- **Declarative specification.** Pipeline files describe desired state
  with no side effects, no accumulated mutation, no ordering dependencies.
  This enables safe re-execution, hot reload, version rollback — the
  properties that make the interactive system work.
- **Named nodes as stable identities.** Variable names become identities
  that the query tools, build reports, and (future) LSP all reference.
- **Separation of data processing from display.** `source()` → filter
  chain → `show()` keeps the data pipeline distinct from visual encoding.
- **Loud error diagnostics.** Field name validation with "did you mean?"
  suggestions, empty output warnings — designed for both human and AI
  readers.

**Where it falls short of GoG:**

- **`show()` is a kitchen sink.** It takes `color_by`, `scalar_range`,
  `lut`, `opacity`, `opacity_function`, `gradient_opacity`, `representation`,
  `specular`, `scalar_bar`, `component`, `volume_resolution`, etc. — all
  as keyword arguments. This is the same parameter explosion problem as
  PyVista's `add_mesh()`, just with different names.
- **No reusable encoding objects.** You cannot define a color mapping
  independently and apply it to multiple layers. Every `show()` call
  specifies its own encoding from scratch.
- **No compositional layer operators.** Multiple `show()` calls create
  multiple layers, but there is no way to group, overlay conditionally,
  or facet. The relationship between layers is implicit (they happen to
  be in the same file) rather than structurally expressed.
- **Filters are VTK-shaped.** `threshold(input=data, ThresholdBy="temperature",
  ThresholdRange=[500, 2000])` is a thin wrapper around `vtkThreshold`.
  The DSL vocabulary mirrors VTK's class hierarchy rather than defining
  its own conceptual model. A user needs to know what a "threshold filter"
  is, not just "I want to see the region where temperature is above 500."
- **The stat layer is external.** Statistical operations (`suggest_isosurface`,
  `suggest_opacity`, `get_statistics`) exist as MCP tools outside the DSL
  rather than as composable transformations within it. In ggplot2, `stat_bin()`
  is part of the grammar; in VisLang, the equivalent is a tool call that
  returns values you paste into the pipeline.

---

## Part 3: A Grammar of 3D Scientific Visualization

### The Central Challenge: Fields, Not Rows

The grammar of graphics assumes visualization is a mapping from **tabular
data rows to visual marks**. Each row becomes a point, a bar, a line
segment. The aesthetic mapping (`aes(x=temp, y=pressure, color=species)`)
selects columns and assigns them to visual channels. This row-to-mark
correspondence is what makes the grammar elegant.

Scientific 3D visualization operates on **fields** — continuous scalar,
vector, and tensor quantities defined over spatial domains. The "mark" is
not placed per-row but is produced by an algorithm operating on the entire
field:

- An **isosurface** is produced by marching cubes traversing a 3D scalar
  field — the surface emerges from the algorithm, not from mapping rows.
- **Volume rendering** casts rays through a 3D grid, accumulating color
  and opacity — there are no discrete marks at all.
- **Streamlines** are produced by numerical integration through a vector
  field — each line is a trajectory, not a data row.
- **Glyphs** *are* the closest to row-to-mark: one glyph per sample point,
  oriented and scaled by data. This is where GoG applies most directly.

The bridge between field data and the grammar's mark-based abstraction
is the central design problem. I propose that the answer is to recognize
that **field-to-representation algorithms** (isosurface extraction,
volume ray casting, streamline integration) play the role of **geometry
generators** — they are the 3D analog of ggplot2's geoms, but they
produce geometry from fields rather than mapping rows to marks.

### Proposed Abstractions

Here is a grammar for 3D scientific visualization with seven components,
adapted from Wilkinson/Wickham:

| GoG Component | 3D Sci-Viz Analog | What it specifies |
|---|---|---|
| **Data** | `data(file)` | The dataset: a spatial field (structured grid, volume, mesh) |
| **Encoding** | `encode(color=..., opacity=..., size=...)` | Mappings from field variables to visual channels |
| **Representation** | `rep(...)` — the 3D geom | The algorithm that produces visible geometry from field data |
| **Scale** | `scale_color(...)`, `scale_opacity(...)` | Domain-to-range mappings for each visual channel |
| **Transform** | `transform(...)` — the 3D stat | Data transformations applied before representation (threshold, gradient, derived fields) |
| **View** | `view(camera=..., projection=...)` | Camera, projection, and coordinate system (3D analog of coord) |
| **Composition** | `layer(...)`, `facet(...)`, `overlay(...)` | How multiple representations relate to each other |

### The Key Insight: Representations Replace Geoms

In 2D, geoms are simple: point, line, bar, area. In 3D sci-viz, the
representation type is the creative core of the visualization. The
representation determines the algorithm:

```python
# Proposed syntax — each rep() is a geometry generator for fields
rep_isosurface(field="temperature", values=[500, 800, 1200])
rep_volume(field="density")
rep_streamlines(field="velocity", seeds=...)
rep_glyphs(field="velocity", shape="arrow", every_nth=20)
rep_slice(normal=[0, 0, 1], origin=[0, 0, 50])
rep_surface()                    # outer boundary surface
rep_outline()                    # bounding box wireframe
```

Each representation type knows what kind of field data it needs (scalar
for isosurface, vector for streamlines), what algorithm to run (marching
cubes, RK4 integration), and what VTK pipeline to construct. The user
does not need to know any of this — they express intent ("show me
isosurfaces of temperature at these values") and the grammar handles the
VTK translation.

### Encodings as First-Class Objects

This is the single highest-leverage GoG idea for 3D. Instead of burying
visual encoding in keyword arguments:

```python
# Current VisLang — encoding mixed into show()
show(hot_region, "fire", color_by="temperature",
     scalar_range=(500, 2000), lut="hot",
     opacity=0.7, specular=0.4, scalar_bar="Temp (K)")
```

Encodings become independent, reusable, inspectable objects:

```python
# Proposed — encoding is separate from what it applies to
fire_encoding = encode(
    color=scale_color("temperature", range=[500, 2000], colormap="hot"),
    opacity=0.7,
    specular=0.4,
    legend="Temp (K)"
)

# Apply the same encoding to multiple representations
show(isosurface_layer, fire_encoding)
show(volume_layer, fire_encoding)
```

This separation enables:
- **Reuse** — define once, apply to many layers
- **Diffing** — changing an encoding is a single-line diff, not scattered
  keyword-argument changes across multiple show() calls
- **Inspection** — an encoding is a structured object that can be
  printed, compared, serialized
- **Consistency** — multiple representations of the same field
  automatically use the same color mapping

### Transfer Functions as Grammar Elements

Volume visualization's central abstraction — the transfer function mapping
scalar values to color and opacity — is poorly served by current APIs.
In PyVista, it is keyword arguments. In VisLang's current DSL, it is
`opacity_function=[(300,0),(600,0.02),(1200,0.5)]` — a list of tuples
buried in `show()`.

In a GoG framework, transfer functions are first-class scale objects:

```python
# Transfer function as a composable scale
opacity = scale_opacity("density",
    control_points=[(0, 0.0), (50, 0.2), (200, 0.8)],
    gradient_modulation=True   # edge enhancement
)

color = scale_color("density",
    colormap="terrain",
    range=[20, 200]
)

# Compose into a volume encoding
vol_encoding = encode(color=color, opacity=opacity, shade=True)

# Apply
show(rep_volume(field="density"), vol_encoding)
```

This makes the transfer function inspectable, serializable, and
independently modifiable — you can change the opacity curve without
touching the color mapping.

### Transforms: Data Processing Within the Grammar

Currently, VisLang's data processing (threshold, gradient, derived fields)
uses VTK-shaped filter functions outside the encoding/display system.
In a GoG framework, these become **transforms** — the 3D analog of
ggplot2's stats:

```python
# Current VisLang — imperative filter chain
data = source("vtkXMLStructuredGridReader", FileName="fire.vts")
vel = make_vector(input=data, components=("u", "v", "w"), result="velocity")
hot = threshold(input=vel, ThresholdBy="temperature", ThresholdRange=[500, 2000])
streams = stream_tracer(input=vel, SeedSource=seeds, Vectors="velocity")

# Proposed — transforms compose with representations
fire_data = data("fire.vts")

show(fire_data
     | where("temperature", between=[500, 2000])
     | rep_volume(field="temperature"),
     encode(color=scale_color("temperature", [500, 2000], "hot"),
            opacity=scale_opacity("temperature", "fire")))

show(fire_data
     | derive_vector("velocity", from_components=["u", "v", "w"])
     | rep_streamlines(field="velocity", seeds=near("temperature", [500, 2000], n=40)),
     encode(color=scale_color("velocity", colormap="wind")))
```

The `|` pipe operator chains transforms and representations. Each
transform produces a new derived dataset; the representation consumes the
final result. This is analogous to ggplot2's `stat_smooth()` or
`stat_bin()` being composed with geoms.

### Composition: Layers, Overlays, Facets

Multiple representations compose through explicit operators:

```python
scene = layer(
    # Volume rendering of the full field
    show(fire_data | rep_volume("temperature"),
         encode(color=temp_color, opacity=temp_opacity)),

    # Isosurface at the fire front
    show(fire_data | rep_isosurface("temperature", values=[800]),
         encode(color=(1.0, 0.4, 0.0), opacity=0.6, specular=0.5)),

    # Streamlines through the plume
    show(fire_data
         | derive_vector("velocity", ["u", "v", "w"])
         | rep_streamlines("velocity", seeds=near("temperature", [500, 2000])),
         encode(color=scale_color("velocity", colormap="wind"))),

    # Bounding box for reference
    show(fire_data | rep_outline(),
         encode(color=(1, 1, 1), opacity=0.3))
)

scene.view(camera=overview_camera, background=(0.02, 0.02, 0.05))
```

**Faceting** — showing the same data from multiple perspectives or with
different parameters — becomes a structural operation:

```python
# Compare two isosurface thresholds side by side
facet(
    show(fire_data | rep_isosurface("temperature", values=[500]),
         encode(color=temp_color)),
    show(fire_data | rep_isosurface("temperature", values=[800]),
         encode(color=temp_color)),
    layout="horizontal",
    shared_camera=True
)

# Same visualization from three angles
facet(
    scene,
    views=[front_camera, side_camera, top_camera],
    layout="row"
)
```

---

## Part 4: Comparative Examples

To make the difference concrete, here is the same visualization task
expressed in PyVista, current VisLang, and the proposed grammar.

### Task: Volume-render a CT scan with an isosurface overlay

**PyVista:**

```python
import pyvista as pv

reader = pv.read("bonsai.vti")

plotter = pv.Plotter()

# Volume rendering
plotter.add_volume(reader, scalars="density", cmap="terrain",
                   opacity="sigmoid_5", clim=[20, 200],
                   shade=True, show_scalar_bar=True,
                   scalar_bar_args={"title": "Density"})

# Isosurface overlay
contour = reader.contour([80], scalars="density")
plotter.add_mesh(contour, color="brown", opacity=0.3,
                 specular=0.5, specular_power=30)

# Bounding box
plotter.add_mesh(reader.outline(), color="white", opacity=0.2)

plotter.set_background(0.02, 0.02, 0.05)
plotter.camera_position = [(400, 400, 400), (128, 128, 64), (0, 0, 1)]
plotter.show()
```

Problems: visual encoding is scattered across keyword arguments in three
separate `add_*` calls. The volume opacity ("sigmoid_5") is a magic
string. No way to reuse the color mapping. Can't diff this meaningfully.
Can't safely re-execute without tearing down the plotter.

**Current VisLang DSL:**

```python
data = source("vtkXMLImageDataReader", FileName="bonsai.vti")

show(data, "volume", representation="Volume",
     color_by="density", scalar_range=(20, 200), lut="terrain",
     opacity_function=[(0,0),(18,0),(26,0.01),(36,0.05),(48,0.13),
                       (60,0.26),(80,0.48),(110,0.68),(150,0.84),(220,0.96)],
     gradient_opacity=True, shade=True, scalar_bar="Density")

iso = contour(input=data, ContourBy="density", Isosurfaces=[80])
show(iso, "bone", color=(0.6, 0.4, 0.2), opacity=0.3,
     specular=0.5, specular_power=30)

box = outline(input=data)
show(box, "bbox", color=(1, 1, 1), opacity=0.2)

camera(position=(400, 400, 400), focal_point=(128, 128, 64))
background(0.02, 0.02, 0.05)
```

Better: declarative, safely re-executable, version-controlled. But
`show()` is still a keyword-argument dump. The opacity transfer function
is a raw list of tuples. VTK class names leak through (`vtkXMLImageDataReader`).

**Proposed GoG-inspired grammar:**

```python
bonsai = data("bonsai.vti")

density_color = scale_color("density", range=[20, 200], colormap="terrain")

scene = layer(
    show(bonsai | rep_volume("density"),
         encode(color=density_color,
                opacity=scale_opacity("density", preset="sigmoid"),
                shade=True,
                legend="Density")),

    show(bonsai | rep_isosurface("density", at=80),
         encode(color=(0.6, 0.4, 0.2), opacity=0.3, specular=0.5)),

    show(bonsai | rep_outline(),
         encode(color=(1, 1, 1), opacity=0.2)),
)

scene.view(
    camera=(400, 400, 400),
    look_at=(128, 128, 64),
    background=(0.02, 0.02, 0.05)
)
```

Key improvements:
- `density_color` is defined once, shared across representations
- `scale_opacity` is a first-class object, not a list of tuples
- `rep_volume`, `rep_isosurface`, `rep_outline` are declarative intent,
  not VTK class names
- `layer(...)` makes the scene structure explicit
- No VTK class names leak through — `data("bonsai.vti")` infers the reader
- Separable concerns: data, transforms, representations, encodings, view

### Task: Fire simulation with streamlines and threshold

**Current VisLang DSL:**

```python
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")

# Threshold to hot region
hot = threshold(input=data, ThresholdBy="theta", ThresholdRange=[500, 2000])
show(hot, "fire", representation="Volume", color_by="theta",
     scalar_range=(500, 2000), lut="hot",
     opacity_function=[(500,0),(800,0.05),(1200,0.3),(2000,0.6)],
     gradient_opacity=True, shade=True, scalar_bar="Theta (K)")

# Streamlines
vel = make_vector(input=data, components=("u", "v", "w"), result="velocity")
seeds = seeds_near(input=data, field="theta", min_val=500, max_val=2000,
                   num_seeds=40)
streams = stream_tracer(input=vel, SeedSource=seeds, Vectors="velocity",
                        IntegrationDirection="Both", MaximumNumberOfSteps=2000)
tubes = tube(input=streams, Radius=1.5, NumberOfSides=8)
show(tubes, "flow", color_by="velocity", opacity=0.8,
     scalar_range=(0, 30), lut="wind", scalar_bar="Wind (m/s)")

# Terrain
terrain = extract_grid(input=data, VOI=[251, 850, 0, 499, 0, 0])
show(terrain, "ground", color_by="theta", scalar_range=(290, 400),
     lut="terrain")

background(0.02, 0.02, 0.05)
```

**Proposed GoG-inspired grammar:**

```python
fire = data("output.30000.vts")

theta_color = scale_color("theta", range=[500, 2000], colormap="hot")

scene = layer(
    # Volume rendering of the fire
    show(fire
         | where("theta", between=[500, 2000])
         | rep_volume("theta"),
         encode(color=theta_color,
                opacity=scale_opacity("theta", [(500,0),(800,0.05),(1200,0.3),(2000,0.6)]),
                shade=True, legend="Theta (K)")),

    # Streamlines through the plume
    show(fire
         | derive("velocity", from_components=["u", "v", "w"])
         | rep_streamlines("velocity",
                           seeds=near("theta", [500, 2000], n=40),
                           tube_radius=1.5),
         encode(color=scale_color("velocity", [0, 30], "wind"),
                opacity=0.8, legend="Wind (m/s)")),

    # Terrain surface
    show(fire
         | slice_grid(k=0)
         | rep_surface(),
         encode(color=scale_color("theta", [290, 400], "terrain"))),
)

scene.view(background=(0.02, 0.02, 0.05))
```

The grammar version is shorter, but more importantly it is *structurally*
clearer. You can see at a glance: three layers, each with its own
data→transform→representation→encoding pipeline. The composition is
explicit (`layer(...)`). The encoding objects are inspectable and
independent.

---

## Part 5: Implementation Feasibility on VTK

The grammar sits on top of VTK as a translation layer. Each grammar
element maps to VTK operations:

### Data → VTK readers

`data("file.vts")` infers the reader from the file extension, exactly
as VisLang's current `load()` tool does. The `data()` function returns
a lazy reference; the VTK reader is created and cached when the pipeline
executes.

### Transforms → VTK filters

Each transform maps to one or more VTK filter classes:

| Grammar transform | VTK implementation |
|---|---|
| `where(field, between=[lo, hi])` | `vtkThreshold` |
| `derive("velocity", from_components=["u","v","w"])` | `vtkArrayCalculator` |
| `gradient(field)` | `vtkGradientFilter` |
| `slice_grid(k=0)` | `vtkExtractGrid` with VOI |
| `clip(normal, origin)` | `vtkClipDataSet` + `vtkPlane` |
| `smooth(iterations=20)` | `vtkWindowedSincPolyDataFilter` |
| `subsample(every_nth=20)` | `vtkMaskPoints` |

This is the same mapping that `dsl.py` already performs. The grammar just
provides a more intent-oriented vocabulary.

### Representations → VTK pipelines

Each `rep_*` function constructs a small VTK pipeline:

- `rep_isosurface(field, at=[...])` → `vtkContourFilter` → `vtkPolyDataMapper`
  → `vtkActor`
- `rep_volume(field)` → (optional `vtkResampleToImage`) →
  `vtkSmartVolumeMapper` → `vtkVolume`
- `rep_streamlines(field, seeds=...)` → `vtkStreamTracer` →
  `vtkTubeFilter` → `vtkPolyDataMapper` → `vtkActor`
- `rep_glyphs(field, shape=...)` → `vtkGlyph3D` → `vtkPolyDataMapper`
  → `vtkActor`
- `rep_slice(normal, origin)` → `vtkCutter` → `vtkPolyDataMapper` →
  `vtkActor`
- `rep_surface()` → `vtkDataSetSurfaceFilter` → `vtkPolyDataMapper`
  → `vtkActor`
- `rep_outline()` → `vtkOutlineFilter` → `vtkPolyDataMapper` → `vtkActor`

The grammar layer constructs the VTK pipeline objects, connects them,
sets properties, and hands off to VTK for execution. This is exactly what
VisLang's `PipelineBuilder.build_pipeline()` already does — the grammar
just provides a different user-facing API for the same machinery.

### Encodings → VTK mapper/actor/property configuration

`encode(color=..., opacity=..., specular=...)` translates to:
- Color scale → `vtkLookupTable` configuration on the mapper
- Opacity (constant) → `actor.GetProperty().SetOpacity()`
- Opacity (transfer function) → `vtkPiecewiseFunction` on the volume property
- Specular → `actor.GetProperty().SetSpecular()`
- Legend → `vtkScalarBarActor`

Again, this is what `create_show()` in `filters.py` already implements.

### Composition → VTK renderer management

`layer(...)` adds all actors to a single renderer. `facet(...)` creates
multiple renderers in a single render window (VTK supports viewport
subdivision natively via `vtkRenderer.SetViewport()`). `overlay(...)` is
semantically identical to `layer()` but could control z-ordering.

### The pipe operator

The `|` pipe chains transforms. Implementation-wise, each transform
returns a lazy specification object (analogous to the current `NodeRef`).
The `rep_*` function consumes the specification and builds the VTK
pipeline. Nothing executes until the scene is rendered — this preserves
the declare-then-build model that makes re-execution safe.

### Verdict: Fully feasible

Every proposed grammar element maps directly to existing VTK functionality.
The translation layer is a modest amount of Python — comparable in
complexity to what `dsl.py` + `filters.py` already implement. The grammar
does not require new VTK capabilities; it is a different organization of
the same underlying operations.

---

## Part 6: Where Would This Be a Significant Improvement?

### Dimensions of improvement over PyVista

| Dimension | PyVista | GoG-3D Grammar | Advantage |
|---|---|---|---|
| **Encoding reuse** | Repeated kwargs per add_mesh | First-class encoding objects | Define once, apply to many layers |
| **Scene structure** | Implicit (call order) | Explicit (layer/facet/overlay) | Readable, diffable, composable |
| **Transfer functions** | Magic strings or raw arrays | Structured scale objects | Inspectable, serializable, diffable |
| **Reproducibility** | Requires discipline | By construction (declarative) | Same spec = same viz, always |
| **Safe re-execution** | Risky (stateful plotter) | Safe (desired-state, tear-down/rebuild) | Enables hot reload, versioning |
| **Error diagnostics** | VTK silent failures leak through | Grammar-level validation | "field 'Temp' not found, did you mean 'Temperature'?" |
| **VTK abstraction** | Intentionally leaky | Opaque (VTK is implementation detail) | Users express intent, not VTK class names |
| **Diffing** | Diff shows changed kwargs | Diff shows changed encoding/representation | Meaningful version control |
| **Multi-panel** | Grid-based subplots, manually managed | `facet()` operator | Structural, camera-aware |
| **AI writability** | Must know PyVista API + VTK details | Intent-oriented vocabulary | LLM says "isosurface at 800" not "vtkContourFilter" |

### Dimensions of improvement over current VisLang DSL

The current DSL already gets declarativeness, safe re-execution, error
diagnostics, and named nodes right. The GoG grammar would improve:

1. **Encoding separation** — the biggest win. `show()` stops being a
   kitchen sink.
2. **Intent-oriented vocabulary** — `where()` instead of `threshold()`,
   `rep_isosurface()` instead of `contour()`, `derive()` instead of
   `make_vector()`. The vocabulary shifts from "what VTK filter to run"
   to "what I want to see."
3. **Compositional scene structure** — `layer()` and `facet()` make
   multi-representation scenes structurally explicit.
4. **Transfer functions as first-class objects** — instead of raw tuple
   lists buried in show() kwargs.
5. **Pipe operator for transforms** — the `data | where(...) | rep_*()`
   syntax makes the data flow visually clear, vs. the current
   `threshold(input=vel, ThresholdBy=..., ThresholdRange=...)` which
   requires reading keyword arguments to understand the flow.

### What would NOT improve

- **Rendering quality** — same VTK backend, same output.
- **Performance** — same filter execution, same caching opportunities.
- **Data format support** — same VTK readers.
- **Interactive manipulation** — same VTK interactor.

The improvements are in **expressiveness, composability, readability,
and the quality of the human↔AI conversation**. These are the dimensions
that matter for VisLang's mission of collaborative scientific visualization.

---

## Part 7: How This Fits Into VisLang

### The grammar as the project's core contribution

VisLang's VISION.md already articulates the key insight: VisLang is a
**programming system**, not just a language. The pipeline file is a
communication medium between human and AI. The DSL must be readable
enough for a scientist to audit and writable enough for an LLM to produce
correctly.

A GoG-inspired grammar is a better fit for this mission than either the
current VTK-shaped DSL or raw PyVista:

- **For the scientist (human reader):** `where("theta", between=[500, 2000])`
  is clearer than `threshold(input=data, ThresholdBy="theta",
  ThresholdRange=[500, 2000])`. The vocabulary matches scientific intent
  ("where is temperature above 500?") rather than implementation mechanism
  ("apply a threshold filter").
- **For the AI (writer):** the grammar's structure constrains the space
  of valid programs. An LLM can compose `data | transform | rep | encode`
  more reliably than it can manage 15+ keyword arguments on `show()`.
  Encoding separation means the LLM can change color without touching
  the rest of the pipeline.
- **For the system (execution engine):** the grammar's declarative,
  desired-state semantics enable everything VisLang already does well —
  safe re-execution, versioning, diffing, hot reload. The grammar is
  a better surface syntax for the same execution model.

### Relationship to existing work

The grammar would replace `dsl.py`'s `PipelineBuilder` API, not the
underlying VTK execution machinery in `filters.py`. The MCP tools, query
layer, error diagnostics, and renderer infrastructure all remain. The
grammar is a new user-facing syntax that compiles down to the same VTK
operations.

The `tracked-execution` experiment's content-addressed caching could
layer underneath the grammar — the pipe chain
`data | where(...) | rep_isosurface(...)` naturally produces a DAG of
operations that can be hashed and cached.

### Research positioning

A unified grammar of 3D scientific visualization — covering volume
rendering, isosurfaces, streamlines, glyphs, and slicing in a single
compositional framework — is an **open research problem**. The Shih et al.
2019 paper covers volume rendering only. ggrgl covers statistical 3D
only. No one has done the full range.

VisLang could be the system that demonstrates this grammar. The research
contribution would be: (1) the grammar design itself, (2) evidence that
it enables better human-AI collaborative visualization than existing
approaches, (3) the VTK translation layer as proof of implementability.

### Risk: Over-abstraction

The biggest risk is designing a grammar that is elegant in theory but
awkward in practice. ggplot2 succeeded because Wickham iterated the
design against real data analysis workflows. The grammar proposed here
needs the same empirical grounding — design it, try to express real
visualizations with it, discover where it's awkward, and refine.

Concrete risks:
- **The pipe syntax may feel foreign** to scientists used to imperative
  Python. Mitigation: the grammar is still Python, and imperative
  fallbacks (assigning intermediate results to variables) still work.
- **Some VTK operations don't fit neatly** into the transform/rep split.
  Mitigation: a `vtk_escape()` function (as in tracked-execution) for
  raw VTK access.
- **The vocabulary may obscure** what VTK is doing, making debugging
  harder. Mitigation: structured error diagnostics that map grammar
  operations back to VTK state (VisLang already does this).

---

## Summary

**Yes, a GoG-inspired 3D grammar could be a significant improvement over
PyVista and over VisLang's current DSL.** The improvement is not in
rendering quality or performance (same VTK backend) but in
**expressiveness, composability, readability, and the quality of human↔AI
collaboration** — which are exactly the dimensions that matter for
VisLang's mission.

The core ideas:
1. **Representations replace geoms** — `rep_volume`, `rep_isosurface`,
   `rep_streamlines` as geometry generators for field data
2. **Encodings as first-class objects** — separate visual mapping from
   what it applies to
3. **Scales for transfer functions** — `scale_opacity`, `scale_color` as
   inspectable, composable objects
4. **Pipe operator for transforms** — `data | where(...) | rep_*(...)`
   makes data flow explicit
5. **Composition operators** — `layer()`, `facet()` for structured scenes

This is implementable on VTK today with modest engineering effort. It is
also an open research contribution — no existing system covers the full
range of 3D scientific visualization with a unified compositional grammar.

The next step would be to implement the grammar for a small set of
representations (volume, isosurface, surface, outline) and test it against
real visualization tasks from VisLang's session history.
