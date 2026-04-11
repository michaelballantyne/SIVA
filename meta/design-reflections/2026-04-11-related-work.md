# Related Work: Automated Data Management for Visualization

*Design reflection — April 11, 2026*

*This document catalogs related work relevant to VisLang's design
— particularly the ideas about a custom interpreter for
PyVista-compatible syntax, a content-hashed DAG as intermediate
representation, a compiler that plans execution against a workspace,
and scope inference from DAG dependencies. For each cluster of work,
we note what VisLang can adapt and where it diverges.*

## 1. Computation graph capture from Python code

VisLang proposes a custom interpreter that takes Python code written
in PyVista+NumPy syntax and gives it DAG-construction semantics.
This pattern — capturing a computation graph from user-written Python
without requiring the user to learn a new language — has been
explored extensively in the ML compiler ecosystem.

**JAX** uses tracing: a function is called with abstract "tracer"
values that record operations into a computation graph (an XLA HLO
program) instead of executing them. The traced graph is then compiled
and optimized. JAX imposes exactly the restrictions VisLang's
pipeline files would need: no data-dependent Python control flow
(`if` on traced values raises `ConcretizationTypeError`), no side
effects, pure functions only. JAX provides structured escape hatches:
`jax.lax.cond` for conditionals, `jax.lax.scan` for loops,
`jax.lax.while_loop` for iteration — all of which embed control flow
into the compiled graph rather than relying on Python's control flow.
JAX's documentation of these restrictions and escapes is a direct
model for how VisLang should document its dialect.

*Adaptable idea:* JAX's approach to teaching users the
restricted-Python dialect — clear error messages naming the
restriction, concrete escape hatches offered in the error, and a
"sharp bits" documentation page — is directly transferable. The
specific control-flow primitives (`cond`, `scan`) may also inform
VisLang's `when()` and parameter-sweep constructs.

**PyTorch 2 / TorchDynamo** takes a different approach: it hooks
into CPython's frame evaluation API (PEP 523) to intercept and
rewrite Python bytecode at runtime. TorchDynamo symbolically
evaluates bytecode to produce FX graph fragments, handling Python
control flow by "graph breaking" — splitting the computation into
graph segments separated by opaque Python code. This allows
TorchDynamo to handle arbitrary Python, including data-dependent
control flow, at the cost of producing smaller, less optimizable
graph fragments. A guard system checks whether cached graph fragments
are still valid for new inputs.

*Adaptable idea:* TorchDynamo's graph-break strategy is relevant if
VisLang wants to support `apply()` escape hatches that contain
arbitrary Python. The outer pipeline is a clean DAG; `apply()` blocks
are graph breaks where the compiler treats the block as opaque. The
guard system (checking whether cached results are still valid) maps
to VisLang's content-hash-based cache invalidation.

**Dask** builds task graphs from familiar NumPy/Pandas-style API
calls. Operations on Dask arrays and DataFrames are lazy — they
record tasks in a DAG rather than executing immediately. A scheduler
then executes the graph with optimizations: task fusion (merging
sequential tasks to reduce overhead), culling (removing tasks whose
results aren't needed), and memory-aware scheduling. Dask's
high-level graph representation preserves structure (e.g., "this is a
blocked matrix multiply") that can be optimized before lowering to
individual tasks.

*Adaptable idea:* Dask's two-level graph representation — high-level
operations that preserve semantic structure, lowered to fine-grained
tasks for execution — maps to VisLang's DAG (high-level visualization
operations) compiled into an execution plan (fine-grained VTK filter
calls, cache lookups, sweep jobs). Dask's graph optimization passes
(fusion, culling) are directly relevant to VisLang's compiler.

## 2. Declarative visualization compilation

VisLang's compiler takes a DAG (the spec) and produces an execution
plan. This pattern — compiling a declarative visualization
specification into an optimized execution strategy — has direct
precedents in the information visualization community.

**Vega-Lite → Vega** is a multi-stage visualization compiler. The
author writes a concise, high-level Vega-Lite specification (data,
marks, encodings, scales). The Vega-Lite compiler expands this into
a full Vega specification — a lower-level reactive dataflow program
that handles scale resolution, axis generation, legend construction,
and data transformation planning. Vega then compiles to a scenegraph
rendered by Canvas or SVG. The key architectural insight: the
compilation stages are cleanly separated, each with its own
intermediate representation. The high-level spec captures intent;
the compiler handles layout, scale binding, and guide generation;
the runtime handles rendering and interaction.

*Adaptable idea:* The multi-stage compilation pattern (high-level
spec → mid-level plan → low-level execution) is exactly VisLang's
proposed architecture. Vega-Lite's automatic scale resolution — where
the compiler infers shared scales across faceted views — parallels
VisLang's scope-from-DAG inference.

**VegaPlus** extends Vega to handle large data by automatically
splitting execution between a client-side Vega runtime and a
server-side DBMS (PostgreSQL or DuckDB). Given a Vega dataflow
with N operators, VegaPlus enumerates valid partitioning plans
(which operators run server-side, which client-side), then uses a
learned cost model (pairwise ranking via RankSVM or Random Forest)
to select the best plan. The optimizer is interaction-aware: it
sums costs across anticipated user interactions to find the globally
best partition, not just the best partition for a single query.

*Adaptable idea:* VegaPlus's plan enumeration and cost-model-based
selection is the closest precedent to what VisLang's compiler needs
to do — except VisLang partitions across pyramid levels, cache,
stats DB, and background sweeps rather than client vs. server. The
interaction-aware optimization (planning for the full interactive
session, not just one frame) is directly relevant to VisLang's
latency-budget approach. VegaPlus's use of learned cost models
rather than hand-tuned heuristics is worth considering for VisLang's
compiler as it matures.

**Mosaic** (UW IDL, Heer & Moritz, 2024) is an architecture where
interactive visualization components publish their data needs as
declarative queries to a coordinator backed by DuckDB. The
coordinator optimizes, caches, and routes queries — pushing
computation (binning, aggregation, regression) down to the database.
Cross-filtering across views works through shared "selections"
(query predicates) that the coordinator propagates to all
subscribing clients. The coordinator is the intelligence layer that
sits between declarative specs and execution, deciding how to serve
each query efficiently.

*Adaptable idea:* Mosaic's coordinator role maps directly to
VisLang's compiler — both sit between declarative intent and
execution, both manage caching and query routing, both propagate
shared state (Mosaic's selections, VisLang's shared scales and
parameters) across views. Mosaic's design of pushing computation
to the most efficient backend (DuckDB) parallels VisLang pushing
computation to the stats DB, pyramid, or feature DB depending on
the node type.

**Tableau / VizQL** compiles visual specifications into optimized
database queries. The user specifies data, visual encodings, and
interactions; VizQL translates this into SQL with automatic
aggregation, filtering, and layout decisions. The user never writes
SQL; the compiler makes all query-planning decisions. This is the
commercial realization of the "declarative spec → compiler → plan"
pattern at scale, deployed to millions of users.

*Adaptable idea:* Tableau demonstrates that the "author writes
intent, compiler handles execution" model works in practice at
scale. Its success suggests that VisLang's bet on a compiler that
absorbs scheduling decisions is viable, provided the compiler's
defaults are good enough for the common case — which Tableau
achieved through years of iteration on heuristics and cost models.

## 3. Large-data interactive visualization

VisLang's workspace architecture — precomputed stats, multi-resolution
pyramids, cached features, background sweeps — is designed to keep
interaction fast on TB-scale data. Several systems have tackled
this problem for 2D information visualization; VisLang extends
the ideas to 3D scientific visualization.

**Falcon** (Moritz, Howe, Heer, CHI 2019) maintains real-time
interactivity (50fps brushing and linking) across multiple
visualizations of large datasets. Its key technique: when the user
activates a view for brushing, Falcon builds an index containing
precomputed aggregations for every possible brush position in that
view. This is expensive up front but makes brushing instant. When
the user switches active views, Falcon loads reduced-resolution
indices first (for immediate responsiveness), then progressively
refines to full resolution. The system sustains constant brushing
performance regardless of dataset size.

*Adaptable idea:* Falcon's "precompute for the active interaction,
progressively refine on view switch" strategy maps directly to
VisLang's approach: serve the current frame from cache/pyramid
instantly, progressively improve resolution, and precompute for
animation (the equivalent of "all possible brush positions" is "all
timesteps"). The progressive refinement pattern — coarse first, then
improve — is how VisLang's compiler should handle pyramid-level
selection.

**Nanocubes** (Lins, Klosowski, Scheidegger, 2013) is a data
structure for real-time exploration of spatiotemporal datasets. It
precomputes hierarchical aggregations over space and time,
supporting heatmaps, histograms, and parallel coordinates at
interactive speed on billions of records. The data structure fits
in laptop memory through careful sharing of subtrees in the
hierarchy.

*Adaptable idea:* Nanocubes demonstrates that hierarchical
precomputation of statistics is the right strategy for interactive
exploration of large spatiotemporal data. VisLang's stats DB
(per-field, per-timestep histograms, percentiles, ranges) is the
3D scientific data analog of Nanocubes' hierarchical aggregation
cubes.

**imMens** (Liu, Jiang, Heer, 2013) precomputes binned
aggregations and uses the GPU to composit them during interactive
brushing. It decomposes multivariate data into projections that
can be independently binned and GPU-composited, enabling
interactive exploration of datasets too large for main memory.

*Adaptable idea:* The decomposition into independently cacheable
projections parallels VisLang's decomposition of a visualization
into independently cacheable DAG subtrees — each filter chain,
each stats query, each derived field can be cached and reused
independently.

**Query-driven visualization** (Rübel, Bethel, et al., 2012)
addresses extreme-scale scientific data by computing only the
subset of interest. Rather than loading and rendering an entire
TB-scale dataset, the system uses index structures to identify
and extract just the "scientifically interesting" subset, then
visualizes that. The premise: for any given visualization task,
the relevant data is a small fraction of the whole.

*Adaptable idea:* This is the intellectual foundation for
VisLang's approach of never putting raw TB-scale data in the
interactive loop. The workspace pyramid, feature DB, and stats
DB are all mechanisms for computing and caching the small
interesting subsets that the visualization actually needs.

## 4. Scientific workflow provenance and caching

VisLang's content-hashed DAG provides identity, diffing, and
caching across sessions. These concerns have been explored in
scientific workflow systems and data pipeline tools.

**VisTrails** (Bavoil, Callahan, Scheidegger, et al.) is the most
directly relevant precedent. It represents visualization workflows
as DAGs with an action-based provenance model: rather than storing
multiple workflow versions, it records the sequence of operations
(add module, change parameter, delete connection) applied to
workflows, like a database transaction log. Any prior state can be
reconstructed by replaying actions. This enables workflow diffing
(compute the transformation sequence between two pipelines),
analogies (apply the same transformation pattern to a different
pipeline), and caching (modules with identical inputs reuse cached
outputs). VisTrails' dataflow DAG executes bottom-up, with modules
producing data consumed by downstream modules — the same pattern as
VisLang's tracked-execution proxy.

*Adaptable ideas:* VisTrails demonstrates that change-based
provenance, DAG-level diffing, and functional caching work together
as a coherent system for scientific visualization. VisLang's
content-hash approach gives similar capabilities with a different
mechanism (structural hashing vs. action replay), and the
hash-based approach is arguably simpler because identity is derived
from content rather than tracked through history. VisTrails' insight
that "the data passed between modules are themselves modules"
(unifying computation and data representation) is also present in
VisLang's DAG where every node — whether a filter result, a stats
query, or a scale — is a first-class DAG node.

**ParaView Cinema** takes a different approach to the large-data
problem: rather than making the visualization interactive against
the full data, it pre-renders images and extracts features across
parameter spaces during in-situ or batch processing, then stores
the results in a database for post-hoc interactive exploration. A
Cinema database contains images rendered at many camera angles,
timesteps, and parameter values; the user explores by browsing
pre-computed views rather than re-rendering.

*Adaptable idea:* Cinema's feature extraction + database approach
maps directly to VisLang's feature DB + sweep records. The key
difference is that Cinema pre-computes everything up front (during
simulation or in batch), while VisLang's compiler schedules
extraction incrementally as the author builds the pipeline. Both
produce the same artifact — a database of pre-extracted features
indexed by parameter values — but VisLang's approach is
demand-driven rather than speculative.

**DVC (Data Version Control)** applies content-addressed caching
and DAG-based pipeline management to ML workflows. DVC computes
cryptographic hashes for data files, stores them in content-
addressed cache, and tracks pipelines as DAGs where each stage has
declared inputs and outputs. On re-execution, DVC automatically
determines which stages need re-running by comparing hashes,
skipping stages whose inputs haven't changed.

*Adaptable idea:* DVC's content-addressed caching with DAG-based
invalidation is structurally identical to what VisLang's
tracked-execution cache does. DVC's `dvc.lock` file — recording
the hashes of all inputs and outputs for each pipeline stage — is
the same concept as VisLang's proposed plan lock file. The parallel
is close enough that VisLang could study DVC's implementation of
incremental re-execution for engineering guidance.

## 5. Domain-specific scientific visualization frameworks

VisLang's workspace manages multi-resolution data, derived fields,
and lazy access for large scientific simulations. Two existing
frameworks have tackled similar problems in their domains.

**yt** is a Python toolkit for analyzing volumetric astrophysical
simulation data. Its architecture is relevant to VisLang in several
ways:

- *Derived field system.* yt has a three-tier field hierarchy:
  on-disk fields (raw simulation output), derived fields (declared
  as Python functions of other fields), and alias fields
  (format-specific name mappings). When a derived field is
  requested, yt automatically traces its dependencies back to
  on-disk fields, computes the minimal set of disk reads needed,
  and executes the derivation. This is very close to VisLang's
  `fire.derive("vorticity", from_="velocity", method="curl")`
  concept, and yt demonstrates that the pattern works at scale
  across hundreds of simulation codes.

- *Lazy data access.* Data selectors (regions, spheres, rays) are
  lightweight objects that don't trigger I/O. Actual data loading
  happens only when array values are accessed, and yt uses chunking
  strategies (spatial, I/O-aligned, or monolithic) to optimize disk
  access patterns. This is the same lazy-handle pattern VisLang's
  dataset handles use.

- *Multi-resolution support.* yt abstracts five major data
  discretization methods (grid AMR, octree AMR, SPH, unstructured
  mesh, discrete particles) behind a unified selection interface.
  A coordinate handler decouples logical data layout from physical
  coordinates, enabling the same analysis code to work across
  different simulation codes and grid types.

- *Science-first design.* yt prioritizes physical correctness over
  raw speed — for example, SPH particle selection includes
  particles whose smoothing kernels overlap the selection region,
  not just those whose centers are inside it. This philosophy
  aligns with VisLang's commitment to correct global statistics
  even when the interactive display is approximate.

*Adaptable ideas:* yt's derived field dependency resolution (trace
back to disk fields, compute minimal reads) is directly relevant to
VisLang's compiler planning derived field computation across
timesteps. The lazy selector pattern validates VisLang's dataset
handle design. The multi-code abstraction through coordinate
handlers suggests that VisLang's workspace manifest should capture
enough grid metadata to support similar format-agnostic access.

**ParaView's pipeline architecture** represents visualization
workflows as a demand-driven DAG of filters. Each filter declares
its inputs and outputs; execution propagates upstream from the
display sink. ParaView's pipeline has no automatic optimizer — the
user manually configures resolution, LOD, and parallel
decomposition — but the architectural pattern (DAG of filters,
demand-driven execution, upstream propagation) is the same
foundation VisLang builds on. The key difference is that VisLang
adds a compiler between the DAG and execution, making the
optimization decisions ParaView leaves to the user.

*Adaptable idea:* ParaView's extensive filter library and its
conventions for declaring input/output types inform what VisLang's
whitelist needs to cover. ParaView's demand-driven execution
(only compute what the display needs) is the baseline strategy
VisLang's compiler should use before applying more sophisticated
optimizations.
