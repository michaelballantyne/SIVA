# Tracked Execution Exploration — 2026-04-10

## What happened

Started from a conversation about whether VisLang's MCP architecture was
overengineered given that Claude can one-shot write PyVista code and wrap it
in Trame. This led to a deep design exploration and prototype implementation
of a fundamentally different approach.

## Key insights from the conversation

1. **Claude writes good PyVista out of the box.** The MCP tools, custom DSL,
   and query infrastructure may not be carrying their weight if Claude can
   just write and run scripts directly.

2. **The real pain point is iteration.** When Claude writes PyVista+Trame,
   each change requires killing and restarting the server process, then
   reloading the browser. VisLang's architecture solves this with a persistent
   renderer that accepts changes. But the solution doesn't need to be MCP.

3. **Hot reload is the core feature, not MCP.** A file watcher that re-executes
   pipeline code on save gives the same fast iteration loop without the 45-tool
   MCP layer.

4. **Hash consing solves reconciliation generically.** Content-addressed hashing
   of operations (not data) provides: (a) automatic caching of unchanged
   subgraphs, (b) incremental scene updates, (c) garbage collection of stale
   entries. This works with any surface syntax.

5. **Pydantic Monty is promising but not ready.** Can't handle arbitrary Python
   objects (only dataclasses + primitives). The internal architecture supports
   adding opaque objects — the dataclass method dispatch is the blueprint. ~1-2
   weeks of Rust work. Worth filing a feature request.

6. **Security layers for different concerns.** Restricted exec namespace prevents
   Claude from accidentally doing harm (sufficient for local use). Monty/containers
   prevent determined attackers (needed for multi-user). The two aren't either/or.

## What was built

### tracked-execution library (experiments/tracked-execution/)

Complete implementation of content-addressed caching for PyVista pipelines:

- **TrackedProxy** — wraps any Python object with a content hash
- **DAG** — cache store with per-run tracking and GC
- **dispatch()** — generic interception: whitelist + hash + cache + execute
- **stable_hash()** — deterministic SHA-256 for operations
- **execute_pipeline()** — restricted exec with tracked entry points
- **inspect_exec()** — one-off data inspection against cached state
- **SceneReconciler** — diff old vs new actor sets for minimal updates
- **Session** — encapsulates DAG + Plotter + reconciler + watcher
- **File watcher** — watchdog-based hot reload with debouncing
- **98 tests**, all passing in under 1 second
- **4 working demos** showing caching, inspection, iterative refinement, GC

### Key results from demos

- **Cached re-execution is instant** (0.000s vs 0.3s+ for 1M point datasets)
- **Read caching works** — file not re-read when only threshold changes
- **Colormap/opacity changes are free** — entire upstream is cached
- **GC correctly evicts stale entries** while preserving shared upstream
- **inspect_exec works against live cached state** — no re-reading data

## Open questions

- Is the reconciler actually faster than clear+rebuild for real VTK scenes?
  The dispatch overhead might eat the savings for small pipelines. Need
  benchmarks on real datasets (wildfire VTS, CT scans).

- How does the proxy overhead affect very tight numpy loops? For a pipeline
  that's mostly numpy math (derived fields, statistics), every operation
  goes through dispatch. Profile this.

- The "PyVista syntax as DSL" approach means we can't easily add domain-
  specific forms (like VisLang's `show()` with `scalar_bar=` or ground
  detection). Is that a loss, or does Claude just write the extra lines?

## Relationship to VisLang

This exploration suggests VisLang's future might be:
- **Keep**: hot reload, restricted execution, incremental updates, data-aware tooling
- **Drop**: most MCP tools (Claude can script its own queries), custom DSL syntax
- **Add**: content-addressed caching, Monty security (when ready), Trame viewer
- **Evolve**: the "DSL" becomes PyVista itself, run through a tracked dispatch layer

The core value proposition shifts from "a custom language for visualization"
to "a smart runtime that makes PyVista fast to iterate on."
