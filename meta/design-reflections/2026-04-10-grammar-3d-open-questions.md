# Grammar of 3D Visualization: Open Design Questions

*Supplement to the main design exploration — April 10, 2026*

These are the harder questions that the grammar proposal raises. They
don't have obvious answers and would need to be worked through during
implementation and testing.

---

## 1. Where does the row-to-mark analogy actually break?

The GoG works because there is a clean mapping: one data row → one
visual mark. In 3D sci-viz, the relationship between data and visual
output is fundamentally different for different representation types:

- **Isosurface:** many voxels → one surface. The marching cubes algorithm
  consumes the entire scalar field and produces geometry that doesn't
  correspond to individual data points. There's no "one row, one triangle."
- **Volume rendering:** all voxels → one image. Ray casting consumes the
  entire volume. The output isn't even geometry — it's a composited image.
- **Streamlines:** the entire vector field → N curves. Each curve is
  produced by numerical integration starting from a seed point. The
  curve doesn't "represent" specific data points; it traces a path through
  the field.
- **Glyphs:** one data point → one glyph. This IS the row-to-mark
  correspondence, and GoG applies cleanly.

The grammar proposal handles this by saying "representations are geometry
generators" — they produce geometry from fields via algorithms. But this
means the grammar has two fundamentally different kinds of representations:

1. **Mark-based** (glyphs, point clouds) — where GoG aesthetics
   (size, color, orientation per mark) apply naturally.
2. **Field-based** (volumes, isosurfaces, streamlines) — where the
   "aesthetic mapping" is not per-mark but per-algorithm. The opacity
   transfer function for volume rendering is an aesthetic mapping, but
   it doesn't map "per mark" — it maps "per ray sample."

Should the grammar make this distinction explicit? Or paper over it with
a unified encoding interface? ggplot2 papers over the stat/geom distinction
elegantly (stat_smooth + geom_line), but the gap here is wider.

## 2. What is the right granularity for transforms?

The proposal uses intent-oriented transforms: `where("theta", between=[500, 2000])`.
But real pipelines often need VTK-specific control:

```python
# Intent-oriented (clear but limited):
fire | where("theta", between=[500, 2000])

# VTK-oriented (powerful but leaky):
threshold(input=data, ThresholdBy="theta", ThresholdRange=[500, 2000],
          AllScalars=False, UseContinuousCellRange=True)
```

The intent-oriented API covers 80% of cases but the remaining 20%
requires VTK-specific parameters. Options:

**a) Allow VTK pass-through kwargs:**
```python
fire | where("theta", between=[500, 2000], vtk_AllScalars=False)
```
Pragmatic but ugly. Breaks the abstraction visually.

**b) Escape to filter():**
```python
fire | vtk_filter("vtkThreshold", ThresholdBy="theta",
                  ThresholdRange=[500, 2000], AllScalars=False)
```
Clear boundary between grammar and VTK. The current DSL's `filter()`
approach.

**c) Just don't cover those cases initially.**
Let users drop to raw VTK via `vtk_escape()` from tracked-execution.
Accept that the grammar covers common cases; edge cases use a different
API.

I lean toward (c) for the prototype and (b) for the longer term. The
intent-oriented vocabulary should cover what domain scientists need; the
VTK-specific knobs are for visualization engineers.

## 3. How do encodings compose with representations?

In ggplot2, aesthetics compose straightforwardly: `aes(color=species)`
means "map the species column to color for this layer." But in 3D:

- A **volume** representation uses a color transfer function AND an
  opacity transfer function AND optional gradient opacity AND shading
  parameters. These interact with each other and with the data.
- A **surface** representation uses a color mapping OR a solid color,
  plus specular/ambient/diffuse material properties.
- A **streamline** representation might color by the traced field
  (e.g., velocity magnitude along the line).

Should `encode()` be representation-aware? That is, should:
```python
encode(color=scale_color("density", ...))
```
mean different things depending on whether it's applied to a volume
or a surface? Or should the encoding be representation-agnostic and the
compiler figure out the mapping?

ggplot2 takes the representation-agnostic approach: `aes(color=species)`
means the same thing regardless of whether it's used with `geom_point`
or `geom_line`. The geom decides how to interpret the mapping. This is
cleaner but may not work for the volume-vs-surface distinction, where
the encoding mechanisms are fundamentally different (transfer function
vs. lookup table).

**My current thinking:** make `encode()` representation-agnostic where
possible (color, opacity, legend), but allow representation-specific
encoding extensions:
```python
# Works for both surface and volume
encode(color=scale_color("density", ...))

# Volume-specific
encode(color=..., opacity=scale_opacity("density", ...),
       shade=True, gradient_opacity=True)

# Surface-specific
encode(color=..., specular=0.5, specular_power=30)
```

The compiler validates that the encoding is compatible with the
representation and produces a clear error if not.

## 4. What role does the LLM play in the grammar?

This is the most VisLang-specific question. The grammar isn't just for
humans writing pipelines in an editor — it's primarily for an LLM
writing pipelines via MCP tools.

This changes the design calculus in several ways:

**a) The grammar should be LLM-steerable.** The LLM should be able to
make small, targeted changes without rewriting the whole pipeline. The
encoding-separation design helps here: "change the colormap to inferno"
is a change to one `scale_color()` object, not a keyword argument buried
in a `show()` call.

**b) The grammar should be discoverable via tools.** The current
`get_dsl_overview()` and `get_dsl_reference()` tools should expose the
grammar's vocabulary. The intent-oriented names (`where`, `rep_isosurface`)
are more LLM-friendly than VTK class names.

**c) The stat/suggest layer should compose with the grammar.** Instead
of `suggest_isosurface()` returning values that the LLM pastes into a
pipeline, could the grammar integrate data-guided defaults?

```python
# LLM writes this; the grammar queries the data to fill in values
show(fire | rep_isosurface("theta", at="auto"),
     encode(color=scale_color("theta", range="auto", colormap="hot")))
```

"auto" means "use the data's statistics to choose good values." This
eliminates the suggest → paste → set_pipeline round trip. The grammar
system calls the same query functions internally.

**d) Error messages should be grammar-aware.** When a pipeline fails,
the error should reference grammar concepts:
```
Error in rep_isosurface("theta", at=3000):
  Value 3000 is outside field "theta" range [289.5, 2019.3].
  Suggested values: 500, 800, 1200 (histogram peaks).
```

Not VTK-level:
```
vtkContourFilter: no output generated
```

## 5. Faceting in 3D is harder than it looks

ggplot2's faceting works because 2D plots tile naturally into grids.
3D viewports tile similarly (VTK supports viewport subdivision), but
there are complications:

- **Camera synchronization.** Do faceted panels share a camera (rotate
  one, all rotate)? This is useful for comparison but complex to
  implement with VTK's per-renderer cameras.
- **Spatial arrangement.** 2D facets are always side-by-side. 3D facets
  could be side-by-side viewports OR overlaid in the same scene with
  spatial offsets. For comparing two isosurface thresholds, overlaying
  with transparency might be more useful than side-by-side.
- **Data-driven faceting.** `facet(by="timestep")` for time series data
  requires loading multiple datasets. `facet(by="field")` for multi-field
  comparison is easier (same data, different encodings).

For the prototype, I'd start with the simplest version: side-by-side
viewports with independent cameras, and add shared-camera and overlay
modes later.

## 6. The naming problem

The grammar proposal uses intent-oriented names:
- `where()` instead of `threshold()`
- `derive()` instead of `make_vector()`
- `rep_isosurface()` instead of `contour()`

But the VTK/visualization community has established terminology:
- "Threshold" is the standard term for range-based subset extraction
- "Contour" is the standard term for isosurface extraction
- "Streamline" is the standard term (not "flow trace" or "field curve")

Should the grammar use domain-standard terminology or intent-oriented
terminology? Arguments on both sides:

**For intent-oriented:** Scientists don't think in VTK terms. A fire
researcher thinks "where is it above 500 K?" not "apply a threshold
filter." The intent-oriented vocabulary makes the pipeline file more
readable for domain experts.

**For domain-standard:** Visualization researchers and VTK users know
what "threshold" and "contour" mean. Using non-standard names creates
a translation barrier and makes the DSL feel invented rather than
grounded.

**Possible compromise:** Use domain-standard names for representations
(`rep_isosurface`, `rep_streamlines`, `rep_volume`) but intent-oriented
names for transforms (`where` instead of `threshold`, `clip` stays as
`clip` since it's already intent-oriented).

Actually, looking at it more carefully, many standard viz terms ARE
intent-oriented:
- `clip` — clip the data (intent-oriented and standard)
- `slice` — slice through the data (intent-oriented and standard)
- `contour` — extract a contour surface (standard, but "isosurface" is
  more specific)
- `threshold` — this is the one that's least intent-oriented. "Threshold"
  is a mechanism; "where" is intent.

For the grammar, I'd go with: `where()` for threshold (biggest win),
standard names for everything else.

## 7. Should the grammar subsume the MCP tools?

Currently, VisLang has ~35 MCP tools — some are query tools (describe_data,
get_statistics), some are mutation tools (set_pipeline, set_colormap),
some are meta tools (get_dsl_reference). The grammar raises the question:
should some of these be grammar elements instead?

For example:
- `suggest_isosurface()` could become `rep_isosurface(field, at="auto")`
- `set_colormap()` could become editing the `scale_color()` in the pipeline
- `set_opacity()` could become editing the `encode()` in the pipeline

The VISION.md already proposes removing mutation tools in favor of
pipeline file edits. The grammar makes this more natural: instead of
calling `set_colormap("fire", "inferno")` as a tool, the LLM edits the
`scale_color(...)` object in the pipeline file.

Query tools (describe_data, get_statistics, suggest_*) should stay as
MCP tools — they don't modify the visualization, they inform it. But the
grammar could integrate their outputs: `range="auto"` could internally
call the equivalent of `suggest_scalar_range()`.

## 8. Performance implications

The grammar's lazy evaluation model (declare everything, compile once)
should have no performance penalty vs. the current approach — both
ultimately construct the same VTK pipeline. But the grammar's
compositional structure enables some optimizations:

- **Encoding-only changes.** If only the `encode()` changes between
  versions (e.g., different colormap), the VTK filter pipeline doesn't
  need to re-execute — only the mapper/actor properties change. The
  current tear-down-and-rebuild model can't exploit this because encoding
  is mixed into the build.
- **Shared sub-pipelines.** If two layers use the same data + transforms,
  the compiler can share the VTK filter pipeline and only diverge at the
  representation stage. The current DSL has no mechanism for this.
- **Incremental compilation.** The grammar's structured specs enable
  diffing old vs. new specs to determine what changed — this is the
  reconciler idea from VISION.md, but the grammar makes it tractable.

These are real advantages that become important for large datasets and
interactive refinement loops.

---

## What to investigate next

1. **Build the prototype** and test it against real visualization tasks
   from VisLang's session history (bonsai CT, wildfire simulation).
2. **Test LLM writability** — can Claude produce correct grammar code
   more reliably than current DSL code? Run comparative trials.
3. **Test human readability** — show grammar pipelines vs current DSL
   pipelines to domain scientists and see which they understand faster.
4. **Explore the "auto" pattern** — how far can data-guided defaults go?
   Can `rep_isosurface(field, at="auto")` produce a useful default?
5. **Read the Shih et al. 2019 paper** in detail — it's the closest
   academic work and may have solved problems we haven't thought of yet.
