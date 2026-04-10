# tracked-execution

A content-addressed caching layer for PyVista visualization pipelines. Write
pipeline scripts as plain Python; the library tracks every operation, hashes its
inputs, and caches the result. Re-running the same script after changing one
parameter recomputes only the downstream operations — upstream filter results
that haven't changed are returned from cache instantly.

---

## Key idea: hash consing for PyVista meshes

Every PyVista method call on a tracked object produces a **content hash**:

```
op_hash = sha256(type(obj), obj_hash, method_name, args_hash, kwargs_hash)
```

The DAG stores `op_hash → result_object`. On the next run, if the op hash is
already in the cache, the stored object is returned immediately. The hash is
computed from the data, not from object identity, so it survives across runs and
is stable when the pipeline script is re-executed with identical parameters.

---

## Quick start

```python
from tracked_execution import DAG, execute_pipeline

dag = DAG()

code = """
mesh = read("data.vts")
surface = mesh.threshold(500, scalars="Temperature").extract_surface()
show(surface, colormap="hot")
"""

result = execute_pipeline(code, dag)
print(result.stats)  # {'hits': 0, 'misses': 2, 'evictions': 0}

# Run again — same parameters, everything cached
result2 = execute_pipeline(code, dag)
print(result2.stats)  # {'hits': 2, 'misses': 0, 'evictions': 0}
```

---

## Architecture

```
TrackedProxy          proxy.py
    |
    v
dispatch()            dispatch.py   ← whitelist check, hash, cache lookup
    |
    +-- cache hit  → return TrackedProxy(cached_result)
    |
    +-- cache miss → execute real method → cache → return TrackedProxy(result)
    |
    v
DAG                   core.py       ← content_hash → object, GC per run
```

**`TrackedProxy`** (`proxy.py`) wraps any Python/VTK/numpy object. Every
attribute access and method call goes through `dispatch()`.

**`dispatch()`** (`dispatch.py`) is the interception point. It checks the method
against the whitelist, computes a content hash combining the object hash, method
name, and argument hashes, then returns a cached or freshly computed result
wrapped in a new `TrackedProxy`.

**`DAG`** (`core.py`) is a dict mapping content hashes to live objects, with
per-run garbage collection. Call `begin_run()` before each pipeline execution and
`end_run()` after; entries not touched during the run are evicted.

**`stable_hash()`** (`dispatch.py`) produces deterministic SHA-256 hashes for
scalars, tuples, dicts, numpy arrays, and `TrackedProxy` instances. It is used
throughout the library as the single hashing primitive.

**`WHITELIST`** (`whitelist.py`) is a manually curated set of `(class, method)`
pairs that may be called through the proxy. Methods not on the whitelist raise
`AttributeError`. The blacklist explicitly blocks mutation and filesystem
operations.

---

## Pipeline execution model

`execute_pipeline(code_or_path, dag)` runs a script in a restricted namespace:

| Name | What it is |
|------|------------|
| `read(path)` | Load a file; cache key = path + mtime |
| `np` | Tracked numpy namespace |
| `show(mesh, **kw)` | Record an actor for rendering |
| `add_mesh(mesh, **kw)` | Alias for `show` |
| `screenshot(path)` | Forward to optional show_callback |
| `pv` | PyVista module for dataset creation |
| `vtk_escape` | Escape hatch for raw VTK (see below) |
| `vtk_escape_multi` | Multi-input variant of vtk_escape |

After execution, `result.actors` holds the list of `(mesh_proxy, kwargs)` tuples
recorded by `show`. Pass these to `SceneReconciler.reconcile()` to apply only
the minimal add/remove operations to a PyVista Plotter.

For higher-level use, `Session` and `run_session()` (`runner.py`) combine the
DAG, Plotter, reconciler, and optional file watcher into a single object.

---

## inspect_exec — ad-hoc queries against cached state

After `execute_pipeline`, the named variables from the script (those bound to
`TrackedProxy` values) are recorded in `dag.names`. `inspect_exec` gives you a
fresh execution environment that sees those same proxies:

```python
result = execute_pipeline(code, dag)
# "surface" was a variable in the pipeline script

inspect = inspect_exec("print(surface.n_points)", dag)
print(inspect.output)  # "12345\n"
```

`inspect_exec` does not call `begin_run`/`end_run` and does not provide `read`,
`show`, or `screenshot`. It is purely read-only and does not modify the cache.

---

## vtk_escape — raw VTK within a tracked pipeline

Most PyVista filters are whitelisted and cached automatically. For filters that
PyVista doesn't expose, use `vtk_escape`:

```python
def smooth_filter(m):
    """Must be pure: same input -> same output."""
    import vtk, pyvista as pv
    f = vtk.vtkWindowedSincPolyDataFilter()
    f.SetInputData(m)
    f.SetNumberOfIterations(20)
    f.Update()
    return pv.wrap(f.GetOutput())

smoothed = vtk_escape(surface_proxy, smooth_filter)
```

The function is hashed via `inspect.getsource()` (or explicit `key=` for
lambdas/closures). The op hash is `hash("vtk_escape", input_hash, func_hash)`.

`vtk_escape_multi` accepts a list of proxies for functions that combine multiple
meshes.

See `VTK-ESCAPE-PATTERN.md` for the full design, hashing strategy, and examples.

---

## Purity contract

Caching is correct only when every proxied operation is **pure**: given the same
inputs it always produces the same output. The library cannot enforce this.

**Known hazards** (from `PURITY-ANALYSIS.md`):

1. **`set_active_scalars` hidden state** (CRITICAL) — `threshold()`, `contour()`,
   and similar filters use `mesh.active_scalars_name` when `scalars=` is omitted.
   This state is not captured in the content hash. Always pass `scalars=`
   explicitly, or the cache may serve results from the wrong field.

2. **VTK passthrough optimization** (MODERATE) — when a threshold filter passes
   all points, VTK reuses the source array directly. The cached result shares
   memory with the source. Mutating the source after caching corrupts future hits.

3. **Cache stores live references** (OPERATIONAL) — the DAG holds real Python
   objects. Bypassing the proxy and mutating them directly corrupts the cache.

---

## Running tests

```bash
python3 -m pytest experiments/tracked-execution/tests/ -q
# 145 passed, 2 xfailed
```

The 2 xfailed tests document the known hazards above (CRITICAL and MODERATE).

---

## Running benchmarks

```bash
cd experiments/tracked-execution
python3 benchmarks/run_all.py
```

Benchmarks cover: parameter sweeps, pipeline refinement (cache speedup),
numpy-heavy computations, visual parameter changes, and A/B comparisons.

---

## Running examples

```bash
cd experiments/tracked-execution
python3 examples/demo_caching.py
python3 examples/demo_iterative_refinement.py
python3 examples/demo_vtk_escape_basic.py
```

Most examples use a synthetic 64×64×64 test volume and require no external data.

---

## Module overview

| File | Purpose |
|------|---------|
| `core.py` | `DAG` — content-addressed cache with per-run GC |
| `proxy.py` | `TrackedProxy` — transparent proxy routing all ops through dispatch |
| `dispatch.py` | `dispatch()`, `stable_hash()` — hash, whitelist check, cache lookup |
| `executor.py` | `execute_pipeline`, `inspect_exec`, `tracked_read` |
| `reconciler.py` | `SceneReconciler` — incremental Plotter updates |
| `runner.py` | `Session`, `run_session` — high-level execution loop |
| `watcher.py` | `watch_and_reload` — file-watching hot reload |
| `vtk_escape.py` | `vtk_escape`, `vtk_escape_multi` — raw VTK escape hatch |
| `whitelist.py` | `WHITELIST`, `BLACKLIST` — allowed PyVista/numpy/ndarray methods |
