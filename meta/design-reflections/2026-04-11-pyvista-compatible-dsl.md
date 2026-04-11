# PyVista-Compatible DSL with Custom Interpreter

*Design reflection — April 11, 2026*

*This document records the conclusions of a design conversation about
whether the VisLang pipeline language needs to depart from PyVista
syntax, or whether a PyVista-compatible surface can support the full
workspace/compiler/DAG architecture described in the companion
reflections (`2026-04-11-dag-as-ir.md`,
`2026-04-11-declarative-spec-workspace.md`). The conclusion is that
PyVista-compatible syntax is sufficient, provided we own the
interpreter and add a small set of additive extensions. This
supersedes the earlier assumption that a meaningfully new language
would be required.*

*Status: design decision. This is the intended direction for the
pipeline authoring surface.*

## The question this document answers

The DAG-as-IR and declarative-spec reflections established that the
pipeline file should be a pure DAG constructor — no side effects, no
eager execution, no data-dependent branching on computed values. The
open question was whether this contract requires a new authoring
syntax, or whether the syntax can remain compatible with PyVista.

The concern was that PyVista's API is imperative, eager, and relies
on mutable state, and that a restricted-Python dialect that looks
like PyVista but behaves differently would be "subtly broken" —
tripping up agents trained on real PyVista code and producing
confusing failures at the boundary of the supported subset.

## The key decision: own the interpreter

The resolution is to build a **custom interpreter** (or compiler)
for a Python+PyVista+NumPy subset, rather than running real Python
with proxy objects.

The difference is profound:

- **Proxy objects in real Python:** `np.linspace(lazy_lo, lazy_hi, 5)`
  calls real NumPy with proxy objects, producing a confusing TypeError.
  The system can't intercept the failure because NumPy's code runs
  first.

- **Custom interpreter:** `np.linspace(lo, hi, 5)` is intercepted by
  our implementation of `linspace`, which takes symbolic endpoints and
  produces a symbolic array DAG node. Same syntax, correct semantics.

With a custom interpreter, every operation either (a) works correctly
as DAG construction, or (b) produces a clear, actionable error message
at the exact line where the unsupported pattern appears. There is no
"subtle breakage" category — every boundary is loud.

## What transfers from PyVista unchanged

### Filter chains

The filter vocabulary — method names, argument signatures, chaining
pattern — maps directly to DAG node construction:

```python
mesh.threshold(500, scalars="temperature")
mesh.contour([800, 1200], scalars="temperature")
mesh.clip(normal="x")
mesh.streamlines(vectors="velocity", source_radius=2.0)
mesh.slice(normal="z", origin=(0, 0, 0))
mesh.extract_surface()
```

Each call records a DAG node; the return value is a handle that
accepts further method calls. This is the largest part of the API
surface and the strongest source of agent fluency from training data.

### Computed properties flowing into operations

Because the interpreter controls evaluation, computed properties work
as DAG nodes without proxy magic:

```python
center = mesh.center                    # symbolic node, not a tuple
clip = mesh.clip(origin=center)         # DAG edge from center to clip
lo, hi = mesh.get_data_range("temperature")  # symbolic unpacking
iso = mesh.contour(np.linspace(lo, hi, 5), scalars="temperature")
```

In real Python with proxies, `np.linspace(lo, hi, 5)` would fail
because NumPy doesn't understand proxy objects. In the custom
interpreter, our `np.linspace` takes symbolic inputs and produces a
symbolic array node. The DAG correctly captures the data flow.

### Plotter accumulation pattern

PyVista's Plotter API looks stateful but is mostly just accumulating
a scene description:

```python
p = Plotter()
p.add_mesh(iso, cmap="hot", clim=temp_range, opacity=0.6)
p.add_mesh(terrain, cmap="terrain")
p.camera_position = "xz"
p.background_color = (0.02, 0.02, 0.05)
p.show()
```

The interpreter treats `Plotter()` as starting a scene builder,
`add_mesh` as adding a layer node, property assignments as view
configuration, and `show()` as the terminal node. The DAG it produces
is structurally a declarative scene description written in imperative
syntax.

The genuinely stateful Plotter operations (`remove_actor`,
`update_scalars`, callbacks) are rare in visualization pipelines and
produce clear errors: "remove_actor is not supported in pipeline
files; the scene is built additively."

### NumPy subset

A bounded set of NumPy operations work as symbolic DAG construction:
arithmetic, `linspace`, `arange`, `cross`, `dot`, basic linalg,
array indexing. These cover the common patterns in visualization code
(computing derived values, generating parameter ranges, basic vector
math).

## Additive extensions to PyVista

These are new concepts that PyVista doesn't have, expressed as
additions that compose cleanly with the existing API rather than
modifications to it.

### Dataset handles and parameters

```python
fire = dataset("fire_sim")
t = param("timestep", over=fire.timesteps, default=50)
mesh = fire.at(t)
```

`dataset()` and `param()` are new. `fire.at(t)` produces a mesh
handle that supports the standard PyVista filter vocabulary. The
parameter `t` is a DAG node that the compiler can sweep over, bind
to a scrubber, or fix at a value.

### Dataset-level stats and derived fields

```python
temp_range = fire.field("temperature").percentile([5, 95])
iso_values = fire.field("temperature").suggest_isosurfaces(n=3)
vort = fire.derive("vorticity", from_="velocity", method="curl")
vort_range = vort.percentile([5, 95])
```

`fire.field(...)` returns a dataset-level field handle with stats
methods. `fire.derive(...)` declares a derived field the workspace
manages. These are new objects, not modifications to PyVista's mesh
API.

### Custom per-frame recipes

```python
seeds = fire.at(t).apply(seed_placement_fn)
mesh.streamlines(vectors="velocity", seeds=seeds)
```

`apply()` wraps a function as a per-frame recipe the workspace runs
and caches. The function body is Python+NumPy that runs eagerly
inside the `apply` context — a structured escape hatch with clear
inputs and outputs.

### Groups

```python
fire_group = p.group("fire")
fire_group.add_mesh(flame, cmap="hot", clim=temp_range, opacity=0.6)
fire_group.add_mesh(fire_vol, cmap="hot", clim=temp_range)
fire_group.opacity = when(t > 10, 1.0, 0.0)

p.add_mesh(terrain, cmap="terrain")
```

`group()` returns a sub-plotter that supports `add_mesh` with the
same signature as `Plotter`. Groups carry group-level properties
(opacity, visibility). The `when()` function is a symbolic
conditional that becomes a DAG node.

### Faceting

```python
member = param("member", over=fire.members)
facets = p.facet(by=member, layout="auto")
facets.add_mesh(flame, cmap="hot", clim=temp_range)
facets.add_mesh(terrain, cmap="terrain")
```

`facet()` returns a sub-plotter. Everything added to it is replicated
across facet panels with the faceting variable varied. The compiler
sees the facet node and plans a sweep across the faceting parameter.

### Linked views

```python
p = Plotter(shape=(1, 2))
left = p.subplot(0, 0)
right = p.subplot(0, 1)
p.link_views()  # already PyVista API

left.add_mesh(overview_mesh, cmap="hot", clim=temp_range)
right.add_mesh(detail_mesh, cmap="hot", clim=temp_range)
```

`Plotter(shape=...)`, `subplot()`, and `link_views()` already exist
in PyVista. No new API needed.

### Animation

```python
p.show(animate_over=t)
```

One new keyword argument on the terminal `show()` call.

## Scope follows from the DAG

A critical design decision that emerged from the conversation: the
scope of any data-dependent computation (stats query, derived field,
spatial selection) is **implicit in its DAG dependencies**, not
declared with annotations like `extent="global"`.

The rule: if a computation depends (transitively in the DAG) on a
parameter node, it varies with that parameter. If it depends only on
dataset-level nodes, it's global.

```python
# Global: operates on dataset-level field handle, no parameter dependency
temp_range = fire.field("temperature").percentile([5, 95])

# Per-frame: operates on a timestep-specific mesh (depends on t)
mesh = fire.at(t)
local_range = mesh.get_data_range("temperature")
```

The compiler reads the DAG to determine scope:

- **Global-scoped nodes** are served from the stats DB if available,
  or approximated from the current frame during interactive work with
  a background job queued for the global computation.

- **Per-frame-scoped nodes** are computed per-frame and cached in the
  feature DB.

- **The compiler escalates approximations.** Moving from interactive
  exploration to animation/commit triggers the compiler to resolve
  any remaining global approximations at their correct scope.

This eliminates the need for:
- `extent="global"` vs `extent="local"` annotations on scales
- The April 10 doc's `ws.extract.*` namespace (cost-in-the-name)
- The `set_working_subset` ceremony
- `StatValue.source` checks by the author

The scope is in the DAG. The compiler reads it. The plan report
surfaces approximation decisions. The author writes intent; the
system handles scope.

### The default is always global

The scientist always *wants* the global answer — the percentile
across all timesteps, the isosurface values from the full
distribution, the color range that's consistent across animation
frames. Per-frame scope is only correct when the input is explicitly
per-frame (e.g., adaptive seed placement that should follow a moving
feature).

During interactive exploration of a single frame, the compiler may
serve a local approximation for a global-scoped value. This is
acceptable because:
- The approximation is reported in the plan
- The global computation runs in the background
- When the author commits or animates, the global answer is used

The rare case where per-frame scope is intentional (per-frame
normalization for relative comparison) works naturally: the author
operates on `fire.at(t)` rather than `fire.field(...)`, and the
compiler sees the parameter dependency.

## Full example: fire simulation pipeline

```python
# Dataset and parameters
fire = dataset("fire_sim")
t = param("timestep", over=fire.timesteps, default=50)

# Dataset-level derived field and stats (global scope)
vort = fire.derive("vorticity", from_="velocity", method="curl")
temp_range = fire.field("temperature").percentile([5, 95])
iso_values = fire.field("temperature").suggest_isosurfaces(n=3)
vort_range = vort.percentile([5, 95])

# Per-frame mesh and filters (PyVista-compatible)
mesh = fire.at(t)
flame = mesh.contour(iso_values, scalars="temperature")
plume = mesh.streamlines(vectors="velocity", source_radius=2.0)
terrain = mesh.slice(normal="z", origin=(0, 0, 0))
vol = mesh.threshold([500, None], scalars="temperature")

# Scene composition (PyVista Plotter with additive extensions)
p = Plotter()

fire_group = p.group("fire")
fire_group.add_mesh(flame, cmap="hot", clim=temp_range, opacity=0.6)
fire_group.add_mesh(vol, cmap="hot", clim=temp_range, opacity=0.3)

p.add_mesh(plume, cmap="coolwarm", clim=vort_range)
p.add_mesh(terrain, cmap="terrain", clim=[290, 400])
p.add_mesh(mesh.outline(), color="white", opacity=0.2)

p.camera_position = "xz"
p.background_color = (0.02, 0.02, 0.05)
p.show(animate_over=t)
```

Points to notice:
- The filter chains (`contour`, `streamlines`, `slice`, `threshold`,
  `outline`) are standard PyVista.
- The Plotter pattern (`add_mesh`, `camera_position`, `show`) is
  standard PyVista.
- The additive vocabulary (`dataset`, `param`, `derive`, `field`,
  `percentile`, `suggest_isosurfaces`, `group`, `animate_over`) is
  small and self-contained.
- Scope is implicit: `temp_range` and `iso_values` are global because
  they depend on `fire.field(...)`. The filter chains are per-frame
  because they depend on `mesh = fire.at(t)`.
- No workspace management, no explicit scheduling, no scope
  annotations.

## The interpreter boundary

The custom interpreter supports a bounded subset. At the boundary,
errors are clear and actionable:

- **Unsupported library:** "scipy.ndimage.gaussian_filter is not
  available in pipeline files. For smoothing, use mesh.smooth()."
- **Data-dependent branching:** "Line 15: if mesh.n_points > 1000000
  — cannot branch on a computed value. Use param('decimate',
  default=False) to make this a parameter, or wrap the logic in
  apply()."
- **Stateful Plotter operation:** "remove_actor is not supported.
  Scenes are built additively in pipeline files."
- **Unsupported NumPy:** "np.fft.fft is not in the supported NumPy
  subset. Use apply() for complex array operations."

Because the interpreter controls all evaluation, every boundary
produces a precise error at the offending line with a concrete
suggestion for what to do instead. There is no "subtle breakage"
where code silently does the wrong thing.

## What the interpreter must implement

The implementation work is bounded:

**Python subset:** variables, assignment, arithmetic, comparison,
boolean operators, `for` loops over static/manifest-resolvable
collections, `if`/`else` on manifest-resolvable values (with clear
errors on symbolic values), function definitions and calls, f-strings,
tuple/list/dict construction, unpacking, slicing.

**PyVista subset:** filter methods (whitelist-based, extending
tracked-execution's existing whitelist), mesh properties (`center`,
`bounds`, `n_points`, `array_names`, `get_data_range`), Plotter
(`add_mesh`, `camera_position`, `background_color`, `show`,
`subplot`, `link_views`).

**NumPy subset:** arithmetic, `linspace`, `arange`, `zeros`, `ones`,
`cross`, `dot`, `linalg.norm`, indexing, slicing, basic reductions
(`mean`, `max`, `min`, `sum`, `percentile`).

**Additive vocabulary:** `dataset`, `param`, `derive`, `apply`,
`group`, `facet`, `when`, dataset-level field handles with stats
methods.

## Relationship to prior reflections

This document resolves the syntax question that was left open in the
DAG-as-IR and declarative-spec reflections. Those documents
established the architectural layering (spec → DAG → compiler → plan
→ runtime) and left the authoring surface as a front-end concern.
This document settles the front-end: PyVista-compatible syntax with
additive extensions, executed by a custom interpreter.

The April 10 workspace design's infrastructure (manifest, stats DB,
pyramid, feature DB, sweep records, cache) carries forward unchanged.
The April 10 upper vocabulary (`ws.extract.*`,
`set_working_subset`, `StatValue.source`) is replaced by the
implicit-scope model: the DAG encodes scope through data
dependencies, and the compiler handles scheduling without author
participation.

The April 10 design remains a valid fallback if the custom
interpreter proves too costly to build. But the scope-from-DAG
insight significantly reduces the gap between the two approaches —
the compiler's job is simpler when scope is structural rather than
inferred from naming conventions.

## Risks

### Interpreter implementation cost

Building a custom interpreter for a Python+PyVista+NumPy subset is
real engineering work. The subset is bounded, but correctness matters:
every supported operation must produce semantically correct DAG nodes.
Bugs in the interpreter produce wrong DAGs silently, which is worse
than crashes.

Mitigation: start with tracked-execution's existing whitelist and
proxy infrastructure. The interpreter is an evolution of the existing
dispatch layer, not a from-scratch build.

### PyVista API inconsistencies

PyVista grew organically. Some filters return `UnstructuredGrid`,
others `PolyData`. Some arguments are `scalars=`, others `vectors=`.
Some operations have implicit state dependencies (active scalars).
The interpreter inherits these inconsistencies or diverges from the
training data.

Mitigation: reproduce PyVista's signatures faithfully and add purity
checks for the implicit-state cases (require explicit `scalars=`
argument, warn on operations that depend on active scalars).

### The "almost compatible" agent behavior

Agents trained on PyVista will occasionally reach for patterns outside
the supported subset. The failure rate depends on how large the subset
is relative to common PyVista usage.

Mitigation: good error messages at the boundary, plus guidance in the
agent's system prompt about what patterns to use and avoid. The
exploration MCP tool (running real Python for data queries) handles
the "figure out what to visualize" phase; the pipeline file handles
the "express the visualization" phase. This separation is already
in place.

### VTK evolution

PyVista wraps VTK, which evolves. Committing to PyVista syntax means
tracking its API evolution or accepting divergence over time.

Mitigation: the supported subset is bounded and stable. Core filter
methods rarely change signatures. New filters can be added to the
whitelist incrementally.

## What this is and is not

### What this is

- **A decision on authoring syntax.** PyVista-compatible, with
  additive extensions, executed by a custom interpreter.
- **A resolution of the "new language" question.** The language is
  not new; the implementation is. Agents write PyVista; the
  interpreter gives it DAG-construction semantics.
- **A design for scope inference.** Scope follows from DAG
  dependencies, not from annotations or naming conventions. Global
  is the default desire; per-frame scope requires explicit per-frame
  input.
- **Compatible with the full DAG/compiler/workspace architecture.**
  Nothing in this decision limits the compiler's sophistication or
  the workspace's capabilities.

### What this is not

- **Not a detailed interpreter specification.** The exact Python
  subset, the NumPy coverage, and the error message catalog need
  further design.
- **Not a commitment to implement everything at once.** The
  interpreter can start minimal (filter chains + Plotter + basic
  NumPy) and grow the supported subset based on what agents
  actually write.
- **Not validated by implementation.** The argument is sound on
  paper; prototyping the interpreter against real examples will
  surface issues this analysis missed.
