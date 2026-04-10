# VTK Escape Pattern

A design note on escaping to raw VTK within a content-addressed tracked pipeline.

---

## The problem

`tracked_execution` caches PyVista filter results by content-hashing every
operation: input hash + method name + arguments → a single op hash. This works
perfectly for the ~80% of VTK functionality that PyVista exposes as named
methods.

For the remaining ~20%, users write raw VTK code:

```python
import vtk, pyvista as pv

smoother = vtk.vtkWindowedSincPolyDataFilter()
smoother.SetInputData(mesh)
smoother.SetNumberOfIterations(20)
smoother.Update()
result = pv.wrap(smoother.GetOutput())
```

In a tracked pipeline, this breaks caching completely: the system has no way to
hash arbitrary imperative code, so every run recomputes it even when nothing
changed.

---

## The solution: `vtk_escape`

```python
from tracked_execution import vtk_escape

def custom_filter(m):
    """Must be pure: same input -> same output."""
    import vtk, pyvista as pv
    smoother = vtk.vtkWindowedSincPolyDataFilter()
    smoother.SetInputData(m)
    smoother.SetNumberOfIterations(20)
    smoother.Update()
    return pv.wrap(smoother.GetOutput())

result = vtk_escape(surface_proxy, custom_filter)
```

`vtk_escape` treats the function itself as a cache key ingredient. The op hash
is:

```
op_hash = hash("vtk_escape", input_proxy_hash, function_hash)
```

Same input + same function = cache hit. The result is a normal `TrackedProxy`
that can be chained with further operations.

---

## Using it in a pipeline file

`vtk_escape` (and `vtk_escape_multi`) are available in the restricted namespace
of `execute_pipeline`, so you can call them directly:

```python
# pipeline.py
mesh = read("data.vts")
clipped = mesh.clip(normal='z')

# PyVista doesn't expose vtkWindowedSincPolyDataFilter directly.
def smooth_filter(m):
    """Pure function: same input mesh -> same smoothed output."""
    import vtk, pyvista as pv
    f = vtk.vtkWindowedSincPolyDataFilter()
    f.SetInputData(m)
    f.SetNumberOfIterations(20)
    f.Update()
    return pv.wrap(f.GetOutput())

smoothed = vtk_escape(clipped, smooth_filter)
show(smoothed, colormap="inferno")
```

---

## How caching works

1. The function is hashed (see "Function identity" below) to produce `func_hash`.
2. The op hash is `stable_hash(("vtk_escape", input_hash, func_hash))`.
3. If the op hash is already in the DAG cache, the cached result is returned
   immediately without calling `func`.
4. On a cache miss the function is called, its output is stored in the cache,
   and a `TrackedProxy` wrapping the result is returned.

The result hash is deterministic, so downstream operations that depend on
`smoothed` are also cached correctly across runs.

---

## The purity contract

**The function MUST be pure:** given the same input mesh it must always return
the same output mesh.

The system cannot enforce this. If a function:
- reads from files
- uses random numbers
- depends on global variables or module state
- has side effects

...the cache will serve stale results silently. This is a contract with the
caller.

Pure functions:
- Apply fixed VTK filters with fixed parameters
- Do mathematical transformations of point/cell data
- Merge, clip, or resample with fixed geometry

Impure functions (do not use without an explicit key that you update on change):
- Read configuration files
- Draw parameters from a random seed
- Call out to an ML model that might be retrained

---

## Function identity

`vtk_escape` hashes the function in this order of preference:

### 1. Explicit key (recommended for lambdas and closures)

```python
result = vtk_escape(proxy, lambda m: m.copy(), key="copy_filter_v1")
```

The key string is hashed directly. Use this whenever:
- The function is a lambda
- The function is a closure that captures variables
- The function is generated dynamically

**Change the key whenever you change the function's behaviour.** This is an
explicit versioning contract: `key="my_filter_v1"` → `key="my_filter_v2"`.

### 2. Source inspection (default for named functions)

```python
def my_filter(m):
    import vtk, pyvista as pv
    f = vtk.vtkSmoothPolyDataFilter()
    f.SetInputData(m)
    f.SetNumberOfIterations(10)
    f.Update()
    return pv.wrap(f.GetOutput())

result = vtk_escape(proxy, my_filter)
```

`inspect.getsource(func)` is called to get the source text, which is then
hashed. This is stable across interpreter restarts as long as the source file
doesn't change — which is exactly the right behaviour. If you change the
function body, the hash changes automatically and the cache is invalidated.

### 3. Bytecode fallback

If source is unavailable (interactive session, `exec`-compiled code), the
fallback hashes `func.__code__.co_code + func.__code__.co_consts +
func.__qualname__`. This is less stable across Python versions and should not
be relied upon for long-lived caches. Provide an explicit `key` if you need
stability in this situation.

---

## Multiple inputs: `vtk_escape_multi`

For functions that need more than one mesh:

```python
from tracked_execution import vtk_escape_multi

def merge_meshes(a, b):
    return a.merge(b)

merged = vtk_escape_multi([proxy_a, proxy_b], merge_meshes)
```

The op hash includes all input hashes: any change to any input mesh causes a
cache miss and a recompute. The function receives the unwrapped PyVista meshes
in the same order as `input_proxies`.

```python
# With an explicit key (for lambdas):
merged = vtk_escape_multi(
    [proxy_a, proxy_b],
    lambda a, b: a.merge(b),
    key="merge_ab_v1"
)
```

---

## Return types

The function may return:
- A `pyvista.DataSet` subclass (PolyData, UnstructuredGrid, ImageData, …)
- Any raw VTK object that `pv.wrap()` can handle (vtkPolyData, vtkDataSet, …)

`vtk_escape` calls `pv.wrap()` automatically if the result is not already a
`pv.DataSet`. The caller does not need to wrap manually.

---

## API reference

```python
def vtk_escape(input_proxy, func, *, key=None):
    """Run a raw VTK/Python function on a tracked proxy's data.

    Args:
        input_proxy: TrackedProxy wrapping a PyVista mesh.
        func: callable(mesh) -> mesh. Must be pure.
        key: Optional explicit cache key string.

    Returns:
        TrackedProxy wrapping the function's output.
    """

def vtk_escape_multi(input_proxies, func, *, key=None):
    """Like vtk_escape but accepts multiple input proxies.

    Args:
        input_proxies: Sequence of TrackedProxy instances.
        func: callable(mesh1, mesh2, ...) -> mesh. Must be pure.
        key: Optional explicit cache key string.

    Returns:
        TrackedProxy wrapping the function's output.
    """
```
