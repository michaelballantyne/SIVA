# Bonsai CT Scan Agent Session Log

**Date:** 2026-04-10  
**Dataset:** bonsai.vti — 256^3 uint8 CT scan of a bonsai tree  
**Field:** density (range 0–255)  
**Format:** VTK ImageData  

---

## Session Overview

This session demonstrates how an AI agent explores CT scan data using the
tracked-execution MCP server. The workflow covers:

1. Loading volumetric ImageData
2. Inspecting density distributions for segmentation thresholds
3. Threshold-based segmentation (air vs wood vs dense material)
4. Isosurface extraction via contour()
5. Creating multiple independent views simultaneously

---

## Step-by-step Workflow

### 1. Set working directory

```python
set_working_directory("/tmp/session-bonsai")
# Returns: "Working directory set to: /tmp/session-bonsai\nData files found:\n  - bonsai.vti (4.3 MB)"
```

The bonsai.vti file is only 4.3 MB (compressed VTI), but expands to
16,777,216 voxels (256^3) in memory.

### 2. Load and inspect the full volume

Pipeline file `view-bonsai.py`:
```python
mesh = read("bonsai.vti")
print(f"Points: {mesh.n_points}")
print(f"Fields: {mesh.array_names}")
show(mesh)
```

Output from `create_view`:
```
View 'view-bonsai' created watching view-bonsai.py
Pipeline output:
Points: 16777216
Fields: ['density']
```

**Lesson:** Stop the watcher immediately after `create_view` to prevent
background thread OpenGL conflicts: `srv._views["view-bonsai"].watcher.stop()`

### 3. Explore density distribution

Inspect call to understand CT scan segmentation:
```python
arr = mesh["density"]
print(f"Density range: {arr.min():.0f} - {arr.max():.0f}")
print(f"Mean: {arr.mean():.1f}, Std: {arr.std():.1f}")
low = int((arr < 30).sum())
mid = int(((arr >= 30) & (arr < 100)).sum())
high = int((arr >= 100).sum())
total = len(arr)
print(f"Air (0-29): {low} ({100*low/total:.1f}%)")
print(f"Wood (30-99): {mid} ({100*mid/total:.1f}%)")
print(f"Dense (100+): {high} ({100*high/total:.1f}%)")
```

Typical output:
```
Density range: 0 - 255
Mean: 21.4, Std: 45.2
Air (0-29): 13847210 (82.5%)
Wood (30-99): 1508432 (9.0%)
Dense (100+): 1421574 (8.5%)
```

**Observation:** ~82% of voxels are air (outside the bonsai body). The wood
and dense regions (branches, pot, soil) occupy ~17%.

### 4. Threshold to wood region

Pipeline `view-wood.py`:
```python
mesh = read("bonsai.vti")
wood = mesh.threshold(value=[30, 145], scalars="density")
print(f"Wood region: {wood.n_points} points")
show(wood, colormap="bone")
```

- Always specify `scalars=` explicitly — omitting it uses hidden state that
  breaks the DAG cache.
- The threshold returns a UnstructuredGrid with fewer points than the full volume.
- `colormap="bone"` gives a natural grey-white CT appearance.

### 5. Isosurface extraction

Pipeline `view-iso.py`:
```python
mesh = read("bonsai.vti")
iso = mesh.contour(isosurfaces=[50, 100, 150], scalars="density")
print(f"Isosurface: {iso.n_points} points")
show(iso, colormap="copper", opacity=0.7)
```

Three isosurfaces capture:
- 50: soft tissue / low-density wood boundary
- 100: primary trunk/branch boundary
- 150: dense pot/soil boundary

### 6. Multiple simultaneous views

The server supports multiple independent views via separate pipeline files:

```python
# View 1: full volume
create_view("full.py")

# View 2: thresholded
create_view("thresh.py")

# Inspect both independently
r1 = inspect("full.py", "print(mesh.n_points)")   # -> 16777216
r2 = inspect("thresh.py", "print(t.n_points)")    # -> fewer than 16777216
```

Each view has its own DAG cache, plotter, and reconciler — they don't
share state.

---

## Key Lessons Learned

### Thread safety
VTK's OpenGL context is **not thread-safe**. After `create_view`, always stop
the file watcher before calling `screenshot()`:

```python
vs = srv._views["view-name"]
vs.watcher.stop()
vs.watcher.join(timeout=2)
vs.watcher = None
```

Failing to do this causes X11 BadAccess errors or segfaults during rendering.

### Always specify scalars=
Operations like `threshold()` and `contour()` require `scalars=` to be
explicit. The bonsai dataset has only one field (`density`), but specifying
it makes the pipeline deterministic and cacheable:

```python
# Good
wood = mesh.threshold(value=[30, 145], scalars="density")
iso  = mesh.contour(isosurfaces=[50], scalars="density")

# Bad (implicit, breaks caching)
wood = mesh.threshold(value=[30, 145])
```

### CT data characteristics
- Format: VTI (ImageData) — uniform rectilinear grid
- Point count: 256^3 = 16,777,216 voxels
- Field: density, dtype uint8, range 0–255
- Most voxels are background (density ~0) — threshold needed to isolate structures

### Cache efficiency
The DAG cache means repeated `inspect()` calls on the same view don't re-read
the data file. After the first `create_view`, all subsequent inspect calls
on that view are essentially free for the read step.

---

## Test Coverage

Four tests validate this workflow:

| Test | What it covers |
|------|---------------|
| `test_ct_exploration` | Full 6-step CT workflow: load → explore → threshold → isosurface → inspect |
| `test_multiple_views_ct` | Two simultaneous views, independent inspect calls |
| `test_inspect_density_stats` | Field names, point count, dtype, min/max validation |
| `test_sequential_inspect_calls` | Multiple sequential inspect calls on same view (cache validation) |

All tests run in ~7.6 seconds total with `xvfb-run -a`.

---

## Running the Tests

```bash
# From the VisLang repo root:
xvfb-run -a .venv/bin/python -m pytest \
    experiments/tracked-execution/mcp_server/tests/test_bonsai_e2e.py \
    -v --timeout=120
```
