# VisLang: Vision and Design

This document serves two purposes: it describes the **current system** as
built, and it lays out the **longer-term vision** for where the project is
heading. The structure reflects this:

- **Part 1 (Current Design)** describes what exists today — the architecture,
  DSL, MCP tools, and interaction model. This is the reference for anyone
  working on the codebase.
- **Part 2 (Next Steps)** covers what's actively being built or designed.
- **Part 3 (Longer-Term Vision)** describes where the system could go —
  the LSP, reconciler, interactive widgets, and the broader pattern of
  human+AI collaborative tooling for stateful runtimes.
- **Part 4 (Research Context)** covers related work and research framing.

When updating this document, preserve this structure. Current design should
reflect what's actually in the code. Vision sections should be clearly
marked as future directions, grounded in real experience and concrete
motivations.

---

# Part 1: Current Design

## What VisLang Is

VisLang is a system for interactively building scientific visualizations
through conversation with an LLM. A human and an AI collaborate through a
shared artifact — a declarative pipeline file — with the AI writing pipeline
code via MCP tools and the human observing results in a live VTK render
window.

The key properties:

- **Declarative** — the pipeline file describes desired state as a Python
  script calling builder functions. Each execution tears down and rebuilds
  the full pipeline (data source readers are cached to avoid re-reading
  large files).
- **Conversational** — the LLM has rich query tools to understand the data
  before making decisions, and gets structured feedback after every change.
- **Transparent** — the pipeline file is readable by both human and AI,
  version-controlled, and reproducible. A colleague can read `view-main.py`
  and understand exactly what's being shown.
- **Interactive** — the user can rotate, zoom, and inspect the visualization
  at any time in the VTK render window.

## Architecture

```
 Claude Code (LLM)          Human
     │                        │
     │  MCP protocol          │  edits pipeline file
     ▼                        │  observes render window
 ┌──────────────────────────┐ │
 │  MCP Server (Python)     │ │
 │                          │ │
 │  Mutation tools:         │ │
 │   set_pipeline(file)     │◄┘
 │   set_camera(...)        │
 │   set_colormap(...)      │
 │   ...                    │
 │                          │
 │  Query tools:            │
 │   describe_data()        │
 │   get_statistics(...)    │
 │   suggest_isosurface()   │
 │   suggest_opacity()      │
 │   screenshot()           │
 │   ...                    │
 │                          │
 │  DSL interpreter         │
 │       │                  │
 │       ▼                  │
 │  Tear-down / rebuild     │
 │  (with reader caching)   │
 └──────────┬───────────────┘
            │
            ▼
    ┌──────────────┐
    │  VTK Renderer │
    │  + Interactor │
    │  (native wnd) │
    └──────────────┘
       ▲
       │ mouse / keyboard
       Human
```

The MCP server and VTK viewer live in the **same process**. The VTK
interactor owns the main loop. The MCP server runs in a background thread
and posts work to the main thread via a queue drained by a VTK timer
callback (~100ms). This keeps mouse interaction smooth while allowing the
LLM to push changes.

The server runs in two modes:
- **Interactive** (default) — opens a live VTK window for direct manipulation
- **Offscreen** (`--offscreen`) — headless rendering, returns screenshots
  only. Used for CI, automated sessions, and subagent work.

## The DSL

A pipeline is a Python script that calls builder functions. It is **not**
imperative VTK code — the builder functions register nodes in a desired-state
graph that is then executed to produce VTK objects.

### Core functions

```python
# Data loading
data = source("vtkNrrdReader", FileName="bonsai.nhdr")

# Filtering
wood = threshold(input=data, ThresholdBy="ImageFile",
                 ThresholdRange=[20, 145])

# Display
show(wood, "wood", representation="Volume",
     color_by="ImageFile", scalar_range=[20, 145],
     colormap="fire", scalar_bar="Density")

# Scene
camera(position=(300, 300, 300), focal_point=(128, 128, 128))
background(0.1, 0.1, 0.15)
```

The DSL provides convenience wrappers for common VTK filters (`threshold`,
`contour`, `stream_tracer`, `glyph`, `clip`, `slice`, `gradient`, etc.)
alongside generic `source()` and `filter()` functions for anything not
wrapped. `show()` handles all mapper/actor/property boilerplate and supports
surface, wireframe, points, and volume representations.

### The pipeline file as communication medium

The pipeline file serves as a **communication medium between the LLM and the
domain expert.** The expert reads it to verify scientific correctness ("is
that really a threshold at density 20?"); the LLM writes it to express
visualization intent. This dual readership is a stronger justification for
the DSL than LLM convenience alone — the DSL must be readable enough that a
scientist can audit it without understanding VTK internals.

### Execution model

Each `set_pipeline` call:

1. Executes the pipeline file as Python, collecting node declarations
2. Tears down all existing VTK objects (except cached readers)
3. Builds the full pipeline from scratch
4. Returns a structured report: node names, point/cell counts, arrays, any
   errors or warnings

Reader caching makes this fast enough for interactive use — the expensive
file I/O happens once, and subsequent rebuilds only recreate the filter
graph.

### Named views

The system supports multiple named views, each with its own pipeline file,
VTK objects, and version history:

```python
# Creates view-overview.py
new_view("overview")

# Switch between views
focus("main")
focus("overview")
```

## MCP Tools

The MCP server exposes ~35 tools organized by function:

**Mutation tools** change the visualization state:
- `set_pipeline(file)` — execute a pipeline file
- `set_camera(...)`, `set_colormap(...)`, `set_opacity(...)` — adjust
  display properties
- `toggle_visibility(...)`, `set_background(...)`, `annotate(...)` — scene
  management
- `load(filename)` — load a data file (auto-detects reader)

**Query tools** provide data-aware intelligence:
- `describe_data()` — field names, types, ranges, percentiles, distribution
  shape
- `get_statistics(node, field)` — min, max, mean, std, percentiles
- `suggest_isosurface(node, field)` — histogram-guided contour values
- `suggest_opacity(node, field)` — histogram-guided opacity transfer functions
- `get_histogram(...)`, `get_spatial_extent(...)`, `sample_points(...)`,
  `profile(...)` — quantitative data exploration
- `screenshot()` — render the current scene and return the image

**Reference tools** help the LLM write correct pipelines:
- `get_dsl_reference(form)` — full documentation for a DSL function
- `get_examples()` — working pipeline patterns
- `list_capabilities()` — overview of available DSL forms

### A programming system, not just a language

VisLang is better understood as a **programming system** than as a DSL.
Drawing inspiration from the live programming tradition — Smalltalk's
inspectability, the Dynabook's vision of a reshapable medium, Victor's
emphasis on making computation visible — the aspiration is an integrated
environment where the language, execution model, feedback mechanisms, and
interaction modalities are designed together and for each other. The value
comes from the system as a whole, not from any single component.

The language has clean declarative semantics — a pipeline spec describes
desired state with no side effects, no accumulated mutation, no ordering
dependencies. This enables the interactive system around them:

- **Safe re-execution** — declarative and stateless means the pipeline
  can be torn down and rebuilt on every edit. This is what makes hot
  reload, version rollback, and parameter scrubbing possible. Imperative
  VTK code accumulates state that makes re-execution unsafe.
- **The pipeline file as shared artifact** — because the spec is
  declarative and readable, it serves as a communication medium between
  human and AI. A scientist can audit `threshold(..., ThresholdRange=[20,
  145])` without understanding VTK internals. The human and the LLM
  iterate on the same file.
- **Named nodes as stable identities** — variable names become node
  identities that the query tools, the build reports, the version diffs,
  and the LSP all reference. This weaves the language into the rest of
  the system: `get_statistics("wood", "ImageFile")` names the same node
  as `wood = threshold(...)` in the spec.
- **Error diagnostics as a first-class design principle** — VTK fails
  silently: a misconfigured filter produces empty output with no
  indication that anything went wrong. This is trust-destroying in an
  interactive loop — the LLM can't recover from something it can't
  detect, and the human has no idea why nothing appeared. The DSL treats
  loud, actionable error diagnostics as a core design concern: field
  name validation with "did you mean?" suggestions, empty output warnings
  with range hints, explicit failure rather than silent fallback. Every
  error message is designed to contain enough information for the next
  correction, whether it's read by an LLM in a tool result or by a
  human in an LSP diagnostic.
- **Abstraction calibrated for both audiences** — `show()` hides VTK's
  mapper/actor/property boilerplate; `colormap="fire"` replaces manual
  lookup table construction. The abstraction level is chosen so the spec
  remains readable to a domain expert while being writable by an LLM
  without deep VTK knowledge.
- **The pipeline file as a learning artifact** — the human starts by
  watching the AI write pipelines, reading the code to understand what's
  being shown. Over time they learn the DSL and begin co-authoring —
  making direct edits, then asking the AI to refine. The DSL's readability and
  the shared-file interaction model are designed to support this progression
  from observer to collaborator.

The query tools (describe_data, suggest_isosurface, get_histogram) and
the feedback tools (screenshot, structured build reports) are equally
part of the system. They close the loop: the language lets you express
intent, the execution gives you feedback, the tools let you query the
data to inform the next edit.

## Version History

Every `set_pipeline` call saves the pipeline code and a screenshot to a
versioned history:

```
.vislang/history/
  v001/
    pipeline.py
    screenshot.png
  v002/
    pipeline.py
    screenshot.png
```

`restore_version(n)` rolls back to any previous state. The version history
makes exploration safe — the user and LLM can experiment freely knowing
any previous state is recoverable.

## User Interface

The interface is three panes:

```
┌─────────────────────────┬──────────────────────┐
│                         │                      │
│   Claude Code terminal  │    VTK render window  │
│                         │                      │
│   (conversation +       │    (rotate, zoom,    │
│    tool responses)      │     inspect)         │
├─────────────────────────┤                      │
│                         │                      │
│   Text editor           │                      │
│   (view-main.py)        │                      │
│                         │                      │
└─────────────────────────┴──────────────────────┘
```

The human watches the pipeline file evolve as the LLM edits it. Over time
they learn the DSL and can make direct edits themselves. The pipeline file
is the shared artifact — readable by both human and LLM, version-controlled,
and reproducible.

---

# Part 2: Next Steps

These are actively being designed or built, informed by real session
experience (particularly the bonsai CT session of April 2026).

## File-watching hot reload

In terms of liveness: the current system requires an explicit tool call to
rebuild (Tanimoto's level 2). Hot reload moves pipeline re-execution to
level 3 (auto on save). Parameter scrubbing (Part 3) aims for level 4
(continuous). Note that liveness isn't a single axis — visual feedback
from the render window is already continuous, while pipeline re-execution
and data query feedback currently are not.

**Motivation:** Currently, editing the pipeline file requires an explicit
`set_pipeline` tool call to trigger a rebuild. This creates friction for
both the human (who must ask Claude to "set pipeline" after every manual
edit) and Claude (who must Write the file and then call set_pipeline as
two separate steps).

**Design:** The server watches pipeline files for changes and auto-rebuilds
on save. Build output (success summary or error) is written to a status file
next to the pipeline file (`view-main.py` → `view-main.status.txt`). The
human sees build feedback by opening the status file in a split view. Claude
reads the status file after writing a pipeline to check for errors.

This eliminates the set_pipeline tool for the common case. The tool may
remain as a fallback or explicit rebuild trigger, but the primary workflow
becomes: edit the file → server rebuilds automatically → check status.

## Screenshot separation from mutation tools

**Motivation:** Every mutation tool currently auto-returns a base64
screenshot in its result. In long sessions, this accumulates tens of MB of
image data in Claude's context, eventually hitting the 20MB API request
limit (observed after ~49 screenshots in the bonsai session).

**Design:** Remove auto-screenshots from all mutation tools. Add resolution
options to `screenshot()` (low/high, defaulting to low). Guide Claude to
call screenshot in the same turn as mutation tools when visual feedback is
needed, and to check the status file first to avoid wasting an image on a
broken build.

## Spatial-region statistics

**Motivation:** `get_statistics` operates on the whole dataset. In the bonsai
session, the user wanted to understand density ranges in specific regions
(above soil vs. below soil) to choose thresholding values. This required
20+ rounds of trial and error.

**Design:** Accept a pipeline node (post-threshold, post-clip) or spatial
bounds as parameters to get_statistics, so statistics can be computed on
subregions.

---

# Part 3: Longer-Term Vision

## The central principle: shared intelligence, different channels

The bonsai session exposed a fundamental asymmetry: Claude has rich tools
for understanding the data (get_statistics, suggest_isosurface,
suggest_opacity) but the human editing the pipeline file has none of this.
When the human edits in their text editor, they're flying blind — no field
name completion, no value range hints, no immediate feedback.

The human and the AI should have access to the same data-aware intelligence,
surfaced through different channels appropriate to each:

| Intelligence | For Claude (MCP) | For the human (editor) |
|---|---|---|
| Field names and types | describe_data() | Autocomplete |
| Value ranges | get_statistics() | Hover info |
| Suggested values | suggest_isosurface() | Code actions |
| Build errors | Tool result text | Status file → LSP diagnostics |
| Visual result | screenshot() | Render window |

This principle should guide all tooling decisions: don't add a capability
for one consumer without considering how the other gets it too.

## Language server for the pipeline DSL

An LSP for the pipeline DSL would surface the same data-aware intelligence
that the MCP tools provide, but through standard editor UI:

- **Autocomplete** for DSL form names, parameter names, and field names
  from the loaded dataset
- **Hover info**: hover over a field name to see its range, type, and
  component count; hover over a threshold value to see what fraction of
  points it selects
- **Inline diagnostics**: "field 'Temperture' not found, did you mean
  'Temperature'?", "threshold range [500, 600] selects 0 points"
- **Code actions**: "suggest isosurface value", "suggest opacity function"

The backend queries already exist — get_statistics, suggest_isosurface,
suggest_opacity, describe_data. The MCP tools and the LSP would share the
same underlying query layer; they're just different delivery channels.

### One query interface, two protocols

This suggests an architecture where the core intelligence lives in a
query layer accessible through both LSP and MCP:

```
                    ┌─────────────┐
     LSP protocol ──┤             │
     (for human)    │ Query Layer │──► VTK data
                    │             │
     MCP tools ─────┤  statistics │
     (for Claude)   │  suggestions│
                    │  validation │
                    └─────────────┘
```

Or more ambitiously: a single LSP server that Claude accesses through an
LSP-to-MCP bridge. This would guarantee that human and AI always have
exactly the same capabilities.

### Node info view

Inspired by Lean's InfoView — where clicking any expression shows what
the type system knows about it — the editor could provide a contextual
info panel for pipeline nodes. Click on a node or `show()` call and see:

- **Data shape** — point/cell counts, dimensions, bounds
- **Array summaries** — per-field statistics, mini inline histograms
- **Isolated render** — for visual elements, a version of the current
  scene showing only that element with the same camera. In a complex
  scene with overlapping layers, this immediately answers "what is this
  `show()` actually contributing?"

The isolated render is especially valuable — it's something neither the
current MCP tools nor statistics can tell you. You can query a node's
point count, but you can't see its visual contribution apart from
everything else in the scene.

For the two contexts:
- **LSP (human)** — a panel that updates on cursor position. Click on
  `wood = threshold(...)` to see data summaries; click on
  `show(wood, ...)` to see the isolated render.
- **MCP (Claude)** — an `inspect("wood")` tool returning the same
  structured info as text, with an optional isolated screenshot. Claude
  calls it when it needs to understand what a specific node contributes.

The underlying query is the same — "tell me everything about this node,
including what it looks like alone" — with presentation adapted to each
channel.

A more ambitious version, inspired by Victor's "Learnable Programming":
make intermediate data summaries *ambient* rather than on-demand. The
editor could show a miniature data summary (point count, bounds, a tiny
histogram) inline next to each pipeline node, always visible, updating
live. Not just click-to-inspect but constant awareness of the dataflow
at every stage. This makes the data pipeline tangible — you see what each
step does to the data without having to ask.

## Bidirectional editing

The current model is code → visualization: edit the pipeline file and the
scene updates. Inspired by Sketch-n-Sketch (Chugh et al.), the system
could support the reverse: manipulate the visualization and the code
updates to match.

- Drag a clip plane in the 3D view → the `clip(origin=(...),
  normal=(...))` line in the pipeline rewrites itself
- Rotate the camera and settle on a view → `camera(position=...,
  focal_point=...)` updates in the file
- Shift-click to place streamline seeds → `seeds_near(...)` parameters
  update

This transforms the pipeline file from "code that drives the scene" to
"a live representation that stays synchronized with the scene regardless
of how the human got there." The code is always true, whether the human
last edited it by typing, by direct manipulation, or by asking the AI.

The existing "widgets as scaffolding" idea (LLM places an interactive
widget, user adjusts, LLM freezes coordinates into code) is a special
case of bidirectional editing. The general version doesn't require
explicit widget deployment — *any* visual manipulation flows back into
the code.

A concrete mechanism from Sketch-n-Sketch: during forward evaluation,
record a *trace* mapping each visual property back to the DSL literals
that produced it (clip plane position → `origin=(x,y,z)`, color range →
`scalar_range=[lo, hi]`). When the user manipulates a visual property,
the trace identifies which code literals to update. When the mapping is
ambiguous, the LLM could serve as the disambiguation layer — or present
ranked options as Sketch-n-Sketch does.

There's a further possibility, inspired by Victor's "Drawing Dynamic
Visualizations" and Lyra 2's design-by-demonstration: the human
manipulates the scene, and the *AI* generalizes the manipulation into
pipeline code. Victor required a custom inference engine to generalize
from examples; an LLM could serve as that generalizer, using its
understanding of the data and the DSL to turn a demonstrated interaction
into a reusable pipeline pattern.

Denicek's programming-by-demonstration idea extends this from individual
manipulations to recorded *sequences* of actions that become replayable
programs. It's unclear how much this applies when the user has an AI
collaborator — most tasks are easier to describe in conversation than
to demonstrate step by step. But for things that are hard to verbalize
(spatial positioning, aesthetic choices), recording and replaying
interactions could complement conversation.

## A reshapable medium

In the Dynabook vision (Kay & Goldberg, 1977), the computer is a medium
the user reshapes, not a fixed tool they operate. VisLang currently
offers a fixed vocabulary — the built-in DSL forms, the built-in query
tools, the built-in colormaps. The user can compose these freely but
can't change the system itself.

A more ambitious version: the user (with AI help) reshapes the system
to fit their domain and workflow:

- **Pipeline abstractions** — define reusable visualization patterns.
  `fire_overview(data, theta_threshold=400)` as a composition of
  threshold + volume + streamlines, becoming a first-class building
  block. Session-specific or domain-specific, composing with built-in
  forms.
- **Custom colormaps and display presets** — define domain-standard
  visual conventions that become part of the vocabulary. "CT bone window"
  or "fire simulation standard" as named presets.
- **Domain-specific validation and queries** — "theta below 300 is
  ambient, don't threshold there" as a rule the system enforces. Custom
  info view panels showing domain-relevant summaries.
- **Workflow patterns** — define multi-step exploration sequences that
  encode domain expertise. "For a new fire simulation: extract terrain,
  find the fire front, seed streamlines upwind."

The LLM is a natural partner for authoring abstractions — it can
recognize repeated patterns across a session and propose extracting them
into named functions. Litt's "Malleable Software" thesis argues that
LLMs are the missing bridge that makes the Dynabook vision practical:
users couldn't reshape their software because programming was too hard;
the LLM translates intent into modifications. Over time, the shared
vocabulary between human and AI grows richer and more domain-specific,
and the system adapts to the user rather than the user adapting to the
system.

How far this goes is an open question. The simplest version is just
Python functions in a shared library that pipeline files can import. The
most ambitious version is a system where every aspect — the DSL forms,
the query tools, the validation rules, the info view — is user-definable
and the built-in components are just the default configuration.

## Pre-execution validation and cost awareness

Currently, validation happens after pipeline execution — field name errors
and empty output are detected post-`Update()`. But for VTK pipelines,
building the graph (instantiating objects, connecting inputs, setting
properties) is cheap; the expensive part is `Update()`, which pushes data
through the filters. This means we can do substantial validation *between
building and executing* — no type inference or constraint solving needed,
just inspecting the VTK objects we've already constructed.

After building the pipeline graph but before calling `Update()`:

- **Field name validation** — check that referenced arrays exist on the
  input data. The source reader is already cached and updated, so its
  output metadata is available. "Field 'Temperture' not found, did you
  mean 'Temperature'?"
- **Connection type checking** — verify data type compatibility. "Volume
  rendering requires structured data, but threshold produces polydata."
  "Stream tracer requires a vector field, but 'ImageFile' is scalar."
- **Range-aware warnings** — check parameter values against known field
  ranges from the source data. "Isosurface value 300 is above field
  maximum of 255."
- **Cost estimation** — estimate execution cost from input size and
  filter complexity before committing to `Update()`. "Streamline tracing
  on 18M cells with 500 seeds: estimated ~30s. Proceed?" This prevents
  the freeze-with-no-feedback failure mode on large data.

For small datasets this barely matters — just run it. But for large data
or expensive filters (streamlines, volume rendering), the gap between
"build" and "execute" could be seconds or minutes. Catching errors before
that wait is the difference between a tight feedback loop and a frustrating
one.

This pre-execution validation is also what the LSP would surface as
diagnostics — the same checks, run on every keystroke against the built
(but not executed) pipeline graph.

Further out, **semantic field annotations** could enhance validation: if
the system knew that "theta" is temperature and "u,v,w" are velocity
components, it could auto-suggest derived quantities, appropriate
colormaps, and meaningful threshold ranges. This connects to the domain
knowledge files in `domains/`.

**Composition patterns** — first-class support for common multi-layer
patterns like "isosurface + volume overlay" or "side-by-side comparison
of two fields" — could also be validated at this stage, checking that the
layers are compatible before executing any of them.

## Interactive parameter scrubbing

Click a numeric literal in the editor, drag to change it, see the render
update live. The declarative, stateless DSL makes this feasible — you can
re-execute the full pipeline on every value change.

The scrubbing UI needs the same range information the LSP already has
(field min/max for bounds, histogram data for meaningful steps). This is
a third channel for the same query layer.

**Performance note:** Interactive scrubbing requires fast pipeline
rebuilds. The current tear-down/rebuild approach works for small-to-medium
datasets but may be too slow for large data. This is where the reconciler
becomes important again (see below).

## Reconciler for incremental updates

The original design specified a reconciler that diffs the desired pipeline
graph against the live VTK state and applies minimal changes. This was
replaced during initial development by a simpler tear-down/rebuild approach
that proved fast enough for the wildfire and bonsai datasets.

The reconciler becomes important again when:
- **Datasets get large** and full rebuilds take noticeable time
- **Interactive scrubbing** requires sub-frame pipeline updates
- **Complex pipelines** with expensive intermediate filters (streamlines,
  volume rendering) would benefit from only re-running the changed branch

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
```

The reconciler would return a structured report including what changed,
output statistics, and what the LLM could do next — collapsing the
act/query cycle into a single response.

## Interactive widgets

VTK has a rich widget framework (planes, spheres, boxes, sliders) for
direct 3D manipulation of filter parameters. The DSL could make these
declarative:

```python
# Interactive clip plane — user drags to explore cross-sections
plane = interactive_plane(normal=(0, 0, 1))
clipped = clip(input=fire, plane=plane)
show(clipped, color_by="theta")

# Interactive threshold range
temp_range = interactive_range(field="theta", initial=[350, 800])
hot_region = threshold(input=data, field="theta", range=temp_range)
show(hot_region, representation="Volume", color_by="theta")
```

### Widgets as temporary scaffolding

Widgets can serve as a parameter-picking UI that the LLM deploys on demand.
When the user says "add streamlines near the fire," the LLM places an
interactive seed sphere. The user drags it to the right spot. The LLM reads
the widget state, replaces the interactive element with fixed coordinates,
and removes the widget. The widget is scaffolding that exists during
collaborative parameter selection and compiles away into concrete values.

## Multi-resolution preview

For large datasets, maintain a subsampled proxy alongside the full data.
Iterate quickly on the cheap version, then promote to full resolution once
the pipeline stabilizes.

Cost estimation becomes empirical: "preview took 0.3s on 1/200th data,
full resolution estimated ~30-60s."

## User interaction capture

The viewer could capture user interactions and make them available to the
LLM, enabling spatially-aware assistance:

- **Camera state** included in screenshot responses, so the LLM knows
  what the user is looking at
- **Point marking** via shift-click — the user marks points of interest,
  the LLM reads coordinates via a tool and uses them for seed placement,
  slice positioning, etc.

## Smart defaults and encoding types

Following Vega-Lite's model, the DSL could infer reasonable defaults from
data characteristics:

- `show(fire)` without `color=` picks a sensible color distinct from
  existing actors
- `show(terrain, color_by="rhof_1")` without `scalar_range=` auto-sets
  from data min/max
- Encoding types (sequential, diverging, categorical) drive automatic
  colormap selection

Every inferred value would be reported in the build output, so the pipeline
remains verifiable even when terse.

## From exploration to communication

An interactive session produces understanding — the human and AI load data,
try representations, query statistics, find the right views. But that
understanding is trapped in the conversation. The session's real output
should be a **communicable artifact**: a notebook or report that tells the
story of what was found, with interactive visualizations that readers can
explore themselves.

After an exploration session, the AI assembles a Jupyter notebook. Each
cell contains a self-contained VisLang pipeline that renders an interactive
3D view (via Trame). The notebook is structured as a narrative: prose
explaining what was found and why it matters, interleaved with live
visualizations the reader can rotate, zoom, and inspect.

The version history from the session provides the raw material — each
version is a pipeline + screenshot pair, and the AI has the conversation
context to explain why each view matters. The pipeline file format makes
this natural — each notebook cell is just a pipeline spec, and a small API
(`vislang.notebook.show(code)`) renders it as an interactive trame widget
inline.

This connects two distinct phases of work:
- **Exploration** — human + AI iterating together through MCP, trying
  things, querying data, building understanding
- **Communication** — producing an artifact that others can read, verify,
  and interact with

The pipeline spec is what bridges them. The same code that was written
interactively becomes the reproducible, auditable content of the report.

Reactive notebook environments (Observable, Pluto.jl) point to a further
possibility: the pipeline specs don't have to live in separate files
referenced by a report — they could live *inline* in the narrative
document, executable and live. The notebook isn't just a container for
pipeline files; it's a document where prose, code, and interactive
visualization are woven together. Petricek and Edwards' Denicek system
pushes this further still — in their model, there is no separate "code"
at all; the document structure itself defines the computation.

Trame also enables replacing the native VTK render window with a
browser-based viewer during the exploration phase, and could host the
parameter scrubbing UI.

## Safe-by-design execution

The current DSL executes as Python, which means pipeline files can contain
arbitrary code. 

A safe-by-design approach should make **bad things impossible to express**.  A
sandboxed interpreter like Starlark embodies this: no imports, no exec, no
filesystem access, no unbounded loops, guaranteed termination. The only callable
functions are those explicitly injected by the host (source, threshold, show,
etc.). The worst an LLM can produce is a bad pipeline spec, not arbitrary code
execution.

This matters for two reasons beyond security:

- **Auto-approval** — in a fully interactive workflow, the LLM should be
  able to write and execute pipelines without the human confirming each
  change. This requires trust that execution is safe by construction, not
  by inspection.
- **Validation at the language level** — a restricted language can be
  analyzed statically. If the only things you can write are pipeline
  specs, the system can validate them completely before execution:
  type-check connections, verify field references, estimate cost. This
  connects to the DSL validation vision — the more constrained the
  language, the more the system can check at spec time.

The DSL syntax is a subset of Python, so migration to Starlark would be
transparent to pipeline authors.

---

# Part 4: Research Context

## The core claim

A declarative DSL with live feedback, data introspection, and incremental
update produces better LLM-driven visualizations than existing approaches
(imperative tool calls, one-shot code generation). This is a systems and
language design contribution, evaluated empirically.

The deeper claim, emerging from the bonsai session experience: the same
data-aware intelligence that helps an LLM write correct pipelines can help
a human edit them, and the best system surfaces this intelligence through
both channels simultaneously. The pipeline file is not just an LLM output
format — it's a **shared communication medium** between human and AI, and
the tooling should treat both as first-class consumers.

## Open questions and falsifiable claims

These are empirically testable hypotheses it would be good to eventually
validate:

- **Is a declarative DSL better than raw VTK + good feedback?** The DSL
  adds a layer of abstraction. That layer provides safety (re-execution),
  readability (auditable specs), and error diagnostics. But it also
  constrains what's expressible and adds concepts to learn. It's possible
  that an LLM writing raw VTK Python with good error feedback and query
  tools would perform comparably. This is testable via ablation.
- **Does the feedback loop converge faster with data-aware queries?** The
  query tools (statistics, histograms, spatial extent) are designed to
  prevent blind parameter guessing. Do they actually reduce iteration
  count, or does the LLM guess well enough without them?
- **Does the human actually learn the DSL and co-author?** The system is
  designed for the human to progress from observer to collaborator. Does
  this happen in practice, or does the human remain a passive consumer of
  AI-generated pipelines?
- **Is the system general across data types?** All early design decisions
  were shaped by one dataset (wildfire structured grid). The bonsai CT
  scan tested a second data type (regular grid volume), but many more
  exist — unstructured meshes, polydata, multi-block, AMR. Design choices
  that seem general may be specific to the data types tested so far.

Cross-dataset validation is a research process concern: new datasets
should be deliberately chosen to stress-test generality, not just to
demonstrate breadth. Each new dataset should be structurally different
from existing ones in ways that could break assumptions.

## The declarative reconciliation pattern

The combination of declarative spec + execution + rich runtime feedback is
a general pattern for LLM collaboration on stateful systems. It applies
wherever:

- There is a **stateful runtime** (renderer, database, cluster, simulator)
- Configuration is a **DAG of components** with parameters and connections
- **Iteration is valuable** — the user and LLM want to tweak and observe
- **Interesting properties are data-dependent** — runtime feedback is more
  informative than static analysis

The pattern:

```
LLM writes declarative spec
  → System executes against live data
  → Returns rich runtime semantic feedback
  → LLM uses feedback to write next version
  → Version history enables rollback
  → Human observes runtime + artifact, provides input
```

Domains beyond visualization where may apply:

| Domain | Runtime | Spec | Runtime feedback |
|---|---|---|---|
| Visualization | VTK renderer | Filter pipeline | Cell counts, bounds, screenshots |
| Infrastructure | Cloud provider | Resource graph | Provision times, costs, health |
| Data pipelines | Airflow/Spark | DAG of transforms | Row counts, schema, quality |
| Simulation | OpenFOAM/FEA | Mesh + physics + BCs | Convergence, residuals, probes |
| Database schema | RDBMS | Tables + indices | Query plans, row counts |

## Where VisLang fits in the landscape

### Existing VTK/visualization tools

**paraview-mcp (LLNL)** — ~20 imperative MCP tools wrapping ParaView.
Sequential tool calls, no declarative state management, multi-client
crashes, no undo. Our work was directly motivated by failures using this.

**viznoir** — 22 MCP tools, headless VTK, physics-aware domain detection,
animation. Still imperative, no pipeline state management. Headless only.

**Patrick O'Leary's suite** (data-mcp, vtkapi-mcp, vtk-python-tests) —
data introspection, VTK API validation, grounded examples. data-mcp's
query tools validate our approach; vtkapi-mcp's class index could
supplement our property resolution.

**vtk-prompt (Kitware)** — Natural language to VTK Python via LLM + RAG.
No pipeline state, no incremental updates. Each prompt generates a fresh
script.

### What VisLang adds over existing tools

- Declarative pipeline specs with version history and rollback
- The pipeline file as a readable, editable shared artifact
- Rich data-aware query tools that inform parameter choices
- Structured build feedback (not just error messages)
- The vision of shared intelligence between human and AI via LSP + MCP

### LLM + DSL approaches

**Typed Holes / ChatLSP (Hazel)** — Type context alone gives 3x improvement
in correct completions; adding error feedback gives another 4x. They propose
ChatLSP extending LSP with AI-specific methods. Our query tools serve a
similar function — providing contextual information to the LLM — and we go
further with runtime semantic feedback (not just static type errors).

**Compiler-Guided Adaptation (Idris)** — GPT-5 goes from 22/56 to 54/56 on
Idris exercises with structured compiler feedback. Key finding: local errors
are far more useful than documentation. Validates our approach of rich
build feedback over extensive prompt context.

**DSL-Xpert 2.0** — Grammar-aware prompting reduces syntax errors. Our DSL's
small, regular grammar is naturally well-suited to this.

**Imperative vs Declarative for Scene Generation (Brown)** — Imperative won
for spatial layout (82-94%). Cautionary for us: camera placement, seed
positioning, and annotation layout involve spatial reasoning that may be
easier to express imperatively. Our `camera()` with explicit coordinates
is already more imperative than declarative.

**Structure-Aware RAG (Notre Dame/LLNL)** — Pipeline topology matters for
retrieval. Our declarative specs encode structural constraints inherently.

**ChatVis (LLNL)** — LLM agent for ParaView with chain-of-thought and RAG.
Their benchmark tasks could serve as evaluation targets for VisLang.

### Theoretical foundations

**The Gamma (Petricek)** — Dot-driven development, type providers from data,
live evaluation with result reuse. Our query tools are the MCP version of
dot-driven development. Petricek's live evaluation formalism could inform
our reconciler — it's essentially the same problem (incremental re-execution
on edit) applied to data exploration.

**Andrew Blinn's thesis proposal** "Structured Semantic Context for
Programming Processes" — extends ChatLSP toward surfacing semantic context
to both humans and LLMs. Directly relevant to our LSP + MCP dual-channel
vision.

### LLM + theorem prover interaction

The Lean 4 theorem proving community faces a structurally similar problem:
a stateful system (proof state), structured feedback (goals, type errors),
and the need for the LLM to make informed decisions based on current state.

**LeanCopilot** — the most polished user-facing tool. Exposes tactics
inside Lean's editor: `suggest_tactics`, `search_proof` (combining LLM
with symbolic search), `select_premises` (retrieve relevant lemmas). The
hybrid approach — neural suggestions filtered through symbolic
verification — is notable.

**llmstep** — the cleanest minimal design. Extracts the current goal,
sends it to an LLM, gets back suggestions, **type-checks each one in
Lean**, and displays only valid suggestions. Key idea: filter LLM output
through the system's own validator before presenting it.

**Pantograph / LeanDojo** — machine-to-machine infrastructure for Lean.
Exposes proof state as structured data (goals, hypotheses, types) rather
than raw text, enabling programmatic tactic execution and verification.
LeanDojo adds retrieval-augmented premise selection — finding relevant
theorems for the current goal via embedding similarity.

**lean4-mcp** — an MCP server that proxies the Lean LSP. Exposes
`apply_edit` + `get_diagnostics` + `get_goal_state`, waiting for
type-checking to complete before returning. This is exactly the
LSP-as-MCP pattern we envision.

**Lean's InfoView** — Lean's IDE features an info panel that updates as
the cursor moves: click any expression to see its type, the current goal
state, and available hypotheses. This "click to inspect" pattern adapts
naturally to visualization: click a pipeline node to see data shape,
array statistics, and an isolated render of its visual contribution.

Key ideas transferable to VisLang:
- **Validate before presenting** — llmstep type-checks every suggestion
  before showing it. Our pre-execution validation could similarly catch
  errors before the user or LLM sees a failed result.
- **Retrieval-augmented suggestions** — ReProver's premise retrieval
  (finding relevant theorems for the current goal) maps to retrieving
  relevant DSL patterns for the current data characteristics.
- **Hybrid neural + symbolic** — LeanCopilot combines LLM generation
  with symbolic proof search. Our equivalent: LLM generates pipeline
  specs, symbolic validation checks field names, data types, value
  ranges, and filter compatibility before execution.
- **LSP-as-MCP bridge** — lean4-mcp's approach of proxying the LSP
  validates our planned architecture.

### Live programming and direct manipulation

The live programming community has explored many of the interaction
patterns VisLang aspires to, in contexts without an AI collaborator.

**"Technical Dimensions of Programming Systems" (Jakubovic, Edwards,
Petricek, 2023)** — A framework for evaluating programming *systems*
rather than just languages, with dimensions like feedback loops,
abstraction construction, notation, conceptual structure, and liveness.
VisLang's own claim ("a programming system, not just a language") is
exactly what this framework is designed to analyze. Edwards is the
Subtext author; Petricek is The Gamma author — so this paper synthesizes
both lineages. Positioning VisLang against their dimensions would be a
rigorous way to articulate what the system does differently from other
visualization tools.

**Tanimoto's liveness levels / Horowitz's critique (LIVE 2024)** —
Tanimoto's framework classifies feedback immediacy: level 2 (explicit
run), level 3 (auto on edit), level 4 (continuous). Horowitz argues
this is too simplistic — liveness is not a single axis. VisLang has
different liveness properties along different dimensions: visual feedback
from the render window is continuous, pipeline re-execution is currently
explicit (moving to auto), parameter sensitivity feedback doesn't exist
yet. Naming these separately is more honest than claiming a single
liveness level.

**Sketch-n-Sketch (Chugh et al.)** — Bidirectional editing between code
and output: edit either one and the other updates to match. The key
technical mechanism is a *trace*: during forward evaluation, the system
records a symbolic expression mapping each output value back to the
source code literals that produced it. When you drag something in the
output, it solves for new literal values. "Prodirect Manipulation"
(UIST 2019) extends this to structural edits. For VisLang, tracking
which DSL parameters map to which visual properties (clip plane position
→ `origin=(x,y,z)`, color range → `scalar_range=[lo, hi]`) could enable
similar inverse editing, with the LLM as the disambiguation layer when
the trace doesn't uniquely determine the edit.

**Bret Victor, "Drawing Dynamic Visualizations" (2013)** — Creates
visualizations by direct manipulation, binding marks to data by example.
The system infers a general program from specific interactions. Almost
eerily relevant to VisLang: the human directly manipulates the scene,
and a generalizer (for Victor, a custom inference engine; for VisLang,
the LLM) turns it into reusable code.

**Bret Victor, "Learnable Programming" (2012)** — Emphasis on making the
flow visible: not just the final output, but intermediate data at every
stage. Influences our node info view and the more ambitious idea of
ambient inline data summaries at every pipeline stage.

**Kay & Goldberg, "Personal Dynamic Media" (1977)** — The Dynabook vision
of the computer as a medium the user reshapes. Influences the idea that
users should be able to grow the DSL vocabulary — defining reusable
visualization patterns that become first-class building blocks.

**Observable / Pluto.jl** — Reactive notebook environments where the
dataflow graph is explicit and always consistent: change a cell and
everything downstream re-executes. VisLang's tear-down/rebuild already
has this consistency property. If the reconciler introduces partial
updates, maintaining the "always consistent" guarantee becomes a design
challenge worth attending to.

**Smalltalk** — Everything is inspectable at runtime. Influences the node
info view: any intermediate result in the pipeline should be zero-cost to
inspect, without prearranging it.

**Geoffrey Litt, "Malleable Software in the Age of LLMs" (2023)** —
Litt's core argument: LLMs are the missing bridge that finally makes
Kay's Dynabook vision practical. Users couldn't reshape their software
because programming was too hard; now the LLM translates intent into
code modifications. VisLang is malleable scientific visualization in
this sense — the DSL is the layer at which the software becomes
malleable to the domain expert, and the LLM bridges any skill gaps.
Litt's Potluck project (Ink & Switch) demonstrates a similar pattern:
freeform notes gradually enriched into structured, computable tools
with AI help.

**Lyra 2 (Zong et al., 2020)** — Design by demonstration: users
perform interactions *on the visualization they're editing*, and the
system infers interaction specifications. A third interaction mode
beyond code and conversation — demonstrate the visualization you want.

**Denicek (Petricek & Edwards, UIST 2025)** — A computational substrate
for document-oriented end-user programming. Programs are sequences of
edit operations on a document of data and formulas; three primitive
operations (add, edit, wrap) compose to support programming by
demonstration, incremental recomputation, collaborative editing, and
schema evolution. The key insight is that the choice of underlying
program representation determines which programming experiences are
easy to build. VisLang's "Python file of builder function calls" is one
substrate; a structured document with embedded formulas (Denicek's
approach) is another, where there is no separate "code" — the document
structure IS the computation. The naïve realism principle ("what the
user sees is all there is") is a stronger version of our goal that the
pipeline file always reflects the scene state.

### Evaluation

**SciVisAgentBench (Notre Dame/LLNL)** — 108 expert-crafted visualization
tasks with multimodal evaluation. We should use this as our benchmark.

### Ablation study design

Compare visualization quality and efficiency across configurations:

1. **One-shot code generation** — LLM writes complete VTK script. Baseline.
2. **+ Interactive iteration** — LLM sees screenshots, can revise.
3. **+ Data queries** — LLM introspects data before writing.
4. **+ Structured error reporting** — actionable feedback vs raw tracebacks.
5. **+ User interaction capture** — spatial grounding from the user.

Metrics: iterations to target, failure rate, time to completion, final
quality vs expert reference.

## Motivation from experience

This project's design was directly motivated by failed sessions using
paraview-mcp:

- Blind parameter guessing (→ data query tools)
- Silent empty output (→ structured error reporting)
- Freezing on large data (→ cost awareness)
- Can't position seeds without spatial knowledge (→ spatial extent queries)
- Stale pipeline objects with no cleanup (→ declarative state + versioning)

Each feature traces to a concrete failure mode observed in practice.

## Domain knowledge

The LLM has broad but shallow domain knowledge about scientific
visualization. Domain-specific knowledge files (in `domains/`) provide
conventions, standard derived quantities, meaningful thresholds, and
community color palettes for specific data types. These may eventually be loaded as
context when working with a particular kind of data, lighter than baked-in
physics layers but addressing the same gap.
