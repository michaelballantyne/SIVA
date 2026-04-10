# The Workspace Layer: Data Management as a Parallel Vocabulary to PyVista

*Design reflection — April 10, 2026*

*This document sketches **one possibility** for how VisLang could handle
TB-scale post-hoc simulation data on local disk while preserving the
interactive, iterative feel of the current prototype. It is not a final
recommendation; alternatives should be considered before committing. It
is written up now so the design can be critiqued as a whole.*

*Status: WIP. Sections currently written: framing, principles,
two-level architecture, workspace artifact, upper vocabulary, workflow
patterns, agent mental model, preserving the interactive feel,
grammar-of-graphics ideas at scale. Remaining sections: connection to
tracked-execution, near-term build order, open questions and risks,
what this design is and is not.*

## The problem being solved

The grant work calls for automated development of visualizations in
contexts where the data is too large to load all at once. The scope
being considered here is deliberately narrow:

- **Post-hoc, not in situ.** The simulation has already run. The files
  sit on disk. No ability to modify the simulation or embed in its
  runtime.
- **Local, not remote.** The data lives on a single workstation's local
  disks — fast NVMe in the multi-terabyte range, not a cluster, not an
  object store, not a remote filesystem.
- **Many timesteps, possibly multiple ensemble members.** The dataset
  is a *sequence* (and potentially a grid) of files, not just one large
  monolithic file.
- **LLM-driven automation.** The agent (not a GUI user) is the primary
  consumer of the API. The interactive loop must be the agent's
  conversational loop: sub-3-second responses for almost all actions,
  with explicit async commitments for the few operations that must
  take longer.
- **Preserve the current interactive feel.** The single-frame
  exploration loop (describe → query → iterate on a pipeline → observe
  result) must feel the same at TB scale as it does on a single small
  file today.

Remote rendering, in-situ coupling, and cloud storage are all out of
scope for now, but the architecture should not preclude adding them
later.

## Design principles

Before describing the architecture, the principles that guide it:

1. **The raw data is never in the interactive loop.** Every action the
   agent takes during exploration either reads pre-computed metadata,
   operates on a small materialized working subset, or explicitly
   launches a background job. Nothing in the interactive loop touches
   the full TB.

2. **Decisions and rendering are separated.** Parameter choices
   (threshold values, ranges, percentiles) are informed by globally-
   accurate statistics from a pre-computed statistics database.
   Rendering is performed on a small working subset for speed. This
   lets the agent iterate visually on a subset while making decisions
   that are globally correct.

3. **The workspace is a growing cached artifact, not a fixed output.**
   Ingestion seeds the workspace with a starter set of metadata and
   statistics. Every session extends it with new derived stats, new
   feature extractions, and new sweep results. Future sessions reuse
   what previous sessions computed. The workspace gets faster and
   richer over time as it is used.

4. **PyVista is the rendering substrate.** Every operation that
   produces visible geometry or pixels is a PyVista operation on a
   materialized, in-memory mesh. PyVista's method vocabulary, plotter
   model, and display conventions are preserved unchanged. The agent's
   existing PyVista knowledge transfers directly.

5. **A small upper vocabulary handles data management.** On top of
   PyVista, a narrow set of workspace verbs handles lazy dataset
   handles, statistics queries, global extractions, parameter
   declarations, and sweep execution. These are Python methods on
   workspace objects, not a new language. They parallel PyVista's
   filter vocabulary in naming so an agent fluent in PyVista can learn
   them quickly.

6. **The same pipeline file works at every scale.** A pipeline written
   against the workspace abstraction runs the same way on a
   hundred-megabyte test file and a hundred-terabyte production dataset.
   Only the backend changes; the file itself does not.

7. **Costs are explicit.** Operations that might take minutes to hours
   are named differently from operations that take sub-second — `ws.extract.*`
   vs `mesh.*`. The agent sees cost implications in the name, not as
   surprise latency.

## The two-level architecture

The design has two clearly separated layers with a well-defined
interface between them.

```
                 ┌───────────────────────────────────────────┐
                 │   Upper vocabulary (declarative, lazy)    │
                 │                                           │
                 │   • Workspace + dataset handles           │
                 │   • Stats queries (ws.field.percentile..) │
                 │   • Global extractions (ws.extract.*)     │
                 │   • Parameters (vislang.param)            │
                 │   • Sweeps (ws.apply_across)              │
                 │   • Feature refs and materialization      │
                 │                                           │
                 │   Deferred, cached, tiered, persistent.   │
                 │   Aware of the workspace and all its      │
                 │   metadata; never holds the raw data      │
                 │   resident in memory.                     │
                 └──────────────────┬────────────────────────┘
                                    │
                      .materialize()│   (crossing point)
                                    ↓
                 ┌───────────────────────────────────────────┐
                 │   Lower vocabulary (eager, local)         │
                 │                                           │
                 │   • PyVista meshes, filters, plotter      │
                 │   • add_mesh, add_volume, camera, etc.    │
                 │   • In-memory, small, sub-second          │
                 │                                           │
                 │   Unchanged from today. All existing      │
                 │   PyVista knowledge and idioms transfer.  │
                 └───────────────────────────────────────────┘
```

**Upper vocabulary.** Operations that know about the workspace, manage
big data, can run asynchronously, and produce deferred handles. Every
upper-layer operation returns either a lightweight handle
(`DatasetHandle`, `FieldHandle`, `FeatureRef`) or a `StatValue` (a
wrapper carrying a scalar or array plus provenance). Nothing is
materialized unless explicitly asked for.

**Lower vocabulary.** PyVista, unchanged. Filter methods on materialized
meshes, plotters, actors, mappers, colormaps. Runs eagerly on in-memory
data that is always small.

**Crossing point.** Exactly one primitive crosses from upper to lower:
`.materialize()` on a handle (or implicit coercion when a handle is
passed to a PyVista method). Materialization reads a small subset into
memory as a real PyVista mesh. Every PyVista operation from that point
runs normally.

Stats queries cross the other direction — from lower to upper — by
taking a handle or field name and returning a `StatValue` after
consulting the stats DB.

The architecture echoes the Altair → Vega-Lite → Vega pattern: a
concise declarative upper layer compiling/dispatching to a full-power
execution substrate. Here it is applied to data access rather than to
visual encoding, which was the right place for it all along.

## The workspace artifact

A workspace is a directory produced by a one-time ingestion pass over
a simulation output directory. It contains:

```
fire_sim_workspace/
  manifest.json                # dataset shape: timesteps, fields, extents,
                               #   types, sizes, ingestion version
  stats/
    base.db                    # precomputed stats from ingestion
    derived.db                 # stats added by sessions
    jobs.db                    # background job queue and history
  pyramid/
    t0000/
      temperature.zarr         # multi-resolution pyramid per field/timestep
      density.zarr
      ...
    t0001/
      ...
  features/
    fire_front_v1/             # extracted isosurface across timesteps
      spec.json                # the declarative extraction request
      frame_0000.vtp
      frame_0001.vtp
      ...
    fire_region_v1/
      ...
  sweeps/
    sweep_042/                 # batch sweep record
      spec.json                # pipeline hash + parameter grid
      status.json              # progress, errors, timing
      outputs/                 # links into features/ and stats/
  cache/
    tracked_execution.db       # content-addressed cache for upper+lower calls
```

### Manifest

Tiny JSON (or similar) describing the dataset shape:

- Timesteps available: `[0, 1, ..., 99]` with physical time values
- Ensemble members: `["member_0", ...]` or empty
- Fields per timestep, with semantic types inferred at ingestion
  (scalar/vector/tensor/categorical, sequential/diverging/probability,
  units, sign)
- Spatial extents and grid topology (structured/curvilinear/unstructured)
- File sizes and ingestion date
- Source files (hashes + mtimes for drift detection)

The manifest answers "what's in this workspace?" in milliseconds. Every
agent session starts by reading it.

### Stats database

A simple key-value store (SQLite is probably fine) mapping declarative
stats requests to cached results. Two tables:

- **`base`** — stats computed at ingestion: per-field-per-timestep
  histograms, percentiles at canonical levels (1, 5, 25, 50, 75, 95,
  99), min/max, mean, std, spatial extent of significant values,
  gradient magnitude stats for scalar fields. Computed once, read many.
- **`derived`** — stats added by sessions as the agent asks for things
  that weren't in the base. Keyed on the declarative request's content
  hash. Persistent across sessions.

Every entry carries provenance: the declarative spec that produced it,
the timestamp, the workspace version. The agent can query
`list_stats()` to see what's been computed and `describe_stat(name)`
to see how.

### Pyramid

Multi-resolution representation of each field for each timestep. The
working subset is drawn from this. For the initial implementation, use
**Zarr with multiscale metadata** (OME-Zarr conventions) for regular
grids — it integrates well with Python, Dask, and napari, and has
active community development. For curvilinear and unstructured grids,
start with a simpler "downsampled VTK file per level" approach and
upgrade later if needed.

An alternative worth benchmarking early is **OpenVisus (IDX)** which
offers better streaming smoothness at the cost of more integration
work. Zarr first, OpenVisus as an optional back-end.

### Feature database

Extracted geometry from global `ws.extract.*` operations. Each feature
has a directory with:

- `spec.json` — the declarative extraction request (verb, args,
  timestep range, content hash)
- `frame_NNNN.vtp` — one polydata file per timestep covered by the
  extraction (or a single file for static extractions)
- `stats.json` — per-frame summary (point count, bounds, field ranges
  of included fields)

FeatureRefs are opened by name or by content hash. Animation playback
loads only the feature DB entries, never the raw data.

### Sweep records

A sweep is a pipeline-plus-parameter-grid executed as a batch job.
Each sweep has a record with:

- Pipeline file hash (content-addressed)
- Parameter grid (declared params and their swept values)
- Input dataset state (manifest hash at sweep time)
- Output references (links to feature DB and derived stats DB entries
  produced by this sweep)
- Status (running, complete, failed, cancelled)
- Timing and cost breakdown per parameter tuple

Sweep records are the "spec-as-data" that earns its keep at scale —
they're the structured artifact that lets sessions coordinate, resume,
compare, and reproduce large-scale computations. The pipeline source
stays Python; the sweep's identity and metadata are structured.

### Cache

Content-addressed cache shared between upper-layer operations (stats
queries, extractions) and lower-layer operations (PyVista filter
results, rendered actors). This extends tracked-execution's current
in-memory cache to a persistent on-disk store, so repeated work across
sessions is automatically reused. Cache keys include the dataset
handle identity, so a filter on the working subset for timestep 50 is
a different cache entry than the same filter on timestep 51.

## The upper vocabulary

The upper layer is a small library with roughly 30-40 methods spread
across four groups.

### Group 1: Workspace and handles

```python
ws = vislang.open_workspace("fire_sim_workspace/")

ws.describe()              # → dict with manifest summary
ws.list_fields()           # → ["temperature", "density", "u", "v", "w"]
ws.list_timesteps()        # → [0, 1, ..., 99]
ws.list_members()          # → [] for single-member; else ["m0", "m1", ...]

# Handle construction
ws.timestep(t=50)          # → DatasetHandle (lazy reference to one timestep)
ws.field("temperature")    # → FieldHandle (lazy field reference, cross-timestep)
ws.timestep(50).field("temperature")   # → TimestepFieldHandle

# The working subset — what the interactive pipeline operates on
ws.working_subset()        # → materialized PyVista mesh of current subset
ws.set_working_subset(timestep=50, bbox=None, level=0)
ws.set_working_subset(mode="overview", max_points=5_000_000)
```

Handles are cheap to construct and never trigger data loads. They
carry references into the workspace and are hashable for use as cache
keys.

### Group 2: Stats queries (declarative, tiered, cached)

Methods on handles that return `StatValue` objects. Each is hashed
into a cache key; results are looked up in the stats DB first, tiered
for compute if missing.

```python
field = ws.field("temperature")

field.percentile(95)                           # scalar
field.percentile([5, 25, 50, 75, 95])          # array
field.percentile(95, where="z > 20")           # conditional
field.histogram(bins=128)                      # dict with bins + counts
field.range()                                  # (min, max) globally
field.global_range(method="percentile", q=(1, 99))  # robust range
field.spatial_extent(value=800)                # bbox of cells above threshold
field.distribution_shape()                     # "sequential"/"bimodal"/etc
field.suggest_isosurfaces(n=3)                 # histogram-guided values
field.suggest_opacity(format="control_points")

# Per-timestep queries
field.at(t=50).percentile(95)                  # just one timestep
field.over(timesteps="all").trend("percentile", q=95)  # value per timestep
```

Each call returns a `StatValue` which:

- Acts like the underlying scalar/array in most contexts (unpacks,
  arithmetic, comparison)
- Carries `.value`, `.spec`, `.source` ("base_db" | "derived_db" |
  "computed_now" | "subset_approximation"), `.global_job_id` if a
  background job was queued

If the query is a cache hit: returned immediately from `base` or
`derived` with `source="base_db"` / `source="derived_db"`.

If cheap and missing: computed immediately (likely by running over the
pyramid at an appropriate level), stored in `derived`, returned with
`source="computed_now"`.

If expensive and missing: returned as a subset-based approximation
with `source="subset_approximation"` and `global_job_id` set. A
background job is queued to compute the global answer; when it
completes, the result lands in `derived` and future queries become
hits.

### Group 3: Global extractions (declarative, async, persistent)

The parallel vocabulary to PyVista's filter methods, for operations
that should run across the whole dataset and produce reusable
geometry.

```python
# Each returns a FeatureRef
fire_front  = ws.extract.isosurface("temperature", values=[800])
fire_region = ws.extract.threshold("temperature", [500, 2000])
plume_flow  = ws.extract.streamlines("velocity", seeds=..., direction="both")
fire_mask   = ws.extract.where("temperature > 500 and density < 100")
terrain     = ws.extract.slice(normal=[0, 0, 1], origin=[0, 0, 0])
surface     = ws.extract.extract_surface(fire_region)   # chained extracts
bbox_crop   = ws.extract.bbox([400, 600, 300, 500, 0, 100])

# Common optional parameters:
#   timesteps=range(0, 100, 5)  — which timesteps to cover (default: "all")
#   members=[0, 1]              — which ensemble members
#   as_="fire_front_v2"         — stable name (default: content hash)
#   priority="high"             — job queue priority
#   on_complete=callback        — notify when done
```

Each extract call:

1. Computes the content hash of the request.
2. Checks the feature DB. Hit → returns the existing `FeatureRef`
   immediately.
3. Miss → enqueues a background sweep job, returns a pending
   `FeatureRef` with `.status == "queued"`.
4. Sweep worker processes timesteps in parallel up to configured
   concurrency, writes feature files as they complete, updates the
   ref's status.
5. Session can poll via `feature_ref.status()`, wait via
   `feature_ref.wait()`, or set up a callback.

FeatureRefs are composable with both vocabularies:

```python
# Use as input to further extracts
fire_surface = ws.extract.extract_surface(fire_region)

# Materialize for PyVista rendering
mesh = fire_front.materialize(timestep=50)     # → PyVista PolyData
plotter.add_mesh(mesh, cmap="hot", clim=field.global_range())

# Animate across the sweep
animate(fire_front, encoding=...)
```

### Group 4: Parameters and sweeps

Declared parameters that the pipeline is implicitly a function of:

```python
# Top of the pipeline file
t = vislang.param("timestep", range=range(0, 100), default=50)
threshold_val = vislang.param("threshold", default=800, min=0, max=2000)
member = vislang.param("member", choices=[0, 1, 2], default=0)
```

In interactive mode, `vislang.param(...)` returns the current value
(the working subset's timestep, the default threshold, etc.) and
registers the param as tunable with the system.

In batch mode, the system sweeps over the declared range:

```python
# Run the pipeline for every timestep, building the feature DB
job = ws.apply_across(pipeline_file, timesteps="all")
job.wait()                   # or job.status()

# Sweep over a combination
job = ws.apply_across(pipeline_file, {
    "timestep": range(0, 100, 5),
    "threshold": [600, 800, 1000],
})
```

The sweep is recorded as a structured sweep spec in the workspace.
`ws.list_sweeps()` shows history; `ws.describe_sweep(id)` shows
parameters, status, and outputs.

### Why this is a library, not a DSL

All four groups are Python method calls on Python objects. No new
syntax, no parser, no AST rewriting. The pipeline file parses as
ordinary Python and runs under the ordinary Python interpreter. The
system observes the calls through the tracked-execution proxy and
dispatches them to the right execution path.

The "declarative" character comes from the fact that each call returns
a handle or `StatValue` or `FeatureRef` rather than executing
immediately and returning raw data. The library's contract is "I
accept your request and promise to produce an answer efficiently;
don't assume the work has happened when the call returns unless I
tell you so." This is the same pattern as Dask, Tensorflow (graph
mode), or SQLAlchemy sessions — deferred execution through an
ordinary Python API.

## Workflow patterns

Four canonical patterns the agent will use repeatedly. Each combines
upper and lower vocabularies in a specific way.

### Pattern 1: Initial exploration

```python
ws = vislang.open_workspace("fire_sim_workspace/")
print(ws.describe())                 # manifest summary — instant

field = ws.field("temperature")
print(field.range())                 # (298, 2418) — stats DB, instant
print(field.suggest_isosurfaces(n=3))  # [540, 875, 1380] — stats DB, instant
print(field.spatial_extent(value=800))  # bbox — stats DB, instant
```

Everything is sub-second. The agent discovers what's in the data
without touching the raw files. Identical in feel to current VisLang
calling `describe_data()` and `get_statistics()` on a small file.

### Pattern 2: Interactive tuning on the working subset

```python
# Set what subset to iterate on
ws.set_working_subset(timestep=50)   # loads the subset from the pyramid

# Iterate in PyVista
mesh = ws.working_subset()           # real PyVista mesh
hot = mesh.threshold([500, 2000], scalars="temperature")
iso = hot.contour([800], scalars="temperature")
plotter = pv.Plotter()
plotter.add_mesh(iso, cmap="hot",
                 clim=field.global_range())  # consistent with global data
plotter.show()
```

Every call here is PyVista operating on a small in-memory mesh. The
agent iterates on threshold values, contour values, camera, colormap,
at PyVista's normal interactive speed. **The rendering is subset-based,
but the `clim` is computed from global stats, so the visual appearance
matches what the final animation will look like.** This is the
decisions-vs-rendering separation in action.

Changing the working subset is a one-liner:

```python
ws.set_working_subset(timestep=80)   # switch to a different frame
ws.set_working_subset(timestep=50, bbox=[400, 600, 300, 500, 0, 100])
ws.set_working_subset(level=2)       # coarser overview
```

Each switch is bounded in cost by the pyramid's structure (a few
seconds at most, likely faster), with progressive refinement rendering
a coarse version first and settling to fine.

### Pattern 3: Commit to global extraction

Once the tuning is good, commit to a global feature.

```python
# Tuning said [550, 2000] is the right range
fire_region = ws.extract.threshold("temperature", [550, 2000])
# Returns immediately with a pending FeatureRef
# Background sweep runs across all timesteps

# Agent can continue exploring while the sweep runs
ws.set_working_subset(timestep=75)
mesh = ws.working_subset()
# ... more interactive work ...

# Check on the sweep
print(fire_region.status())          # "running: 73/100"

# Later: wait for completion before proceeding
fire_region.wait()
print(fire_region.status())          # "complete"
```

The agent knows this operation is expensive because it's named
`ws.extract.*`, not `mesh.*`. The async nature preserves the
interactive loop for everything else in the session.

### Pattern 4: Animation playback

Once features are extracted, playback is interactive again.

```python
animate(fire_region,
        encoding={"cmap": "hot", "clim": field.global_range()},
        fps=10)
```

Playback loads feature frames on demand from the feature DB. Because
the features are small polydata per timestep (10-50 MB rather than
the original gigabytes), the animation is smooth. The agent can
change colormap, camera, and opacity interactively; changing the
extraction parameters means re-running the extract (back to Pattern 3).

### The four-phase loop

These patterns compose into a canonical session structure:

```
 ┌──────────────────────────────────────────────────────────────┐
 │                                                              │
 │   1. Explore stats ──▶ 2. Tune locally ──▶ 3. Commit global ─┼─▶ 4. Animate
 │          │                    │                   │         │
 │          │                    │                   │         │
 │          │                    ▼                   │         │
 │          └───── refine ◀──────┴──── subset-switch ┘         │
 │                                                              │
 └──────────────────────────────────────────────────────────────┘

     ↑ Phases 1, 2, 4 are fully interactive (sub-3-second loop).
     ↑ Phase 3 is explicitly async. Agent knows. Doesn't block.
```

Most of the session lives in phases 1 and 2, cycling between stats
queries and local tuning. Phase 3 happens once the tuning has
converged. Phase 4 runs after phase 3 completes. The entire workflow
preserves the interactive feel because **the expensive work is
isolated in phase 3 and phase 3 is explicitly async**.

## The agent's mental model

What the agent has to understand, as a learnable set of rules:

1. **A workspace is a cached artifact you open, not a file you load.**
   `vislang.open_workspace(path)` is the entry point. It's fast because
   it only reads the manifest.

2. **Stats questions go through the workspace, not through meshes.**
   `ws.field("temperature").percentile(95)` rather than
   `mesh["temperature"].percentile(95)`. The workspace answers
   globally; the mesh answers only about the subset.

3. **Local work happens on the working subset.** `ws.working_subset()`
   returns a real PyVista mesh. Use it for rendering and for trying
   out filter parameters interactively.

4. **Global work uses `ws.extract.*` verbs.** Parallel to PyVista's
   filter methods but with an `extract` namespace. Each one is
   explicitly a batch operation.

5. **Extracts return FeatureRefs, which are handles you can
   materialize, chain, or animate.** FeatureRefs plug into both
   vocabularies — as inputs to further extracts, or materialized to
   PyVista meshes for rendering.

6. **Parameters you want to tune should be declared at the top of the
   pipeline file.** `vislang.param("threshold", default=800,
   min=0, max=2000)`. Everything else is an ordinary variable.

7. **Nothing long-running is implicit.** If an operation might take
   minutes, it's either named `ws.extract.*` (async, returns a
   handle) or you explicitly called `ws.apply_across(...)`.
   Everything else is fast.

These seven rules plus existing PyVista knowledge are enough to write
any pipeline in the workspace model.

## Preserving the interactive feel as an invariant

The design's central success criterion is that the iterative
exploration loop at TB scale feels the same as it does on small data
today. This is not a nice-to-have — it is the thing that makes
LLM-driven visualization development work at all. An agent that has
to wait 30 seconds between iterations starts batching speculative
edits, avoiding exploratory actions, and losing the tight
observe-hypothesize-act cycle.

### The latency budget

The design takes as a hard invariant: **every action in the
interactive loop produces a visible response in under ~3 seconds**.
The only exceptions are actions the agent has explicitly understood
as long-running (`ws.extract.*`, `ws.apply_across`, `ws.precompute`),
and those return job handles immediately and continue in the
background.

This budget is not an aspiration but a design constraint. Every piece
of the architecture is chosen to preserve it.

### How each primitive meets the budget

Walking through the interactive primitives and naming how each stays
fast:

**Workspace open** (`vislang.open_workspace`). Reads the manifest and
opens the stats DB handle. No data load. Target: under 500 ms
regardless of dataset size.

**Field listing, timestep listing, manifest summary** (`ws.describe`,
`ws.list_fields`, etc.). Manifest JSON lookups. Instant.

**Stats queries on handles** (`field.percentile`, `field.histogram`,
`field.range`, `field.suggest_isosurfaces`). Stats DB lookups.
Sub-100ms for cache hits. For Tier 2 misses, computed on the pyramid
at a level proportional to the answer's required accuracy, cached in
the derived stats DB for future sessions. For Tier 3 misses, returned
as a subset-based approximation immediately with a background job
queued; the caller sees a result in under 3 seconds and the global
answer lands later.

**Working subset switch** (`ws.set_working_subset`). Bounded in cost
by pyramid structure, not by dataset size. Progressive refinement:
the coarse level loads first (fast), then refines to the requested
level. The agent sees a usable mesh in under 3 seconds even on TB
data. Subsequent switches to nearby subsets are warm from the cache
or prefetched speculatively.

**Pipeline execution** (`set_pipeline` against the working subset).
Runs PyVista filters on a small in-memory mesh, same as current
VisLang. Identical performance to today. Tracked-execution's
content-hash caching makes iteration on the same subset with
incrementally different parameters cache-hit-heavy.

**Screenshot / rendering**. Unchanged. Small mesh, fast render.

**Stats DB extension via query** (asking for a derived stat that
isn't in `base` yet). Two paths: cheap stats computed immediately
from the pyramid (few seconds, cached, budget-compliant);
expensive stats returned as subset approximation with background
escalation. Either way the interactive turn completes in under 3
seconds.

**Extract request** (`ws.extract.*`). Returns a pending `FeatureRef`
immediately with a job queued. Budget compliant because the return
is instant; the actual work happens asynchronously.

**Animation playback**. Operates on the feature DB which is small.
Frame transitions, colormap changes, camera adjustments all run at
PyVista's normal interactive speed. Changing the extract parameters
(not the encoding) is what requires a new sweep and goes back to
Pattern 3.

### The things that could break the invariant, and how they don't

**Stats on a newly-encountered derived quantity.** Example: agent
wants the 95th percentile of curl magnitude, a derived field not
pre-computed at ingestion. Solution: the pyramid supports level-based
computation; compute the derived field at a coarse level (fast),
return that as the answer, queue a background job to compute it at
full resolution and fold into the stats DB. The agent sees a number
within the budget.

**Visualization of a region not in the working subset.** Example: the
current subset is timestep 50, but the agent wants to see timestep 80.
Solution: subset switch is a single cheap call backed by the pyramid.
The first render after a switch shows a coarse preview instantly, then
refines. The agent never stares at a blank window.

**A pipeline that implicitly needs global data.** Example: agent
writes `mesh.threshold([percentile(95), percentile(99)])` where the
percentile values were intended to be global. Solution: the
percentile call is a stats query on `ws.field(...)`, not on `mesh`.
The stats are global; the threshold applies to the subset. The agent's
decisions are globally accurate; only the rendering is subset-scoped.
This is the decisions-vs-rendering separation enforced by the API
shape: if the agent uses `mesh.percentile(...)`, that's a subset-only
answer, and the naming convention should strongly discourage it.

**First-ever open of an un-ingested directory.** Not interactive;
explicitly an offline step. The system tells the agent "this
directory is not a workspace yet; run `vislang ingest` first." No
surprise long waits during exploration.

**Feature extraction that the agent naively expects to be cheap.**
The extract namespace is the mitigation — `ws.extract.*` is named
specifically to communicate "this is a sweep, not an instant
operation." The return type (`FeatureRef` with `status="queued"`)
confirms it.

### The subtle epistemic shift

Today in VisLang, every query the agent makes is a global, exact
answer on the loaded file. At TB scale with this design, most queries
are still global and exact (the stats DB is authoritative), but a few
of them can be approximations: a subset-based number with a pending
global job. The agent has to understand which is which.

The design mitigates this by attaching `source` metadata to every
`StatValue`: `"base_db"`, `"derived_db"`, `"computed_now"`, or
`"subset_approximation"`. The agent sees the source. For decisions
that don't need to be exact (pick a colormap range, choose a starting
threshold for tuning), an approximation is fine. For decisions that
commit to a global extraction (the threshold values that get swept
across 100 timesteps), the agent should wait for or request the
global version. A thin convention — "read the source; if it says
approximation and the decision matters, wait for the global" — is
enough to make this robust in practice.

This is a real change in epistemics from the current prototype. It
is worth acknowledging that explicitly rather than hiding it. The
alternative (pretending every answer is exact) would produce silently
wrong visualizations. Making the approximation visible is honest and
the agent can handle it.

## Grammar-of-graphics ideas that earn their keep at scale

The earlier design reflection (`2026-04-10-gog-ideas-within-pyvista.md`)
looked at grammar-of-graphics ideas in the small-data case and
concluded that most of them are achievable within a PyVista-subset
approach using helper libraries and conventions, with only three
surviving as "genuinely needs a grammar layer" candidates. The
surprising result of the big-data exploration is that **several of
those marginal-in-small-data ideas become structurally necessary at
scale** — not because the concepts changed, but because the cost of
iteration is higher and structure pays for itself by preventing
mistakes you can't iterate your way out of.

Here is how each GoG-derived idea lands in the workspace architecture.

### Parameters as first-class grammar elements — structurally necessary

In the small-data reflection, this was listed as a survivor but one
that could wait until bidirectional editing became a priority. In the
big-data case, it moves from "eventually worth doing" to "probably
needed from day one."

The reason: a pipeline operating on a workspace is implicitly a
function of at least `(timestep, member, region, resolution_level)`,
and possibly more. Every batch sweep is a parameter sweep. Every
animation is a timestep scrub. Every ensemble comparison is a member
iteration. The system has to know what axes to sweep over and what
the interactive working subset's coordinates are. Without a
`vislang.param(...)` mechanism, this information lives in ad-hoc
Python variables that the system cannot inspect, the LSP cannot
autocomplete against, and the sweep executor cannot unify with the
interactive loop.

The declaration pattern stays lightweight:

```python
t = vislang.param("timestep", range=range(0, 100), default=50)
threshold = vislang.param("threshold", default=800, min=0, max=2000)
```

At runtime, `vislang.param(...)` returns the current value in
interactive mode (the working subset's timestep, or the default for
other params) and the swept value in batch mode. The system knows
what's tunable because it sees the `param` calls.

This is the Vega-Lite params idea landing cleanly in a Python library,
not as grammar syntax. The declaration is a function call; the
observation is a runtime introspection; the sweeping is a driver
that understands the declared ranges. No new language — just a
convention the tooling understands.

**The compound benefit.** Once the system knows what params exist,
several features fall out:

- **Batch sweeps use the declared ranges automatically.** The agent
  writes `ws.apply_across(pipeline_file)` and the system sweeps
  exactly the declared params.
- **Animation scrubbing knows which axis to scrub along.** If one
  param is named `timestep` and its range matches the workspace's
  timestep list, animation playback is "just scrub that param."
- **Bidirectional editing becomes cheap later.** When the system
  eventually supports manipulating the 3D view and propagating
  changes back to code, the "which literal to update?" question
  dissolves — drags update declared params, not arbitrary literals.
- **The pipeline file is self-documenting about what's meant to
  vary.** A human reader sees the params at the top and knows the
  rest is fixed.

### Declarative stats and extract sublanguage — structurally necessary

The small-data reflection walked this back: "fewer magic constants"
is solved by a good stats library plus function calls, not by making
stats first-class grammar elements. At TB scale that argument
reverses, for a specific reason I did not see in the small-data
analysis: **the system needs to see the request before computing,
not after**.

At small scale, `suggest_iso(mesh, "temperature", n=3)` runs eagerly
and returns a list. The function has already done its work by the
time the system has a chance to observe it. That's fine because the
work was cheap.

At TB scale, the work might be expensive enough to need tiering
(cheap / expensive), caching (hit / miss), escalation (subset
approximation plus background global job), and deduplication (if
two layers both want the same derivation, compute it once). None of
these are possible if the request has already executed by the time
the system sees it. The system needs the request as a *value* it can
hash, route, and defer.

The workspace architecture solves this by making stats and extract
requests **method calls on handles that return handles/StatValues
rather than executing eagerly**. The signature is Python; the
semantics are declarative. Each call is logically a specification
that the system schedules.

This is exactly the "stats as a small composable grammar" idea from
the earlier discussion, landing in a different place: not as a
replacement for the visualization DSL, but as a declarative
sublanguage specifically for data-extension requests. The small-data
version is "function calls." The big-data version is the same API
shape but with deferred semantics. The agent learns the same method
names either way; the system tier them differently underneath.

**The compound benefit.** Because requests are structured values:

- The workspace **caches** them across sessions.
- The executor **tiers** them by cost.
- Repeated requests in the same session become instant on the second
  call (cache hit).
- Batch sweeps can **deduplicate** shared derivations across layers.
- The workspace is **self-describing**: `list_artifacts()` enumerates
  every computed thing with its spec.

### Semantic field types driving defaults — stronger at scale

The small-data reflection rated this medium-strength. At scale it's
stronger for three reasons:

- **Ingestion benefits from knowing types.** The one-time ingestion
  pass can precompute different stats for different field types —
  signed histograms for diverging scalars, magnitude distributions
  for vectors, unique-value sets for categorical, etc. A type-unaware
  ingestion either computes the wrong thing or computes all possible
  things and wastes space.
- **Mistakes are more expensive.** Picking the wrong colormap for a
  signed anomaly field, then running a 100-timestep batch sweep, is
  a real waste of hours. Data-type-driven defaults prevent a whole
  class of expensive mistakes.
- **Cross-session consistency matters more.** The type is inferred
  once, at ingestion, and used by every session after. Contrast with
  small-data re-inference on each load.

The workspace stores inferred field types in the manifest and every
higher-level operation (stats precomputation, encoding defaults,
suggestion helpers, LSP hover, MCP describe tools) consults them
from there. One inference, many consumers. This is the "one
intelligence layer, multiple surfaces" story landing cleanly, with
the workspace manifest as the shared home.

### Global scales — structurally important for animation consistency

At single-frame scale, a helper object that bundles `clim` and `cmap`
and threads it into add_mesh calls is enough. At animation scale, it
becomes structural: you do **not** want the colormap range to vary
frame-to-frame — the animation would flicker and be unreadable. The
range has to be globally computed once and applied to every frame.

The workspace makes this a natural operation:

```python
temp_scale = ws.field("temperature").global_scale(
    method="percentile", range=(1, 99), cmap="hot"
)

# Every frame, materialized from features or working subset, uses it
plotter.add_mesh(mesh, **temp_scale.kwargs())
```

The `global_scale` call goes through the stats DB to look up the
relevant percentiles (instant, cached). The returned scale object is
a reusable configuration applicable to any render of the field. The
animation playback path automatically consults it so every frame is
consistent without manual discipline. Multi-view layouts similarly
use the same scale across views.

This is the ggplot2 "scales apply globally across layers" idea,
landing as a workspace-aware helper rather than as grammar syntax.
The structural property — "all renders of this field share this
scale" — is real, but it's a helper pattern, not a language feature.

### Spec-as-data for sweep records — the right place for it

The small-data reflection dismissed spec-as-data as "use the Python
AST instead of JSON." For pipeline files that conclusion stands. But
**batch sweep records** are a different kind of artifact and they
benefit from structured storage:

- Sweeps outlive sessions and must be queryable from Python by a
  different session, possibly days later.
- Sweeps need to be inspectable by tooling that doesn't parse Python
  (status dashboards, progress reports, provenance trackers).
- Sweep records must be comparable ("sweep 42 vs sweep 43 — what
  changed?") and merging structured records is simpler than diffing
  Python source.
- Sweeps should be resumable after interruption, which requires
  reading back the spec and restarting from where the executor
  stopped. A structured record is easier to resume from than
  re-parsing source.

So: sweep records are JSON (or similar) in the workspace, containing
the pipeline file's content hash, the parameter grid, the manifest
state, the output references, and the status. The pipeline Python
source lives alongside as a provenance reference (copied into the
sweep directory so it's immutable). Both representations earn their
keep: Python for authoring, JSON for the sweep's identity and
durability.

This is "spec as data" landing exactly where it belongs — the
persistent artifact layer — rather than being imposed on the
authoring surface.

### Encodings as reusable objects — unchanged, still a dataclass

The small-data verdict was: encodings-as-reusable-objects is a
ten-line dataclass, no DSL needed. The big-data verdict is the same.
The workspace layer doesn't change anything about how encodings are
structured; the `Encoding` dataclass from the earlier reflection
works identically in this architecture. It just gets fed values
from `ws.field(...).global_scale(...)` rather than hand-computed
from a single mesh.

### Orthogonal introspection — naming still carries the weight

Named variables are still the anchor for info-view, diffs, and LSP
hover. The workspace adds new structural nouns (handles, stats,
extracts, features, sweeps) that the introspection tooling also
understands. `list_artifacts()` gives an enumerable view of the
workspace's structured contents, complementing named-variable
introspection of the pipeline file.

### The overall shift

Looking at the list, the pattern is consistent: **ideas that bought
"ergonomic niceness" at small scale buy "structural necessity" at
large scale**. Params, declarative stats, semantic types, global
scales, and spec-as-data for sweeps all transition from
"nice-to-have" to "needed" when the cost of getting things wrong or
duplicating work becomes real. The earlier reflection's reluctance
to commit to these ideas was appropriate for the small-data case;
the big-data case flips the cost-benefit enough to commit to them.

What stays the same across both scales:

- **PyVista is the rendering substrate.** No change.
- **Pipeline files are Python.** No change.
- **Method chaining is fine for the lower vocabulary.** No change.
- **Encodings, layers, facets are helpers, not grammar.** No change.

What changes at scale:

- The upper layer is **larger and more structured** (workspace,
  stats, extracts, params, sweeps).
- Some of the upper-layer methods are **declarative and deferred**
  rather than eager.
- The workspace has a **persistent cached artifact** that grows
  across sessions.

None of this requires abandoning the PyVista substrate or building a
new DSL. It requires building a specific, well-scoped library above
PyVista that captures the structural ideas in the narrow places where
they earn their keep.

