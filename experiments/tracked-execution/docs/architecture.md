# Architecture

This page describes how the tracked execution system works internally. It is
intended for people who want to understand or extend the system.

---

## High-Level Architecture

```
User / AI Agent
      |
      | writes pipeline .py file
      v
  pipeline.py ──────────────────────────────────┐
                                                 |
  MCP Server (mcp_server/server.py)              |
      |                                          |
      | create_view() ─────────────────────────> |
      |                               File Watcher (watcher.py)
      |                                   |  on file save
      |                                   v
      |                          execute_pipeline()
      |                                   |
      v                                   v
  Restricted namespace ──────────> TrackedProxy layer
      read(), show(), np,               |
      vtk_escape                        |
                                        v
                                    dispatch()  (dispatch.py)
                                        |
                             ┌──────────┴──────────┐
                             |                     |
                         cache hit            cache miss
                             |                     |
                    return cached          execute real method
                    TrackedProxy           cache result
                                          return TrackedProxy
                                                |
                                                v
                                        DAG  (core.py)
                                  content_hash -> object
                                                |
                                                v
                                    SceneReconciler (reconciler.py)
                                    diff old/new actor sets
                                    minimal add/remove to Plotter
                                                |
                                                v
                                        pyvista.Plotter
                                    (main thread, OpenGL)
```

---

## Content-Addressed Caching

Every operation on a tracked object produces a **content hash**:

```
op_hash = sha256(type(obj), obj_hash, method_name, args_hash, kwargs_hash)
```

The hash is computed from the data, not from object identity. This means:

- The same logical operation on the same data always produces the same hash,
  even across Python interpreter restarts.
- If a pipeline script is re-run with identical parameters, every step is a
  cache hit. The pipeline "executes" in microseconds.
- If one parameter changes, only that step and its downstream dependents
  recompute. Upstream steps are unaffected.

### stable_hash()

All hashing uses `stable_hash()` in `dispatch.py`. It handles:

- Scalars: `int`, `float`, `str`, `bool`, `None` → deterministic repr
- Tuples and lists → hash of elements recursively
- Dicts → sorted items hashed recursively
- `numpy.ndarray` → SHA-256 of the raw bytes
- `TrackedProxy` → the proxy's stored `_hash` attribute

The result is always a hex string (SHA-256 digest).

---

## TrackedProxy

`TrackedProxy` (`proxy.py`) is a transparent wrapper around any Python/VTK/numpy
object. Every attribute access and method call is intercepted and routed through
`dispatch()`.

```python
proxy = TrackedProxy(real_object, content_hash, dag)

# This:
result = proxy.threshold(value=500, scalars="T")

# Becomes:
result = dispatch(dag, proxy, "threshold", (500,), {"scalars": "T"})
```

`TrackedProxy` stores three things via `object.__getattribute__`:

- `_obj`: the real Python object (PyVista mesh, numpy array, etc.)
- `_hash`: the content hash of this object
- `_dag`: reference to the active DAG for cache lookups

Standard Python introspection (`isinstance`, `type`) is forwarded to the
wrapped object so proxies are transparent from the outside.

---

## dispatch()

`dispatch()` (`dispatch.py`) is the single interception point for all proxy
operations. It:

1. **Checks the blacklist.** Raises `AttributeError` immediately for blocked
   operations (`save`, `__setitem__`, `set_active_scalars`, etc.).

2. **Checks the whitelist.** Raises `AttributeError` for operations not in the
   allowed set, with a message suggesting `vtk_escape()` as a workaround.

3. **Computes the op hash.** `stable_hash(type, method_name, input_hash, args, kwargs)`.

4. **Looks up the cache.** If `op_hash` is in `dag.cache`, increments hit
   counter and returns `TrackedProxy(dag.cache[op_hash], op_hash, dag)`.

5. **On cache miss:** unwraps all proxy arguments to their real objects, calls
   the real method, stores the result in `dag.cache`, increments miss counter,
   and returns a new `TrackedProxy` wrapping the result.

Properties (attributes that are not callable) are handled similarly but without
arguments.

---

## DAG

`DAG` (`core.py`) is the content-addressed object store. It is a dict mapping
content hashes to live Python objects, with per-run garbage collection.

```python
dag = DAG()
dag.begin_run()    # mark the start of a pipeline execution
# ... pipeline executes; cache is populated via dispatch() ...
dag.end_run()      # evict objects not touched during this run
```

Between runs, `dag.cache` accumulates objects. `begin_run()` resets a "touched"
set. Every cache hit or miss marks the accessed hash as touched. `end_run()`
evicts any hash not in the touched set and updates eviction counters.

`dag.names` stores a mapping from pipeline variable names to their content
hashes after each `execute_pipeline()` call. This is how `inspect_pipeline()`
accesses the post-run state without re-executing the pipeline.

`dag.stats()` returns `{"hits": N, "misses": N, "evictions": N}` for the
most recent run.

---

## Pipeline Execution Model

`execute_pipeline(code_or_path, dag)` (`executor.py`) runs a pipeline script
in a restricted namespace. The namespace is built fresh for each execution:

```python
namespace = {
    "__builtins__": _SAFE_BUILTINS,  # curated subset, no exec/eval/open
    "np":           _TrackedNumpyNamespace(dag),
    "print":        captured_print_fn,
    "read":         lambda path: tracked_read(path, dag),
    "show":         lambda mesh, **kw: actors.append((mesh, kw)),
    "screenshot":   show_callback or no-op,
    "pv":           pyvista_module,
    "vtk_escape":   vtk_escape,
    "vtk_escape_multi": vtk_escape_multi,
}
exec(compile(code, "<pipeline>", "exec"), namespace)
```

After execution, named `TrackedProxy` variables from the namespace are stored
in `dag.names`. The returned `ExecutionResult` contains:

- `actors`: list of `(mesh_proxy, kwargs)` tuples from `show()` calls
- `output`: captured `print()` output
- `stats`: `dag.stats()` for this run
- `names`: variable names that resolved to `TrackedProxy` instances

### inspect_pipeline

`inspect_pipeline(code, dag)` builds a read-only namespace from `dag.names`:
all named proxies are available; `read`, `show`, and `screenshot` raise
descriptive errors. It does not call `begin_run()`/`end_run()`, so it works
against the live post-pipeline cache without disturbing the GC state.

---

## File Watching and Reconciliation

When `create_view(pipeline_file)` is called, the server:

1. Creates a `pyvista.Plotter` (on the main thread).
2. Creates a `DAG` for this view.
3. Executes the pipeline immediately.
4. Starts a `watchdog.Observer` via `watch_and_reload()`.

The `ReloadHandler` (`watcher.py`) debounces filesystem events (100 ms window)
to suppress duplicate save events from editors. On each debounced event:

1. `execute_pipeline()` is called with the view's DAG. This is pure Python
   and safe to call from any thread.
2. The `on_reload` callback is invoked with the `ExecutionResult`.
3. Inside the callback, `reconciler.reconcile(result.actors)` is called on
   the main thread (via `run_on_main_thread()`).

### SceneReconciler

`SceneReconciler` (`reconciler.py`) diffs the previous actor set against the
new one and applies only the minimal changes to the `pyvista.Plotter`:

- **Unchanged** (same mesh hash + same display params hash): skip.
- **Updated** (same name, different mesh or params): remove and re-add.
- **Added** (new name): add.
- **Removed** (name no longer in actor list): remove.

This avoids full scene teardown on every pipeline edit and prevents flickering
in interactive windows.

Actor identity is based on the `name` parameter from `show()`. If `name` is
not provided, actors are named `actor_0`, `actor_1`, etc. by position.

---

## Threading Model

VTK's OpenGL context is not thread-safe. The MCP server's tool handlers run on
a background thread (the MCP transport thread). The watcher callbacks also run
on a background thread.

In **interactive mode** (the default without `--offscreen`), the main thread
runs `run_event_loop()`, which:

- Drains a `queue.Queue` of pending VTK operations at ~60 fps.
- Calls `plotter.iren.process_events()` for each active view to pump the VTK
  event loop.

Any code that touches a `pyvista.Plotter` is wrapped in `run_on_main_thread()`:

```python
def run_on_main_thread(fn):
    if _offscreen or threading.get_ident() == _main_thread_id:
        return fn()        # already on main thread, or offscreen mode
    result_q = queue.Queue()
    _work_queue.put((fn, result_q))
    ok, result = result_q.get()   # blocks until main thread executes fn
    if ok:
        return result
    raise result
```

Operations marshaled to the main thread:

- `pv.Plotter()` creation
- `plotter.render()`
- `plotter.screenshot()`
- `plotter.close()`
- `reconciler.reconcile()` (which calls `plotter.add_mesh()` / `plotter.remove_actor()`)

In **offscreen mode** (`--offscreen`), `run_on_main_thread()` calls `fn()`
directly on the calling thread. No event loop is needed.

---

## Security Model

The pipeline namespace is a restricted execution environment. The design goals:

1. **No filesystem access.** `open()` raises `PermissionError`. `mesh.save()`,
   `mesh.write()`, `mesh.export()` are blacklisted on the proxy.

2. **No imports.** `__import__` is replaced with a function that raises
   `ImportError` with a descriptive message. `import` statements fail.

3. **No code execution.** `exec`, `eval`, `compile`, `globals`, `locals` are
   not in the builtins.

4. **No network access.** Not explicitly blocked, but `urllib`, `socket`, etc.
   are not in the namespace and cannot be imported.

5. **No mutation.** `__setitem__` on proxied objects raises `AttributeError`.
   In-place numpy operators (`__iadd__`, etc.) are blacklisted.

6. **No hidden state.** `set_active_scalars`, `set_active_vectors`, and
   `set_active_tensors` are blacklisted to prevent silent cache correctness
   bugs.

The `vtk_escape` escape hatch deliberately breaks these rules: functions passed
to `vtk_escape` run with full Python access. The security model relies on the
fact that `vtk_escape` functions are written by a trusted agent, not by
arbitrary user input.

---

## Module Map

| File | Purpose |
|------|---------|
| `tracked_execution/proxy.py` | `TrackedProxy` — transparent proxy, routes all ops through dispatch |
| `tracked_execution/dispatch.py` | `DAG`, `dispatch()`, `stable_hash()` — cache, hash, whitelist check |
| `tracked_execution/executor.py` | `execute_pipeline`, `inspect_pipeline`, `tracked_read` |
| `tracked_execution/reconciler.py` | `SceneReconciler` — incremental Plotter updates |
| `tracked_execution/runner.py` | `Session` — high-level execution loop (library API) |
| `tracked_execution/watcher.py` | `watch_and_reload`, `ReloadHandler` — file-watching hot reload |
| `tracked_execution/vtk_escape.py` | `vtk_escape`, `vtk_escape_multi` — raw VTK escape hatch |
| `tracked_execution/whitelist.py` | `WHITELIST`, `BLACKLIST` — allowed PyVista/numpy/ndarray methods |
| `mcp_server/server.py` | FastMCP server — tool definitions, threading, view state |
| `mcp_server/run.py` | Entry point — argument parsing, interactive vs. offscreen mode |
