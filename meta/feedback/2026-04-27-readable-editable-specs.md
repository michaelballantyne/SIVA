# Making specs readable and editable: anchors, queries, LIVE direction

## Origin

Conversation prompted by a wildfire streamline view where the seed line was
expressed as raw VTK coordinates (`Point1=(-50, -200, 185)`). A reader can't
tell that those numbers mean "100m upwind of the fire's leading edge, at
ground level." The literal hides the intent. The discussion wandered through
several framings of "what would make these specs explainable and editable
by humans" and ended up touching DSL design, MCP surface, and the LIVE
programming tradition. This entry captures the threads, not a single
proposal.

## The wedge

The agent often writes literals that encode three different things, all
flattened to the same syntactic form:

1. **References**: values whose meaning depends on the data
   (`-150` = "fire's leading x"). These should be derived, not baked.
2. **Parameters**: values that encode human intent in physical units
   (`100` m upwind, `2` m above ground). These benefit from names.
3. **Structural choices**: aesthetic / resolution knobs with no real
   "right answer" (`Resolution=80`, `NumberOfSides=6`,
   `MaximumNumberOfSteps=4000`). These are fine as inline literals.

A lot of the readability problem is that all three live as bare numbers
in tuples, and the reader has no signal to distinguish them. The design
question is whether to fix this with new DSL forms, with convention,
with tooling, or with all three at different leverage points.

## Idea 1: anchor objects + relative placement combinators

A new DSL layer of named features extracted from the data (fire_edge,
ground_surface, domain_bounds, flow_direction) plus combinators for
placing geometry relative to them:

```python
seed_line = line(
    along_axis="y", length=400,
    at=fire.center.shift(x=-100),
    above=ground, height=2,
)
```

**Why it might be worth it.** The vocabulary is reusable and self-documenting.
Every "upwind of the fire" expression looks the same. Pairs naturally with
GIS spatial-operator vocabulary (buffer, centroid, near, intersect).

**Why it might not.** Lots of new DSL surface, lots of names to learn, and
a constant pull toward over-abstraction. Only pays off if the same anchors
recur across many views and datasets — if every visualization needs bespoke
anchors, you've just moved the complexity.

Verdict: not the right starting point. Hold as a target if patterns
crystallize. Don't pre-build a vocabulary.

## Idea 2: convention — name intent-bearing intermediates

Rather than new DSL, instruct the agent to compute and name the
intent-bearing quantities as plain variables before passing them to
existing forms:

```python
seed_x = fire_x_min - 100        # 100m upwind
seed_z = ground_z + 2            # 2m above ground

seeds = source("vtkLineSource",
               Point1=(seed_x, -200, seed_z),
               Point2=(seed_x, +200, seed_z),
               Resolution=80)
```

Same arithmetic, restated in a form a human can tweak (`100` → `150`)
without doing the math. Costs nothing at runtime; just a system-prompt
rule.

**Subtlety.** "Name everything" is the wrong rule. Two refinements:

- **Locality beats lifting.** A `seed_upwind_dist = 100` thirty lines
  above the seed line forces the reader to look back. The same value
  inline with a one-line comment is easier to follow. Lift only when the
  same value feeds *several* sites (DRY) or when a small group of related
  knobs genuinely belongs together. **Rejected: a top-of-file controls
  block** — fragments the spec; hot-reload + node-level rebuild make
  every literal editable already, so the controls block was importing a
  workaround for a constraint we don't have.

- **Not every literal carries intent.** `Resolution=80`,
  `NumberOfSides=6`, `MaximumNumberOfSteps=4000` are structural choices
  where the answer is "enough." Naming adds ceremony without payoff.

Working rule: **lift when the form alone doesn't tell the story.**
`Point1=(-150, -200, 185)` doesn't tell the story; `(seed_x, -200,
seed_z)` does. The y-span literals and `Resolution=80` stay inline.
Half the numbers explained, half left as form shape.

## Idea 3: query primitives in the DSL

Promote a small set of MCP-equivalent queries to build-time DSL functions
returning introspectable, composable result objects:

```python
fire   = spatial_extent(data, where=f"theta > {fire_threshold_K}")
ground = ground_surface(data)
flow   = flow_summary(data, vector="velocity")

seed_x = fire.x_min - 100
seed_z = ground.z_at(0, 0) + 2
```

Three benefits, only the third of which is editability:
- The spec is **reproducible** — re-run on a different timestep, anchors
  recompute.
- The values stay close to their meaning; tweaking the threshold moves
  the seeds automatically.
- Annotation: each query result has a "show your work" line
  (`# observed: x=[-180,-110] ...`) that grounds a future reader.

Worth keeping the surface narrow: `spatial_extent`, `field_stats`,
`ground_surface`, `centroid`, `flow_summary`, `histogram`. Don't let
arbitrary computation leak in.

## Three orthogonal motivations, easy to conflate

The earlier framing was muddled because it bundled three different jobs:

| Motivation | Mechanism | Example |
|---|---|---|
| **Legibility** | Names with unit comments at point of use | `seed_x = fire.x_min - 100  # m upwind` |
| **Reproducibility** | Query-derived references rather than baked literals | `fire.x_min` not `-150` |
| **Editability / exploration** | Named or unnamed literals — modern LIVE tools scrub bare numbers | `Resolution=80` is scrubbable as-is |

Names aren't required for editability; they're required for legibility.
Queries aren't required for editability; they're required for
reproducibility. Scrubbing works on bare literals. Treating these as
separable lets each be solved by the right tool.

## Adjacent traditions

Several non-LIVE inspirations worth naming, since they inform specific
moves above:

- **Parametric CAD** (OpenSCAD, Fusion 360, FreeCAD). Every value in
  the model is either a *parameter* (an editable knob) or a
  *derivation* (computed from parameters and other derivations);
  features carry named anchors (face, edge, vertex) that downstream
  geometry references with phrases like "extrude 10mm normal to top
  face." The LLM-generated spec is structurally a parametric model
  with the parameters hidden as literals — ideas 2 and 3 are about
  surfacing them. Idea 1 (anchor combinators) is the CAD-style
  feature-and-anchor vocabulary directly.

- **GIS spatial operators**. Decades of well-named, composable
  primitives — `buffer`, `centroid`, `dissolve`, `nearest`,
  `intersect`. The vocabulary for "give me a region 100m around
  feature X" already exists. Scientific viz is the same problem with
  a Z axis and continuous scalar fields; the operator names port over
  (`extent`, `centroid`, `surface`, `near`, `above`). Direct source
  for the query primitives in idea 3 and the anchor combinators in
  idea 1.

- **Vega-Lite & grammar of graphics** (ggplot's `aes()`, Vega-Lite's
  `param` blocks). Separates *vocabulary of intent* from *render
  mechanics*. The original "controls block at top of spec" framing
  came from here — the rejection isn't about Vega-Lite being wrong
  but about that move being a workaround for rebuild costs we don't
  have. The intent/render separation itself is still load-bearing —
  it's the same split idea 4 keeps between query and show.

- **Terraform `data` vs `resource`**. Grammatically explicit split
  between *discovering* existing state and *declaring* desired state.
  VisLang has the same shape: query primitives discover; geometry
  primitives declare. Worth keeping this distinction visible in spec
  layout, and informs why a unified `query(expr)` makes sense — it's
  the `data`-block analogue.

- **Spreadsheets / Observable-style notebooks**. Named cells with
  values shown next to their definitions; downstream recomputation on
  edit. The inline value annotation idea in the LIVE section below is
  essentially "make the spec into a spreadsheet that lives as a file."

## LIVE workshop direction

Hot reload + node-level dependency tracking is already a live-programming
runtime. The framing should match. Several LIVE traditions point toward
features that compose well with what we have:

- **Inline values everywhere** (Bret Victor; Light Table; Subtext;
  Observable). The build evaluates every node's result. Decorate the
  source file in place: `seed_z = ground.z_at(0, 0) + seed_height  # → 185.0`.
  Spec becomes a self-documenting live trace; reader doesn't need to
  re-run anything to see what the agent saw. **Highest-leverage single
  feature** — powers everything below.

- **Scrubbing** (Victor; Apparatus). Once values are named or addressable,
  sweep across a range and return a screenshot strip. Cheap. **An LLM
  variant the original LIVE work didn't have**: speculative scrubbing in
  parallel — try four threshold values, present four screenshots, commit
  one. Only available because there's an agent in the loop.

- **Bidirectional editing** (Sketch-n-Sketch; Recursive Drawing).
  Partial version exists today: human moves camera in live window, that's
  a graphical edit. Closing-the-loop: agent reads new camera state and
  writes it back as a `camera(...)` call. Same shape works for
  click-to-place-seed and drag-to-set-isovalue. The interesting choice
  the LLM uniquely makes is whether to write a literal (`seed_x = -247`)
  or rewrite as a derivation (`fire.x_min - 50`) given context.

- **Moldable inspectors** (Glamorous Toolkit / Tudor Girba). Each DSL
  result type renders itself in the live view: `spatial_extent` ghosts a
  bounding box; `seed_line` highlights endpoints; `flow_summary` draws a
  wind arrow at a domain corner. Spec's *structure* becomes visible, not
  just its final geometry. Side benefit: hallucination canary — wrong
  extent shows up as a misplaced box.

- **Probes** (Light Table; Hazel). A `probe(expr)` form or a `#?`
  comment evaluates at build time and surfaces in the build report or
  inline. Pure observability surface, no semantic effect.

- **Reverse pointing.** Human circles a feature in the screenshot;
  system identifies which DSL nodes contributed. VTK can answer
  pixel→actor; the missing piece is actor→spec line, which falls out of
  the existing node graph if every actor remembers its source node.

The LLM-mediated angle changes the design space. Most LIVE work assumed
a human at the keyboard. With an agent in between:
- Speculative parallel scrubbing becomes natural.
- Bidirectional edits with non-unique inverses can be mediated
  ("you dragged the seed near the fire — should I update the literal or
  rewrite as `fire.x_min - 50`?").
- The chat history is already an annotated edit trace with rationale,
  which Apparatus and Sketch-n-Sketch had to construct deliberately.

## Idea 4: unify MCP query tools through one `query(expr)`

Push it further: turn every read-only MCP query into a DSL form, and
expose a single `query(expr)` MCP tool that evaluates a DSL expression
in the context of the last-run namespace (no `show` forms allowed).

Today's pattern:

```
agent calls query_stats(field="theta")  → number
agent writes field_stats(input, field="theta")  → in spec
```

Two near-identical forms on different sides of a wall. Unify and the
wall disappears. Anything explored is one copy-paste away from being
in the spec, because it's literally the same syntax.

This shrinks the MCP surface dramatically:
- **Stays in MCP**: `load`, `new_view`, `close_view`, `screenshot`,
  `wait_for_pipeline`, view manipulation (camera, window, focus),
  version management, `query`. ~7-10 tools.
- **Moves into DSL** (callable via `query`): `describe_data`,
  `query_stats`, `get_histogram`, `get_spatial_extent`, `get_ground_z`,
  `sample_points`, `profile`, `suggest_isosurface`. ~8 tools collapse
  into "these are now DSL forms."
- **Stays separate, probably**: `get_dsl_overview`,
  `get_dsl_reference` (meta-information, not data computation).

## The output-form tension and rich reprs

Same form serves two consumers with different needs:

- **Programmatic** (in-spec use): `histogram.p99` returns a number you
  do arithmetic on.
- **Conversational** (MCP query result): an LLM/human-readable form,
  often visual.

Wrong move: pick one and force the other to live with it. Right move:
**rich-repr objects, Jupyter's pattern**. One DSL form, one returned
object, multiple representations.

```python
h = histogram(data, field="theta")
#   h.p99           → 412.3                                   programmatic
#   h.bins, .counts → arrays                                  programmatic
#   repr(h)         → "Histogram(theta): n=14523 p99=412"     terse text
#   h._repr_image() → rendered chart                          visual
```

The `query` tool evaluates the expression and returns the appropriate
repr. Default = terse text + thumbnail; LLM can be specific
(`query("histogram(data, theta).p99")`) or pass a `format="image"` hint.

## Why the pieces compose

The big payoff is that the dual-repr discipline plus query unification
is a **substrate**, not a feature. It makes everything else above
cheaper to build:

- Inline value annotation = `query(name).repr_summary()` written next to
  each binding after a build.
- Moldable inspectors = `result._repr_3d()` overlaid in the live view.
- Speculative scrubbing = `query("[expr(t) for t in range(...)]")` and
  show the strip.
- Reverse pointing = the bindings dict is what you map back to.
- Reproducible specs = queries are first-class so reference values aren't
  baked.

You're not designing five features; you're designing one thing the
features compose out of.

## Provisional priority

If we were to pick from this:

1. **Inline value annotation in the build report.** Cheapest, biggest
   readability win, doesn't commit us to anything else.
2. **Promote spatial_extent / field_stats / ground / centroid into DSL
   forms** with introspectable result objects. Makes specs reproducible;
   gives the agent the right vocabulary for reference values.
3. **Unified `query(expr)` MCP tool** + retire the one-off query tools.
   Whole-system simplification.
4. **Rich-repr objects** for query results — paves the way for moldable
   inspectors later.
5. Convention update (in instructions) — *lift names where the form
   alone doesn't tell the story; prefer queries for reference values;
   don't lift structural literals.* No top-of-file ceremony.

Bidirectional editing, scrubbing strips, reverse pointing, and moldable
3D inspectors all sit further out, but each becomes a small addition
once the substrate exists.

## Open questions

- **Namespace state for `query`.** Queries against the "last-run
  namespace" need something to have run. Default: queries see the active
  view's most recent bindings; if none, the query can include its own
  `source(...)`. MCP signature probably `query(expr, view=None)`.
- **Pure-form predicate.** Disallowing `show` is easy; mutating /
  filesystem-touching forms need a `@pure` marker.
- **Where do `# observed:` annotations live** — written to the file by
  the build, or shown only in the build report? File-write is more
  durable but couples the build to source mutation; report-only keeps
  the spec source untouched but loses the artifact.
- **Do `_repr_3d` overlays accumulate or are they on-demand?** If every
  `spatial_extent` ghosts a box, a complex spec would clutter fast. Want
  an explicit `inspect(expr)` form or hover-style trigger.
- **Anchor combinators (idea 1) revisited later?** Possibly worth it if,
  after queries land, we see the same arithmetic patterns recurring
  across many specs. Defer until evidence.
