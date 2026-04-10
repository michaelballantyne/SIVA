# Design Reflection: Tracked Execution Experiment — 2026-04-10

## What we built

A standalone library (`experiments/tracked-execution/`) that provides
content-addressed caching for PyVista visualization pipelines. The agent
writes normal PyVista code; the library wraps objects in tracked proxies
that hash operations by their inputs and cache results. On re-execution,
only changed operations re-run.

**Final state:**
- 9 library modules, ~1840 lines
- 204 tests + 2 xfailed (known VTK purity hazards)
- 4 core examples + 4 vtk_escape examples
- 5 benchmarks with harness
- Purity analysis documenting 3 hazards and 7 safe behaviors
- VTK escape pattern for raw VTK within tracked pipelines
- Agent-facing guide (AGENT-GUIDE.md)
- README with architecture overview

**Benchmark highlights:**
- Visual parameter changes (colormap, opacity): 3000-4000x speedup
- Pipeline refinement (Gamma-style edits): 12x-3257x depending on edit
- Numpy queries on cached data: 98-2247x
- Parameter sweeps: ~1x for heavy filters (read cached, but filter dominates)

## What this means for VisLang

### The MCP layer is probably not the right architecture

The original VisLang has 45 MCP tools, a custom DSL, threading for the
render window, and extensive query infrastructure. The tracked execution
experiment achieves most of the same value with:

- **One execution primitive** (`execute_pipeline`) instead of 45 tools
- **PyVista syntax** instead of a custom DSL
- **Content-addressed caching** instead of tear-down-and-rebuild
- **`inspect_pipeline`** instead of separate query tools
- **`vtk_escape`** for anything not in PyVista's API

The MCP tools were solutions to problems that disappear when the agent
can just write Python. `describe_data` → write a script. `suggest_isosurface`
→ compute percentiles. `get_statistics` → `arr.mean()`. The restricted
namespace keeps the agent safe without limiting its expressiveness.

### What VisLang should become

Based on this experiment, VisLang's future architecture might be:

```
Agent writes PyVista code
         │
         ▼
┌─────────────────────┐
│  Tracked Execution   │
│  (proxy + cache)     │
│                      │
│  execute_pipeline()  │
│  inspect_pipeline()  │
│  vtk_escape()        │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│  Renderer            │
│  (Trame or native)   │
│                      │
│  SceneReconciler     │
│  File watcher        │
└─────────────────────┘
```

**Keep from current VisLang:**
- The conversational workflow (agent explores data, builds visualization iteratively)
- Server instructions / system prompt guiding the agent
- Session management (working directories, data files)
- The insight that query and mutation should be separate concerns

**Drop:**
- Most MCP tools (replace with `execute_pipeline` + `inspect_pipeline`)
- Custom DSL (replace with PyVista syntax)
- `set_pipeline` / mutation tools dichotomy (just re-execute the file)
- Manual query tool implementations (agent writes its own queries)

**Add:**
- Content-addressed caching (the core contribution of this experiment)
- `vtk_escape` for extending beyond PyVista
- Agent-friendly error messages
- Purity warnings (missing `scalars=`)

**Maybe keep as convenience MCP tools:**
- `screenshot()` — agent needs to see the render
- `describe_data()` — saves 3-4 round trips on first exploration
- `execute` / `inspect` — thin MCP wrappers around execute_pipeline/inspect_pipeline

### The security story

Three layers, each appropriate to its use case:

1. **Restricted namespace** (current) — prevents accidental harm. Good
   enough for "Claude on my laptop." No `import`, no `open()`, no
   filesystem access from pipeline code.

2. **Pydantic Monty** (future) — real sandbox via Rust interpreter.
   Currently can't handle arbitrary Python objects (only dataclasses).
   Our analysis shows it's ~1-2 weeks of Rust work to add opaque object
   support. When that ships, swap `exec()` for Monty and get real
   security without changing any user-facing code.

3. **Container/sandbox** (deployment) — for multi-user or untrusted
   scenarios. Claude Code's sandbox, E2B, Docker+gVisor. The library
   works in any of these; Trame viewer makes container deployment clean
   (just a port, no display forwarding).

### The reconciliation contribution

Content-addressed pipeline reconciliation is genuinely novel in the Python
scientific visualization space. React-three-fiber does tree diffing for
3D scenes, and Reactive Vega does dataflow-based updates for 2D charts,
but nobody does content-addressed caching of VTK filter pipelines.

This could be valuable beyond VisLang — as a standalone library for anyone
doing iterative PyVista work (Jupyter notebooks, teaching, interactive
exploration).

## Known limitations to address

1. **`set_active_scalars` hidden state** — the most serious correctness
   issue. We emit warnings but don't prevent wrong cache hits. Fix:
   include `active_scalars_name` in the hash for scalar-sensitive methods.

2. **VTK passthrough in all-pass case** — when all points pass a threshold,
   VTK shares the source buffer. Source mutation corrupts cached result.
   Fix: defensive copy, or document as limitation.

3. **vtk_escape functions can't use imports inside pipeline strings** —
   the restricted namespace blocks `import`. Functions needing VTK imports
   must be defined outside the pipeline. This is a usability gap.

4. **No rendering integration tested** — the SceneReconciler works in
   diff-only mode (plotter=None). We haven't validated it with a real
   PyVista Plotter + Trame. This is the next concrete engineering task.

5. **GC evicts on pipeline switch** — alternating between two pipelines
   evicts each other's cached results. The benchmark shows ~1x for "hard"
   A/B switches. A multi-pipeline cache (keyed by pipeline identity) could
   fix this but adds complexity.

## Broader next steps

### Short term (next session)
- Validate SceneReconciler with a real PyVista Plotter (offscreen first,
  then Trame)
- Fix the `active_scalars_name` hashing issue
- Try the library with the wildfire dataset end-to-end
- Prototype a minimal MCP server (3-4 tools) wrapping execute_pipeline

### Medium term
- Package as a standalone library (separate from VisLang)
- File a feature request on pydantic/monty for opaque object support
- Build the file-watching hot-reload loop with Trame viewer
- Explore whether this approach works for Jupyter (cell-level caching)

### Long term
- Port to Monty when opaque objects are supported
- Investigate the LSP idea from VISION.md — the tracked DAG provides
  exactly the information an LSP would need (what nodes exist, what
  fields are available, what each operation does to the data)
- Explore the hash-consing approach for other domains beyond visualization
  (any pure-ish computation pipeline could benefit)

## What I learned about the design process

Starting from "is VisLang on the wrong track?" and letting that question
drive a concrete prototype was more productive than incrementally improving
the existing system. The prototype revealed that:

1. The valuable parts of VisLang (iterative exploration, data-aware tooling,
   safe execution) are separable from the parts that may be unnecessary
   (MCP tool proliferation, custom DSL, threading complexity).

2. Benchmarking realistic edit patterns (not microbenchmarks) showed where
   caching helps (visual params: 3000x) and where it doesn't (heavy filter
   changes: ~1x). This grounds future design decisions in data.

3. The purity analysis surfaced real correctness issues that would have
   been invisible in the current VisLang (which rebuilds from scratch
   every time). Caching forces you to think about what's actually pure.

4. The agent-guide exercise (writing docs for Claude as the user) clarified
   what the API surface should look like. Designing for an AI agent is
   different from designing for a human — the agent needs explicit error
   messages, consistent patterns, and a small API it can hold in context.
