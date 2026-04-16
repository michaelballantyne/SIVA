# Big data as a design opportunity -- streaming, pushdown, and the DSL

Date: 2026-04-16

Companion to the security entry from earlier today. Same origin: a
comparison pass with viznoir (`~/code/viznoir`). Where the security
entry was "here's what viznoir has that we should steal," this one is
the opposite -- a gap viznoir also has, but one where VisLang's richer
DSL might actually be better-positioned to close it. Posting as
research framing, not as a concrete work item.

---

## Current state: VisLang and viznoir both load everything

Both projects call `reader.Update()` and take whatever comes back. Neither
uses VTK's streaming pipeline. Neither pushes spatial subsetting into
the reader. Neither has automatic decimation, LOD, or memory guards.
Viznoir at least does frame-by-frame timestep loading in `animate` (one
timestep in RAM at a time); VisLang doesn't do multi-timestep at all
yet (BACKLOG item, line ~295).

Concretely in VisLang:

- Zero uses of `vtkStreamingDemandDrivenPipeline`,
  `UPDATE_PIECE_NUMBER`, `UPDATE_NUMBER_OF_PIECES`, `UPDATE_EXTENT`, or
  `UpdateInformation()`-before-`Update()`.
- `extract_region` and `clip` are post-load filters. They crop data
  that's already in memory.
- `list_data_files()` reports file size to the LLM; `describe_data`
  doesn't; nothing suggests "this is 1.1GB, consider decimation."
- The shared read cache in `tracked_core` is LRU-by-staleness, not
  size-bounded. It's a performance optimization for filter re-use, not
  a big-data solution.
- The wildfire dataset (~1.1GB) is the only one approaching
  interesting size; everything else is <50MB. The ceiling isn't being
  tested.

VISION.md has a "scale independence and HPC execution" section but
explicitly defers the mechanism -- "the reconciler generalizes...
different backends can execute it differently." That's a research
posture, not a design.

## Why VTK's streaming pipeline is the real lever

The thing both projects are leaving on the table is that VTK readers
already know how to return pieces, extents, and time subsets on
request, without loading the whole file. The pipeline protocol is:

1. Call `reader.UpdateInformation()` (not `Update()`) -- this reads
   metadata only: extent, timesteps, data type, array names, sizes.
2. Set requests on the output: `UPDATE_PIECE_NUMBER`,
   `UPDATE_NUMBER_OF_PIECES`, `UPDATE_EXTENT`, `UPDATE_TIME_STEP`.
3. Call `Update()`. The reader returns only what was requested.

Filters downstream honor the same requests. `vtkExtractVOI` and
`vtkExtractUnstructuredGrid` can push extent requests upstream so that
a "show me slice Z=0.5 of this 40GB structured grid" pipeline never
reads more than one Z-layer off disk. Partitioned formats (.pvtu,
.pvti, .pvd) carry this further -- each piece is a separate file, so
N-of-M reads fetch one file and leave the others untouched.

Neither VisLang nor viznoir wires any of this up. Every `Update()` is
a full-dataset `Update()`. That's *the* missed opportunity.

## Why VisLang's DSL might express this better than viznoir's

Viznoir's DSL is a flat Pydantic tree. To make it stream-aware,
viznoir would need to add `extent`, `piece`, `piece_count`,
`timestep_range` fields to every source type, and then wire them
through the compiler into the generated VTK script. Doable but
awkward: every source type in the schema grows new optional fields
that most users won't set, and the compiler has to check each one.

VisLang's DSL is executable Python against a builder. Streaming
concepts could be first-class verbs:

```python
# Declarative spatial pushdown
data = source("vtkXMLStructuredGridReader", FileName="sim.vts")
region = data.region(z=0.5)         # request extent, not post-filter
show(region.contour("temperature"))

# Declarative temporal pushdown
frames = data.timesteps(range(0, 100, 5))   # request 20 timesteps
animate(frames, lambda d: d.slice(z=0.5).show())

# Partitioned pushdown
pieces = data.pieces(of=total_pieces)       # request metadata first
for piece in pieces.take(4):                 # only load 4 of N
    process(piece)
```

The DSL already hides VTK's per-class idiosyncrasies behind builder
methods. Streaming requests are another category of VTK mechanics
that benefit from the same treatment -- "tell the reader what you
want" is a better abstraction than "load then filter." This is a
thing the expressive DSL can buy us that a flat JSON schema would
struggle with.

The key architectural shift: `source()` should not eagerly call
`Update()`. It should construct a reader, call `UpdateInformation()`
only, and return a proxy. `.region()`, `.timesteps()`, `.pieces()`
would set request metadata. The actual `Update()` -- the expensive
one -- only fires when the pipeline is committed to render or when a
downstream operation forces materialization (e.g. an MCP query tool
that needs array stats). Lazy source + eager-on-demand execution.
The `tracked_core` work is the right substrate for this: the DAG
already models "what's been materialized" vs "what hasn't."

## What gets easy once sources are lazy

A few things that are currently awkward or impossible fall out
naturally:

- **`describe_data` on a 40GB file.** Right now this would OOM. With
  `UpdateInformation()`-only source construction, metadata (bounds,
  extent, array names, timesteps) is cheap and the LLM gets to reason
  about the dataset before committing to load it.
- **"Preview first, refine later" workflows.** The LLM could start
  with `data.region(coarse)` to get a cheap render, look at it, then
  ask for `data.region(focus_bbox).at_full_res()` to drill in. This
  is how human analysts actually work with big data.
- **Spatially-aware suggestions.** `suggest_isosurface`,
  `suggest_scalar_range` could sample from a small extent rather
  than the whole volume, making them viable on datasets where they
  currently wouldn't be.
- **Multi-timestep without memory explosion.** The BACKLOG item
  becomes trivially tractable: `data.timesteps(...)` streams one
  timestep's `Update()` at a time; the DAG caches what's still in
  scope.
- **Partitioned data from HPC simulations.** The wildfire-style
  use cases scale: a simulation that wrote 64 partitions becomes
  usable without concatenating them.

## What's hard or risky

- **Filter composition with lazy sources.** Not every VTK filter
  honors extent/piece requests. Some materialize the full input
  regardless (e.g. iso-surface on unstructured grids across
  partitions, where the contour may cross pieces). We'd need a
  small annotation on each builder method -- "streams" vs
  "materializes" -- so the DSL can warn or fall back.
- **The `tracked_core` cache becomes more interesting.** Cache keys
  currently assume full-materialization. With partial requests, the
  key needs to include the extent/piece/timestep, and invalidation
  needs to handle the relationship "I have this region cached, you
  asked for a superset." Workable but adds state complexity.
- **Error surfaces shift.** "File not found" used to happen at
  `load()`; now it happens lazily, potentially deep in a pipeline
  the LLM has already committed to. We'd want `source()` to do a
  stat-and-open check eagerly even if it doesn't `Update()`.
- **Interactive renderer expectations.** The VTK interactor pipeline
  may call `Update()` on its own. We'd need the lazy-source layer to
  sit *between* the builder and the rendering pipeline, not replace
  it wholesale.

## Smaller wins that don't require the big rewrite

If the lazy-source overhaul is a multi-week research project, there
are incremental steps that each improve the big-data story on their
own:

1. **Add file size to `describe_data` output.** Cheap; lets the LLM
   notice before trying to render.
2. **Add `decimate`, `mask_points`, `resample_to_image`-with-dims
   helpers to the DSL.** These already exist in VTK; VisLang just
   hasn't exposed them. Gives the LLM a lever to say "this is big,
   let me reduce before rendering."
3. **Emit a warning when a dataset exceeds a threshold** (say 1GB)
   on first `Update()`. Surfaces the problem without blocking.
4. **Implement the BACKLOG's multi-timestep support frame-by-frame**
   (like viznoir's `animate`), not all-at-once. The viznoir pattern
   is simple and will keep working even once lazy sources land.
5. **In `tracked_core`, add a memory-budget eviction policy** on top
   of the current staleness-based one. Protects long interactive
   sessions from unbounded cache growth.

These five are all independent, each one a day or two, and each
moves VisLang from "assumes RAM is infinite" closer to "handles big
data gracefully even in the dumb mode." The lazy-source redesign is
the ambitious version; these are the unambitious versions that still
add value.

## Framing for the research question

If VisLang wants a differentiator against viznoir and the broader
"LLM + VTK" space, "expressive DSL with streaming semantics" is a
better pitch than "richer DSL" alone. The richness is only
interesting if it lets the DSL express things a flat schema can't --
and pushdown/streaming is the canonical example. An LLM writing
`data.region(z=0.5).contour("T")` and getting a one-slice read off
disk is qualitatively different from an LLM writing the same thing
against a system that loaded 40GB first and then threw most of it
away.

The framing I'd suggest: don't pitch this as a performance feature.
Pitch it as "what lets VisLang work on real simulation data."
Wildfire at 1.1GB is already the boundary of what fits comfortably
on a developer laptop. Real HPC output is 100-1000x that. A tool
that can't work with it is a toy.

## Notes for whoever picks this up

- The smaller-wins list above can start today and doesn't depend on
  the design question.
- The lazy-source design should start with a prototype in
  `experiments/`, not a refactor of `vislang/`. Two or three source
  types (`vtkXMLStructuredGridReader`, `vtkXMLUnstructuredGridReader`,
  `vtkPVDReader`) driving `UpdateInformation()`-only + extent
  requests, with a tiny test that verifies peak memory stays
  bounded. Prove the pattern before reshaping the DSL around it.
- `vtk.vtkStreamingDemandDrivenPipeline` is the relevant class; the
  VTK docs are terse and the examples live mostly in ParaView's C++.
  Budget time for spelunking.
- Worth talking to someone who's driven VTK streaming in anger
  before committing to the design. The pipeline's semantics around
  "what happens when a filter doesn't honor a request" are subtle
  and easy to get wrong.
