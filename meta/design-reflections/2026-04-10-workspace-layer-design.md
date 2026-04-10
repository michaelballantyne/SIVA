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
grammar-of-graphics ideas at scale, tracked-execution connection,
near-term build order, open questions and risks. Remaining sections:
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

## Connection to tracked-execution

The `tracked-execution` experiment in `experiments/tracked-execution/`
is already the right foundation for most of this design. Its core
machinery — proxy wrapping, content-addressed caching, scene
reconciliation, AST-level subset enforcement, `vtk_escape` as a
principled low-level hatch — maps cleanly onto the workspace
architecture. The changes needed are additive extensions, not a
rewrite.

Walking through the tracked-execution components and how they relate:

### What transfers directly

**Proxy-based dispatch.** `TrackedProxy` intercepts every method call
on a wrapped object, hashes the operation, looks it up in the DAG,
and either returns a cached result or executes and caches. This is
exactly the mechanism the workspace needs for its lower-layer
operations: PyVista filter calls on materialized working subsets
should go through the proxy so they're cached across iterations. No
change required.

**Content-addressed hashing.** `stable_hash` over scalars, tuples,
dicts, numpy arrays, and tracked proxies is the primitive the
workspace layer will reuse for stats-query cache keys, extract
request identity, and sweep record identity. The workspace extends
the set of hashable operand types (dataset handles, field handles,
feature refs) but the hashing primitive stays the same.

**SceneReconciler.** The reconciler's diff-and-apply pattern for
PyVista plotter actors is exactly what the animation playback mode
needs: as the animation scrubs through timesteps, the set of actors
changes per-frame, and reconciling with minimal updates is the
smooth-playback requirement. The reconciler generalizes from
"current pipeline vs. previous pipeline" to "current frame vs.
previous frame" with no conceptual change.

**`vtk_escape`.** The principled escape hatch for raw VTK operations
that PyVista does not expose, with function-hash-based caching that
participates in the DAG. This becomes the escape hatch at *both*
levels: lower-layer PyVista code uses it for one-off VTK filters, and
upper-layer extract verbs use it internally to implement operations
PyVista does not have a filter for. No change.

**AST-level whitelist enforcement.** The tracked-execution whitelist
validates pipeline files against an allowed subset of PyVista and
numpy operations. The workspace adds new whitelisted names (the
workspace/field/extract/param methods) but the validation mechanism
is the same AST walker.

**Pipeline file as the shared artifact.** Tracked-execution's model
of "pipeline file on disk, re-executed on change, watched by the
server, status reported to a file" carries over unchanged. The
workspace layer extends what's available in the namespace the file
runs in; everything else is identical.

### What needs to be extended

**Persistent cache.** Today the DAG is in-memory and GC'd between
runs. The workspace needs the cache to persist across sessions so
stats computed yesterday are available today. Two approaches:

1. **Pickle the DAG to a cache file** at session end and load it at
   session start. Simple; works for scalar/array results; gets awkward
   for VTK PolyData which needs VTK-native serialization.
2. **Store cache entries in their native formats** (VTP for polydata,
   Parquet or npz for arrays, JSON for scalars) keyed on the content
   hash, with a small index mapping hashes to file paths.

Approach 2 is more work but better for the workspace use case,
because the cache entries are the same files that populate the
feature DB, the stats DB, and the sweep outputs. Effectively, **the
workspace directories *are* the persistent DAG**: each
directory-per-hash corresponds to a DAG entry. A cache "lookup" is a
file existence check; a cache "store" is a file write with a hash
name. No separate cache layer is needed if the workspace format is
designed for this.

**Handle-aware operand types.** Today the proxy wraps PyVista meshes,
numpy arrays, and derived results. It needs to wrap new upper-layer
types: `DatasetHandle`, `FieldHandle`, `FeatureRef`. Each has its own
dispatch rules — stats methods on a handle route to the stats DB;
extract methods route to the background job queue; materialization
produces a real PyVista mesh that proxies as before.

The extension point is `dispatch()` in `dispatch.py`: add a
`_handle_upper_layer_call(obj, method_name, args)` branch that
detects upper-layer operand types and routes accordingly. Lower-layer
calls continue through the existing path.

**Tier-aware dispatch.** Today every dispatched call runs immediately
(hit or compute). The workspace needs three tiers:

- **Tier 1 (cache hit):** Return existing result. No change from
  today.
- **Tier 2 (cheap compute):** Compute now, cache, return. Same as
  today's miss path for most operations.
- **Tier 3 (expensive compute):** Return a pending handle/approximation,
  enqueue a background job. This is new — tracked-execution currently
  blocks until computation completes.

Adding Tier 3 requires a **background job queue**. Simplest version:
a persistent queue (SQLite rows, or a directory of queued-job files)
with a worker process that pulls from it and writes results back to
the workspace. The session interacts with the queue via "enqueue,"
"poll status," and "wait." The worker is a separate process started
by the VisLang server on first use.

**Extended whitelist for upper-layer methods.** The workspace/field/
extract/param method names need to be added to the whitelist. Their
argument types include handles, so the whitelist walker has to
understand handle references as valid operands.

### What does not change

- The authoring model (pipeline files on disk, plain Python)
- The server/MCP tool layer (still set_pipeline, screenshot, etc.)
- The file watcher and hot reload
- The sandbox/safety model (restricted builtins, import blocking)
- Error diagnostics and structured reports
- The interaction with PyVista itself (proxy interception of filter
  methods)

### Proxy layer responsibilities, summarized

The extended proxy layer becomes the **single point of dispatch** for
both vocabularies:

```
         user code
             │
             ▼
    ┌────────────────┐
    │  proxy layer   │  ← intercepts all method calls
    │                │
    │  dispatch:     │
    │   1. upper?    │  → workspace ops (stats, extract, sweep)
    │   2. lower?    │  → PyVista ops (existing path)
    │   3. escape?   │  → vtk_escape (existing path)
    │                │
    │  tier:         │
    │   1. cache hit │  → return
    │   2. cheap     │  → compute, cache, return
    │   3. expensive │  → enqueue, return pending
    └────────────────┘
             │
             ▼
      (lower layer, PyVista, or job queue)
```

Three dispatch branches (upper / lower / escape), three tiers
(hit / cheap / expensive). Nine combinations, all with well-defined
behavior. The existing tracked-execution code handles the
lower+cheap and lower+escape combinations. The workspace extensions
add the upper branches and the expensive tier.

This is a meaningful but well-scoped extension, not a rewrite. The
tracked-execution project's investment in the proxy, hashing,
reconciler, and whitelist all pay off in the workspace design.

### The version-3 intuition

Thinking of tracked-execution as the foundation of VisLang's next
phase, the progression looks like:

- **v1:** Custom VTK-direct DSL (`vislang/dsl.py`) — the original.
- **v2:** PyVista subset with tracked execution — what tracked-execution
  currently builds.
- **v3:** PyVista subset with tracked execution *plus* the workspace
  layer — the architecture in this document.

Each step is additive. v2 extends v1's lessons onto a better
substrate (PyVista instead of hand-written VTK wrappers). v3 extends
v2 to handle big data without losing what v2 got right. The
tracked-execution experiment is the thing that makes v3 tractable —
without the proxy, caching, and reconciliation work already done,
the workspace layer would need to rebuild all of that infrastructure
from scratch.

## Near-term build order

The design is big enough that how it's *sequenced* matters as much as
what's in it. The sequencing principle is: **each step should be
independently useful and should preserve the interactive feel**. No
"big bang" migrations where the system breaks for weeks while a
refactor settles. Each step lands a working system that is strictly
better than the previous one.

The build order falls out of two observations:

1. **The interactive feel is preserved by the stats DB and the
   working-subset abstraction, not by the pyramid.** You can build a
   working workspace with `base.db` + per-file materialization before
   adding pyramid streaming. The single working-subset case is just
   "this timestep's file," loaded whole. That's the same thing
   VisLang does today for a single file — so the interactive feel is
   preserved by construction.
2. **The upper vocabulary can land on small data first.** The
   workspace abstraction, handles, stats queries, and extract verbs
   all make sense for single-file exploration too — they just route
   to in-memory operations instead of persistent caches. Landing the
   API shape early gives the agent time to learn it while the big
   data infrastructure is still being built underneath.

With those two observations, the order below is organized into six
phases. Each phase is a shippable state.

### Phase 0: Baseline (tracked-execution + PyVista subset)

The state you're already planning to reach: tracked-execution
consolidated, PyVista restricted subset adopted, whitelist
enforcement, file watcher, reconciler, MCP server with a stable set
of query and mutation tools. This is the substrate everything else
builds on.

Nothing workspace-related here. Just making the current plan land
cleanly.

### Phase 1: Workspace API with single-file backend

**Goal:** introduce the workspace abstraction and upper-layer
vocabulary, backed by a trivial "one file per timestep, loaded
whole" implementation. No pyramid, no background jobs, no persistent
cache beyond what tracked-execution already has.

Items:

1. **`vislang.open_workspace(path)`** pointing at a directory that
   contains a simple `manifest.json` plus data files.
2. **Minimal manifest format:** list of timesteps with file paths,
   list of fields with types (inferred by quick scan of first
   timestep), extents, sizes. Manually constructed or generated by a
   trivial `vislang ingest` that only produces the manifest and
   nothing else.
3. **Dataset handles and working subset:** `ws.timestep(t)`,
   `ws.field(f)`, `ws.working_subset()`, `ws.set_working_subset(...)`.
   The working subset is initially just the currently-selected
   timestep's file loaded whole.
4. **Stats queries backed by in-memory computation:**
   `field.percentile()`, `field.histogram()`, `field.range()`,
   `field.suggest_isosurfaces()` — all computed on-demand from the
   loaded working subset. Cache results in the tracked-execution DAG
   so iteration is fast.
5. **`vislang.param(...)`** with its runtime observation mechanism.
   In interactive mode it just returns the default value. Batch mode
   doesn't exist yet.
6. **Pipeline files that use the workspace abstraction.** Update
   existing examples and docs to use `ws = open_workspace(...)`
   instead of `mesh = pv.read(...)` where applicable, and establish
   the workflow pattern.

**What's not here yet:** no extract verbs, no background jobs, no
pyramid, no persistent stats DB across sessions, no animation, no
batch sweeps. Just the API shape and the single-file backend.

**Why this is worth shipping standalone:** the API shape is the
thing the agent needs to learn. Landing it on small data first means
the agent can build fluency with handles, field references, and
stats queries before the scale-up introduces big-data subtleties.
Existing single-file workflows gain the benefit of "stats queries
are explicit and composable" without any regression.

### Phase 2: Persistent workspace and stats DB

**Goal:** the workspace becomes a durable artifact on disk. Stats
computed in one session are available in the next. No pyramid yet,
but the workspace is now a real thing with its own directory layout
and some discipline about what's in it.

Items:

1. **Workspace directory layout** as described in the "workspace
   artifact" section, minus the pyramid and feature DB.
2. **`vislang ingest` command** that walks a simulation output
   directory, produces the manifest, runs the base-stats pass (one
   pass over each field computing histogram, percentiles, spatial
   extent, distribution shape), and writes `stats/base.db`.
3. **Stats DB lookup path:** `field.percentile(q)` first checks
   `base.db`, then `derived.db`, then computes on the working subset
   as a fallback, then caches the result back into `derived.db`.
4. **`list_stats()` and `describe_stat()`** for workspace
   introspection.
5. **Field type inference during ingestion:** sequential/diverging
   scalar, vector, categorical, etc. Written to the manifest.
6. **Source-file drift detection:** manifest records file hashes;
   `ws.describe()` reports inconsistencies.

**What's not here yet:** still no pyramid, no extract verbs, no
animation. But the workspace is now a real cached artifact whose
value grows as it's used.

**Why ship this standalone:** this is the phase where the
interactive feel is *demonstrably preserved at TB scale* — the
stats queries become instant because they're reading precomputed
values rather than walking arrays. Loading a single timestep from a
TB dataset is still limited by file I/O, but every query about the
data is free. For many exploratory workflows where the agent wants
to "understand the data" without rendering every timestep, this is
already a huge win.

### Phase 3: Pyramid, working-subset switching, progressive rendering

**Goal:** fast switching between subsets of a large dataset. Loading
a single full timestep is no longer the bottleneck because you can
load a smaller-bounding-box or lower-resolution view cheaply.

Items:

1. **Pyramid format integration.** Start with Zarr multi-scale
   (OME-Zarr conventions) for regular grids. Add ingestion step that
   builds the pyramid per field per timestep.
2. **Pyramid-backed working subset:** `ws.set_working_subset(level=n)`,
   `ws.set_working_subset(bbox=[...])`, combinations of the two.
3. **Progressive rendering:** when a subset switch happens, render
   the current coarse level immediately and refine to the requested
   level in a background thread. The agent sees motion immediately
   and the final quality arrives shortly after.
4. **Prefetching:** speculative loading of adjacent subsets (the
   next timestep, a larger bbox, a finer level) in the background so
   switches are often warm.
5. **Curvilinear/unstructured support:** falls back to "downsampled
   VTK file per level" for now, upgrade later if benchmarks show
   it's the bottleneck.
6. **Optional: OpenVisus backend** as an alternative pyramid format
   for cases where Zarr streaming is not smooth enough. Start with
   Zarr; benchmark; add OpenVisus if needed.

**What's not here yet:** still no extract verbs for global feature
computation, still no animation.

**Why ship this standalone:** this is where the "zoom around huge
data" story lands. The agent can now open a TB workspace and
interactively explore any region of any timestep at any resolution
with sub-3-second response times. The stats DB makes the decisions
fast; the pyramid makes the rendering fast.

### Phase 4: Extract verbs and background jobs

**Goal:** global feature extraction across timesteps, producing
persistent feature database entries. This is the phase where the
user's "I want to find all the regions where fire is burning across
the whole simulation" example becomes a single API call.

Items:

1. **Background job queue** (SQLite-backed or directory-based) with
   a worker process that the VisLang server spawns.
2. **`ws.extract.*` verbs:** `isosurface`, `threshold`, `slice`,
   `clip`, `streamlines`, `glyph`, `where`, `bbox`, `extract_surface`.
   Each records a request, enqueues a sweep job, returns a pending
   `FeatureRef`.
3. **Feature database format** on disk:
   `features/<name>/frame_NNNN.vtp` plus `spec.json` and `stats.json`
   per feature.
4. **Job lifecycle:** `feature_ref.status()`, `feature_ref.wait()`,
   `feature_ref.cancel()`, `ws.list_jobs()`, `ws.cancel_job(id)`.
5. **Materialization of FeatureRefs:** `feature_ref.materialize(t)`
   returns a real PyVista mesh from the feature DB for use in
   interactive rendering.
6. **Chained extracts:** `ws.extract.surface(ws.extract.threshold(...))`
   producing nested feature DB entries.

**Why ship this standalone:** this is the phase where "visualize
features globally without materializing the raw data" becomes real.
The interactive loop is unaffected (extracts are async), but the
agent now has a persistent, cached, cross-session surface for
computed features.

### Phase 5: Animation playback and batch sweeps

**Goal:** smooth animation across a sweep dimension, driven by the
feature database.

Items:

1. **`animate(feature_ref)` mode** in the VisLang server, with its
   own interaction affordances (play/pause, scrub, loop, fps
   control).
2. **`ws.apply_across(pipeline_file, params)`** general sweep
   operation that goes beyond single-extract calls: apply the entire
   pipeline to a parameter grid. Output is a new feature DB entry
   per (pipeline_version × param_tuple).
3. **Sweep record persistence:** structured sweep specs written to
   `sweeps/<id>/spec.json` with status and output references.
4. **Progress reporting through the MCP server:** `list_jobs`,
   `describe_job`, `list_sweeps`, `describe_sweep` tools so the
   agent can see what's running.
5. **Sweep resumability:** if the worker crashes or is killed, the
   sweep can be resumed from the last completed parameter tuple.

**Why ship this standalone:** this is the phase where the agent can
say "build me an animation of the fire front across all 100
timesteps" and have it actually work smoothly end-to-end. The entire
workflow chain — stats queries, interactive tuning, global commit,
animation playback — is operational.

### Phase 6 and beyond: polish and specialization

After phase 5 the architecture is complete and the remaining work is
polish:

- Ensemble-specific visualization modes (small multiples, overlays,
  outlier highlighting)
- Parameter-space visualization for large sweeps
- LSP integration for workspace-aware editing
- Bidirectional editing leveraging declared params
- Advanced cinema-style exploration over sweep outputs
- In-situ and remote-data extensions (out of scope for the current
  grant but architecturally compatible)

These are individually valuable but not required for the core grant
deliverable, which is met by phases 1-5.

### What this sequence preserves

Every phase is independently useful, and every phase preserves the
interactive feel:

- **Phase 1** gains the API shape on small data; feel is identical
  to today.
- **Phase 2** makes stats queries instant on TB data; feel is
  preserved for queries.
- **Phase 3** makes subset switching and raw-data rendering fast on
  TB data; feel is preserved for visual iteration.
- **Phase 4** adds async global extractions; feel is preserved
  because extracts are explicitly not interactive.
- **Phase 5** adds smooth animation; feel is preserved because
  playback runs on the feature DB.

At no point does the system regress. No phase requires the other
phases to ship first (except the obvious phase-0 foundations). This
gives flexibility in the build order if benchmarks or user feedback
suggest reordering.

## Open questions and risks

The design has places where the right answer isn't obvious and
where the wrong answer would hurt. These are worth naming explicitly
so they get revisited during implementation rather than locked in by
the first plausible choice.

### Questions about the design that need resolution

**1. What exactly does "working subset" mean for non-regular grids?**

The design assumes you can ask for a subset of a dataset that is
small enough to fit in memory, cheap to load, and representative
enough to render. For regular grids (image data, structured grids
resampled to regular), this is easy: subsample, bbox, pyramid level.
For curvilinear structured grids it's harder but feasible (the VTK
extract-grid filter does it today). For unstructured grids it's
genuinely hard — there is no canonical "pyramid" for a tetrahedral
mesh, and bounding-box extraction doesn't always preserve the
topological structure that filters need.

Pragmatic answer: start with regular and curvilinear grids; accept
that unstructured grids initially fall back to "load the whole
timestep or nothing." If the grant use cases don't need
unstructured at scale, this is fine. If they do, we need a separate
research pass on unstructured-mesh pyramid construction. This should
be decided based on the specific datasets the grant targets.

**2. What happens when the working subset and the extract output
disagree about scale?**

Example: the agent tunes a threshold on a timestep-50 working subset
at level 2 (coarse), settles on a value, commits via
`ws.extract.threshold(...)`. The extract runs on level 0 (full
resolution) across all timesteps. The full-resolution threshold
might look different from the coarse-level threshold the agent was
tuning against — different edge topology, different feature count,
different spatial extent. The agent's decision was informed by a
different scale than the commit produces.

Possible mitigations:
- Tune on level-0 subsets whenever possible; use coarse levels only
  for overview.
- The extract verbs could warn if the tuning was done at a coarser
  level than the extraction will use.
- The stats DB reports values at every level, so the agent can
  check whether a value that works at level 2 also works at level 0.
- Progressive refinement during tuning: render coarse first, then
  refine to the level the extract will use, so the agent sees the
  final-scale preview before committing.

None of these is automatic. This is a real sharp edge in the
interactive loop and deserves careful attention during Phase 3.

**3. How does the stats DB handle derived fields the agent invents
on the fly?**

The base stats DB precomputes for the fields present in the raw
data. If the agent writes a pipeline that computes a new field
(e.g., `compute_vorticity(velocity)` or a custom expression), the
stats DB doesn't know about it. The agent queries `field.percentile`
on the derived field and... what? Possible answers:

- **Extend the stats DB lazily.** On first query, compute the
  derived field across the pyramid and cache the stats. Subsequent
  queries hit the cache. This is the "derived stats DB" behavior
  from the main design.
- **Scope the derived field to the working subset.** The derivation
  and its stats only exist for the currently-loaded subset. Queries
  about the global distribution are honest errors: "you haven't
  computed this globally yet."
- **Require explicit registration.** The agent has to call
  `ws.register_derived_field("vorticity", ...)` before stats are
  available globally. More ceremony, clearer semantics.

The lazy extension feels right, but it has cost implications (the
derivation runs on the full pyramid on first query). The other
options are cheaper but more awkward. This needs a sharper decision
during Phase 2.

**4. Should the upper vocabulary be an entirely separate module or
integrated into PyVista-compatible subclasses?**

Two ways to arrange the API:

- **Option A: separate module.** `vislang.open_workspace(...)` returns
  a workspace object from the `vislang` package; PyVista is imported
  alongside. The agent uses two namespaces (`ws.*` and `pv.*`) that
  interoperate at materialization points.
- **Option B: PyVista subclasses.** The workspace's handle types
  subclass (or duck-type) PyVista base classes so they look like
  PyVista meshes to code that doesn't know the difference. The agent
  can often forget which is which.

Option A is cleaner conceptually (two vocabularies, explicitly
separated) but requires the agent to learn the boundary. Option B is
more seamless but makes the "when is this cheap vs expensive?"
question harder to answer by reading the code.

My current inclination is Option A because the cost distinction is
the whole point of the two vocabularies — blurring it defeats the
design. But I don't hold this strongly and the ergonomics of Option
B might win in practice. Worth prototyping both early.

**5. How does the pipeline file change between interactive and
batch modes?**

The design says "the same pipeline file works at every scale." But
the specifics need work. In interactive mode, `vislang.param("timestep",
...)` returns the current working subset's timestep. In batch mode,
it returns the current sweep value. Is the pipeline file:

- Executed top-to-bottom once per sweep value (Python-function
  semantics)? Re-importing modules, re-running setup, etc.
- Executed in a scope where the `param()` calls mutate a shared
  state (closure semantics)? Cheaper but weirder.
- Literally wrapped in a function the driver calls with different
  values?

Each has trade-offs. The middle option (shared closure) is closest
to the current "script-like pipeline file" feel but makes the
state-per-param-value unclear. The first option (function-like) is
cleanest semantically but has more overhead per iteration.
Probably needs a prototype to decide.

**6. What's the right scope for parameters?**

A pipeline file might declare many params. Some are sweep axes
(timestep, member). Some are tunables (threshold, colormap). Some
are both (a threshold that's tuned interactively but also swept for
a comparative animation). The design needs a clear story for:

- Which params are swept by a given `apply_across` call?
- How are params that are *not* being swept set for the sweep?
- Can params be dependent on each other? (e.g., a z-range that
  depends on the current timestep's ground elevation)

This is the kind of thing that looks simple until you try to
formalize it. Probably needs a few iterations of use before the
right answer is obvious.

### Risks to the interactive feel

**Risk 1: Stats DB misses are more common than expected.**

The design assumes most stats queries hit the base DB or the
derived DB. If the actual query pattern is "agent asks for weird
conditional stats that are almost always misses," then every query
goes through the expensive-compute path, the subset-approximation
caveat lands on everything, and the feel degrades.

Mitigation: observe the query patterns in early prototypes and
expand the base stats DB with whatever is commonly asked. The
database is meant to grow — it's not a fixed set. If it turns out
the agent always wants 90% conditional percentiles over thresholded
regions, precompute those at ingestion.

**Risk 2: Progressive rendering feels jarring.**

Going from "the final render appears after ~1 second" to "a coarse
render appears immediately and refines to final after ~3 seconds"
is a different interaction feel, even if the total latency is the
same. The agent might prefer the hard-delayed final to the
progressive intermediate. Or vice versa. Untested.

Mitigation: make the refinement fast (small number of discrete
levels, not continuous streaming) and consider whether to skip
progressive rendering when the subset is small enough to load at
full resolution immediately.

**Risk 3: Subset switching breaks visual continuity during
iteration.**

If the agent changes the working subset mid-session, the next
render is "different data" and its results may not be directly
comparable to the previous render. The agent's iteration loop
assumes "same data, different parameters" — switching data is an
axis it may not reason about well.

Mitigation: make subset switches explicit actions (not implicit
side effects), report them prominently in the response ("now viewing
timestep 80, level 0"), and offer a "switch back" operation that
returns to the previously-active subset. Possibly track the history
of subsets the agent has used and surface it in responses.

**Risk 4: Background jobs fail silently.**

A sweep runs in the background, produces wrong results or crashes,
and the agent doesn't notice until they try to use the output.
Depending on the failure mode, this could waste hours of the
agent's session.

Mitigation: aggressive status reporting (every job has a visible
state and any failure is surfaced in the next agent interaction),
automatic detection of partial failures during the sweep, and the
option to halt-on-first-failure vs continue-best-effort. Consider a
"notify me when this job finishes or fails" callback the agent can
register.

**Risk 5: Agent forgets which mode it's in.**

Four operational modes (explore, tune, commit, animate) plus
multiple subsets plus sweeps in flight. The agent might lose track
of state — "am I tuning or committing?" — and take actions
inappropriate for the current mode. Especially if multiple sessions
are active.

Mitigation: every agent response from the server includes a
compact state summary: current workspace, current subset, active
jobs, recent sweeps. The agent sees its own state as part of every
interaction rather than having to maintain it from memory.

### Risks to the architecture

**Risk 6: The pyramid format becomes the bottleneck.**

If Zarr (or whatever is chosen) doesn't deliver the streaming
smoothness the interactive feel requires, Phase 3 stalls. Switching
to OpenVisus or a custom format is weeks of work and throws off the
build order.

Mitigation: benchmark the pyramid format against realistic TB
datasets early in Phase 3, before committing to downstream phases.
Have a fallback (OpenVisus) identified and scoped so switching is
a known-cost operation if needed.

**Risk 7: The ingestion pass is too slow to be practical.**

If ingestion takes 48 hours on a TB dataset, users won't run it,
and the workspace model becomes aspirational. The design depends on
ingestion being "a few hours of overnight compute per dataset" or
faster.

Mitigation: parallelize ingestion aggressively (it's embarrassingly
parallel across fields and timesteps). Measure early. Consider
whether incremental ingestion (stats first, pyramid second, types
third) lets users start exploring partially-ingested workspaces.

**Risk 8: The workspace grows without bound.**

Every session extends the derived stats DB and the feature DB. Over
months of use, the workspace could balloon to multiples of the raw
data size. Disk consumption becomes a chronic complaint.

Mitigation: LRU eviction based on access time, with pinning for
entries the user marks as keepers. A `vislang prune --older-than 30d`
command to manually reclaim space. Size reporting in `ws.describe()`
so the user sees it growing.

**Risk 9: Proxy layer complexity becomes unmaintainable.**

The proxy is already nontrivial in tracked-execution. Adding
upper-layer dispatch, tier awareness, persistent caching, handle
types, and background job dispatch multiplies the complexity. At
some point the proxy becomes the hardest part of the system to
reason about.

Mitigation: be ruthless about separation of concerns in the
proxy. Each extension point (upper dispatch, tier dispatch,
persistence) should be a clearly-bounded module with its own tests.
Resist the urge to make the proxy "smart" beyond what's strictly
necessary. If the proxy starts feeling like a frame for every
possible behavior, something is wrong with the design.

**Risk 10: The LLM gets confused by the two vocabularies.**

The whole design hinges on the agent understanding when to use
`ws.extract.*` vs `mesh.*`. If the LLM can't reliably pick — if it
calls expensive operations thinking they're cheap, or vice versa —
the feel breaks.

Mitigation: lean hard on naming. The `extract` namespace is
specifically named to communicate "expensive." The return types are
different (`FeatureRef` vs `Mesh`). The ACI/MCP tool descriptions
emphasize the distinction. Error messages from wrong-mode calls
explain which vocabulary the agent should be using. Measure
empirically with real LLM sessions — don't trust intuition about
what's "obvious" to a language model.

