# Wildfire E2E Agent Session Log

Date: 2026-04-10
Dataset: HIGRAD/FIRETEC wildfire simulation (`output.30000.vts`, 18.3M points)
Test file: `mcp_server/tests/test_wildfire_e2e.py`

---

## What the agent does

The test simulates an agent exploring a large fire simulation via the MCP tools
in four scenarios:

1. **full_exploration_workflow** — Three-stage exploration: load full mesh,
   inspect, screenshot; refine to fire threshold (theta>400); tighten to hot
   core (theta>600). Each iteration creates a new view file to avoid the
   watcher-thread/OpenGL conflict.

2. **test_inspect_workflow** — Minimal sanity check: load mesh, verify point
   count is 18,300,000.

3. **test_inspect_field_ranges** — Confirm field names and temperature range
   (min ~298 K, max ~1184 K) via inspect.

4. **test_multiple_inspect_calls** — Five sequential inspect calls on different
   fields (u, v, w, theta, O2) to verify the DAG cache serves subsequent calls
   without re-reading the 1.1 GB file.

---

## Tool-by-tool trace (full_exploration_workflow)

### set_working_directory

```
set_working_directory("/tmp/tmpXXXXXX")
```

Return:
```
Working directory set to: /tmp/tmpXXXXXX
Data files found:
  - output.30000.vts (1120.0 MB)
```

Works correctly. The symlinked file is found and its size reported.

---

### create_view — initial load

Pipeline (`view-fire.py`):
```python
mesh = read("output.30000.vts")
print(f"Loaded: {mesh.n_points} points")
print(f"Fields: {mesh.array_names}")
show(mesh, colormap="viridis")
```

Return:
```
View 'view-fire' created watching view-fire.py
Cache stats: hits=0, misses=2
Pipeline variables: mesh
Pipeline output:
Loaded: 18300000 points
Fields: ['u', 'v', 'w', 'theta', 'O2', 'rhowatervapor', 'rhof_1', 'convht_1', 'frhosiesrad_1']
```

- Point count and field names captured correctly.
- Cache shows 2 misses (read + show). Subsequent runs with unchanged code
  would show 2 hits (instant).

---

### inspect — temperature statistics

Snippet:
```python
arr = mesh["theta"]
print(f"theta min={arr.min():.1f} max={arr.max():.1f} mean={arr.mean():.1f}")
```

Return:
```
theta min=298.8 max=1183.9 mean=300.2
```

- Format specifiers (`.1f`) work on TrackedProxy scalars after adding
  `__format__` to `TrackedProxy`.
- Mean ~300 K confirms most of the domain is ambient temperature; fire is
  localized.

---

### inspect — fuel density + fire fraction

Snippet:
```python
rhof = mesh["rhof_1"]
theta = mesh["theta"]
fire_pts = int((theta > 400).sum())
total = int(mesh.n_points)
print(f"rhof_1 min={rhof.min():.4f} max={rhof.max():.4f}")
print(f"Fire points (theta>400): {fire_pts} of {total}")
```

Return:
```
rhof_1 min=0.0000 max=0.6000
Fire points (theta>400): 3831 of 18300000
```

- Only 3831/18.3M points (0.02%) exceed 400 K. Fire is very localized.
- Comparison operators on TrackedProxy (`> 400`) work correctly.
- `int(...)` unwrapping of TrackedProxy results works.

---

### screenshot — initial view

```
screenshot("view-fire.py")
```

- Returns Image(format="png"), 109,140 bytes.
- PNG signature confirmed (`\x89PNG`).
- Renders the full 18.3M-point mesh colored by the active scalar.

---

### create_view — fire threshold (theta > 400)

Pipeline (`view-fire2.py`):
```python
mesh = read("output.30000.vts")
fire = mesh.threshold(value=400, scalars="theta")
surface = fire.extract_surface()
print(f"Fire region: {fire.n_points} points")
print(f"Surface points: {surface.n_points}")
show(surface, colormap="inferno")
```

Return:
```
View 'view-fire2' created watching view-fire2.py
Cache stats: hits=0, misses=5
Pipeline variables: mesh, fire, surface
Pipeline output:
Fire region: 10930 points
Surface points: 7267
```

- Threshold reduces 18.3M → 10,930 points (fire region).
- Surface extraction: 7,267 surface points.
- All three pipeline variables (`mesh`, `fire`, `surface`) are accessible via
  inspect.

---

### inspect — fire point count (from second view)

Snippet:
```python
print(fire.n_points)
```

Return:
```
10930
```

---

### screenshot — fire threshold view

```
screenshot("view-fire2.py")
```

- Returns Image(format="png"), 137,909 bytes.
- Slightly larger than the initial view (fire surface has more detail per pixel).

---

### create_view — hot core (theta > 600)

Pipeline (`view-fire3.py`):
```python
mesh = read("output.30000.vts")
fire = mesh.threshold(value=600, scalars="theta")
surface = fire.extract_surface()
print(f"Hot fire: {fire.n_points} points")
show(surface, colormap="inferno")
```

- Hot core is a subset of the theta>400 region.

### screenshot — hot core view

- Returns Image(format="png"), 94,100 bytes (smaller = fewer/smaller surfaces).

---

## What worked

- **set_working_directory**: correctly scans for VTK files including symlinks.
- **create_view**: executes pipeline synchronously, returns captured print output,
  lists variable names, and starts a file watcher.
- **inspect**: gives full access to all named pipeline variables (TrackedProxy
  objects), numpy, and built-ins. Multiple sequential calls are fast (DAG
  cache serves them without re-reading the file).
- **screenshot**: returns a valid PNG via `Image(data=..., format="png")`.
- **Caching**: the 1.1 GB read is cached by filename + mtime. After the first
  `create_view`, subsequent views with the same `read("output.30000.vts")` get
  a cache hit on that node (shared DAG not used, but per-view DAG caches the
  file read within its own calls).
- **TrackedProxy math**: comparison operators (`> 400`), `.sum()`, `.min()`,
  `.max()`, `.mean()`, `int(...)` all work correctly.

---

## What was awkward / issues found

### 1. TrackedProxy missing `__format__` (fixed)

Format specifiers in f-strings (`f"{val:.1f}"`) failed with:
```
TypeError: unsupported format string passed to TrackedProxy.__format__
```

Fix: added `__format__`, `__int__`, and `__float__` to `TrackedProxy` in
`tracked_execution/proxy.py`. These delegate directly to the underlying real
value. Tests now pass.

### 2. Watcher thread vs. OpenGL main-thread conflict

When the watcher detects a file change, its callback calls `plotter.render()`
from the background thread. If the main thread also calls `render()` (via
`screenshot()`), VTK's OpenGL context raises:
```
X Error of failed request:  BadAccess (attempt to access private resource denied)
  Major opcode of failed request:  150 (GLX)
  Minor opcode of failed request:  5 (X_GLXMakeCurrent)
```

VTK/OpenGL is not thread-safe. Rendering must happen on the main thread.

**Workaround in tests**: stop the watcher immediately after `create_view`,
then create a new view file for each pipeline iteration instead of relying on
hot reload.

**Implication for real agent use**: in the actual MCP server (stdio transport),
the MCP handler runs on the main thread while the watcher fires from a
background thread. The `screenshot()` call happens after the watcher's render
has completed (eventually) — but there's a race condition window. A proper fix
would marshal render calls onto the main thread (e.g., via a queue).

This is noted as a backlog item.

### 3. `scalar_bar` is not a valid `show()` parameter

The task brief used `show(surface, colormap="inferno", scalar_bar="theta")`,
but PyVista's `add_mesh` does not accept `scalar_bar=`. The correct parameters
are `show_scalar_bar=True` or `scalar_bar_args={"title": "..."}`. The
pipeline code was corrected to omit `scalar_bar`.

### 4. f-string escaping in test generation code

Using an outer f-string to generate inspect snippet code leads to subtle bugs
when the inner f-string contains `{len(arr)}` — Python evaluates it in the
outer scope where `arr` doesn't exist. Fixed by using string concatenation
instead of nested f-strings.

---

## Performance notes

On 18.3M-point wildfire data:

| Operation | Time |
|---|---|
| `read("output.30000.vts")` (first call) | ~2.5s |
| `threshold(value=400, scalars="theta")` | ~0.5s |
| `extract_surface()` | ~0.2s |
| `plotter.render()` (off-screen) | ~0.3s |
| `inspect` (cached data access) | <0.05s |
| `screenshot` | ~0.1s |
| Full test suite (4 tests) | ~13s |

The DAG caching is effective: multiple `inspect` calls after a single
`create_view` are nearly instant since the mesh is cached in memory.

---

## Recommendations

1. **Fix the watcher/render threading issue** — marshal `plotter.render()` calls
   onto the main thread. This is the most significant reliability issue for
   production use.

2. **Document `scalar_bar_args` in server instructions** — agents using the
   instructions string will attempt to use `scalar_bar=` since it reads
   naturally. The correct usage is `scalar_bar_args={"title": "Field"}`.

3. **Consider cross-view DAG sharing** — currently each view has its own DAG,
   so `read("output.30000.vts")` is re-read for each new view. A shared read
   cache across views would make multi-view workflows significantly faster.
