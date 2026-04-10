# Pipeline File Reference

Pipeline files are plain Python scripts executed in a restricted namespace
with content-addressed caching. This page documents everything available in
that namespace, common patterns, and what is intentionally blocked.

---

## Available Names

| Name | Type | Description |
|------|------|-------------|
| `read(path)` | function | Load a data file; cached by path + mtime |
| `show(mesh, **kwargs)` | function | Record a mesh actor for rendering |
| `screenshot(path=None)` | function | Capture a screenshot (optional path) |
| `print(...)` | function | Captured output; returned in pipeline status |
| `np` | tracked numpy | Numpy functions with content-addressed caching |
| `pv` | module | PyVista module (intended for use inside `vtk_escape`) |
| `vtk_escape(proxy, func, *, key=None)` | function | Run raw VTK code with caching |
| `vtk_escape_multi(proxies, func, *, key=None)` | function | Multi-input `vtk_escape` |

Standard Python builtins are also available: `int`, `float`, `str`, `bool`,
`list`, `tuple`, `dict`, `set`, `range`, `enumerate`, `zip`, `len`, `min`,
`max`, `sum`, `abs`, `round`, `sorted`, `isinstance`, `hasattr`, etc.

---

## Data Loading

```python
mesh = read("output.30000.vts")    # StructuredGrid
mesh = read("scan.vti")             # ImageData
mesh = read("cloud.vtp")            # PolyData
```

`read()` accepts any file format PyVista can read (VTK XML formats, legacy VTK,
NRRD/NHDR, etc.). The cache key is the absolute file path plus the file's
modification time. If the file hasn't changed since the last run, the cached
mesh is returned instantly.

---

## Filters

Objects returned by `read()` and filter methods are `TrackedProxy` wrappers.
They behave like PyVista meshes for all whitelisted operations.

### Threshold and clip

```python
hot = mesh.threshold(value=500, scalars="temperature")
cold = mesh.threshold_percent(percent=0.1, scalars="temperature")
half = mesh.clip(normal="z")
region = mesh.clip_scalar(value=300, scalars="pressure")
```

### Isosurfaces and slices

```python
iso = mesh.contour(isosurfaces=[500, 700], scalars="temperature")
plane = mesh.slice(normal="y", origin=[0, 0.5, 0])
ortho = mesh.slice_orthogonal()
```

### Surface and mesh operations

```python
surface = mesh.extract_surface()
smooth = surface.smooth(n_iter=100)
simple = surface.decimate(target_reduction=0.5)
normed = surface.compute_normals()
```

### Gradients and derived quantities

```python
grad = mesh.compute_gradient(scalars="temperature")
```

### Geometric transforms

```python
scaled = mesh.scale([2.0, 2.0, 1.0])
moved = mesh.translate([100, 0, 0])
```

### Combining meshes

```python
combined = mesh_a.merge(mesh_b)
```

---

## Data Access

```python
# Array names
print(mesh.array_names)

# Mesh properties
print(mesh.n_points, mesh.n_cells)
print(mesh.bounds)        # (xmin, xmax, ymin, ymax, zmin, zmax)
print(mesh.center)

# Get a scalar array — returns a TrackedProxy wrapping a numpy array
arr = mesh["temperature"]
print(arr.min(), arr.max(), arr.mean())
print(arr.shape, arr.dtype)

# Range via method
lo, hi = mesh.get_data_range(scalars="temperature")
```

---

## Numpy via `np`

The `np` object wraps numpy with content-addressed caching. Use it like
standard numpy:

```python
arr = mesh["temperature"]
p95 = np.percentile(arr, 95)
hist, edges = np.histogram(arr, bins=20)
mask = arr > 500
magnitude = np.sqrt(arr ** 2)
log_vals = np.log(arr + 1)
clipped = np.clip(arr, 200, 800)
```

### Tracked numpy functions (cached)

`sqrt`, `abs`, `mean`, `std`, `min`, `max`, `sum`, `log`, `log10`, `exp`,
`sort`, `unique`, `array`, `zeros`, `ones`, `percentile`, `histogram`,
`where`, `linspace`, `clip`, `concatenate`

### Pass-through (not cached, but available)

Constants and less-common attributes fall through to real numpy: `np.pi`,
`np.inf`, `np.nan`, `np.float32`, `np.uint8`, and any function not listed
above.

---

## Display with `show()`

```python
show(mesh)
show(mesh, colormap="inferno")
show(mesh, scalars="temperature", colormap="plasma", opacity=0.8)
show(surface, clim=[200, 1000], show_scalar_bar=True,
     scalar_bar_args={"title": "Temperature (K)"})
```

Display parameters passed to `show()` — `colormap`, `opacity`, `clim`,
`show_scalar_bar`, `scalar_bar_args` — do not trigger any recomputation. They
are display-only and can be changed freely without cache invalidation.

### Common colormaps

`viridis`, `plasma`, `inferno`, `magma`, `coolwarm`, `bone`, `copper`, `jet`,
`rainbow`, `hot`, `gray`

---

## The `scalars=` Rule

**Always specify `scalars=` on any method that accepts it.**

When `scalars=` is omitted, PyVista falls back to the mesh's
`active_scalars_name` — internal metadata that is NOT captured in the content
hash. Two runs with different active scalars will produce different results but
the same hash, causing the cache to return stale data from the first run.

```python
# Wrong — uses hidden active_scalars_name, breaks caching
hot = mesh.threshold(value=500)

# Right — field is explicit and hashed
hot = mesh.threshold(value=500, scalars="temperature")
```

Methods that require explicit `scalars=`:

- `threshold(value, scalars=...)`
- `threshold_percent(percent, scalars=...)`
- `contour(isosurfaces, scalars=...)`
- `clip_scalar(value, scalars=...)`
- `get_data_range(scalars=...)`
- `warp_by_scalar(scalars=...)`
- `extract_values(ranges, scalars=...)`

---

## vtk_escape — Raw VTK Within a Tracked Pipeline

For operations not whitelisted in the proxy layer, use `vtk_escape`. The
function runs outside the proxy with full Python access and is cached by
input hash + function source text.

```python
surface = mesh.threshold(value=500, scalars="theta").extract_surface()

def sinc_smooth(m):
    """Must be pure: same input -> same output."""
    import vtk, pyvista as pv
    f = vtk.vtkWindowedSincPolyDataFilter()
    f.SetInputData(m)
    f.SetNumberOfIterations(20)
    f.Update()
    return pv.wrap(f.GetOutput())

smoothed = vtk_escape(surface, sinc_smooth)
show(smoothed, colormap="viridis")
```

### Adding computed fields

`vtk_escape` is the right way to add derived scalar fields, since in-place
mutation of proxy objects is blocked:

```python
mesh = read("data.vts")

def add_magnitude(m):
    import numpy as np
    result = m.copy()
    result["VelMag"] = np.sqrt(m["Vx"]**2 + m["Vy"]**2 + m["Vz"]**2)
    return result

enriched = vtk_escape(mesh, add_magnitude)
fast = enriched.threshold(value=10, scalars="VelMag")
show(fast, colormap="plasma")
```

### Multi-input: vtk_escape_multi

```python
mesh_a = read("region_a.vts")
mesh_b = read("region_b.vts")

def merge(a, b):
    return a.merge(b)

combined = vtk_escape_multi([mesh_a, mesh_b], merge)
show(combined, colormap="coolwarm")
```

### Lambdas and closures — use an explicit key

The default hashing reads the function's source text via `inspect.getsource()`.
Lambdas and closures often lack stable source (no source file, or source
changes without the lambda text changing). Provide an explicit `key=` string
and increment it when the logic changes:

```python
scale = 2.0
enriched = vtk_escape(
    mesh,
    lambda m: m.warp_by_scalar(scalars="Displacement", factor=scale),
    key="warp_displacement_v1",    # increment to v2 if logic changes
)
```

---

## Whitelisted PyVista Operations

The proxy layer allows the following categories of operations. Anything else
raises `AttributeError: 'X' is not whitelisted — use vtk_escape()`.

### Filters

`threshold`, `threshold_percent`, `clip`, `clip_box`, `clip_scalar`,
`clip_surface`, `contour`, `slice`, `slice_orthogonal`, `slice_along_axis`,
`extract_surface`, `extract_geometry`, `extract_all_edges`,
`extract_feature_edges`, `extract_cells`, `extract_points`, `extract_values`,
`cell_data_to_point_data`, `point_data_to_cell_data`, `compute_gradient`,
`compute_normals`, `compute_cell_quality`, `compute_cell_sizes`,
`compute_derivative`, `smooth`, `subdivide`, `decimate`, `warp_by_scalar`,
`warp_by_vector`, `extract_largest`, `connectivity`, `merge`,
`boolean_union`, `boolean_difference`, `transform`, `translate`, `scale`,
`rotate_x`, `rotate_y`, `rotate_z`, `reflect`, `elevation`, `glyph`,
`outline`, `delaunay_3d`, `integrate_data`, `interpolate`, `sample`, `probe`,
`streamlines`, `streamlines_from_source`, `concatenate`

### Metadata and data access

`n_points`, `n_cells`, `n_arrays`, `bounds`, `center`, `length`, `area`,
`actual_memory_size`, `points`, `get_array`, `active_scalars_name`,
`active_scalars`, `active_vectors`, `active_tensors`, `array_names`,
`point_data`, `cell_data`, `field_data`, `get_data_range`, `__getitem__`

### Copy and conversion

`copy`, `deep_copy`, `shallow_copy`, `to_polydata`,
`cast_to_unstructured_grid`, `cast_to_multiblock`

### PolyData-specific

`triangulate`, `clean`, `fill_holes`, `strip`, `tube`, `ribbon`, `extrude`,
`delaunay_2d`, `curvature`, `geodesic`, `geodesic_distance`, `flip_normals`

### ImageData-specific

`dimensions`, `spacing`, `origin`, `extent`, `image_threshold`,
`gaussian_smooth`, `median_smooth`, `low_pass`, `high_pass`, `fft`, `rfft`,
`pad_image`, `resize`, `resample`

### Numpy ndarray (on array proxies)

Arithmetic operators, comparison operators, statistical reductions (`mean`,
`std`, `min`, `max`, `sum`), shape operations (`reshape`, `flatten`,
`transpose`), sorting, type conversion, indexing.

---

## What Is NOT Available

### No imports

```python
# Wrong — raises NameError
import numpy as np
import vtk

# Right — use the provided np and vtk_escape
arr = np.percentile(mesh["T"], 95)
```

### No file I/O

`mesh.save()`, `mesh.write()`, `mesh.export()` are blacklisted. Output goes
through `show()` and `screenshot()` only. `open()` raises `PermissionError`
with a descriptive message.

### No in-place mutation

```python
# Wrong — raises AttributeError
mesh["NewField"] = computed_array

# Right — use vtk_escape to return a modified copy
def add_field(m):
    result = m.copy()
    result["NewField"] = ...
    return result

enriched = vtk_escape(mesh, add_field)
```

The proxy's `__setitem__` is blacklisted. This is by design: the cache assumes
all stored objects are immutable.

### No Plotter creation

You cannot instantiate `pv.Plotter()` or call `mesh.plot()`. Use `show()`.

### No `set_active_scalars` and related

`set_active_scalars`, `set_active_vectors`, and `set_active_tensors` are
blacklisted because they mutate hidden state that is not captured in the
content hash. Always use the explicit `scalars=` parameter instead.

### No `exec`, `eval`, `compile`, `globals`, `locals`

These are not in the restricted builtins.

---

## How Caching Works From the Pipeline's Perspective

Every method call on a `TrackedProxy` is hashed as:

```
op_hash = sha256(type, method_name, input_proxy_hash, args, kwargs)
```

- If `op_hash` is in the cache, the cached result is returned immediately.
- If not, the real method is executed, the result is cached, and the result is
  returned wrapped in a new `TrackedProxy`.

**What this means for editing:**

```python
# Run 1: all three steps compute (3 misses)
mesh = read("data.vts")
hot = mesh.threshold(value=500, scalars="T")
surface = hot.extract_surface()

# Run 2: change the threshold value
mesh = read("data.vts")        # HIT (file unchanged)
hot = mesh.threshold(value=600, scalars="T")  # miss (value changed)
surface = hot.extract_surface()               # miss (input changed)

# Run 3: change only the colormap
show(surface, colormap="plasma")   # colormap is not a mesh operation; no recomputation
```

**Per-run garbage collection:** The DAG calls `begin_run()` and `end_run()`
around each execution. Objects not accessed during a run are evicted. This
prevents unbounded memory growth during long iterative sessions.

**Read caching:** `read(path)` is cached by absolute path + file modification
time. If the file changes on disk, the mtime changes and the cache is
invalidated automatically.

**Shared read cache:** When multiple views read the same large file, the server
maintains a cross-view shared cache so the file is loaded from disk only once
per mtime, regardless of how many views reference it.
