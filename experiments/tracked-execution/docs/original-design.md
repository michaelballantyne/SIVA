# Tracked Execution: Secure, Reconciled Scientific Visualization

> **Historical note.** This is the original design prompt used to kick off
> the tracked-execution work — it is *not* a description of the current
> implementation. The actual prototype diverged in one major way: it runs
> pipelines under CPython's `exec` with a restricted namespace rather than
> the Rust-based Monty interpreter described below. The Monty path remains
> future work (see `monty-analysis.md`). The whitelist/blacklist reasoning,
> hash-consing model, DAG garbage collection, scene reconciler, and file
> watcher described here are still load-bearing — refer to `architecture.md`
> for what actually exists, and to the "Open Questions" section at the end
> of this file for the original risk list (several of which — e.g. VTK
> filter purity — have since been investigated in `purity-analysis.md`).

A design for a library that lets an AI agent (or human) write PyVista code
in a secure sandbox, with automatic caching and incremental reconciliation
across re-executions. The pipeline file is re-run on every edit; the library
ensures only the parts that changed actually re-execute.

---

## Problem

Interactive scientific visualization with an AI agent involves a tight loop:

1. Agent writes a pipeline script (PyVista code)
2. Script executes, producing a rendered scene
3. Human observes the result
4. Agent modifies the script
5. Repeat

Naive re-execution rebuilds everything from scratch on every edit — re-reads
files, re-runs filters, re-creates actors. For large datasets this is slow
and wasteful. Most edits change one parameter (a threshold value, a colormap,
an opacity); the upstream data and filters are identical.

Separately, the agent's code runs with full Python access. A restricted
namespace helps but is fundamentally [breakable in CPython][sandbox-escape].
We want real security without the overhead of a full container.

[sandbox-escape]: https://book.hacktricks.xyz/generic-methodologies-and-resources/python/bypass-python-sandboxes

## Core Idea

Run pipeline scripts inside [Pydantic Monty][monty] — a minimal Python
interpreter written in Rust, designed for safe execution of AI-generated code.
Monty provides Python-like syntax (classes, methods, f-strings, operators) but
executes in a completely sandboxed runtime with no default access to the host.

The only way Monty code interacts with the outside world is through
**external functions** we provide. We use this mechanism to:

1. **Whitelist** — only allowed operations can execute
2. **Track** — every operation is recorded in a dependency DAG
3. **Cache** — results are keyed by content hash; unchanged subgraphs
   skip re-execution on subsequent runs

One generic interception point handles all three concerns.

[monty]: https://github.com/pydantic/monty

## Architecture

```
┌─────────────────────────────────────────────┐
│  Pipeline file (PyVista-syntax Python)       │
│                                              │
│  mesh = pv.read("fire.vts")                  │
│  fire = mesh.threshold(value=500,            │
│              scalars="Temperature")           │
│  p.add_mesh(fire, colormap="inferno")        │
│  p.show()                                    │
└──────────────────┬──────────────────────────┘
                   │ executed in
                   ▼
┌─────────────────────────────────────────────┐
│  Monty interpreter (Rust)                    │
│                                              │
│  - Sandboxed: no fs, no network, no imports  │
│  - External objects returned as opaque refs   │
│  - Method calls on external objects → host    │
└──────────────────┬──────────────────────────┘
                   │ dispatches to
                   ▼
┌─────────────────────────────────────────────┐
│  Host dispatch layer (Python)                │
│                                              │
│  For every method call on an external object: │
│  1. Check whitelist                           │
│  2. Compute content hash                      │
│  3. Check cache → return cached if hit        │
│  4. Execute real PyVista/numpy operation       │
│  5. Cache result, record in DAG               │
│  6. Return wrapped result to Monty             │
└──────────────────┬──────────────────────────┘
                   │ results feed
                   ▼
┌─────────────────────────────────────────────┐
│  Renderer (PyVista Plotter)                  │
│                                              │
│  Persistent process. Receives show() calls    │
│  from the dispatch layer. Clears and rebuilds │
│  only the actors that changed.                │
└─────────────────────────────────────────────┘
```

## Security: Whitelisted Dispatch

Monty's external object mechanism pauses the VM on method calls and delegates
to host Python. We intercept every call with a whitelist check.

### Whitelist generation

Auto-generate from PyVista's and numpy's public APIs, then subtract a small
blacklist of dangerous operations:

```python
import inspect
import pyvista as pv
import numpy as np

def generate_whitelist(*classes):
    allowed = set()
    for cls in classes:
        for name, _ in inspect.getmembers(cls):
            if not name.startswith('_'):
                allowed.add((cls, name))
        # Safe dunders
        for d in ('__getitem__', '__len__', '__iter__',
                  '__add__', '__sub__', '__mul__', '__truediv__',
                  '__gt__', '__lt__', '__ge__', '__le__', '__eq__',
                  '__neg__', '__abs__'):
            allowed.add((cls, d))
    return allowed

WHITELIST = generate_whitelist(
    pv.DataSet, pv.StructuredGrid, pv.UnstructuredGrid,
    pv.ImageData, pv.PolyData, pv.MultiBlock,
    np.ndarray,
)

# Small, obvious blacklist: filesystem and network access
BLACKLIST = {
    (pv.DataSet, "save"), (pv.DataSet, "write"),
    (pv.DataSet, "export"),
    # __setitem__ — prevent in-place mutation of cached data
    (np.ndarray, "__setitem__"),
    (np.ndarray, "__iadd__"), (np.ndarray, "__isub__"),
    (np.ndarray, "__imul__"), (np.ndarray, "__itruediv__"),
}
WHITELIST -= BLACKLIST
```

External entry-point functions (`pv.read`, `pv.Plotter`, `np.percentile`,
etc.) are registered explicitly — these are the roots the code can call.

### Security properties

- **No CPython introspection.** Monty is a Rust interpreter, not CPython.
  `__subclasses__`, `__globals__`, MRO traversal — none of these exist.
  The classic sandbox escapes are impossible by construction.
- **Capability-based.** Default is zero access. Each callable is explicitly
  provided. The whitelist is the complete set of reachable operations.
- **Blacklist is small.** PyVista's dangerous methods are filesystem I/O.
  Numpy has essentially none. Everything else is pure computation on
  in-memory data.
- **In-place mutation blocked.** `__setitem__` and `__iadd__` etc. are
  blacklisted on arrays, preserving the purity assumption needed for caching.

## Reconciliation: Content-Addressed Caching

### Hash consing

Every operation produces a content hash derived from its inputs, not from
the data it produces. Identity flows from the source through the DAG:

```
H_read      = hash("read", "fire.vts", file_mtime)
H_threshold = hash("threshold", H_read, "Temperature", 500)
H_show      = hash("show", H_threshold, "inferno", 1.0)
```

Changing `value=500` to `value=600` produces a new `H_threshold`. Everything
upstream (`H_read`) keeps the same hash and is served from cache. Everything
downstream gets a new hash and re-executes.

### The dispatch loop

```python
class TrackedProxy:
    """Wraps a real object with its content hash."""
    __slots__ = ('_real', '_hash', '_dag')

    def __init__(self, real_obj, content_hash, dag):
        self._real = real_obj
        self._hash = content_hash
        self._dag = dag

def dispatch(proxy, method_name, args, kwargs):
    real_obj = proxy._real

    # 1. Security
    for cls in type(real_obj).__mro__:
        if (cls, method_name) in WHITELIST:
            break
    else:
        raise AttributeError(f"{type(real_obj).__name__}.{method_name} blocked")

    # 2. Content hash
    def arg_hash(a):
        if isinstance(a, TrackedProxy):
            return a._hash
        return stable_hash(a)  # scalars, strings, tuples

    op_hash = stable_hash((
        type(real_obj).__qualname__,
        proxy._hash,
        method_name,
        tuple(arg_hash(a) for a in args),
        tuple((k, arg_hash(v)) for k, v in sorted(kwargs.items())),
    ))

    # 3. Cache check
    dag = proxy._dag
    if op_hash in dag.cache:
        dag.current_run.add(op_hash)
        return TrackedProxy(dag.cache[op_hash], op_hash, dag)

    # 4. Execute
    real_args = [a._real if isinstance(a, TrackedProxy) else a for a in args]
    real_kwargs = {k: v._real if isinstance(v, TrackedProxy) else v
                   for k, v in kwargs.items()}
    result = getattr(real_obj, method_name)(*real_args, **real_kwargs)

    # 5. Cache and record
    dag.cache[op_hash] = result
    dag.current_run.add(op_hash)

    return TrackedProxy(result, op_hash, dag)
```

### Scalar escape

When a tracked operation returns a Python scalar (e.g. `arr.mean()` returns
a float), it leaves the proxy world. This is fine:

- Scalars are cheap to recompute.
- When used as arguments to downstream operations, their literal values
  are inlined into the content hash. If the scalar changes (because
  upstream data changed), the downstream hash changes, triggering
  re-execution.
- No `ScalarProxy` wrapper is needed.

### Garbage collection

After each execution, the cache retains everything computed during that run
and discards everything else:

```python
class DAG:
    def __init__(self):
        self.cache = {}       # content_hash -> real object
        self.current_run = set()

    def begin_run(self):
        self.current_run = set()

    def end_run(self):
        # Keep only what this run touched
        stale = set(self.cache.keys()) - self.current_run
        for h in stale:
            del self.cache[h]
```

Why this is correct:

- Each execution is a complete run of the pipeline file. It produces
  exactly one set of operations. That set is the full extent of what
  the current pipeline needs.
- There is no accumulation across runs. Run N+1's `current_run` set
  replaces run N's. Hashes present in both are cache hits (skip
  execution); the rest are evicted.
- Dead code (computed but unused by `show()`) is retained for one run,
  then evicted if not repeated. This is negligible overhead.

### What gets cached

The cache holds real Python/VTK objects in memory:

- **Readers** — `pv.read()` results. These are the most valuable cache
  entries (large files, slow I/O).
- **Filter outputs** — `threshold()`, `clip()`, `contour()` results.
  These are VTK datasets that can be large but are expensive to recompute.
- **Numpy arrays** — intermediate computations. Usually cheap to recompute
  but free to cache if already in memory.
- **Scalars** — fall out of the proxy system, recomputed each run. Cheap.

Memory is bounded by "one pipeline's worth of live data." The GC ensures
no growth across runs.

### Source identity

File readers need special handling. The content hash for `pv.read(path)`
should include the file's modification time (or content hash) so that
changes to the underlying data file invalidate the cache:

```python
def tracked_read(path, dag):
    mtime = os.path.getmtime(path)
    op_hash = stable_hash(("read", path, mtime))
    if op_hash in dag.cache:
        dag.current_run.add(op_hash)
        return TrackedProxy(dag.cache[op_hash], op_hash, dag)
    result = pv.read(path)
    dag.cache[op_hash] = result
    dag.current_run.add(op_hash)
    return TrackedProxy(result, op_hash, dag)
```

## Scene Reconciliation

The dispatch layer tracks which `show()` / `add_mesh()` calls were made
and their content hashes. After execution completes:

1. Compare the new set of `(name, mesh_hash, display_params_hash)` tuples
   against the previous set.
2. For actors whose hash is unchanged: do nothing.
3. For actors whose mesh is the same but display params changed: update
   actor properties in place (colormap, opacity, visibility).
4. For new actors: add to renderer.
5. For removed actors: remove from renderer.
6. Call `plotter.render()` once.

This avoids the current pattern of `plotter.clear()` + rebuild everything.

## Putting It Together: The Execution Loop

```python
def execute_pipeline(file_path, dag, plotter):
    """Re-execute a pipeline file with caching and reconciliation."""
    code = open(file_path).read()

    # Provide entry points as Monty external functions
    externals = {
        "read": lambda path: tracked_read(path, dag),
        "Plotter": lambda: TrackedProxy(plotter, stable_hash("plotter"), dag),
        "np_percentile": lambda arr, q: tracked_call(np.percentile, arr, q, dag),
        # ... other numpy/utility entry points
    }

    dag.begin_run()

    # Execute in Monty with tracked dispatch
    monty_exec(code, externals=externals, method_handler=dispatch)

    dag.end_run()    # GC stale cache entries
    reconcile(dag)   # diff and update the plotter
    plotter.render()
```

## File Watching

A file watcher triggers re-execution on save:

```python
from watchdog.observers import Observer
from watchdog.events import FileModifiedHandler

def watch_and_reload(file_path, dag, plotter):
    def on_modified(event):
        if event.src_path == file_path:
            execute_pipeline(file_path, dag, plotter)

    handler = FileModifiedHandler()
    handler.on_modified = on_modified
    observer = Observer()
    observer.schedule(handler, path=os.path.dirname(file_path))
    observer.start()
```

The human (or AI agent) edits the file. The watcher re-executes. The
cache ensures only changed operations run. The reconciler updates only
changed actors. The render window updates in place.

## Minimal Testbed

The simplest way to validate this design — no MCP server, no interactive
window, no Trame. Just:

1. A pipeline file that reads data and produces a visualization.
2. Tracked execution with caching.
3. Render to an image file (PyVista offscreen).
4. Modify the pipeline file.
5. Re-execute — verify cache hits and that only changed nodes re-execute.
6. Render again — verify the image reflects the change.

```python
# test_pipeline.py — a simple pipeline to iterate on
mesh = read("datasets/fire.vts")
temp = mesh.threshold(value=500, scalars="Temperature")
add_mesh(temp, colormap="inferno", opacity=0.8)
screenshot("output.png")
```

Change `value=500` to `value=600`. Re-execute. The reader is cached,
the threshold re-executes, the image updates. That's the core loop.

## Open Questions

- **Monty maturity.** Monty is v0.0.3 (Feb 2026). Does it support enough
  Python syntax for natural PyVista code? Operator overloading on external
  objects? f-strings with method calls? List comprehensions over tracked
  arrays? Needs empirical testing.

- **Operator dispatch.** `arr > 500` needs `__gt__` on external objects to
  route through the tracked dispatch. Does Monty's method_handler cover
  operators, or only named method calls?

- **numpy coverage.** Which numpy functions does the agent actually use?
  Start with a small set (`percentile`, `histogram`, `sqrt`, `abs`, `sum`,
  `mean`, `std`, `min`, `max`, `where`) and expand based on real usage.

- **VTK filter purity.** The caching assumes same inputs produce same
  outputs. Are any VTK filters nondeterministic? (Unlikely for the common
  ones, but worth auditing.)

- **Large dataset memory.** For very large datasets, even one pipeline's
  worth of cached filter outputs may be significant. May need an option
  to mark certain nodes as "don't cache, always recompute" — but this is
  an optimization, not a design change.

- **Fallback to CPython.** For ad-hoc data inspection (not the pipeline
  file), it may be useful to have a second execution path: restricted-
  namespace `exec()` in CPython with read-only access to cached data.
  Not safe against a determined attacker, but sufficient for preventing
  accidental scene mutation by the agent. The container/sandbox provides
  the real security boundary here.

## Summary

| Concern | Mechanism |
|---|---|
| Security | Monty (Rust interpreter) + whitelisted dispatch |
| Caching | Content-addressed hashes (hash consing) |
| Reconciliation | DAG diff between runs; minimal scene updates |
| GC | Retain everything from last execution, evict the rest |
| Syntax | PyVista-compatible via generic proxied dispatch |
| File watching | Watchdog triggers re-execution on save |
