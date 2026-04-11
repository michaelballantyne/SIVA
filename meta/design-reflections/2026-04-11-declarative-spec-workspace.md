# The Workspace Layer, Rebuilt on a Declarative Spec

*Design reflection — April 11, 2026*

*This document is an alternative framing of the workspace layer
design from `2026-04-10-workspace-layer-design.md`. It rebuilds
the data-management architecture on top of two commitments:
(1) the content-hashed DAG is the primary representation the
compiler and runtime work with, as developed in
`2026-04-11-dag-as-ir.md`; and (2) the primary DSL is a declarative
spec with no implementation concerns — no working-subset management,
no tiered dispatch vocabulary, no explicit cost markers in method
names.*

*It is framed as an alternative that would realize the "spec as a
communication medium between human and agent" goal more cleanly
than the April 10 doc did, but it comes with trade-offs and is
harder to pull off. Most notably, it depends on a compiler that
can do real planning work — the hardest component in the whole
picture — and it requires language restrictions that will
occasionally bite authors. The April 10 doc remains a perfectly
reasonable (and simpler) alternative shape if the compiler-heavy
approach turns out to be impractical.*

*Status: exploratory. This is for thinking with, not for building
from.*

## What this document is arguing

The April 10 workspace doc correctly identified the right
infrastructure: a persistent on-disk workspace with stats DB,
pyramid, feature DB, sweep records, and shared cache; a
decisions-vs-rendering separation; a commitment to the sub-3-second
interactive loop as a hard invariant. That infrastructure survives
into this document unchanged. What this document proposes changing
is the **authoring surface** and the **division of labor between
author and system**.

In the April 10 doc, the author of a pipeline file participates
actively in scheduling: they write `ws.extract.isosurface(...)` to
promote an operation into async sweep territory, call
`set_working_subset(...)` to pick what to render interactively,
notice `StatValue.source` metadata to distinguish global answers
from subset approximations, and generally juggle an upper
vocabulary whose methods carry implicit scheduling decisions. The
vocabulary is tastefully named — `ws.extract.*` reads as "this is
expensive" — but the scheduling decisions themselves live in the
author's head.

In this document, the author writes a pipeline file that describes
the visualization they want. Nothing in that file names a working
subset, a pyramid level, a tier, a sweep, or a cache. The file
produces a DAG, and a **compiler** takes the DAG plus the workspace
state plus a latency budget and produces an **execution plan**.
The compiler is where scheduling lives; the spec is where intent
lives; the plan is where the author and the compiler can review
scheduling decisions together.

The phrase to hold in mind: **the spec is what the agent and human
agree on; the plan is what the compiler and runtime negotiate on
their behalf.**

## The three layers

The design has three clearly separated layers, with the DAG and
the plan as the interface artifacts between them.

```
   ┌─────────────────────────────────────────────────────────┐
   │  Spec layer                                             │
   │                                                         │
   │  Pipeline file in restricted Python.                    │
   │  Describes *what* to visualize.                         │
   │  No working subsets, no tiers, no cache, no scheduling. │
   │                                                         │
   │  Executing the file produces a content-hashed DAG —     │
   │  the artifact downstream layers consume.                │
   └───────────────────────┬─────────────────────────────────┘
                           │  DAG
                           ▼
   ┌─────────────────────────────────────────────────────────┐
   │  Compiler layer                                         │
   │                                                         │
   │  Reads the DAG + workspace state + latency budget       │
   │  + any author hints, and produces an execution plan:    │
   │    - which nodes to compute interactively               │
   │    - which nodes to serve from stats DB / cache         │
   │    - which nodes to approximate (and how)               │
   │    - which nodes to promote to background sweeps        │
   │    - which nodes to defer until commit                  │
   │                                                         │
   │  Produces a human-readable plan report for review,      │
   │  and a structured plan artifact the runtime executes.   │
   └───────────────────────┬─────────────────────────────────┘
                           │  Plan
                           ▼
   ┌─────────────────────────────────────────────────────────┐
   │  Runtime + workspace layer                              │
   │                                                         │
   │  Executes the plan. Reads from and writes to the        │
   │  workspace: stats DB, pyramid, feature DB, sweep        │
   │  records, persistent cache. Renders frames, runs        │
   │  background jobs, reports progress.                     │
   │                                                         │
   │  The workspace artifact is unchanged from the           │
   │  April 10 design — same directory layout, same          │
   │  components — but now it is the infrastructure          │
   │  the compiler uses, not a vocabulary the author         │
   │  manipulates.                                           │
   └─────────────────────────────────────────────────────────┘
```

The crossing points are the DAG (spec → compiler) and the plan
(compiler → runtime). Both are structured artifacts that can be
serialized, diffed, inspected, stored, and replayed. They are not
hidden machinery; they are first-class things the agent and human
can look at.

Two commitments distinguish this layering from the April 10 doc:

1. **The spec layer knows nothing about scale.** The same pipeline
   file runs against a 64³ synthetic volume, a 100-timestep fire
   simulation, and a 50 TB ensemble run. What changes between
   these is the compiler's plan, not the spec. Scale-invariance of
   the spec is an architectural invariant.

2. **The workspace is not part of the authoring vocabulary.** The
   author does not open workspaces, manipulate handles, call
   `ws.extract.*`, or manage working subsets. The compiler opens
   the workspace, reads its manifest and stats DB, writes features
   and sweep records, and manages persistent state. The author's
   only contact with the workspace is through the data they
   reference (e.g., `dataset("fire_sim")`) and through the plan
   report they review.

## The spec layer: what the author writes

The spec layer is a restricted-Python front-end that produces a
DAG. The exact surface syntax is an open question (see
`2026-04-11-dag-as-ir.md` for why syntax is a front-end concern
under this framing), but the shape is stable enough to illustrate
with a concrete example.

### The fire-sim spec

Here is the fire-sim visualization the April 10 workspace doc was
motivated by, written as a declarative spec:

```python
# fire.py — the entire pipeline file

fire = dataset("fire_sim")

# Parameters that might be swept or scrubbed
t = param("timestep", over=fire.timesteps, default=50)

# Representations
flame      = fire.at(t).isosurface("temperature", at=[800, 1200])
fire_vol   = fire.at(t).volume("temperature", where="temperature > 500")
plume      = fire.at(t).streamlines(
    "velocity",
    seeds=near("temperature", above=500, n=40),
)
terrain    = fire.at(t).slice(plane="z=0")
box        = fire.at(t).outline()

# Scales — "global" means "consistent across all frames and layers"
temp_scale = color("temperature", extent="global", cmap="hot")
wind_scale = color("velocity",    extent="global", cmap="wind")
terra_scale= color("temperature", extent=(290, 400), cmap="terrain")

# Scene
scene = scene(
    layers=[
        layer(flame,    encoding=temp_scale, opacity=0.6),
        layer(fire_vol, encoding=encoding(color=temp_scale,
                                           opacity=opacity("temperature",
                                                           preset="fire"))),
        layer(plume,    encoding=wind_scale, opacity=0.8),
        layer(terrain,  encoding=terra_scale),
        layer(box,      encoding=encoding(color=(1,1,1), opacity=0.2)),
    ],
    view=view(background=(0.02, 0.02, 0.05)),
    animate_over=t,
)

show(scene)
```

Points to notice:

- **No workspace vocabulary.** There is no `open_workspace(...)`,
  no `ws.field(...)`, no `ws.extract.*`, no `set_working_subset`.
  The dataset reference is symbolic (`"fire_sim"`); whether that
  resolves to a workspace, a single file, or something else is the
  compiler's problem.
- **No scheduling decisions.** Nothing in the file says which layer
  is computed interactively vs globally, which stats are cached vs
  computed, which operations are expensive vs cheap. The compiler
  decides.
- **Scales are declaratively global.** `extent="global"` is a
  commitment to consistency across frames and layers; the compiler
  is responsible for delivering that commitment, whether by reading
  the stats DB, approximating from a subset, or computing fresh.
- **Parameters are first-class.** `t = param("timestep", ...)` is a
  named DAG node with a declared range. `animate_over=t` references
  the same parameter, telling the compiler that the scene's
  animation axis is this parameter. The same parameter can drive
  interactive scrubbing, batch sweeps, and bidirectional editing.
- **The terminal node is `show(scene)`.** This is what tells the
  compiler "plan and execute this." Without a `show` (or
  `screenshot`, or `export`) terminal node, the DAG has no outputs
  and the compiler has nothing to do.
- **Named intermediate variables** (`flame`, `plume`, `temp_scale`)
  are ergonomic for the author but are not load-bearing for
  identity. The DAG's content hashes provide identity; names are
  presentation.

### What the same file means at different scales

The same file runs, unchanged, against:

- **A single 200 MB test file.** The compiler notices there is no
  workspace, falls back to a trivial plan: read the file once, run
  all operations eagerly, render the scene, report completion.
  Identical in feel to current single-file VisLang.
- **A 100-timestep fire simulation (~100 GB).** The compiler
  notices a workspace exists with base stats and a feature DB.
  Its plan: read the timestep-50 pyramid level 1 for interactive
  rendering, serve color scales from the stats DB, schedule global
  isosurface and streamlines extractions as background sweeps so
  animation playback is smooth. Report the plan; begin rendering.
- **A 50 TB ensemble simulation (100 timesteps × 20 members).**
  Same plan shape, different numbers: load a coarser level
  initially, larger background sweep queue, plan report includes
  longer commit times. The spec is byte-identical; the plan is
  bigger.

This is the scale-invariance commitment in practice. The author
writes intent; the compiler adapts to context.

### What the spec vocabulary needs to cover

The spec layer's node vocabulary is the substance of the language
design. A minimally complete vocabulary for the grant-relevant
workflows probably needs:

**Data.** `dataset(name_or_path)` is the entry point. Produces a
dataset node whose capabilities depend on what backing storage is
available. Sub-references: `.at(timestep)`, `.member(id)`,
`.field(name)`, `.timesteps`, `.fields`, `.members`. All of these
are DAG nodes, not eager accessors — asking for `.timesteps`
records a node that the compiler resolves against the workspace
manifest.

**Transforms.** Functions and methods that produce new field or
geometry nodes from existing ones: `where("field > value")`,
`clip(plane)`, `gradient("field")`, `derive("vorticity", from_=...)`,
`smooth(iterations=20)`, `resample(grid=...)`. The compiler picks
execution strategy (on subset, on pyramid level, on full
resolution, cached or not) based on downstream use.

**Representations.** `isosurface`, `volume`, `streamlines`,
`glyphs`, `slice`, `surface`, `outline`. Each produces a
representation node the compiler knows how to execute (via VTK
filters or equivalents) and render.

**Encodings and scales.** `color(field, extent=..., cmap=...)`,
`opacity(field, preset=... | control_points=...)`,
`size(field, range=...)`, `shade(...)`. Scales carry a declared
`extent` (literal, `"global"`, `"local"`, percentile-based) that
the compiler resolves against the workspace stats DB.

**Composition.** `layer(rep, encoding=..., opacity=...)`,
`scene(layers=[...], view=..., animate_over=...)`,
`facet(scene, by=...)`, `overlay(...)`.

**Parameters.** `param(name, over=range, default=...)`. Parameters
are nodes whose value is resolved at plan time and whose range the
compiler can sweep over, scrub across, or bind to UI controls.

**Stats.** Usually implicit (the `extent="global"` on a color
scale triggers a `PercentileRange` stats node underneath), but
also available as first-class nodes when the author needs an
explicit handle: `percentile("temperature", 95)`,
`histogram("temperature", bins=128)`,
`suggest_isosurfaces("temperature", n=3)`. The compiler tiers and
caches these the same way it tiers and caches anything else.

**Terminal nodes.** `show(scene)`, `screenshot(scene, path=...)`,
`export(scene, format=...)`, `animate(scene)`. Each tells the
compiler what output the DAG is producing.

### What the spec does not contain

These are the things the April 10 doc put in the authoring surface
that are pushed out of the spec in this framing:

- `open_workspace` / `ws.*` / `describe_workspace` — workspace
  access is the compiler's job.
- `set_working_subset` / `.working_subset()` / `.materialize()` —
  there is no author-visible "working subset." The compiler picks
  what to compute at what resolution.
- `ws.extract.*` as a distinct namespace — there is no cost-in-the-
  name distinction. Author writes `fire.isosurface(...)` whether
  the compiler ends up running it on a subset, a pyramid level,
  or as a background sweep.
- `StatValue.source` checks — the author does not inspect whether
  a stats query was served from base DB or subset approximation.
  The compiler reports approximation choices in the plan; the
  author reviews the plan.
- Tier hints in method names — no `_fast` / `_exact` / `_preview`
  variants. The compiler picks based on latency budget and cache
  state.
- Explicit async markers — no `.async_()` or `.queue()` or job
  lifecycle calls. The compiler schedules; the plan reports which
  nodes are deferred; the runtime manages the queue.

## The compiler layer

The compiler is where the design's ambition lives and where it
could most plausibly fail. This section describes what the
compiler is supposed to do, the inputs and outputs it works with,
and the strategies it might employ. It does not attempt to
specify the compiler; it stakes out the problem.

### Inputs and outputs

The compiler takes five inputs:

1. **The DAG** produced by executing the pipeline file. The
   content-hashed, typed-node graph that is the spec.
2. **The workspace state.** The manifest, stats DB contents,
   feature DB index, pyramid availability, existing cache entries,
   active sweep records. Everything the persistent artifact knows
   about the data.
3. **The latency budget.** A target for how long interactive
   operations may take before the agent or human notices friction.
   Default: 3 seconds. Configurable per session or per operation.
4. **The author's hints, if any.** Optional side-car annotations
   that constrain the compiler's choices (discussed below).
5. **The prior plan, if any.** When the pipeline file changes and
   only some DAG nodes change, the compiler reuses the prior
   plan's decisions on unchanged nodes.

It produces two outputs:

1. **A structured plan artifact** the runtime executes. A typed
   data structure mapping each DAG node to an execution strategy:
   run interactively on the current subset, serve from stats DB
   cache, serve from feature DB cache, compute on pyramid level N,
   defer to background sweep, approximate and escalate, skip
   entirely (terminal node not reached this round), etc.
2. **A plan report**, human- and agent-readable, that summarizes
   the plan's key decisions and any warnings or caveats. This is
   the artifact the author reviews. Its shape matters as much as
   the plan itself, because it is where the agent-human
   conversation about scheduling happens.

### What the compiler decides

For each node in the DAG, the compiler picks an execution
strategy from a finite set of options. The set varies by node
kind; a non-exhaustive list:

**Transforms and representations** (threshold, isosurface,
streamlines, etc.):

- Run on the currently-selected interactive subset at the current
  level, for the current frame only.
- Run on a coarser pyramid level (faster, less faithful).
- Run on the full resolution for a single frame (the current
  one).
- Promote to a background sweep over all frames, write results
  to the feature DB.
- Serve from the feature DB if a matching entry exists.
- Defer entirely: the node is part of the DAG but not part of
  this round's execution (e.g., a layer the author toggled off).

**Stats queries** (percentile, histogram, range):

- Serve from `base.db` (instant, exact).
- Serve from `derived.db` (instant, exact, computed in a
  previous session).
- Compute now on the working subset (fast, approximate).
- Compute now on a pyramid level (fast, approximate with a
  known error bound).
- Compute now on full resolution (slow, exact).
- Approximate immediately with a subset answer *and* queue a
  background job to compute the exact answer.

**Encoding scales with `extent="global"`:**

- Resolve against the stats DB. If missing, fall back to one of
  the stats strategies above.
- If the field is derived (not in the manifest), either run the
  derivation through the pyramid once and cache, or scope the
  scale to the working subset with a warning.

**Terminal nodes** (show, screenshot, animate):

- Render the current frame from the current plan's interactive
  strategy.
- Report which layers are at full fidelity vs approximation.
- If the terminal is `animate`, require that all per-frame
  nodes have a global strategy (either cached or queued), not
  subset-local ones.

### Strategies the compiler can employ

The compiler's job is to pick a combination of node strategies
that meets the latency budget, respects the author's hints,
exploits the workspace state, and produces a visually correct
result. A plausible approach:

1. **Start from the terminal nodes and walk backwards.** For each
   terminal, determine what fidelity level it needs (interactive
   preview? exact animation? publication render?) and propagate
   fidelity requirements back through the DAG.
2. **Classify each node** by (a) whether the workspace has a
   matching cached result, (b) whether it can be served from the
   stats DB, (c) its approximate cost at each pyramid level, and
   (d) whether it is on the critical path for an interactive
   terminal or a deferrable one.
3. **Enumerate candidate strategies** for each node. In a first
   implementation, this may be a simple "pick the cheapest
   strategy that meets the fidelity requirement." In a smarter
   implementation, it could be a search over combinations with
   a cost model.
4. **Check the plan against the latency budget.** If the
   interactive critical path exceeds the budget, promote nodes
   to coarser strategies (pyramid level up, approximation in,
   defer non-essential layers). If the budget is comfortably
   met, use finer strategies where they are cheap.
5. **Queue background work.** Any node whose final strategy
   involves a sweep or expensive computation goes into the
   background job queue.
6. **Emit the plan and the plan report.**

The first version of the compiler will almost certainly be dumb
— greedy heuristics, obvious cases handled well, everything else
falling back to the cheapest-valid strategy with a warning. That
is fine. The point of separating the compiler from the spec is
that the compiler can improve over time without changing the
authoring surface.

### The hint surface

The compiler will be wrong sometimes. When it is, the author
needs a way to override its decisions without polluting the spec.
Hints live in a sidecar file — `fire.hints.py` or
`fire.hints.json` alongside the pipeline — so the spec itself
stays clean.

A plausible hint vocabulary:

```python
# fire.hints.py

hint(node="flame",      strategy="sweep_eager")
hint(node="plume",      pyramid_level=0)
hint(node="fire_vol",   max_wait_ms=5000)
hint(node="terrain",    approximate=False)
hint(scale="temp_scale", source="base_db_only")  # no approximation
```

Key properties of hints:

- **Hints are optional.** The compiler's defaults should be good
  enough for the common case; hints are for when they are not.
- **Hints are separable.** Deleting the hints file reverts to
  default compilation. The spec is unchanged.
- **Hints are advisory, not mandatory.** If a hint conflicts with
  the latency budget or with the workspace state, the compiler
  warns and picks the best available strategy. Hints that cannot
  be satisfied become warnings in the plan report, not errors.
- **Hints reference nodes by name or by structural position.**
  Named intermediate variables in the spec give hints a stable
  handle. When the spec changes and named variables disappear,
  stale hints become warnings rather than silent drift.

This is the Vega-Lite → Vega escape pattern applied to
scheduling. The primary surface stays declarative; the escape
is a separate, advisory, well-scoped mechanism.

### The plan artifact and the sidecar lock file

For reproducibility, the compiler writes its resolved plan to a
sidecar lock file alongside the pipeline. Something like
`fire.plan.lock.json`, automatically maintained:

```json
{
  "spec_hash": "a7f2c...",
  "workspace_hash": "4d91e...",
  "compiled_at": "2026-04-11T14:22:10Z",
  "latency_budget_ms": 3000,
  "nodes": {
    "flame":    {"strategy": "sweep_cached",    "feature_ref": "fire_iso_v3"},
    "plume":    {"strategy": "sweep_pending",   "sweep_id": "sw-042"},
    "fire_vol": {"strategy": "pyramid",         "level": 1},
    "temp_scale": {"strategy": "base_db",
                   "source": "temperature/percentile_5_95"},
    "...": "..."
  },
  "warnings": [
    "plume: background sweep pending (est 45 min)",
    "fire_vol: rendered at pyramid level 1 (full resolution on commit)"
  ]
}
```

The lock file serves several purposes:

- **Reproducibility.** Spec + lock + workspace state = exact
  reproduction of the rendered result, even months later.
  Analogous to `package.json` + `package-lock.json` in the Node
  ecosystem.
- **Incremental replanning.** When the spec changes, the
  compiler compares the new DAG to the old one (via the stored
  `spec_hash` plus per-node hashes) and reuses lock entries for
  unchanged nodes. Only changed subtrees get replanned.
- **Plan history.** The lock file is version-controllable
  alongside the spec. Reviewing how the plan has evolved across
  commits gives insight into how the compiler and the workspace
  interact over a project's life.
- **Offline inspection.** Tools that need to understand the plan
  without re-running the compiler (status dashboards, progress
  trackers, external reviewers) can read the lock file.

Whether the lock file is human-edited is a design choice. The
pattern to prefer is probably "read-only, auto-generated, and
the way to change a decision is through a hint, not by editing
the lock." This keeps the lock's semantics clear and avoids
merge conflicts on a machine-generated artifact.

## The plan report and the agent-human conversation

The plan report is the surface through which the agent, the
human, and the compiler negotiate about scheduling. It deserves
careful design because it is where the "spec as communication
medium" aspiration is actually tested: if the report is unclear,
the aspiration fails in practice even if the layering is clean
in theory.

### What the report contains

A good plan report answers four questions, in roughly this
order:

1. **What is this visualization going to show?** A one-paragraph
   narrative summary of the spec, derived from the DAG's terminal
   nodes and their immediate dependencies. "This scene has three
   layers: an isosurface of temperature at 800 and 1200 K, volume
   rendering of the fire region, and streamlines through the
   plume. Animated over 100 timesteps. Colored consistently by
   temperature and velocity with global scales."
2. **What will I see right now, and how good is it?** The
   interactive preview state, broken down by layer. For each
   layer, the fidelity level (exact, pyramid level N, subset
   approximation), the source of its encoding (stats DB, cache,
   subset), and any caveats ("approximate color range, global
   version pending").
3. **What's happening in the background, and when will it
   finish?** The queued sweeps and long-running jobs, with
   estimated completion times and what they unblock. "Sweep
   sw-042 runs the streamlines extraction across 100 timesteps;
   est 45 min; unblocks the animation."
4. **What decisions did the compiler make that I should know
   about?** Anything the compiler chose that an author might
   want to override: approximation choices, pyramid-level
   fallbacks, unused hints, conflicts, resource constraints.
   This is where the compiler's "I had to pick something, I
   picked this, here's my reasoning" shows up.

A sketch of what a report might look like, for the fire-sim
spec at first compilation against a populated workspace:

```
Spec: fire.py
Workspace: fire_sim_workspace (50 TB, 100 timesteps, base stats complete)
Latency budget: 3000 ms

Visualization
  Animated scene, 5 layers, swept over timestep (0-99, default 50).
  - flame:    isosurface of temperature at [800, 1200] K
  - fire_vol: volume rendering of temperature where > 500 K
  - plume:    streamlines of velocity, seeded near hot regions
  - terrain:  z=0 slice, colored by temperature (290-400 K)
  - box:      bounding outline, white

Interactive preview (timestep 50)
  flame:     feature_db hit  (fire_iso_v3)              full fidelity
  fire_vol:  pyramid level 1                            ~2x coarser than full
  plume:     subset preview (seeds from base.db)         16 of 40 seeds
  terrain:   pyramid level 0                            full fidelity
  box:       full fidelity

  Color scales
    temp_scale:  base.db  (1st-99th percentile: 287-2418 K)  exact
    wind_scale:  base.db  (1st-99th percentile: 0.2-34 m/s)  exact
    terra_scale: literal  (290-400)                           exact

  Estimated first frame: 1.8 s

Background
  sw-042  streamlines across all 100 timesteps           est 45 min
          unblocks: animate(scene) at full fidelity
  sw-043  fire_vol at pyramid level 0 across timesteps   est 12 min
          unblocks: animate(scene) full volume fidelity

Notes
  - plume preview uses 16 seeds; full extraction uses 40.
    Visual: same seed locations, fewer tubes.
  - fire_vol preview is coarser than commit. Transfer function
    behavior may differ slightly at fine detail.
  - No warnings. No unused hints.

Actions
  show()     render interactive preview with current plan [default]
  commit()   wait for all background jobs, then render at full fidelity
  explain(node)  show why the compiler picked a strategy
  hint(...)  override a decision for next compile
```

The point of the report is that an author (human or agent) can
read it and understand, in a few seconds, what they are going
to see and what is still in flight. The author can then either
proceed (most of the time), adjust a hint (occasionally), or
change the spec (when the report reveals the spec itself was
wrong).

### How the agent participates

An LLM agent reading this report does roughly what a human
would do, only faster:

1. **Summarize.** The report's narrative section goes into the
   agent's response to the human: "I've built a scene with
   five layers. The interactive preview will be ready in about
   2 seconds; two background sweeps are running for full-
   fidelity animation."
2. **Flag.** If anything in the report is surprising — an
   unexpected approximation, a sweep the agent did not anticipate,
   a stats fallback — the agent mentions it before the human asks.
3. **Propose.** If the agent notices the compiler made a choice
   it disagrees with (e.g., "plume is previewing at 16 seeds but
   the user asked to see the seed pattern specifically"), it
   proposes a hint.
4. **Ask.** When the report surfaces a genuine choice — commit
   now versus wait for the sweep, accept an approximation or wait
   for the exact answer — the agent asks the human which they
   prefer.
5. **Act.** Once the human approves, the agent either calls
   `show()`, adds a hint and recompiles, or edits the spec.

This is a much cleaner conversation than "the agent has to know
which vocabulary to use, remember to check StatValue sources, and
decide whether to promote operations to `ws.extract.*` by itself."
The compiler handles the decisions; the report externalizes
them; the agent and human review them together.

### Why this beats cost-in-the-name

The April 10 doc's key design move was making cost visible in
the method vocabulary: `ws.extract.*` means "expensive and async,"
`mesh.*` means "cheap and sync." This is a clever use of naming,
but it pushes scheduling into the author's head.

The plan-report approach makes cost visible in the *plan*, not
in the *spec*. The author writes intent. The report shows cost.
If the cost is unacceptable, the author adjusts — via a hint, a
spec change, or a decision to proceed anyway — and the report
updates. The author never has to pre-classify operations as
"cheap" or "expensive" while writing the spec, because the
classification depends on context the author does not have
(workspace state, cache contents, latency budget) and which the
compiler does.

The naming-conventions approach works at small scale. The plan-
report approach scales better because it lets the cost model
grow richer without changing the vocabulary the author has to
learn.

## The workspace artifact: unchanged, reinterpreted

The on-disk workspace format from the April 10 doc carries
forward without modification:

```
fire_sim_workspace/
  manifest.json
  stats/
    base.db
    derived.db
    jobs.db
  pyramid/
    t0000/...
  features/
    fire_front_v1/...
  sweeps/
    sweep_042/...
  cache/
    tracked_execution.db
```

What changes is *who talks to it*. In the April 10 doc, the
author talks to the workspace through the upper vocabulary
(`ws.field`, `ws.extract`, `set_working_subset`). In this doc,
the **compiler** talks to the workspace, and the author never
does.

Specifically:

- The **manifest** is read by the compiler to resolve
  `dataset(name)` nodes, determine available timesteps and
  members, inject late-bound parameter ranges, and check for
  drift.
- The **stats DB** is queried by the compiler when resolving
  `extent="global"` scale commitments, stats DAG nodes, and
  suggestion helpers. Cache misses that are cheap trigger
  compute-and-cache during planning; expensive misses trigger
  subset approximation plus background job enqueue.
- The **pyramid** is consulted by the compiler when picking
  interactive rendering levels and intermediate computation
  levels. The author never picks a level.
- The **feature DB** is the cache the compiler checks before
  running extractions. Matches become "served from cache"
  entries in the plan; misses become "pending sweep" entries.
- The **sweep records** document the provenance of long-running
  jobs. They reference DAG nodes by content hash, so the
  compiler can match a new DAG against past sweeps.
- The **cache** is the general-purpose persistent cache for
  tracked-execution's DAG. Under this framing it becomes part
  of the compiler's cache-lookup logic.

The workspace is still the growing cached artifact that gets
faster and richer with use. The mechanism is unchanged. The
only difference is that the growth is driven by the compiler's
planning decisions rather than by author-written extract calls.

## What dissolves under this framing

Several things the April 10 doc spent significant attention on
simply go away once the layering is taken seriously.

### The upper-vocabulary / lower-vocabulary distinction

The April 10 doc carefully separated two method vocabularies —
the workspace's declarative, tiered, deferred upper layer and
PyVista's eager, in-memory lower layer — with `.materialize()`
as the single crossing point.

Under the DAG framing, there is no such distinction. Every
operation the author writes records a DAG node. The compiler
decides which nodes are executed where: some as PyVista filter
calls on a materialized subset, some as VTK operations across
the full pyramid, some as sweep jobs, some as stats DB lookups.
These are backend strategies, not distinct vocabularies.

The author's mental model simplifies from "two vocabularies with
a crossing point" to "one vocabulary; the compiler picks
execution."

### The `.materialize()` crossing point

Since there is no upper/lower distinction, there is no crossing
point. The compiler materializes data when it has to for
execution; the author never calls a materialize method. Data
moves from on-disk workspace into in-memory PyVista meshes under
the compiler's control, for the nodes that need it.

### Cost-visible naming conventions

`ws.extract.*` versus `mesh.*` disappears. The author writes
`fire.isosurface("temperature", at=[800, 1200])` and the
compiler decides whether that runs on a subset (cheap) or as a
background sweep (expensive), reporting the choice in the plan.
Cost is visible in the report, not in the name.

### The set_working_subset ceremony

The author does not pick a working subset. The compiler picks
one, based on the spec's terminal nodes, the latency budget, and
the workspace state. If the spec is a single-frame `show(scene)`,
the compiler picks the subset for that frame. If the spec is
`animate(scene)`, the compiler picks subsets frame by frame and
schedules sweeps for anything that can be precomputed. The
author's view is always consistent with the spec they wrote.

### Subset-approximation caveats in the author's head

The April 10 doc's `StatValue.source` metadata — "this answer is
from the base DB" vs "this is a subset approximation" — is still
present in this framing, but it lives in the plan report rather
than in the author's runtime inspection. The author does not
have to remember to check; the report surfaces it when it is
relevant.

### Phase-based author discipline

The April 10 doc organized the workflow into four phases:
explore stats, tune locally, commit globally, animate. Each
phase had its own expected vocabulary and discipline. Under the
compiled framing, phases are **emergent from the compiler's
plan**, not imposed on the author. The author writes a
visualization spec; the compiler's first plan includes
interactive preview; as the author iterates, the compiler
re-plans; when the author is ready, they `commit()` and the
compiler runs whatever sweeps are still pending.

The four phases still exist in the runtime's behavior, but the
author does not have to switch modes explicitly.

## What gets harder under this framing

Honest accounting requires naming the costs. This framing trades
several moderately hard problems (working-subset discipline,
cost-in-the-name conventions, explicit async juggling) for
several genuinely hard ones.

### The compiler is the hardest component in the project

Everything the April 10 doc asked the author to decide, the
compiler now has to decide. That is a lot:

- Which pyramid level to use for interactive rendering, given a
  latency budget and a DAG.
- Whether to approximate a stats query or compute exactly.
- When to promote an extraction to a background sweep vs running
  on the current frame.
- How to reuse cached results across spec edits without
  producing stale plans.
- How to allocate compute across layers when the budget is
  tight.
- How to handle derived fields the workspace has never seen.
- How to handle streaming / progressive rendering without
  violating the latency invariant.
- How to emit a plan report that is accurate, concise, and
  actionable.

A first-pass compiler will be dumb: greedy heuristics, obvious
cases handled well, hints as the escape for everything else. It
will probably be good enough for the grant's demonstration
workflows with careful hint authoring. Over time it gets smarter.
This is a long project — measured in years, not weeks.

### Termination constraints leak to the author

The spec layer's restriction on data-dependent control flow
(documented in the DAG-as-IR reflection) hits authors at the
worst times: when they are iterating quickly and want a
conditional `if`. The error message has to be exceptionally good,
and the escape mechanisms (parameters, DAG conditionals,
`vtk_escape`) have to be obvious and well-documented.

### Debugging crosses layers

A wrong visualization might be a spec bug, a plan bug, a runtime
bug, or a workspace staleness issue. The diagnostic tooling has
to tell the author which layer is responsible. Every error
message has to identify its layer; every warning has to name its
source; every plan report has to be accurate enough to trust.
This is substantial tooling work and there is no shortcut.

### Incremental replanning has to be fast

The interactive loop depends on the compiler replanning quickly
when the spec changes. If the compiler does a full replan from
scratch on every edit, the latency budget is burned before
anything runs. Incremental replanning via content-hash matching
against the prior plan is how the budget is preserved, and
getting it right is a nontrivial engineering problem.

### The stakes of a bad plan are higher

In the April 10 doc, if the author makes a scheduling mistake
(running an expensive operation eagerly, forgetting to use
`ws.extract`), the mistake is visible to the author immediately.
In this framing, if the compiler makes a scheduling mistake
(picking a strategy that wastes an hour of background compute,
or producing a misleading approximation), the mistake may not
be visible until the sweep finishes. Robust warnings and
progress reporting are essential to catch compiler mistakes
before they cascade.

### The plan report is itself a design problem

The plan report carries enormous weight in this framing: it is
the primary interface where scheduling is reviewed. If it is
unclear, the whole approach degrades to "the compiler does
opaque stuff and the author has to trust it." Getting the report
right — concise, accurate, actionable, agent-parseable, human-
readable — is a UX investment that most design docs underweight.

### Feedback loops are longer

Tight iteration in the April 10 doc works because every
operation the author writes runs immediately on the working
subset. Tight iteration in this framing depends on the compiler
recognizing small edits and replanning incrementally. If the
incremental-replan story is weak, every edit recompiles and
re-reports from scratch, which is slower than the imperative
version.

## Build order implications

The April 10 doc's six-phase build order is still mostly sound
under this framing, but with different emphasis on which phases
carry the project's weight.

### Phase 0: Foundation (tracked-execution consolidated)

Same as April 10. Tracked-execution's proxy, hashing, DAG,
whitelist, reconciler, and `vtk_escape` all need to be stable
before further work.

### Phase 1: DAG as first-class spec, small data

**Goal:** extend tracked-execution so that the DAG produced by a
pipeline file is a standalone artifact the rest of the system
can reason about, before the workspace or compiler exist.

Items:

1. Make the DAG serializable to JSON or a similar structured
   format.
2. Add richer node types: `ShowLayer`, `Screenshot`, `Scale`,
   `Encoding`, `Param`, `Scene`, terminal nodes. The whitelist
   grows to include them.
3. A minimal, dumb compiler that walks the DAG and executes it
   naively — no workspace, no planning, just "do the obvious
   thing for each node." This compiler is a placeholder for the
   smarter versions later.
4. A minimal plan artifact and report — even if the compiler is
   dumb, the shape of the report is landed and the author gets
   used to seeing it.

This phase delivers the declarative-spec authoring experience
on single files. The report is mostly boring ("everything ran,
no surprises") but the pipeline is set up for the interesting
work later.

### Phase 2: Workspace and stats DB, compiler learns to use them

**Goal:** the workspace appears on disk, and the compiler learns
to consult its stats DB when resolving `extent="global"` scales
and stats nodes.

Items:

1. Workspace directory, manifest, `base.db`, `derived.db`,
   ingestion command (same as April 10 phase 2).
2. Compiler extension: recognize when a DAG node can be served
   from the stats DB, and emit plan entries that do so.
3. Plan report extensions: show which scales came from the stats
   DB vs computed, flag approximations.
4. Drift detection and reporting when the workspace disagrees
   with the spec's referenced fields.

This is where the "instant stats queries on TB data" property
lands. The author sees no change in the spec; the plan report
shows "color scale: base.db, exact, instant" instead of
"computed from working subset."

### Phase 3: Pyramid and interactive subset selection

**Goal:** the compiler learns to pick pyramid levels and subset
bounding boxes for interactive rendering, replacing the April 10
`set_working_subset` ceremony.

Items:

1. Pyramid format integration (Zarr first, as in April 10).
2. Compiler extension: latency-budget-aware pyramid level
   selection per DAG node. Progressive refinement from coarse
   preview to refined final.
3. Plan report extensions: show pyramid level per layer,
   fidelity estimates.
4. Prefetching: compiler anticipates parameter scrubbing (e.g.,
   timestep changes) and speculatively pre-compiles adjacent
   plans.

This is where "fast subset switching on TB data" lands. The
author still writes one spec; the compiler now makes the subset
decisions it was manually managed in April 10.

### Phase 4: Background job queue and sweep-based extraction

**Goal:** the compiler learns to promote expensive extractions
to background sweeps, and the runtime manages the job queue.

Items:

1. Background job queue (SQLite-backed or directory-based).
2. Compiler extension: recognize DAG nodes that need global
   extraction, emit `sweep_pending` plan entries, enqueue jobs.
3. Feature DB format and cache-hit logic.
4. Plan report extensions: show pending sweeps, estimates,
   what they unblock.

This is the biggest behavioral change for the author: the spec
implicitly triggers background work. The April 10 `ws.extract.*`
namespace disappears and the compiler takes over.

### Phase 5: Animation and parameter-space sweeps

**Goal:** terminal animation nodes drive multi-frame sweeps; the
compiler plans ensemble and parameter-space explorations from
declared params.

Items:

1. `animate(scene)` terminal node and its plan semantics.
2. Parameter-sweep inference from `param(...)` declarations —
   sweeps inherit their grids from the spec's params.
3. Sweep record persistence and resumability (same as April 10).
4. Plan report extensions: show sweep progress, offer `commit()`
   to wait for completion.

### Phase 6: Hints, debug tooling, and polish

**Goal:** the hint mechanism, debug surfaces, incremental
replanning, and production-quality plan reports.

Items:

1. Hint file format and compiler hint-resolution logic.
2. `explain(node)` diagnostic for tracing plan decisions.
3. Incremental-replan path keyed on DAG subtree hashes.
4. Plan report refinements based on real use.
5. LSP integration for spec-layer editing with inline plan
   feedback.

### What this build order preserves and what it sacrifices

The order above preserves every April 10 phase's end state
("phase N gives you X") while shifting the work inside each
phase toward the compiler. The main sacrifice is that **Phase 1
requires more design work than April 10's Phase 1** — landing
the DAG as a first-class spec, with richer node types and a
standalone compiler, is more than landing a new workspace API.
It is also riskier, because it commits to a node vocabulary
that is hard to change later.

A pragmatic hedge: build the April 10 approach in parallel for
the first few phases, and only commit to the compiler-heavy
framing once the minimal compiler is working and the vocabulary
has stabilized. The two approaches share almost all of their
infrastructure; only the authoring surface differs.

## Open questions and risks

### Can the compiler actually be good enough?

The biggest unknown. A compiler that picks pyramid levels badly
wastes compute; a compiler that approximates wrongly produces
misleading visualizations; a compiler that schedules poorly
breaks the interactive feel. The first-pass compiler is
definitely dumb, and the question is whether "dumb compiler plus
hints" is enough to demonstrate the grant goals.

Mitigation: start with a minimal set of visualization types
where the compiler's job is genuinely simple (single timestep,
single dataset, straightforward extraction), and grow the set
as the compiler gets smarter. Do not try to cover everything at
once.

### How do authors learn the restricted-Python dialect?

The termination and purity constraints will bite authors the
first time they hit them. The error messages need to be
actionable, but more fundamentally the documentation needs to
explain up front "here are the things that look like Python but
are not." An agent writing spec files has to know these rules as
part of its guidance; a human writing them has to have a cheat
sheet.

Mitigation: write the dialect description as a short, concrete
document ("The VisLang spec dialect: what works, what doesn't,
how to escape"), and reference it from error messages.

### What happens when the compiler disagrees with the author's
intent?

The compiler might pick an approximation the author thinks is
too coarse, or schedule a sweep the author did not want. The
hint mechanism exists for this, but the author has to notice
the disagreement first. If the plan report does not make the
disagreement visible, the author proceeds unaware.

Mitigation: the plan report should highlight any "compiler
chose something non-default" entry prominently, and the agent
should surface these in its response rather than letting them
pass silently.

### How does the compiler handle spec edits during background
jobs?

The author edits the spec while a sweep is still running from
the previous spec. The new spec might still use the sweep's
output, or might invalidate it, or might use it partially.
Deciding what to do is nuanced.

Mitigation: treat background jobs as keyed on the DAG subtree
hash they are computing. If the new spec's DAG contains the
same subtree, the job is still useful. If not, the job is
obsolete and can be cancelled (or left running for later
reuse). The compiler decides at plan time; the author sees the
decision in the report.

### How expressive does the hint vocabulary need to be?

A small hint vocabulary is easy to learn and hard to misuse,
but may be too weak to override the compiler when it matters. A
large hint vocabulary is expressive but re-introduces the
complexity the compiler was supposed to absorb. Finding the
right surface area is an iterative process.

Mitigation: start with a very small vocabulary (strategy,
pyramid level, max wait), and grow only in response to concrete
cases where the compiler's defaults are unacceptable and the
spec itself should not change.

### Is the sidecar lock file human-editable or machine-managed?

Arguments for human-editable: transparent, simple, no magic.
Arguments for machine-managed: no merge conflicts, no drift,
the author thinks in hints instead. Probably the machine-managed
answer is right, but it leaks a "you cannot just edit this file"
constraint authors will bump into.

Mitigation: tooling. A command to inspect the lock, a command
to diff locks, a command to mint a hint from a plan decision.

### Does the DAG framing support in-situ or streaming data?

The grant scope is explicitly post-hoc local data, so this is
not a near-term question, but it is worth flagging. A static
DAG is a natural fit for batch data; it is less obviously right
for streaming, in-situ, or reactive data sources. Future
extensions may require a different framing for those cases.

Mitigation: keep the current framing scoped to the batch case,
and revisit if streaming becomes important.

### Will agents write good specs in this dialect?

The whole design rests on the assumption that an LLM agent can
fluently write pipeline files in the spec dialect. If the
dialect is restrictive enough that the agent fights it, the
design fails even if everything else works. This needs early
empirical testing — write some specs, try them, see what the
agent gets wrong.

Mitigation: measure. Do not trust intuition about what is
"obvious" to a language model. Build early prototypes and test
with real agent sessions.

### What's the fallback if the compiler can't be built well
enough?

If the compiler turns out to be too hard to write well, the
project still has the April 10 workspace design as a fallback.
That design works with the existing machinery, demands less of
the compiler, and delivers the grant goals at the cost of
pushing scheduling into the author's head. It is a strictly
simpler target if the compiler-heavy approach stalls.

Mitigation: the fallback is real and documented. Choosing this
framing is not a one-way door.

## What this is and is not

### What this is

- **An alternative framing** for the workspace layer design,
  not a replacement. The April 10 design remains a valid shape
  that works with less compiler ambition; this document explores
  what becomes possible if the compiler can be built well.
- **A commitment to scale-invariance of the spec.** The same
  pipeline file runs against small test data and TB production
  data; only the compiler's plan differs.
- **A commitment to separating intent from scheduling.** The
  spec layer expresses what to show; the compiler decides how
  and when; the runtime executes. The author's contact with
  scheduling is through the plan report, not through the
  authoring vocabulary.
- **A specific proposal for the artifacts that make the
  layering work.** The DAG is the spec-to-compiler artifact;
  the plan (and its human-readable report) is the compiler-to-
  runtime artifact; the sidecar lock file is the reproducibility
  anchor; the hint file is the escape surface.
- **An extension of the April 10 workspace design**, not a
  replacement for it. The workspace artifact (manifest, stats
  DB, pyramid, feature DB, sweep records, cache) carries
  forward unchanged. What changes is who interacts with it.
- **Aligned with the DAG-as-IR reflection**
  (`2026-04-11-dag-as-ir.md`). That document works out the
  architectural argument; this one works out the data-management
  implications.

### What this is not

- **Not a finished design.** The compiler's internal algorithm,
  the exact spec vocabulary, the hint surface, the plan report
  format, and the incremental replan strategy are all open.
- **Not a commitment that the compiler is buildable.** A dumb
  first compiler is certainly buildable. Whether it can grow
  into something smart enough to carry the project's ambitions
  is genuinely uncertain.
- **Not a rejection of the April 10 doc.** That document is a
  better fit for the near-term grant work, because it does not
  bet on compiler ambition. The two documents describe
  complementary options along a spectrum, not opposing camps.
- **Not a green light to start building.** The next step is to
  prototype the minimal compiler and a handful of spec
  vocabulary decisions, see how they feel, and revise the
  design before committing resources.
- **Not validated by implementation.** The whole thing is on
  paper. Several of the risks above can only be resolved by
  trying things and measuring.

### Alternatives worth holding open

The April 10 doc listed several alternatives. Most are still
alternatives to this framing too:

- **Stay with the April 10 workspace design.** Simpler,
  doesn't depend on compiler ambition, easier to implement.
  Arguably the right near-term choice.
- **Integrate with an existing big-data viz tool** (napari,
  ParaView client-server). Skips most of this design entirely.
- **Use Dask + xarray directly** with a thin PyVista adapter.
  Leans on existing chunked-array infrastructure.
- **Hybrid: April 10 authoring surface with DAG-as-IR
  internals.** Keep the April 10 upper vocabulary as the
  author-visible surface but implement it as DAG node
  construction under the hood. This gets some of the layering
  benefits without committing to a new authoring dialect.
  Possibly the most pragmatic path.

The last option is worth highlighting: it is a middle ground
that captures much of the architectural benefit (one IR, hash-
consed diffs, compiler-driven caching) without asking authors
to learn a new surface. It may be where the project actually
lands even if the full declarative-spec vision proves too
ambitious.

### Signals that would tip toward building this

These are the conditions under which committing to the
compiler-heavy framing becomes the right call:

1. **A minimal compiler works on the fire-sim and bonsai
   examples** and the plan reports are clearly readable by
   both humans and agents.
2. **The spec dialect feels writable.** Agents can produce
   valid spec files without extensive prompting; humans can
   read them without needing to understand the compiler.
3. **The incremental-replan path is fast enough** that
   iterative editing stays within the latency budget.
4. **Hint authoring is rare.** Most real pipelines compile
   cleanly without hints; hints are for genuine special cases,
   not everyday use.
5. **The workspace doc's sharpest edges** (working subset
   discipline, tune/commit scale disagreement, derived-field
   stats management) actually start biting in the April 10
   implementation, and users ask for relief.

If those signals fire, this framing is worth building. If they
do not, the simpler April 10 design is the right target.

## Closing note

This document and its companion (`2026-04-11-dag-as-ir.md`)
describe a more ambitious version of the workspace layer than
the April 10 doc staked out. The ambition is concentrated in
the compiler: if the compiler can be built well enough to take
scheduling off the author's hands, the authoring surface becomes
a genuinely declarative spec — a communication medium between
human and agent that captures intent and nothing else.

The April 10 doc is the pragmatic path: less compiler ambition,
more author discipline, easier to implement, less risky. This
document is the ambitious path: a harder compiler, a simpler
authoring surface, a cleaner separation of intent from
implementation, and a more principled story about what the
pipeline file actually is.

Both are worth holding open. The decision between them is not
urgent — most of the infrastructure is the same either way, and
the authoring surface is the last thing that needs to be
settled. The next productive step is probably to prototype a
minimal DAG node vocabulary, a minimal compiler, and a minimal
plan report, and see how they feel in practice against the
fire-sim and bonsai examples. If they feel good, commit. If
they feel brittle, the April 10 design is waiting.

