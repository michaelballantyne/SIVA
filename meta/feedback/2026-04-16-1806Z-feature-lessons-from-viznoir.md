# Feature lessons from viznoir -- speculative

Date: 2026-04-16

**Status: speculative. Not human-agreed. Not a plan.** Fourth and final
entry in today's viznoir-comparison thread. The first three covered
security, big-data, and architecture; this one covers *features*. Same
caveats apply: opinionated framing for a conversation, not a backlog.

Complement to `2026-04-16-1804Z-architectural-lessons-from-viznoir.md`,
which explicitly excluded features. This entry picks them back up and
asks which are worth adapting for VisLang, which conflict with
VisLang's design, and which should be left alone.

The inventory below reflects what VisLang already has as of this
writing. Some of it is quite strong (streamlines, volume rendering,
filter set); some of it is missing entirely (temporal animation,
compare, probe-over-time); some exists in a weaker form than viznoir
(auto-camera is bbox+offsets vs viznoir's PCA+frustum).

---

## Features VisLang is missing that look like clear fits

### Domain-semantic data inspection

**Viznoir:** `inspect_physics` / `analyze_data` parse domain
structure. For OpenFOAM datasets, viznoir has
`OpenFOAMContextParser` that extracts boundary conditions, transport
properties, solver info, and computes derived quantities (Reynolds
number). A generic parser walks any VTK file for mesh quality
metrics.

**VisLang:** `describe_data` and `get_array_info` report VTK
metadata (field names, bounds, counts). Domain knowledge lives in
`domains/wildfire.md` as prose the LLM reads and interprets.

**Why it matters:** the LLM reading a markdown file is very
different from the tool returning structured data. Prose requires
interpretation on every session; structured parsers give consistent
answers the LLM can reliably act on. "Re = 2.1e5" as a tool return
value is much stronger than "according to domains/wildfire.md,
Reynolds number for this simulation is typically around 10^5."

**Speculative adaptation:** an `inspect_domain()` MCP tool backed by
a pluggable `DomainParser` registry. Start with two parsers: a
generic one for "any VTK dataset, report mesh quality + field
categorization," and wildfire-specific one for the dataset we
actually use. `domains/wildfire.md` becomes the *reference*, but the
*tool* returns structured `DomainInfo` that the LLM can branch on.

**Caveats:**
- Domain parsers are open-ended work. Viznoir has one good parser
  (OpenFOAM) and a weaker generic one; that took real effort.
- "Domain knowledge as code vs as prose" is a genuine design choice,
  not a pure upgrade. Prose is flexible; code is reliable. If
  VisLang's vision is "LLM reads the domain file and reasons about
  it," adding a parser competes with that philosophy.
- Worth starting with the question: what does the LLM currently get
  *wrong* about our test datasets that a structured parser would
  prevent? Feedback entries from bonsai/wildfire sessions probably
  have the answer.

### Compare / diff between runs

**Viznoir:** `compare` tool takes two simulation results, renders
them side-by-side or as a difference field.

**VisLang:** no equivalent. The LLM would have to load one, render,
load the other, render, and position the images externally.

**Why it matters:** comparing runs is a canonical scientific
workflow. Parameter sweeps, before/after optimization, grid
resolution studies -- all require side-by-side or delta views. This
is one of the few tools where "viznoir has it, VisLang doesn't" maps
directly to "this workflow works on viznoir and doesn't on VisLang."

**Speculative adaptation:** a `compare(dataset_a, dataset_b, field,
mode="side_by_side"|"diff")` tool. Side-by-side is a composition
primitive (relates to the split-pane item below). Diff requires
computing `field_a - field_b` on matching topology, which is only
well-defined when grids align -- so the tool needs to detect grid
match and error cleanly when they don't.

**Caveats:**
- Diff-field semantics are subtle. Unstructured grids from different
  runs may have different point ordering even with the same
  topology. Viznoir sidesteps some of this with `vtkResampleToImage`
  before diffing; that works for some cases and silently
  discretizes others.
- The error-reporting from this tool is where the typed-error work
  from the architecture entry would pay off.

### Probe-over-time (time-series sampling)

**Viznoir:** `probe_timeseries` samples a field at a fixed spatial
point across all timesteps, returns a time-series.

**VisLang:** `sample_points` samples at a moment; no temporal
variant. Multi-timestep is on the BACKLOG generally.

**Why it matters:** probing at a point over time is one of the most
common scientific tasks ("show me temperature at the sensor location
over the whole simulation"). It's also one of the simplest primitive
operations once multi-timestep loading exists. If time-series lands
(per the big-data entry), probe-over-time should land with it, not
as a later follow-up.

**Speculative adaptation:** `probe_timeseries(x, y, z, field)` that
returns `[(t_0, value_0), (t_1, value_1), ...]`. Pairs naturally
with the lazy-source proposal from the big-data entry -- it's one of
the operations that benefits most from *not* loading all timesteps
into memory first.

**Caveats:**
- Blocked on multi-timestep support. That's the upstream dependency.
- Format of the return value matters: raw data array? PNG plot?
  Probably both, with the LLM choosing via a `format=` arg.

### Multi-pane synchronized composition

**Viznoir:** `split_animate` produces 2-4 panes in a grid, mixing 3D
renders with time-series plots, all synchronized to the same time
base. Layouts: story, grid, slides, video.

**VisLang:** `new_view`/`focus`/`close_view` create independent
render contexts for *view-switching*. Not composition. There's no
primitive for "produce a single image with 3D render on the left and
a plot on the right."

**Why it matters:** scientific presentations and papers are almost
always multi-pane ("Figure 3: left, temperature isosurface; right,
time-history at the centerline"). Having composition as a
first-class primitive -- rather than something the user does in
PowerPoint after the fact -- is the kind of feature that changes how
scientists think about the tool.

**Speculative adaptation:** a `compose(layout, panes)` tool where
panes are 3D views, plots, or text. Or, more VisLang-shaped: expose
composition in the DSL so pipelines can declare their own layouts.
`grid([render_a, render_b], [plot_c])` as a sibling of `show()`.

**Caveats:**
- Serious scope. Viznoir's `anim/compositor.py` is ~hundreds of
  lines and handles frame synchronization, transitions, layouts.
- This is where the "interactor vs static output" tension bites.
  Composed multi-pane output is inherently a static artifact (PNG,
  GIF, MP4). VisLang's interactor has one window. Either we accept
  that compose produces static output separately from the
  interactor, or we contort the interactor to show multi-pane (VTK
  supports this via multiple `vtkRenderer`s in one
  `vtkRenderWindow`, but the UX is awkward).
- Maps to the "new_view is switching, not composition" observation
  -- making new_view *compositional* instead of replacing it is the
  smaller-scope version of this idea.

### Case presets / pipeline templates as resources

**Viznoir:** MCP resources at `viznoir://pipelines/cfd`,
`viznoir://pipelines/fea`, `viznoir://pipelines/split-animate`
return ready-to-run pipeline DSL snippets. Case presets
(`viznoir://case-presets`) are configuration bundles for common
simulation types.

**VisLang:** `quick_start(filename)` generates starter code but is
generic. `domains/wildfire.md` is prose. No structured catalog of
"here's a pipeline template for CT volume rendering with a good
transfer function."

**Why it matters:** the LLM's first-pipeline quality is mostly
determined by what it starts from. A strong starting point shortens
the feedback loop from "load dataset -> render -> realize it looks
wrong -> iterate" to "load template -> adjust one parameter."

**Speculative adaptation:** a `pipelines/` resource tree with
snippets indexed by dataset type ("ct-volume", "cfd-flow",
"structured-scalar-field"). Tool: `get_pipeline_template(kind)`
returns the Python DSL source. The LLM can use it verbatim, edit it,
or inline it into a session-specific pipeline.

**Caveats:**
- Feels obvious but the failure mode is a shallow catalog that no
  one maintains. Two or three really good templates are worth a
  dozen half-complete ones. Start with "CT volume + bonsai/cthead"
  and "wildfire structured scalar + streamlines" since those are
  the datasets with real feedback history.

---

## Quality-of-defaults upgrades

VisLang has these, but viznoir's implementation is better in ways
that matter for the LLM's "first render looks right" experience.

### PCA-based auto-camera

**Viznoir:** `engine/camera_auto.py` -- PCA on the geometry to find
principal axes, classify shape (plate / tube / sphere), compute
frustum-fitted distance with a target fill-ratio. Output: camera
pose that actually frames the dataset well regardless of aspect
ratio.

**VisLang:** `suggest_camera` uses bounding-box center with
style-specific fixed offsets ("overview", "closeup", "top_down",
"side"). Works; but long-thin geometries (a channel flow, a wing, a
fire column) get framed poorly.

**Why it matters:** this is the single biggest lever for "the LLM's
first render looks correct." A good camera makes every subsequent
interaction shorter because the LLM doesn't have to iterate on pose
before iterating on content.

**Speculative adaptation:** port the PCA + frustum-fit approach. The
shape classification is optional but cheap (eigenvalue ratios tell
you plate vs tube vs sphere); worth doing. Keep `suggest_camera`'s
styles as modes on top of the PCA fit.

**Caveats:**
- Viznoir's `camera_auto.py` is ~450 lines. A lot of that is edge
  cases. Budget more than the initial estimate.
- Works better on geometry-dominant datasets than volume datasets.
  Keep the bbox-offset fallback.

### Animation easing

**Viznoir:** `anim/easing.py` has 17 easing functions (based on
Manim's `rate_functions`). Orbit animations and transitions feel
less robotic with ease-in/out applied.

**VisLang:** `camera_orbit` is linear interpolation.

**Why it matters:** minor polish. Matters more for animations
shared with other people (papers, talks) than for interactive
iteration.

**Speculative adaptation:** add an `easing=` kwarg to `camera_orbit`
and any future animation tools. Default to "ease_in_out_sine" or
similar.

**Caveats:**
- Low-priority polish. Worth noting but not worth building before
  the bigger gaps (#1-#5 above) are closed.

---

## Bigger product directions

These are features where the adaptation question is less "should we"
and more "is this the kind of tool VisLang wants to be."

### Cinematic rendering stack

**Viznoir:** `cinematic_render` with 3-point lighting presets
(cinematic/dramatic/studio/publication/outdoor), SSAO, FXAA, PBR
materials, auto-framing. Meant for publication-quality output.

**VisLang:** basic VTK rendering. Specular/specular_power exposed
but no PBR/SSAO/FXAA/lighting pipeline.

**The question:** VisLang's center of gravity is interactive
research use (native interactor, live iteration). Viznoir's
center of gravity is async publication-quality output. Cinematic
rendering serves the latter. Adopting the whole stack would pull
VisLang toward a use case it's not currently aimed at.

**Speculative position:** probably skip the full stack. Possibly
adopt a subset: FXAA is cheap and makes interactive renders look
less aliased; a single "publication" lighting preset (simpler than
viznoir's five) might be worth it for users who want to export a
figure. SSAO and PBR are the parts that really are "for
publication" and probably don't pay off for interactive use.

### Auto-postprocess / one-shot render

**Viznoir:** `auto_postprocess` picks defaults and produces a render
without the LLM specifying filter details. The "give me a picture"
button.

**VisLang:** no equivalent. The LLM always specifies the pipeline.

**The question:** does this fit VisLang's declarative-DSL
philosophy? The DSL exists *because* specifying the pipeline
explicitly is the value prop. An auto-postprocess tool competes
with that -- it's a shortcut that makes the DSL feel optional.

**Speculative position:** skip, unless framed specifically as a
template-lookup (which is the "case presets" item above) rather
than a black box. "Based on this dataset's characteristics, here's
a pipeline template I generated for you" preserves the DSL-centric
shape; "here's a picture, I'm not telling you what I did" doesn't.

### glTF / glB web export

**Viznoir:** `preview_3d` exports to glTF/glB for browser-based
interactive viewing.

**VisLang:** native VTK interactor instead. glTF is absent.

**The question:** VisLang's interactor *is* the interactive viewer.
glTF is redundant for the local-user case. It's only interesting
for sharing results with people who don't have VisLang installed
(collaborators, reviewers, a web page).

**Speculative position:** low priority. Add if/when there's a
concrete sharing use case. The native interactor serves the primary
use case; glTF is a nice-to-have for a future collaboration feature.

---

## Features I'd skip outright

- **Batch render**: "render the same scene with N fields in one
  call" is a convenience wrapper, not a feature. The LLM can call
  render N times.
- **Mesh quality analysis (FEA)**: useful, but domain-specific. If
  VisLang gets FEA users, revisit. For now it's scope creep.

---

## Pattern-level lessons (more interesting than individual features)

A few meta-observations from comparing the feature sets that are
worth naming even though they don't map to individual tools:

### Domain knowledge as code, not just prose

Viznoir codes its OpenFOAM knowledge into a parser. VisLang keeps
its wildfire knowledge in markdown. Both work; the parser gives the
LLM structured data that's consistent across sessions, prose
requires the LLM to re-interpret each time. As VisLang's domain
coverage grows, the parser approach scales better. But committing
to it means writing parsers, which is domain-expert work, not just
documentation.

### Composition as a first-class concept

Viznoir's `split_animate` treats multi-pane output as a pipeline
primitive. VisLang's `new_view` treats multi-view as a mode-switch.
Those are different mental models. The compositional model is the
one that lets "3D view + time-series plot + text annotation" be a
single artifact. It's the single pattern most worth stealing if
VisLang's audience includes people who make presentations or
papers.

### Suggestions vs. automatic defaults

VisLang has many `suggest_*` tools. Viznoir has `auto_postprocess`.
Both address "make the LLM's job easier" but differently:
suggestions are a recommendation the LLM acts on; auto-postprocess
is action-taken-for-you. VisLang's existing choice of "suggest, let
the LLM choose" feels right for a declarative DSL tool. Don't drift
toward auto-action without deliberate thought about whether it
fits.

### Per-session templates vs global templates

Viznoir ships case presets as global resources. VisLang has
`sessions/*/pipeline.py` as per-session state. These are
complementary, not competing. A workflow like "start from template,
customize in session, extract reusable patterns back to template
catalog" could be really nice. Requires the template catalog
exist, which it currently doesn't.

---

## Suggested ordering

From "most likely to pay off" to "most research-question":

1. **PCA auto-camera** -- biggest quality-of-first-render lever,
   bounded effort (~450 lines of reference code to port).
2. **Pipeline templates / case presets** -- small amount of content
   work, big LLM-experience win. Start with two templates for the
   datasets with the most feedback history.
3. **Compare tool** -- landmark scientific workflow, missing gap.
   Medium effort, high value.
4. **Domain-semantic inspection** (pluggable parser) -- design
   question plus real content work. Worth the conversation about
   "parser vs prose" before building.
5. **Probe-over-time** -- blocked on multi-timestep; small once
   that lands.
6. **Multi-pane composition** -- biggest feature, biggest value,
   biggest design question. Research-grade.
7. **Easing, FXAA, individual polish items** -- land opportunistically
   when touching the relevant code paths.

Items to skip (for now): cinematic stack, auto-postprocess, glTF
export, batch render, mesh quality analysis.

---

## Meta

As with the architecture entry, this is a read for conversation,
not a mandate. The "clear fits" section is the highest-confidence
material; the "bigger product directions" section is where the
real strategy questions live. Anyone picking this up should
probably start by asking the human which direction VisLang is
aimed at -- interactive research tool, publication-quality output,
collaborative sharing tool -- because the feature priorities
differ significantly across those three.
