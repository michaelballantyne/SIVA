# Grammar-of-Graphics Ideas Within a PyVista Approach

*Design reflection — April 10, 2026*

*This supersedes the earlier `2026-04-10-grammar-of-graphics-3d.md` draft,
which over-emphasized a custom-DSL framing that did not survive
skeptical examination. The earlier doc is kept as a record of the
exploration, not as a recommendation.*

## Framing

VisLang's short-term plan is to adopt PyVista, restricted to a safe /
pure subset (in the spirit of `experiments/tracked-execution/`). The
question this document addresses is the longer-term one: **are there
benefits from grammar-of-graphics thinking — as embodied by ggplot2,
Vega, and Vega-Lite — that would justify building a custom DSL on top of
VTK eventually? And which of those benefits can we achieve within a
PyVista-subset approach without leaving the substrate?**

The short answer, after working through the design space:

- **Most of what looks like a "grammar of graphics" argument is actually
  achievable within a PyVista-subset approach**, with conventions,
  helper libraries, and AST-level tooling. Spec-as-data, stats,
  encodings-as-objects, orthogonal introspection, compiler reasoning
  about derivations, expression sublanguages, faceting, and escape
  hatches all have clean PyVista-compatible realizations.
- **Two or three ideas genuinely need a grammar layer** to work
  cleanly. These are the ones worth holding open as long-term options.
  They are: **parameters as first-class grammar elements**, **semantic
  field types driving defaults**, and (to a lesser extent) **global
  scales as a structural property**.
- **The biggest wins VisLang can deliver are in the surrounding
  system** — query layer, ACI/LSP, hot reload, tracked execution,
  bidirectional editing — and none of these depend on whether the
  authoring surface is a custom DSL or Python-over-PyVista.

The rest of this document explains what survived, what didn't, and what
to do in each case.

---

## Part 1 — Ideas That Genuinely Need a Grammar Layer

These are the ideas where "just do it in PyVista with helpers" does not
quite reach. They are the surviving case for a long-term custom DSL.

### 1.1 Parameters as first-class grammar elements

**The Vega-Lite pattern.** Vega-Lite has a `params` block that declares
named, bindable, tweakable values referenced by encodings and transforms:

```json
"params": [{"name": "threshold", "value": 500,
            "bind": {"input": "range", "min": 0, "max": 2000}}],
"transform": [{"filter": "datum.temp > threshold"}]
```

The param is a grammar-level noun. The system knows it exists, knows its
range, can auto-bind a slider, and can update just the param's value
without rewriting the rest of the spec.

**Why this matters for VisLang.** Two vision-section features become
dramatically easier when parameters are a grammar concept:

- **Bidirectional editing** (drag a clip plane in the 3D view → the
  corresponding code literal updates). In a method-call world, the
  system has to do AST-level source rewriting to find the right literal
  and change it. In a grammar with named params, a drag is a **param
  value update** — the spec structure doesn't change, just one field of
  a param object. This dissolves the hardest part of bidirectional
  editing (Sketch-n-Sketch's "which literal to rewrite?" problem).
- **Parameter scrubbing** and the Tanimoto-level-4 "continuous
  refinement" aspiration. The system can only auto-generate controls
  for things it knows are parameters. A grammar with explicit params
  tells the system exactly what's tunable; imperative code has to be
  heuristically analyzed.

**Why PyVista can't cleanly absorb this.** You can simulate it —
"declare your tunables at the top of the file as variables, then
reference them" — but nothing in PyVista's API or Python itself
distinguishes a tunable from any other variable. The LSP can't tell
which identifiers are meant to become sliders; the bidirectional editor
can't tell which literals are meant to be bound to visual widgets. You
can add a convention (`TUNE_threshold = 500` or `param("threshold",
500)`), but at that point you're building a small DSL on the side, just
with awkward syntax.

**When this becomes worth building.** When bidirectional editing
becomes a priority and the AST-rewrite approach starts feeling too
fragile. Or when users start asking for parameter sweeps and sliders
frequently enough that ad-hoc solutions become unmaintainable. Until
then, it's fine to leave parameters as ordinary Python variables with
a documentation convention.

### 1.2 Semantic field types driving defaults

**The Vega-Lite pattern.** Vega-Lite's type system (nominal / ordinal /
quantitative / temporal) carries enormous weight for such a small
system: it drives default scales, mark defaults, axis formatting, and
legend styles. `"color": {"field": "species", "type": "nominal"}`
automatically picks a categorical palette. Change `type` to
`"quantitative"` and it switches to a continuous ramp.

**The 3D sci-viz analog is richer.** Scientific fields have semantic
types with strong default implications:

| Field type | Default implications |
|---|---|
| scalar, sequential (temperature) | sequential colormap, linear scale, histogram-derived range |
| scalar, diverging (anomaly, signed) | diverging colormap centered on zero |
| scalar, probability (0–1) | fixed [0,1] range, specific palette |
| vector (velocity) | default to magnitude, sequential colormap, auto-glyph option |
| tensor (stress) | eigenvalue decomposition or glyph-based default |
| categorical (material_id) | categorical palette, discrete legend, no interpolation |
| spatial (x, y, z) | identity mapping to position channels |
| temporal (time) | slider/animation default |

**Why this is a synergy with the query layer.** `describe_data` already
computes most of these inferences — field dimensionality, value range,
distribution shape, component count, sign. Today those inferences sit
*outside* the spec and get consumed via MCP tool calls or by pasting
suggested values. If a grammar knew field types, the same inferences
would be consumed by the compiler automatically: `show(mesh,
encode(color="temperature"))` without further arguments would get a
sensible default because the grammar asked `describe_data` what
temperature is.

This is the **one intelligence layer, multiple surfaces** story, done
right. MCP tools remain for interactive queries by the LLM; LSP hover
surfaces the same inferences for the human; the grammar consumes them
as defaults at build time. Three surfaces, one implementation.

**Why PyVista can't cleanly absorb this.** `add_mesh(mesh,
scalars="temperature")` with no other arguments does *some* default
inference (uses the array's range), but it's limited and inconsistent
— the defaults don't know the difference between a signed anomaly field
and a sequential temperature field. You could wrap `add_mesh` in a
helper that consults `describe_data` and picks defaults, which is
probably the right near-term move, but that helper is a small DSL: it
has to understand the field type system and make choices. You don't
escape the type system by hiding it in a helper.

**The small-layer version that gets you 70%.** Build a
`vislang.encoding` helper that takes a mesh + field name, consults a
type inference function, and returns a dict of kwargs to splat into
`add_mesh`:

```python
enc = vislang.encoding(mesh, color="temperature")  # returns {"scalars": "temperature", "cmap": "viridis", "clim": [487, 1820], "scalar_bar_args": {...}}
plotter.add_mesh(mesh, **enc)
```

This gets you sensible defaults and can grow a type-system internally.
The remaining 30% — defaults that span layers, or defaults that the LSP
needs to show without actually running the helper — is what a full
grammar would provide.

### 1.3 Global scales as a structural property

**The ggplot2 pattern.** A single `scale_color_viridis()` call applies
to every layer that maps data to color. You cannot forget to be
consistent — consistency is the default, and inconsistency is what you
have to ask for explicitly (via separate scales per layer). Add a
fourth layer, and it automatically gets the same color scale as the
first three.

**Why this matters for VisLang.** Multi-view and multi-layer
visualization is a common workflow (comparing temperature and density,
showing overview + closeup, side-by-side parameter sweeps). Cross-layer
color consistency is the difference between a publication-ready figure
and a confusing one. Today in PyVista, consistency is manual discipline
— every `add_mesh` call repeats `clim=..., cmap=...`, and it only
takes one forgotten argument for a layer to diverge.

**Why PyVista can partially absorb this.** A thin helper layer gives
you the ergonomic win without needing a DSL:

```python
temp_scale = vislang.SharedScale(mesh, "temperature", cmap="hot",
                                  clim=(500, 2000))

plotter.add_mesh(iso1, **temp_scale.apply())
plotter.add_mesh(iso2, **temp_scale.apply())
plotter.add_volume(vol, **temp_scale.apply())
```

This is fine — a dozen lines of Python. What you don't get, compared
to a grammar:

- **Enforcement.** A user can still forget to `.apply()` on a new layer
  and silently diverge. In a grammar with implicit global scales,
  omitting the scale means "use the global one"; you can't omit it and
  get something inconsistent.
- **Cross-layer inspection.** "Show me all layers that map color to
  temperature" is a structural query on a grammar. On a PyVista script,
  it's a textual search.
- **Late binding.** The grammar can lazily compute the range across
  all layers that use the scale, so multi-layer range normalization
  (single color scale covering the union of data ranges) is automatic.
  In the helper version, you thread the computation manually.

**When this becomes worth a grammar.** When cross-layer color-scale
bugs become a recurring complaint, or when multi-layer range
normalization is wanted frequently enough that the manual version feels
tedious. Until then, the `SharedScale` helper pattern is plenty.

---

## Summary: what's left for the grammar case

Three survivors from a long list of originally-proposed grammar
benefits:

1. **Params as first-class grammar elements** — for bidirectional
   editing and parameter scrubbing.
2. **Semantic field types driving defaults** — as a place for the
   query layer's type inference to plug in.
3. **Global scales as a structural property** — for multi-layer
   consistency without manual discipline.

Everything else I initially thought required a grammar turns out to be
achievable within a PyVista-subset approach. The rest of this document
walks through those and describes how to get them without leaving
PyVista.

---

## Part 2 — Ideas That Look Like They Need a Grammar But Don't

This is the important section for the short-term plan. Each of these
ideas is drawn from ggplot2 / Vega / Vega-Lite and initially looked
like it required a custom DSL. On closer examination, each has a clean
realization within a PyVista-subset approach that captures most of the
value.

### 2.1 Spec-as-data / structured editing / static checking

**What it looks like you need.** A JSON spec format with a JSON schema,
so that: LLMs can do structured edits, tools can validate before
execution, diffs are semantic, and the spec can travel to non-Python
environments.

**What you actually need.** An AST you can walk. Python already has
one. `ast.parse(source)` gives you a tree that is just as manipulable,
inspectable, and validatable as a JSON tree — with the small cost of
slightly less ergonomic point-edit operations.

**The PyVista-subset realization.** `experiments/tracked-execution/`
already has most of the machinery. The pattern is:

1. **Define a whitelist.** Which classes, methods, and top-level names
   are permitted in pipeline files. The rest are rejected at parse
   time with a clear diagnostic. This is the Python analog of "invalid
   per JSON schema."
2. **Enforce at the AST level.** Walk the AST before execution and
   check every `Call`, `Attribute`, and `Name` against the whitelist.
   Reject unsupported constructs (arbitrary imports, loops, mutation)
   with pointing diagnostics.
3. **Treat the AST as the canonical spec.** For diffs, info-view
   lookups, LSP operations, and rewrites, operate on the AST (not the
   text). Use `libcst` when you need to preserve comments and
   formatting during rewrites.
4. **Offer JSON export if needed.** A `spec_to_json(ast)` function
   that serializes a validated pipeline AST to a structured dict, for
   use cases where a Python-independent format matters (embed in a
   paper, web viewer, database). Not needed for anything else.

**What you get for free.**
- Pyright / Jedi / LSP already understand Python; you inherit
  autocomplete, hover, go-to-definition.
- Comments and named intermediate values (`hot_region = ...`) — which
  JSON cannot express — are available for the file-as-artifact story.
- Scientists read Python natively.
- Structural editing via `libcst` is slightly more work than JSON
  patches, but well-supported.

**Conclusion.** The "spec-as-data" argument collapses into "use a
restricted Python subset and operate on the AST." Tracked-execution
has proven this works. Adding a JSON export is an hour of code if a
specific need arises.

### 2.2 Stats as grammar elements / fewer magic constants

**What it looks like you need.** A grammar where `stat_suggest_iso()`,
`stat_percentile_range()`, etc. are first-class composable elements
that resolve lazily against data, so the spec is free of literal magic
numbers and auto-updates when data changes.

**What you actually need.** A stats module with well-named functions
and good caching. In a tracked-execution world, re-computing a stat is
free on cache hits, so "resolve lazily at build time" and "call the
function on every build" are observationally identical.

**The PyVista-subset realization.**

Build `vislang.stats` as a stable, documented module:

```python
# vislang/stats.py

def suggest_iso(mesh, field, n=3):
    """Return n histogram-peak-derived isosurface values."""
    ...

def percentile_range(mesh, field, lo=5, hi=95):
    """Return (low, high) bracketing the given percentiles."""
    ...

def auto_clim(mesh, field, robust=True):
    """Return a sensible color range for this field."""
    ...

def auto_opacity(mesh, field, format="control_points"):
    """Return a histogram-guided opacity transfer function."""
    ...
```

Use in pipeline files:

```python
mesh = pv.read("fire.vts")

hot_range = percentile_range(mesh, "temperature", 5, 95)
iso_vals = suggest_iso(mesh, "temperature", n=3)
iso = mesh.contour(iso_vals, scalars="temperature")

plotter.add_mesh(iso, scalars="temperature", clim=hot_range, cmap="hot")
```

No magic constants. Full provenance visible in the source. Cached by
tracked execution so there's no cost to calling these on every build.

**Optional enhancement: provenance-carrying wrapper objects.** For the
info view and LSP hover to show "this range was computed as the 5–95
percentile of temperature, currently [487.3, 1842.9]", the stat
functions can return a small wrapper:

```python
@dataclass
class StatValue:
    value: Any                 # the actual value (list, tuple, dict)
    computation: str           # "percentile_range('temperature', 5, 95)"
    resolved_on: str           # data hash or file path
    def __iter__(self): return iter(self.value)
    def __getitem__(self, i): return self.value[i]
    # etc — acts like the underlying value transparently
```

Now the info view can inspect any argument of an `add_mesh` call and,
if it's a `StatValue`, show the provenance. The grammar version would
do this automatically; this version does it by having the stat library
opt into the pattern. Small convention, large benefit.

**Conclusion.** "Fewer magic constants" wants a good stats library,
not a grammar. The library + tracked execution + optional provenance
wrappers delivers what you actually want.

### 2.3 Encodings as reusable objects

**What it looks like you need.** A first-class `encode(...)` object in
a grammar, separate from the data it applies to, so you can define a
mapping once and reuse it.

**What you actually need.** A dataclass or TypedDict that bundles the
display kwargs.

**The PyVista-subset realization.**

```python
# vislang/encoding.py
from dataclasses import dataclass, field, asdict

@dataclass
class Encoding:
    scalars: str | None = None
    clim: tuple | None = None
    cmap: str | None = None
    opacity: float | None = None
    show_scalar_bar: bool = False
    scalar_bar_args: dict = field(default_factory=dict)

    def kwargs(self, **overrides):
        """Return a dict suitable for add_mesh(**).

        Drops None values and merges overrides."""
        base = {k: v for k, v in asdict(self).items() if v is not None}
        base.update(overrides)
        return base
```

Usage:

```python
fire = Encoding(scalars="temperature",
                clim=percentile_range(mesh, "temperature"),
                cmap="hot",
                show_scalar_bar=True,
                scalar_bar_args={"title": "Temperature (K)"})

plotter.add_mesh(iso,  **fire.kwargs())
plotter.add_mesh(surf, **fire.kwargs(opacity=0.3))  # override one field
plotter.add_volume(vol, **fire.kwargs())
```

Define once, apply to many. Diffable (compare two Encoding instances).
Printable (dataclass `__repr__`). Inspectable (introspect the fields).
Everything ggplot2's `aes()` gives you, minus the late-binding by
column name — and even that could be added with a small wrapper.

**Conclusion.** Encodings-as-reusable-objects is a ten-line dataclass.
No DSL required.

### 2.4 Orthogonal introspection — named variables give you most of it

**What it looks like you need.** A grammar tree with typed nouns
(layer, encoding, scale, transform, rep) so the info view, diff
engine, and LSP have structured hooks to attach to.

**What you actually need.** Named Python variables + a scene model
that tracks them + a reconciler that uses variable names as identity.

**The PyVista-subset realization.** Tracked-execution's reconciler
already uses actor names as identity. Extend this one step further:
when the pipeline file assigns `wood = mesh.threshold(...)`, the
system records "the variable `wood` holds a filter result with hash H
and shape S." Three features follow:

- **Info-view lookup by name.** The LLM or user asks "what is
  `wood`?" and the system looks up the tracked variable and returns
  shape, hash, bounds, isolated render — the same info a grammar-tree
  walk would return.
- **Diffs by name.** "Between v3 and v4, `wood` changed: threshold
  range went from [20, 145] to [25, 160]." This is achieved by
  comparing the AST of successive pipeline files and matching
  assignments by variable name.
- **Hover by name.** Over a variable in the editor, show its tracked
  metadata.

These features need:
- A scene model that records `name → {ast_node, mesh_hash, params_hash,
  display_kwargs}`
- An AST walker that extracts `name = expr` assignments from pipeline
  files
- A diff function that matches names across file versions

This is maybe a few hundred lines on top of tracked-execution.

**What a grammar tree would give you additionally.** Typed introspection
— "show me everything that is a scale" across the whole spec. Useful
for global operations (themeing, scale coordination) but niche enough
to defer. Named variables cover the common case.

**Conclusion.** Build the "name as identity" infrastructure early.
Most of the typed-noun benefits fall out of it.

### 2.5 Layers and scenes as values

**What it looks like you need.** `layer(rep, encoding, data)` as a
first-class value you can construct, pass around, store, compose.

**What you actually already have in PyVista.** Meshes are values.
Filter results are values. You can construct them anywhere, store them
in variables, pass them through functions, reuse them across plotters.

**The small gap.** PyVista doesn't have a "fully-bundled layer" type
that includes the visual encoding together with the mesh. You pass the
mesh to `add_mesh` and supply the encoding at that moment.

**Trivial realization.**

```python
@dataclass
class Layer:
    mesh: Any
    encoding: Encoding
    name: str | None = None

    def add_to(self, plotter):
        name = self.name or f"layer_{id(self)}"
        plotter.add_mesh(self.mesh, name=name, **self.encoding.kwargs())

layers = [
    Layer(iso, fire_encoding, name="flame"),
    Layer(outline, outline_encoding, name="box"),
    Layer(streams, velocity_encoding, name="flow"),
]

for layer in layers:
    layer.add_to(plotter)
```

**What you get.** Layers as values. Pass through functions, filter
lists, reorder, comprehend over. Full "layers first-class" benefit
with a ten-line wrapper.

**Conclusion.** This is a trivial helper. No DSL required.

### 2.6 Compiler reasoning about shared derivations

**What it looks like you need.** A declarative transform list the
compiler can walk to find shared derivations, reorder for efficiency,
and deduplicate.

**What you actually need.** Content-addressed caching. Tracked-execution
already does this: two layers calling
`mesh.threshold(500).extract_surface()` with identical arguments hit
the same cache entry. The "compiler sees shared derivations" benefit
is already delivered — by the cache, not by declaration.

**Where the cache falls short.**
- **Reordering for efficiency.** The cache can't reorder operations
  the user wrote. If the user chains `.smooth().threshold()` but the
  threshold should logically happen first (to avoid smoothing data
  that's about to be dropped), the cache doesn't help. A declarative
  transform list with commutative rewrites would.
- **Cost estimation before running.** The cache tells you which calls
  are hot; it can't tell you about a call before you've made it. A
  declarative list can be walked and summed.

**Small-layer realizations.**

For reordering: a lint/advisor that walks the AST and suggests
commutative rearrangements ("move threshold before smooth — cheaper,
same result"). Not automatic rewrite; a suggestion the user accepts.

For cost estimation: a dry-run mode that walks the pipeline file,
looks up each operation in a cost model keyed on input size and
operation type, and reports an estimate before executing. Independent
of caching; operates on the AST.

**Conclusion.** Caching covers 80% of the "compiler reasoning"
benefit. The remaining 20% (reordering, cost estimation) is served by
AST-level tooling, not by a declarative list format.

### 2.7 Expression sublanguage for predicates and derivations

**What it looks like you need.** A mini expression language
(`"temperature > 500 and density < 100"`) that's sandboxed,
analyzable, and safer than arbitrary Python.

**What you actually need.** A parser for a Python expression subset.
`ast.parse(expr_string, mode="eval")` gives you an expression AST for
free, and you can walk it to enforce a safe subset (only names,
arithmetic, comparisons, function calls from an approved list).

**The PyVista-subset realization.** Offer predicate-based filtering
with a string predicate:

```python
hot = vislang.filter_mesh(mesh, "temperature > 500 and density < 100")
```

Internally:

```python
def filter_mesh(mesh, predicate_str):
    tree = ast.parse(predicate_str, mode="eval")
    _validate_safe(tree)  # walk, reject disallowed nodes
    fields = _extract_field_refs(tree)  # for validation + autocomplete
    # compile to a vtkThreshold / vtkThresholdPoints expression,
    # or (for complex predicates) to a vtkArrayCalculator output
    # filter, and then vtkThreshold on the boolean result
    return _compile_predicate_filter(mesh, tree, fields)
```

What you get:
- Static field-name validation ("Temperture not found, did you mean
  Temperature?") from walking the AST.
- Autocomplete of field names in predicate strings from LSP.
- Safe evaluation (no side effects, no arbitrary imports).
- Multi-field predicates in one call (vs. PyVista's single-field
  `mesh.threshold`).

**Alternative: Python lambdas with inspection.** Instead of a string
sublanguage, take a Python lambda and inspect its source / AST:

```python
hot = vislang.filter_mesh(mesh, lambda t, d: t > 500 and d < 100)
```

Slightly less safe (you have to enforce purity) but more ergonomic.
The tracked-execution project already has machinery for hashing
functions by source; the same walker can extract field dependencies.

**Conclusion.** The expression-sublanguage benefit is "Python's `ast`
module." Wrap it in a `filter_mesh(mesh, predicate_str)` helper and
you have Vega-Lite's filter transform without the JSON.

### 2.8 Faceting

**What it looks like you need.** A `facet(spec, by="timestep")`
grammar operation that auto-generates small multiples.

**What you actually need.** `pv.Plotter(shape=(m, n))` with a loop.

**The PyVista-subset realization.**

```python
def facet(plot_fn, values, shape=None, layout="grid", shared_camera=True):
    """Call plot_fn(plotter, value) for each value, in a subplot grid."""
    n = len(values)
    if shape is None:
        shape = _auto_grid(n, layout)
    plotter = pv.Plotter(shape=shape)
    for i, value in enumerate(values):
        plotter.subplot(*_index_to_subplot(i, shape))
        plot_fn(plotter, value)
    if shared_camera:
        plotter.link_views()
    return plotter

# Usage
def plot_iso(p, threshold):
    iso = mesh.contour([threshold], scalars="temperature")
    p.add_mesh(iso, **fire_encoding.kwargs())

facet(plot_iso, values=[500, 800, 1200], shape=(1, 3))
```

**What's missing vs. a grammar.** Semantic faceting ("facet by the
`timestep` column") requires inspecting data and partitioning
automatically. Easy to add:

```python
def facet_by(mesh, field, plot_fn, **kwargs):
    unique_values = np.unique(mesh[field])
    return facet(lambda p, v: plot_fn(p, mesh.threshold_percent([v, v])),
                 values=unique_values, **kwargs)
```

**Conclusion.** Faceting is a helper function. Vega-Lite's version is
nicer because the spec composes more uniformly, but the capability is
trivially available in PyVista.

### 2.9 Compiler pipeline with a lower-level escape hatch

**What it looks like you need.** A two-level architecture like Vega-Lite
→ Vega, where the high-level spec compiles to a lower-level format
that users can drop into when they need raw power.

**What you actually have.** Tracked-execution's `vtk_escape`. The "low
level" is raw VTK, accessed through an explicit escape that still
participates in caching. The "high level" is the PyVista subset.

The two-level structure is already there — it's just not framed that
way. PyVista is the high level, raw VTK through `vtk_escape` is the
low level, and the escape is principled (cached, hashed, composable
with tracked operations).

**Enhancement opportunity.** Document the two-level model explicitly.
The pattern is: "Use PyVista for everything; drop to `vtk_escape` for
the one filter or parameter PyVista doesn't expose; don't think about
anything below that." This framing — that raw VTK is a first-class
escape hatch from a principled subset — is the Vega-Lite / Vega
architectural pattern, just with different layer names.

**Conclusion.** Already in place. Frame it explicitly.

### 2.10 Additive composition syntax (+, pipe, chaining)

**What it looks like.** `plot + layer1 + layer2 + scale_color(...) +
theme_minimal()` or `data | where(...) | rep(...)`.

**What it actually buys.** Cosmetic difference. Method chaining gives
you the same ordering properties. Sequential `add_mesh` calls give you
the same accumulation properties. The magic of ggplot2's `+` is not
the `+` — it's that layers, scales, coords, themes, and facets are
independent specifications being merged, not a pipeline.

**Conclusion.** Don't build a pipe operator. It's syntactic sugar with
no structural benefit. The "layers as values" and "encodings as
reusable objects" points above deliver the actual compositional
benefit.

---

## Part 3 — Concrete Near-Term Agenda

Rank-ordered by how much they'd improve VisLang-on-PyVista today,
with rough scope estimates. None of these commits to a custom DSL;
each is a helper library, convention, or tooling improvement that
captures grammar-inspired ideas within the PyVista-subset substrate.

### 3.1 `vislang.stats` module (small, high-value)

A stable, documented stats library: `suggest_iso`, `percentile_range`,
`auto_clim`, `auto_opacity`, `peak_values`, `robust_range`. Each
returns either a plain value or a `StatValue` wrapper that carries
provenance metadata. Importable from pipeline files, cached by
tracked execution.

This is the concrete answer to "fewer magic constants." Should land
early because every other helper benefits from it.

### 3.2 `vislang.Encoding` and `vislang.Layer` dataclasses (small)

Bundled visual encoding and bundled layer types, as shown in §2.3 and
§2.5. Let pipeline files factor out repeated display kwargs and pass
layers around as values.

### 3.3 Field-type inference in `describe_data` → defaults helper (medium)

Extend `describe_data` (or tracked-execution's equivalent) to infer
field types: sequential scalar, diverging scalar, probability,
categorical, vector, tensor, spatial, temporal. Then build a
`vislang.smart_encoding(mesh, color=field)` helper that consults the
inference and returns a sensible `Encoding` automatically.

This is the §1.2 "semantic field types" idea, realized as a helper
rather than as grammar defaults. The helper captures 70% of the value;
the remaining 30% (grammar-ambient defaults, LSP display without
execution) can come later if needed.

### 3.4 Named-variable scene model + info-view lookup (medium)

Extend tracked-execution's reconciler to record `name → metadata` for
every top-level assignment in a pipeline file. Offer:

- `inspect(name)` — shape, hash, bounds, isolated render
- `diff(name, v1, v2)` — what changed between pipeline versions
- LSP hover on `wood` showing its tracked metadata

This is the §2.4 "orthogonal introspection via names" idea. The
biggest single improvement for the info-view and LSP features on the
VisLang roadmap.

### 3.5 Restricted-subset validator and AST walker (medium)

The tracked-execution whitelist generalized into a proper validator:
walk the AST of a pipeline file before execution, enforce the
whitelist, produce pointing diagnostics. Separates "file parse / valid
subset" from "VTK execution" as two distinct failure modes, each with
its own error channel.

### 3.6 `vislang.filter_mesh(mesh, predicate_str)` with AST-level parsing (small)

Multi-field predicate filtering via a restricted Python expression
subset, parsed with `ast.parse`, validated against field names, and
compiled to a VTK pipeline. This is the §2.7 "expression sublanguage"
idea.

Incidental benefit: once you have the predicate parser, you can reuse
it for other things (scalar_range queries with conditions, subset
statistics, facet expressions).

### 3.7 `vislang.SharedScale` and `vislang.facet` helpers (small)

The §1.3 and §2.8 helpers. Each is a dozen lines of Python. Deliver
cross-layer consistency and small-multiples without a grammar.

### 3.8 Two-level docs framing (tiny, write-up only)

Document the PyVista-subset + `vtk_escape` architecture as a
two-level pattern, analogous to Vega-Lite / Vega. Name the layers.
Explain when to drop from high to low.

### 3.9 Tunable-parameter convention (tiny)

Even without a grammar, establish a naming or annotation convention
for tunables:

```python
threshold = vislang.tune(500, min=0, max=2000, label="Temperature threshold")
```

`tune()` returns the default value unchanged, but the LSP / MCP can
recognize the call and auto-generate sliders, respond to bidirectional
drags, and record provenance. This is a stepping-stone to the
full-grammar params story in §1.1: if bidirectional editing becomes a
priority later, the convention is already documented and you just
enrich the runtime semantics.

### What's NOT on this list

- A custom DSL or AST-building interpreter
- A JSON spec format
- A pipe operator
- Representation / encoding / layer wrapping classes that duplicate
  PyVista concepts
- Any structural departure from "Python subset calling PyVista"

All of those are either deferred indefinitely or achievable later as
incremental enrichments of the subset without rewriting the substrate.

---

## Part 4 — Signals That Would Tip Toward a Real DSL

These are the concrete things that, if they start to bite, would
justify reopening the custom-DSL case. None of them is biting yet, so
the short-term PyVista-subset plan is clearly correct. But keeping
the signals explicit lets us notice when the situation changes.

### 4.1 Bidirectional editing becomes a priority

If users start asking for "drag the clip plane in the 3D view and
have the code update" as a core feature, the AST-rewrite approach
starts feeling fragile: finding which literal to rewrite, preserving
formatting, handling cases where the value was computed from a stat
rather than a literal. Grammar-level params (§1.1) would dissolve all
of these. If `vislang.tune(...)` convention usage gets dense enough
that it feels like a half-implemented DSL, that's the signal to
formalize.

### 4.2 Cross-layer consistency bugs become chronic

If the feedback entries start showing "user complained that two views
of the same field used inconsistent color ranges" frequently, the
§2.1 helper approach isn't enforcing enough. Grammar-level global
scales (§1.3) would make inconsistency impossible rather than just
discouraged.

### 4.3 Field-type defaults get re-implemented inconsistently

If `vislang.smart_encoding`, the LSP hover, and the MCP suggestion
tools all grow their own copies of "is this a diverging field?"
logic, that's a sign the type system wants to live somewhere more
central. A grammar gives it a place; without one, you either
centralize via shared utilities (fine) or accept drift (bad).

### 4.4 The AST whitelist grows a de-facto type system

If the AST walker starts checking not just "is this method allowed"
but "is this argument the right type", "does this field exist on the
upstream dataset", "is this combination of kwargs coherent" — that's
a type system being built inside a validator. At some point it's
cleaner to make the types first-class, which means a grammar with
typed nouns.

### 4.5 The file-as-artifact readability starts suffering

The "pipeline file that a scientist audits" property depends on the
file being concise and named. If helper-layer accumulation makes
typical files 3x longer than they need to be (every layer bundling
its own encoding, every stat wrapped, every tune annotated, every
scale helper threaded through), the helpers start *hurting*
readability rather than helping. A grammar would restore concision by
pushing structure into the language.

### 4.6 Multi-dataset polymorphism becomes important

If users frequently want to apply the same visualization to multiple
timesteps, multiple parameter sweeps, or multiple simulation runs,
late-bound aesthetic references (Vega-Lite's `"field": "column_name"`
at encoding time) would be genuinely useful. The helper approach
handles single-dataset cases fine but gets clumsy when the "spec"
wants to outlive a particular mesh.

### When to actually build it

The honest test: when two or more of these signals are firing at the
same time, and the helper-layer patches have become convoluted enough
to obscure rather than clarify, that's the time to sit down and
design a small grammar layer *specifically to address the biting
problems*. Not as a greenfield research project, but as a carefully
scoped refactor of conventions that have already proven their value
as helpers.

The grammar, if it's ever built, should grow out of the helpers —
not replace them in one big leap. `vislang.Encoding` becomes the
grammar's `encode()`. `vislang.stats.suggest_iso` becomes the
grammar's `stat_suggest_iso()`. `vislang.tune` becomes `param`. The
transition is incremental and the grammar is proved necessary by the
friction of the PyVista-subset version rather than assumed on
theoretical grounds.

---

## Part 5 — What VisLang Should Study From the Vega Community That I
Haven't Explored Here

A few Vega/Vega-Lite ideas that came up in conversation and are worth
keeping in the back of the mind, even if they don't require immediate
action:

**Recommendation engines (Voyager, Draco).** Because Vega-Lite's spec
space is finite and constrainable, downstream tools can enumerate
alternatives and rank them. For VisLang with an LLM in the loop, the
analog is the LLM proposing visualization alternatives with trade-off
explanations — which it already does informally. A structured subset
would make the enumeration well-defined and the trade-offs (cost,
clarity, coverage) computable.

**Signals / reactive dataflow (Vega runtime, DIVA).** The idea that a
visualization is a dataflow graph where signals propagate changes,
rather than an imperative construction. Tracked-execution's DAG with
content hashing is a primitive version of this. The more principled
version would let the hot-reload story become "change the file, only
affected layers rebuild, everything else stays live in the render
window." This is strictly better than "rebuild the whole scene on
save" and deserves a design pass when hot-reload ships.

**The two-level compiler architecture (Altair → Vega-Lite → Vega).**
The pattern of a concise authoring surface compiling to a more
verbose intermediate form, compiling to a runtime, with user escapes
at each level. VisLang's analog (PyVista subset → VTK filter graph →
VTK execution) is already there but not framed as a compiler
pipeline. Naming and documenting the levels would help the design
thinking and make the architecture easier to explain.

**Gestalt composition operators (GoFish, IEEE VIS 2026).** The very
recent work on recursive compositional operators for Gestalt
principles (spacing, containment, connection). Not directly
applicable to 3D sci-viz, but an interesting data point about where
the 2D grammar community is pushing the design envelope.

---

## Conclusion

The question this document started with was: does a grammar-of-graphics-
inspired DSL offer benefits that would justify building it on top of
VTK eventually?

The answer, after working through the design space skeptically, is
**probably yes, but narrower than I initially thought, and not
urgent**. The surviving case is two or three specific benefits
(parameters as first-class grammar elements, semantic field types
driving defaults, global scales as a structural property) that would
genuinely need a grammar layer to deliver cleanly. Everything else —
spec-as-data, stats unification, orthogonal introspection, reusable
encodings, faceting, expression sublanguages, compiler reasoning, and
composition syntax — has a clean realization within a PyVista-subset
approach using AST-level tooling, helper libraries, and conventions.

The short-term plan (adopt PyVista restricted to a safe subset, invest
in the surrounding query layer and LSP and hot reload) is clearly
right. The long-term grammar question should stay open, monitored by
the concrete signals in Part 4. When those signals start firing, the
grammar should grow incrementally out of helpers that have already
proven their value, not land as a greenfield DSL project.

The biggest single takeaway from this exploration: **the grammar-of-
graphics tradition has more to teach us about what we want from the
surrounding system than about what we want from the DSL itself.**
Vega-Lite's insight that specs should be tool-manipulable
(realized via AST walkers on Python source). ggplot2's insight that
encodings should be reusable values (realized as dataclasses).
Vega's signal-graph runtime (realized as tracked execution with
content hashing). Altair's two-level authoring surface (realized as
PyVista subset over VTK). Each of these is a structural idea that
VisLang can absorb without adopting any particular syntactic surface.
The ideas are portable. The DSLs are not.
