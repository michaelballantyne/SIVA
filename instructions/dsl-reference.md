# DSL Reference

A spec is a plain Python file — always `spec.py`, edited in place — executed by
`run_pipeline("spec.py")`. These names are injected into the spec's namespace —
**do not import them**. Every verb that returns a `DatasetInfo` can be chained.

## inspect(filepath, positions=None) -> DatasetInfo
Reads metadata only (variables, dimensions, attributes); no bulk arrays. The
format is auto-detected by the adapter registry (see
`vislang://instructions/adapters`). Raises `UnsupportedFormatError` if nothing
can read it. Also resolves `info.positions` — the spatial-coordinate variables
`('x','y','z')`, auto-detected from the names (None for data with no explicit
coordinates, e.g. a grid). Pass `positions=('a','b','c')` to override when the
names don't auto-detect.

## subset(info, variables=None, dimensions=None) -> DatasetInfo
The **only** narrowing verb. Metadata-only — reads no bulk data. Returns a new
`DatasetInfo`; the input is untouched. `variables` *removes* the fields you don't
keep (must be a subset of what's currently listed). `dimensions` *records* a
slice policy that `load` applies:
- `{'particles': 0.1}` — random 10% of particles
- `{'particles': 5000}` — random 5000 particles
- `{'particles': slice(0, 1000)}` — first 1000
- `{'particles': slice(None, None, 10)}` — every 10th
- `{'grid': 64}` — stride a 3-D grid to ~64 cells per axis
- `{'grid': 0.25}` — keep ~25% of cells per axis

Selection is pushed into the read where the library supports it (HDF5 grids),
else read-full then sliced (e.g. GenericIO particles — the file is read in full,
then subsampled, so this trims memory/render payload but not disk I/O). Always
stride large grids before `render` (see rendering doc).

## load(info) -> DatasetInfo
Materializes exactly what `info` describes — every variable still listed on it,
sliced by whatever `subset` recorded. Pure "load what's described": it takes no
selection arguments, so it never silently loads less than asked. To load only
part of a dataset, `subset` it first. Call `load` before `render`/`compress` —
they operate on already-loaded data and raise otherwise.

## download(remote_source, local_path) -> path
Fetch a remote dataset to a local path; returns the local path string.

## compress(info, variables, error_bound) -> DatasetInfo
Error-bounded compression of selected variables.

## render(info, cmap=None, opacity=None)
Serves a browser viewer and prints its URL. Renders **everything** the loaded
info describes; call `load` first (raises if data isn't loaded). To show only
part, narrow before loading:
`render(load(subset(info, variables=[...], dimensions={...})))`. 3-D fields render
as k3d volumes (headless-safe); particle/point data uses the scene + render_server path.
Its only args are the **look** — narrowing lives in `subset`, coordinate
interpretation in `inspect`:
- `cmap` — `'green'` (custom ramp), any matplotlib name (`'viridis'`, `'hot'`,
  `'plasma'`, …), or `None` (default per-field colormaps).
- `opacity` — override the k3d opacity transfer function (flat `[t, a, …]`).

Particle coordinates come from `info.positions` (set by `inspect`); fix a
mis-detection with `inspect(path, positions=('x','y','z'))`, not a render arg.
See `vislang://instructions/rendering` for the viewing model and SSH tunnel.

## Example spec
```python
# Inspect -> subset (stride for the browser) -> load -> render in green.
info = inspect("/path/to/csafe_heptane_302x302x302_uint8.raw")
view = subset(info, dimensions={'grid': 150})   # 302^3 -> ~151^3
render(load(view), cmap='green')
```
Re-running an edited spec replaces the view in place. Discuss changes with the
human at the spec level — change one line, re-run, look.
