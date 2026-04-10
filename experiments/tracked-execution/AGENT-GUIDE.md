# Agent Guide: Writing Pipeline Code for the Tracked Execution Library

This guide is for an AI agent writing PyVista pipeline code that runs through
the tracked execution library. Code looks like normal PyVista but executes in
a restricted namespace with content-addressed caching.

---

## 1. Quick Reference

### Namespace — what you can use

| Name | Type | Description |
|------|------|-------------|
| `read(path)` | function | Load a PyVista mesh; cached by path + mtime |
| `show(mesh, **kwargs)` | function | Render a mesh; alias `add_mesh` |
| `add_mesh(mesh, **kwargs)` | function | Alias for `show` |
| `screenshot(path=None, **kwargs)` | function | Capture a screenshot |
| `print(...)` | function | Captured print; output returned in ExecutionResult |
| `np` | tracked numpy | Numpy functions with caching (see Section 2) |
| `pv` | pyvista module | PyVista for dataset constructors inside `vtk_escape` |
| `vtk_escape(proxy, func, *, key=None)` | function | Run raw VTK code with caching |
| `vtk_escape_multi(proxies, func, *, key=None)` | function | Multi-input vtk_escape |

### What is not available

- No `import` statements — the namespace is restricted, no `__import__`
- No `open()`, `os`, `sys`, `subprocess`, or any filesystem/network access
- No `exec`, `eval`, `compile`, `globals`, `locals`

### What objects are

Objects returned from `read()` and filter methods are `TrackedProxy` wrappers.
They intercept every method call, hash (type, method, input_hash, args), and
cache the result. They behave like PyVista meshes for all whitelisted operations.

---

## 2. What PyVista API Works

### Data loading

```python
mesh = read("output.30000.vts")        # StructuredGrid, PolyData, etc.
mesh = read("scan.vti")                 # ImageData
```

### Filters — the main tools

```python
# Threshold: keep cells where a scalar exceeds a value
hot = mesh.threshold(value=500, scalars="Temperature")

# Contour: extract isosurface
iso = mesh.contour(isosurfaces=[500], scalars="Temperature")

# Clip: half-space cut
half = mesh.clip(normal="z")
half = mesh.clip_scalar(value=300, scalars="Pressure")

# Slices
plane = mesh.slice(normal="y", origin=[0, 0.5, 0])
ortho = mesh.slice_orthogonal()

# Surface extraction
surface = mesh.extract_surface()

# Smoothing / decimation (PolyData)
smooth = surface.smooth(n_iter=100)
simple = surface.decimate(target_reduction=0.5)

# Gradients, normals
grad = mesh.compute_gradient(scalars="Temperature")
normed = surface.compute_normals()
```

### Data access

```python
# Array names
print(mesh.array_names)                 # list of field names
print(mesh.n_points, mesh.n_cells)
print(mesh.bounds)                      # (xmin, xmax, ymin, ymax, zmin, zmax)

# Get a scalar array — returns a TrackedProxy wrapping a numpy array
arr = mesh["Temperature"]
print(arr.min(), arr.max(), arr.mean())
print(arr.shape, arr.dtype)

# Statistics
lo, hi = mesh.get_data_range(scalars="Temperature")
```

### Numpy via `np`

The `np` object wraps numpy with caching. Use it like normal numpy.

```python
arr = mesh["Temperature"]
p95 = np.percentile(arr, 95)
hist, edges = np.histogram(arr, bins=20)
mask = arr > 500                         # boolean TrackedProxy
filtered_vals = arr[mask]                # indexing works
magnitude = np.sqrt(arr ** 2)
log_vals = np.log(arr + 1)
```

Tracked numpy functions: `sqrt`, `abs`, `mean`, `std`, `min`, `max`, `sum`,
`log`, `log10`, `exp`, `sort`, `unique`, `array`, `zeros`, `ones`,
`percentile`, `histogram`, `where`, `linspace`, `clip`, `concatenate`.

All other numpy attributes fall through to real numpy (constants like `np.pi`,
`np.inf`, less-common functions).

### Display

```python
show(mesh)
show(mesh, colormap="inferno", opacity=0.8)
show(mesh, scalars="Temperature", show_scalar_bar=True, scalar_bar_args={"title": "Temperature"})
add_mesh(surface, colormap="plasma", clim=[200, 1000])
```

`show()` and `add_mesh()` are identical. Colormap/opacity changes cost nothing —
they do not re-run any computation.

---

## 3. What Does Not Work (And Why)

### No imports

```python
# WRONG — will raise NameError
import numpy as np
import vtk

# RIGHT — use the provided np and vtk_escape
arr = np.percentile(mesh["T"], 95)
```

### No file I/O

`mesh.save()`, `mesh.write()`, `mesh.export()` are blacklisted. Any attempt
raises `AttributeError`.

```python
# WRONG — blacklisted
mesh.save("output.vtk")

# There is no workaround — output goes through show()/screenshot() only.
```

### No in-place mutation

`mesh["field"] = array` and `arr[0] = 5` are blocked. The proxy layer intercepts
`__setitem__` and raises `AttributeError`. This is by design: the caching system
assumes all cached objects are immutable after storage.

```python
# WRONG — raises AttributeError
mesh["Magnitude"] = np.sqrt(mesh["Vx"]**2)

# RIGHT — use vtk_escape to create a new mesh with the extra field
def add_magnitude(m):
    import numpy as np, pyvista as pv
    result = m.copy()
    result["Magnitude"] = np.sqrt(m["Vx"]**2 + m["Vy"]**2 + m["Vz"]**2)
    return result

enriched = vtk_escape(mesh, add_magnitude)
```

### No Plotter creation

You cannot instantiate `pv.Plotter()`, add widgets, or set camera positions
programmatically. Use `show()` and `add_mesh()`.

### `pv` is available but meant for vtk_escape

`pv` (the pyvista module) is in the namespace, so `pv.ImageData()`,
`pv.PolyData()`, etc. can be called. However, objects created this way are
plain PyVista meshes — not `TrackedProxy` instances — so they bypass the
caching system entirely. Use `pv` only inside `vtk_escape` functions, where
caching is handled by the escape hatch itself. For data entry, always prefer
`read(path)` over constructing meshes manually.

---

## 4. Critical: Always Specify `scalars=`

This is the most important rule. Omitting `scalars=` from filter calls causes
**silent wrong results** from the cache.

### Why it matters

When `scalars=` is omitted, PyVista uses the mesh's `active_scalars_name` —
internal metadata that is NOT part of the cache hash. Two pipeline runs with
different active scalars produce different results, but the system sees the same
hash and returns a cache hit from the first run.

```python
# WRONG — uses hidden active_scalars_name, not captured in hash
hot = mesh.threshold(value=500)         # which field? unknown, unsafe

# RIGHT — explicit scalars= is included in the hash
hot = mesh.threshold(value=500, scalars="Temperature")
```

### Affected methods — always pass `scalars=`

- `threshold(value, scalars=...)`
- `threshold_percent(percent, scalars=...)`
- `contour(isosurfaces, scalars=...)`
- `clip_scalar(value, scalars=...)`
- `get_data_range(scalars=...)`
- `warp_by_scalar(scalars=...)`
- `extract_values(ranges, scalars=...)`

**Rule**: if a method accepts `scalars=`, always provide it. Never rely on the
active scalar default.

---

## 5. How Caching Works

Every method call is hashed as:

```
op_hash = hash(type, method_name, input_proxy_hash, positional_args, keyword_args)
```

- Same operation + same inputs = cache hit (zero computation, instant)
- Changing any argument busts the cache for that step and everything downstream
- `read()` is hashed by absolute path + file modification time
- `vtk_escape` is hashed by input hash + function source text (or explicit key)

### Incremental editing is efficient

When you modify a pipeline, only the changed step and its downstream dependents
recompute. Upstream steps are always cached.

```python
# Run 1: all three steps compute
mesh = read("data.vts")                              # miss
hot = mesh.threshold(value=500, scalars="T")         # miss
surface = hot.extract_surface()                      # miss

# Run 2: change only the threshold value
mesh = read("data.vts")                              # HIT (file unchanged)
hot = mesh.threshold(value=600, scalars="T")         # miss (value changed)
surface = hot.extract_surface()                      # miss (input changed)

# Run 3: change only the colormap
show(surface, colormap="plasma")                     # HIT — surface is cached,
                                                     # colormap is display-only
```

### Display parameters are free

Changing `colormap`, `opacity`, `clim`, `show_scalar_bar`, `scalar_bar_args` in `show()` does not
trigger any recomputation. Experiment freely.

---

## 6. Patterns for Common Tasks

### Pipeline files: visualization code only

A pipeline file contains `read`, filters, and `show`. Its purpose is to
produce a visualization. Keep it focused — don't put data exploration prints
in the pipeline file.

```python
# view-fire.py — a complete pipeline file
mesh = read("data.vts")
fire = mesh.threshold(value=400, scalars="theta")
surface = fire.extract_surface()
show(surface, colormap="inferno")
```

The pipeline is re-executed on every change. Exploration prints would fire on
every re-run and clutter the output. Do exploration separately, via
`inspect_pipeline`.

### Data exploration via inspect_pipeline

Use `inspect_pipeline` (the `inspect` MCP tool) to query cached pipeline
variables **without re-running the pipeline**. All named `TrackedProxy`
variables from the last pipeline run are available. No `read`, `show`, or
`screenshot` — read-only access only.

```python
# inspect snippet — not a pipeline file:
arr = mesh["theta"]
print(f"range: {arr.min():.1f} - {arr.max():.1f}")
print(f"mean: {arr.mean():.1f}, std: {arr.std():.1f}")
p5 = np.percentile(arr, 5)
p95 = np.percentile(arr, 95)
print(f"5th-95th percentile: {p5:.1f} - {p95:.1f}")
```

```python
# More inspect examples:
print(mesh.array_names)
print(f"points: {mesh.n_points}, cells: {mesh.n_cells}")
print(f"bounds: {mesh.bounds}")

# Query an intermediate result directly
print(f"hot region: {fire.n_points} points after threshold")
```

**Rule**: if you need to understand the data — ranges, field names,
point counts, statistics — use `inspect_pipeline`. Don't add exploratory
`print()` calls to the pipeline file.

### Building a visualization incrementally

Start broad, narrow down. Each edit is cheap because upstream is cached.

```python
# view-fire.py — step 1: show the full mesh to orient yourself
mesh = read("data.vts")
show(mesh, colormap="viridis")
```

Then query data to find good threshold values:

```python
# inspect snippet to find range:
arr = mesh["Temperature"]
print(f"range: {arr.min():.1f} - {arr.max():.1f}")
```

Then refine the pipeline:

```python
# view-fire.py — step 2: threshold at a value chosen from inspect output
mesh = read("data.vts")
hot = mesh.threshold(value=500, scalars="Temperature")
surface = hot.extract_surface()
show(surface, colormap="inferno", scalar_bar_args={"title": "Temperature"})
```

Changing only the display parameters (colormap, opacity) costs nothing — those
are not re-computed.

### Computing derived fields with vtk_escape

When you need to add a computed field that isn't a standard PyVista filter,
use `vtk_escape`. The function runs outside the proxy layer with full Python
access; it is cached by input hash + function source.

```python
mesh = read("data.vts")

def compute_magnitude(m):
    import numpy as np
    result = m.copy()
    result["Magnitude"] = np.sqrt(m["Vx"]**2 + m["Vy"]**2 + m["Vz"]**2)
    return result

enriched = vtk_escape(mesh, compute_magnitude)
fast = enriched.threshold(value=10, scalars="Magnitude")
show(fast, colormap="plasma", scalar_bar_args={"title": "Magnitude"})
```

### Using VTK filters not exposed by PyVista

```python
surface = mesh.threshold(value=500, scalars="T").extract_surface()

def sinc_smooth(m):
    import vtk, pyvista as pv
    f = vtk.vtkWindowedSincPolyDataFilter()
    f.SetInputData(m)
    f.SetNumberOfIterations(20)
    f.Update()
    return pv.wrap(f.GetOutput())

smoothed = vtk_escape(surface, sinc_smooth)
show(smoothed, colormap="viridis")
```

### Multiple mesh inputs with vtk_escape_multi

```python
mesh_a = read("region_a.vts")
mesh_b = read("region_b.vts")

def merge(a, b):
    return a.merge(b)

combined = vtk_escape_multi([mesh_a, mesh_b], merge)
show(combined, colormap="coolwarm")
```

### Lambdas and closures in vtk_escape — use explicit key

The default hashing reads function source text. Lambdas and closures often
lack stable source text. Provide an explicit `key` and increment it when the
function changes.

```python
scale = 2.0

enriched = vtk_escape(
    mesh,
    lambda m: m.warp_by_scalar(scalars="Displacement", factor=scale),
    key="warp_displacement_v1",    # change to v2 if you change scale or logic
)
```

---

## 7. Performance Tips

- Prefer changing downstream parameters over upstream ones — more cache hits.
- Colormap and opacity changes are free — try many variations.
- `read()` is always cached unless the file changes on disk.
- When exploring statistics, use `inspect_pipeline` rather than re-running the
  whole pipeline.
- `vtk_escape` functions are cached too: same input + same source = instant.
- If a vtk_escape function is slow and you haven't changed it, change nothing
  — you'll get a hit on the next run.

---

## 8. Troubleshooting

### `AttributeError: 'X' is not whitelisted`

The method isn't in the allowed list. Common cases:

- `mesh.plot()` — not whitelisted, use `show(mesh)`
- `mesh.save()` — blacklisted, no workaround for file output
- A filter method that exists in PyVista but wasn't included

Workaround: implement the operation inside a `vtk_escape` function.

```python
def do_unlisted_thing(m):
    import pyvista as pv
    # full pyvista available inside here
    return m.some_unlisted_method()

result = vtk_escape(mesh, do_unlisted_thing)
```

### `AttributeError: can't set attribute` (mutation blocked)

You tried to assign to a field on a proxy:

```python
mesh["NewField"] = arr      # AttributeError
```

Use `vtk_escape` to create a modified copy instead (see Section 6).

### Unexpected results after changing the pipeline

Check whether you omitted `scalars=` on a filter call. The active-scalars
hazard causes the cache to silently return results from a previous run. Fix:
add explicit `scalars="FieldName"` to every call that accepts it.

### `NameError: name 'X' is not defined`

`X` is not in the restricted namespace. This means:
- You used `import` — not allowed
- You referenced a variable that wasn't defined in the pipeline
- You tried to use a builtin that was excluded (`open`, `__import__`, etc.)

### `TypeError: input_proxy must be a TrackedProxy`

You passed a plain PyVista mesh (or other non-proxy object) to `vtk_escape`.
All objects that originate from `read()` or filter calls are already proxies.
If you created a mesh inside a function and are trying to escape it, the
function itself should just return the result — `vtk_escape` wraps the return
value automatically.

### Cache hit returns wrong data

Two possible causes:

1. **Active scalars hazard** — add `scalars=` to all filter calls.
2. **VTK passthrough** — when a threshold passes ALL points (value below data
   minimum), VTK shares the source array buffer with the output. Avoid
   degenerate thresholds that pass everything; use `inspect_pipeline` to verify
   the filtered point count makes sense.

---

## 9. Complete Example: Wildfire Simulation

**Step 1** — Write a minimal pipeline to load the data:

```python
# view-fire.py
mesh = read("output.30000.vts")
show(mesh, colormap="viridis")
```

**Step 2** — Explore with `inspect_pipeline` (the `inspect` MCP tool) to find
good threshold values. Do not put these prints in the pipeline file:

```python
# inspect snippet:
print(mesh.array_names)
print(f"bounds: {mesh.bounds}")

temp = mesh["Temperature"]
print(f"Temperature range: {temp.min():.1f} - {temp.max():.1f} K")
p10 = np.percentile(temp, 10)
p90 = np.percentile(temp, 90)
print(f"10th-90th pct: {p10:.1f} - {p90:.1f}")
```

**Step 3** — Update the pipeline file with the values learned from inspect:

```python
# view-fire.py
mesh = read("output.30000.vts")
hot = mesh.threshold(value=700, scalars="Temperature")
surface = hot.extract_surface()
show(surface, scalars="Temperature", colormap="inferno",
     scalar_bar_args={"title": "Temperature (K)"}, opacity=0.9)
```

**Step 4** — Add a derived field via `vtk_escape`:

```python
# view-fire.py (with velocity magnitude layer)
mesh = read("output.30000.vts")

hot = mesh.threshold(value=700, scalars="Temperature")
surface = hot.extract_surface()
show(surface, scalars="Temperature", colormap="inferno",
     scalar_bar_args={"title": "Temperature (K)"}, opacity=0.9)

def add_vel_magnitude(m):
    import numpy as np
    result = m.copy()
    result["VelMag"] = np.sqrt(m["Vx"]**2 + m["Vy"]**2 + m["Vz"]**2)
    return result

enriched = vtk_escape(mesh, add_vel_magnitude)
fast = enriched.threshold(value=5, scalars="VelMag")
show(fast.extract_surface(), scalars="VelMag", colormap="plasma",
     scalar_bar_args={"title": "Velocity Magnitude (m/s)"})
```

Use `inspect_pipeline` between steps to query intermediate results (e.g.
`hot.n_points`, `surface.bounds`) without re-running the pipeline.
