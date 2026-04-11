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
