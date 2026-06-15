# Tracked Execution: An Adapted Vision for SIVA

This document re-presents the SIVA concept adapted to what we learned
from the tracked execution experiment. It describes what exists, what
it means, and where it could go.

---

## What Changed

The original SIVA was an MCP server with 45 tools, a custom DSL, and
a VTK renderer in the same process. The tracked execution experiment
started from the question: "Claude can just one-shot write PyVista —
is the custom DSL unnecessary?"

The answer: **mostly yes, but the iteration loop matters.**

Claude writes good PyVista. The MCP tools for querying data
(`describe_data`, `suggest_isosurface`, `get_statistics`) are things
Claude can just compute by writing scripts. The custom DSL adds a
learning curve without clear benefit when Claude already knows PyVista.

What the original SIVA got right was the **tight iteration loop**:
write code → see result → refine → repeat. The tracked execution
approach preserves this loop but simplifies everything else.

## What Exists Now

### The core idea: content-addressed caching

Pipeline scripts are re-executed on every edit. A proxy layer intercepts
all PyVista operations and caches results by content hash:

```
hash("threshold", hash(mesh), "Temperature", 500) → cached VTK dataset
```

Same operation + same inputs = cache hit. Change one parameter and only
the operations downstream of it re-execute. Upstream data stays cached.

**Benchmark results:**
- Colormap/opacity changes: **3000-4000x** faster (mesh fully cached)
- Pipeline refinement: **12-3000x** depending on what changed
- File reading: cached by filename + mtime (never re-reads unchanged files)

### The MCP server

7 tools — minimal and focused:

| Tool | Purpose |
|------|---------|
| `set_working_directory` | Point at your data |
| `create_view` | Start watching a pipeline file; returns data description |
| `inspect` | Query cached data without modifying the pipeline |
| `screenshot` | Capture the current render |
| `list_views` | See all active views and their watcher/error status |
| `close_view` | Clean up |

The agent writes pipeline files directly (visible in the human's editor),
the server watches for changes and re-executes with caching.

### Pipeline files

Standard PyVista syntax in a restricted namespace:

```python
mesh = read("output.30000.vts")
fire = mesh.threshold(value=400, scalars="theta")
surface = fire.extract_surface()
show(surface, colormap="inferno")
```

Available: `read`, `show`, `np` (numpy), `vtk_escape`, `pv`, `print`.
Not available: `import`, `open`, filesystem access, in-place mutation.

### The `vtk_escape` hatch

For anything PyVista doesn't expose, write a pure function:

```python
def smooth_sinc(m):
    import vtk
    f = vtk.vtkWindowedSincPolyDataFilter()
    f.SetInputData(m)
    f.SetNumberOfIterations(20)
    f.Update()
    return pv.wrap(f.GetOutput())

smoothed = vtk_escape(surface, smooth_sinc)
```

Cached by function source hash + input hash. Same function on same input
= cache hit.

### Threading and interactive windows

VTK's OpenGL is not thread-safe. In interactive mode:
- The main thread runs the VTK event loop (mouse rotation, zoom)
- MCP tool calls and watcher callbacks run on background threads
- All VTK operations are marshaled to the main thread via a queue

In offscreen mode, everything runs directly (no event loop needed).

## What This Means for SIVA

### What to keep from original SIVA
- **The conversational workflow** — agent explores data, builds visualization
  iteratively through conversation
- **Multiple views** — different aspects of the data in separate windows
- **Server instructions** — system prompt guiding the agent's approach
- **Interactive VTK windows** — human sees live 3D, rotates, zooms

### What to drop
- **45 MCP tools → 7** — the agent writes Python instead of calling tools
- **Custom DSL** — PyVista is the DSL. Claude already knows it.
- **Query tools** — `describe_data`, `get_statistics`, `suggest_isosurface` →
  the agent writes a 3-line `inspect` snippet instead
- **Mutation tools** — `set_colormap`, `set_opacity` → edit the file
- **`set_pipeline` / pipeline version history** — the file system IS the
  version history (git, undo in editor)

### What's new
- **Content-addressed caching** — the core innovation. Makes iteration fast
  without a reconciler that understands VTK pipeline semantics.
- **`inspect`** — lightweight ad-hoc queries on cached data, separate from
  the pipeline file (which stays focused on visualization)
- **`vtk_escape`** — escape hatch for the 20% of VTK that PyVista doesn't
  cover. Participates in caching.
- **File watching** — the pipeline file is the shared artifact. Both human
  and agent edit it; the server re-executes on every save.

## Where It Could Go

### Near term: production-quality MCP server

The current prototype validates the architecture. Making it production-
quality means:

- **Trame integration** — browser-based rendering alongside native VTK.
  The tracked execution layer doesn't care which renderer it uses.
- **Shared read cache eviction** — currently the shared cache grows without
  bound. Add LRU or reference-counted eviction.
- **Reconciler property updates** — currently the reconciler removes and
  re-adds changed actors. In-place property updates (change a colormap
  without recreating the mapper) would reduce flicker in interactive windows.
- **VTK progress callbacks** — for long operations, report progress so the
  agent knows something is happening.

### Medium term: the pipeline file as a live document

The pipeline file is a Python script that describes desired visualization
state. This is the foundation for richer tooling:

**Language server (LSP):**
The tracked DAG knows what arrays exist, what their ranges are, what
filters produce. An LSP could provide:
- Autocomplete for field names from loaded data
- Hover info: "this threshold selects 42,000 of 18M points"
- Inline diagnostics: "field 'Temperture' not found, did you mean
  'Temperature'?"
- The same intelligence that MCP query tools provided, but through the
  editor, for the human

**Bidirectional editing:**
The pipeline file describes state declaratively. VTK interactions
(rotate camera, move clip plane) could flow back into the code:
- Rotate in the VTK window → `camera(...)` line updates
- Drag a clip plane → `clip(origin=..., normal=...)` rewrites itself
- The code is always true, whether the human last typed, dragged, or
  asked the AI

**Node info view (Lean-style):**
Click on any line in the pipeline file and see:
- Data shape at that point (points, cells, arrays)
- Mini histogram of the scalar field
- Isolated render showing only that layer's visual contribution

### Longer term: the pattern beyond visualization

Content-addressed caching of a proxied computation DAG is not specific
to visualization. The same approach works for any pipeline of pure-ish
transformations:

- **Data processing** — pandas/polars pipelines where the agent iterates
  on data cleaning steps
- **Image processing** — PIL/OpenCV pipelines with parameter sweeps
- **Simulation post-processing** — extract, filter, derive, aggregate
- **Machine learning** — feature engineering pipelines where changing one
  feature re-runs only downstream steps

The tracked execution library (`TrackedProxy`, `DAG`, `dispatch`,
`whitelist`) is generic. The MCP server and PyVista-specific whitelist
are the visualization-specific parts.

### The security trajectory

Three layers, each appropriate to its deployment:

1. **Restricted namespace** (now) — prevents accidental harm. Claude
   can't `import os` or `open()` files. Sufficient for local use.

2. **Pydantic Monty** (when it supports opaque objects) — real sandbox
   via Rust interpreter. Same PyVista syntax, but impossible to escape.
   Our analysis shows ~1-2 weeks of Rust work to add opaque object
   support to Monty. The tracked execution layer is designed to swap
   out the execution backend without changing the user-facing API.

3. **Container** (for multi-user deployment) — E2B, Docker+gVisor, or
   Claude Code's sandbox. The Trame viewer makes containerization clean
   (just a TCP port, no display forwarding).

## The Bigger Picture

SIVA started as "a custom language for AI-driven visualization."
The tracked execution experiment suggests it should become:

**A smart runtime that makes PyVista fast to iterate on.**

The value isn't in a new language or a gallery of tools. It's in:
1. The **caching layer** that makes re-execution instant
2. The **file-watching loop** that keeps human and AI synchronized
3. The **restricted execution** that keeps it safe
4. The **inspect mechanism** that separates exploration from visualization

PyVista is the syntax. VTK is the engine. The tracked execution layer
is the intelligence in between — making the iteration loop as fast as
the human's (or agent's) ability to think of what to try next.
