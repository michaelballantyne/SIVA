# Related Work: Automated Data Management for Visualization

*Design reflection — April 11, 2026*

*VisLang's design combines ideas from several distinct research
communities: ML compilers (capturing computation graphs from
Python), information visualization (compiling declarative specs into
optimized execution), large-data systems (precomputation and
progressive refinement for interactive speed), scientific workflow
management (provenance, caching, reproducibility), and domain-specific
scientific visualization (derived fields, multi-resolution access).
No single system combines these for 3D scientific visualization with
an LLM agent as the primary author.*

*This document surveys the most relevant prior work, organized by
which aspect of VisLang's design it informs. For each system, we
note what VisLang can adapt and where its needs diverge.*

## 1. Computation graph capture from Python code

VisLang proposes a custom interpreter that takes Python code written
in PyVista+NumPy syntax and gives it DAG-construction semantics.
This pattern — capturing a computation graph from user-written Python
without requiring a new language — has been explored extensively in
the ML compiler ecosystem. The three major approaches each offer
different tradeoffs that inform VisLang's design.

**JAX** uses tracing: a function is called with abstract "tracer"
values that record operations into a computation graph instead of
executing them. JAX imposes exactly the restrictions VisLang's
pipeline files would need: no data-dependent Python control flow
(`if` on traced values raises `ConcretizationTypeError`), no side
effects, pure functions only. JAX provides structured escape hatches:
`jax.lax.cond` for conditionals, `jax.lax.scan` for loops — all of
which embed control flow into the compiled graph rather than relying
on Python's runtime evaluation.

*What to adapt:* JAX's approach to teaching the restricted dialect is
directly transferable — clear error messages naming the restriction,
concrete escape hatches offered in the error itself, and a "sharp
bits" documentation page. The control-flow primitives (`cond`, `scan`)
may inform VisLang's `when()` and parameter-sweep constructs.

**PyTorch 2 / TorchDynamo** hooks into CPython's frame evaluation
API (PEP 523) to intercept and rewrite Python bytecode at runtime.
It symbolically evaluates bytecode to produce FX graph fragments,
handling data-dependent control flow by "graph breaking" — splitting
the computation into graph segments separated by opaque Python code.
This handles arbitrary Python at the cost of producing smaller, less
optimizable graph fragments.

*What to adapt:* The graph-break concept maps to VisLang's `apply()`
escape hatches: the outer pipeline is a clean DAG; `apply()` blocks
are graph breaks that the compiler treats as opaque. TorchDynamo's
guard system (checking whether cached graph fragments remain valid)
parallels VisLang's content-hash-based cache invalidation.

**Dask** builds task graphs from familiar NumPy/Pandas-style API
calls. Operations are lazy, recording tasks in a DAG rather than
executing immediately. A scheduler executes the graph with
optimizations: task fusion (merging sequential tasks), culling
(removing unneeded tasks), and memory-aware scheduling. Dask's
high-level graph preserves semantic structure that can be optimized
before lowering to individual tasks.

*What to adapt:* Dask's two-level graph — high-level operations
preserving semantic structure, lowered to fine-grained tasks — maps
to VisLang's DAG (visualization operations) compiled into an
execution plan (VTK filter calls, cache lookups, sweep jobs). The
graph optimization passes (fusion, culling) are directly relevant.

## 2. Declarative visualization compilation

VisLang's compiler takes a DAG (the spec) and produces an execution
plan. This pattern — compiling a declarative visualization
specification into an optimized execution strategy — has direct
precedents in the information visualization community.

**Vega-Lite → Vega** is a multi-stage visualization compiler.
A concise high-level specification compiles to a lower-level reactive
dataflow program that handles scale resolution, axis generation,
and data transformation planning. The dataflow then compiles to a
scenegraph for rendering. The key insight: compilation stages are
cleanly separated, each with its own intermediate representation.
The high-level spec captures intent; the compiler handles binding;
the runtime handles rendering.

*What to adapt:* The multi-stage pattern (spec → plan → execution) is
exactly VisLang's proposed architecture. Vega-Lite's automatic scale
resolution — inferring shared scales across faceted views — parallels
VisLang's scope-from-DAG inference.

**VegaPlus** (Yang, Joo, Yerramreddy, Moritz, Battle; SIGMOD 2024)
extends Vega to handle large data by splitting execution between a
client-side runtime and a server-side DBMS. Given a Vega dataflow
with N operators, VegaPlus enumerates valid partitioning plans, then
uses learned cost models (pairwise ranking via RankSVM or Random
Forest) to select the best plan. Critically, the optimizer is
interaction-aware: it sums costs across anticipated user interactions
to pick the globally best partition for an entire session, not just
one query.

*What to adapt:* VegaPlus's plan enumeration and cost-model selection
is the closest precedent to VisLang's compiler — except VisLang
partitions across pyramid levels, cache, stats DB, and background
sweeps rather than client vs. server. The interaction-aware
optimization (planning for the session, not the frame) directly
informs VisLang's latency-budget approach. Learned cost models are
worth considering as the compiler matures beyond heuristics.

**Mosaic** (Heer & Moritz; TVCG 2024) is an architecture where
visualization components publish data needs as declarative queries
to a coordinator backed by DuckDB. The coordinator optimizes, caches,
and routes queries, pushing computation down to the database.
Cross-filtering across views works through shared "selections"
(query predicates) that the coordinator propagates to all subscribing
clients.

*What to adapt:* Mosaic's coordinator is the closest architectural
analog to VisLang's compiler — both sit between declarative intent
and execution, both manage caching and query routing, both propagate
shared state across views. Mosaic pushing computation to the most
efficient backend parallels VisLang routing to the stats DB, pyramid,
or feature DB depending on the node type.

**Tableau / VizQL** compiles visual specifications into optimized
database queries with automatic aggregation, filtering, and layout.
The user never writes SQL; the compiler makes all query-planning
decisions. This is the commercial realization of the "spec → compiler
→ plan" pattern, deployed at scale to millions of users, demonstrating
that the model is viable when the compiler's defaults are good enough
for the common case.

## 3. Large-data interactive visualization

VisLang's workspace architecture — precomputed stats, multi-resolution
pyramids, cached features, background sweeps — keeps interaction fast
on TB-scale data. Several systems have tackled this problem for 2D
information visualization; VisLang extends the core ideas to 3D
scientific data.

**Falcon** (Moritz, Howe, Heer; CHI 2019) maintains 50fps brushing
and linking across multiple views of large datasets. When the user
activates a view for brushing, Falcon builds an index of precomputed
aggregations for every possible brush position. On view switch, it
loads reduced-resolution indices first for immediate responsiveness,
then progressively refines.

*What to adapt:* Falcon's "precompute for the active interaction,
progressively refine on switch" maps to VisLang's approach: serve
the current frame from cache/pyramid instantly, progressively improve,
precompute for animation. The progressive refinement pattern is how
VisLang's compiler should handle pyramid-level selection.

**Nanocubes** (Lins, Klosowski, Scheidegger; 2013) precomputes
hierarchical aggregations over space and time for real-time
exploration of billion-record spatiotemporal datasets. The data
structure fits in laptop memory through careful subtree sharing.

*What to adapt:* Hierarchical precomputation of statistics is the
right strategy for interactive exploration of large spatiotemporal
data. VisLang's stats DB (per-field, per-timestep histograms,
percentiles, ranges) is the 3D scientific data analog.

**imMens** (Liu, Jiang, Heer; 2013) decomposes multivariate data
into independently binned projections composited on the GPU during
interactive brushing. **Query-driven visualization** (Rübel, Bethel,
et al.; 2012) uses index structures to extract only the
"scientifically interesting" subset from extreme-scale scientific
data, avoiding full data loads entirely.

*What to adapt:* The decomposition into independently cacheable
projections (imMens) parallels VisLang's decomposition into
independently cacheable DAG subtrees. The query-driven premise —
that the relevant data is always a small fraction of the whole — is
the intellectual foundation for VisLang's workspace design, which
never puts raw TB-scale data in the interactive loop.

## 4. Scientific workflow provenance and caching

VisLang's content-hashed DAG provides identity, diffing, and
caching across sessions. Scientific workflow systems and data
pipeline tools have explored these concerns.

**VisTrails** (Bavoil, Callahan, Scheidegger, et al.) is the most
directly relevant precedent. It represents visualization workflows
as DAGs with an action-based provenance model: rather than storing
multiple versions, it records operations applied to workflows (like
a database transaction log). Any prior state can be reconstructed
by replaying actions. This enables workflow diffing, analogies
(applying transformation patterns across pipelines), and caching
(modules with identical inputs reuse results). The dataflow DAG
executes bottom-up, with modules producing data consumed downstream.

*What to adapt:* VisTrails demonstrates that change-based provenance,
DAG-level diffing, and functional caching work as a coherent system
for visualization. VisLang's content-hash approach provides similar
capabilities with arguably simpler mechanics — identity derived from
content rather than tracked through history. VisTrails' architectural
insight that data and computation are both first-class nodes in the
DAG is also present in VisLang's design.

**ParaView Cinema** pre-renders images and extracts features across
parameter spaces during batch or in-situ processing, storing results
in a database for post-hoc interactive exploration. A Cinema database
contains views rendered at many camera angles, timesteps, and
parameter values.

*What to adapt:* Cinema's feature extraction + database approach maps
directly to VisLang's feature DB + sweep records. The key difference:
Cinema pre-computes everything speculatively up front, while VisLang's
compiler schedules extraction incrementally as the author builds the
pipeline — demand-driven rather than speculative.

**DVC (Data Version Control)** applies content-addressed caching and
DAG-based pipeline management to ML workflows. It hashes data files,
stores them in content-addressed cache, and automatically determines
which pipeline stages need re-running by comparing hashes.

*What to adapt:* DVC's content-addressed caching with DAG-based
invalidation is structurally identical to VisLang's tracked-execution
cache. DVC's `dvc.lock` file — recording hashes of all inputs and
outputs — is the same concept as VisLang's proposed plan lock file.

## 5. Domain-specific scientific visualization

**yt** is a Python toolkit for volumetric astrophysical simulation
data. Several aspects of its architecture are directly relevant:

- *Derived fields.* A three-tier hierarchy: on-disk fields, derived
  fields (declared as Python functions of other fields), and aliases.
  yt traces dependencies back to disk fields, computes the minimal
  set of reads needed, and executes derivations on demand. This
  validates VisLang's `derive()` concept and demonstrates that the
  pattern scales across hundreds of simulation codes.

- *Lazy data access.* Selectors (regions, spheres, rays) are
  lightweight objects; I/O happens only when values are accessed.
  Chunking strategies (spatial, I/O-aligned, monolithic) optimize
  access patterns. This is the same lazy-handle pattern as VisLang's
  dataset handles.

- *Multi-resolution abstraction.* Five data discretization methods
  (grid AMR, octree AMR, SPH, unstructured mesh, particles) behind
  a unified selection interface, with coordinate handlers decoupling
  logical layout from physical coordinates.

- *Correctness-first.* yt prioritizes physical correctness over raw
  speed, aligning with VisLang's commitment to correct global
  statistics even when the interactive display is approximate.

*What to adapt:* yt's derived field dependency resolution informs
VisLang's compiler planning. The lazy selector validates the dataset
handle design. The coordinate handler pattern suggests VisLang's
manifest should capture enough grid metadata for format-agnostic
access.

**ParaView's pipeline** represents workflows as a demand-driven
filter DAG, executing upstream from the display sink. It has no
automatic optimizer — the user configures resolution, LOD, and
parallelism manually. VisLang adds a compiler between DAG and
execution, automating the decisions ParaView leaves to the user.

## 6. LLM-driven visualization

A growing body of work uses LLMs to generate visualizations.
**LIDA** (Microsoft Research) orchestrates summarization, goal
exploration, code generation, and self-repair evaluation.
**PlotGen** uses multimodal feedback (the agent sees the rendered
chart and iterates). **Data-to-Dashboard** automates dashboard
generation with domain detection. These systems demonstrate that
LLM agents can produce reasonable visualization code through
iterative refinement.

*Where VisLang diverges:* None of these systems address data that's
too large to load, consistency across hundreds of timesteps, or
keeping the agent's iteration loop fast on TB-scale data. VisLang's
contribution is the layer beneath the LLM — the workspace, compiler,
and execution infrastructure that makes the generated spec executable
at scale. The LLM-viz work validates that agents can write
visualization code; VisLang addresses what that code runs against.

## Synthesis

No single system combines what VisLang proposes. The novelty is in
the combination:

| Concern | Prior art | VisLang |
|---|---|---|
| DAG from Python | JAX, TorchDynamo, Dask | Custom interpreter for PyVista+NumPy subset |
| Spec → plan compilation | VegaPlus, Vega-Lite, Tableau | Plans against workspace state + latency budget |
| Interactive on large data | Falcon, Nanocubes, Mosaic | Stats DB + pyramid + feature DB + progressive refinement |
| Provenance + caching | VisTrails, DVC | Content-hashed DAG with scope-from-dependencies |
| Derived fields + lazy access | yt | Dataset handles, `derive()`, manifest-resolved properties |
| Pre-extracted features | ParaView Cinema | Feature DB populated by compiler-scheduled sweeps |
| LLM as primary author | LIDA, PlotGen | Execution substrate designed for agent iteration speed |

The closest architectural analog is **Mosaic** — a coordinator
between declarative specs and a database backend, managing query
routing, caching, and cross-view coordination. But Mosaic targets
2D information visualization against tabular data. VisLang targets
3D scientific data at TB scale, with an LLM agent as the primary
author. The pattern is the same; the domain, data model, and user
model are different.

Two database concepts also underpin the design, though the
connection is rarely made explicit in visualization work.
**Materialized views** — precomputed query results stored for fast
access — describe exactly what the workspace's stats DB, feature
DB, and pyramid are. **Incremental view maintenance (IVM)** — the
problem of efficiently updating materialized views when inputs
change — is structurally what the compiler does when replanning
after a spec edit: compare content hashes, reuse valid subtrees,
recompute only what changed. Systems like Materialize and Enzyme
demonstrate IVM at scale, giving VisLang a theoretical foundation
for its caching and replanning strategy.

The most actionable lessons from this survey:

1. **JAX's dialect documentation and error messages** are the model
   for teaching agents and humans the restricted-Python pipeline
   dialect.
2. **VegaPlus's interaction-aware plan optimization** is the model
   for VisLang's compiler — plan for the session, not the frame.
3. **Falcon's progressive refinement** is the model for pyramid-level
   selection — coarse first, refine progressively, precompute for
   the anticipated interaction (animation = all timesteps).
4. **yt's derived field resolution** is the model for VisLang's
   `derive()` and workspace-level field management.
5. **DVC's lock file and content-addressed caching** are the model
   for VisLang's plan lock file and incremental re-execution.
6. **Mosaic's coordinator pattern** is the closest overall
   architectural precedent and the one most worth studying in detail.
