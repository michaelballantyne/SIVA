# DSL Reference

A spec is a plain Python file executed by `run_pipeline(spec_path)`. These names
are injected into the spec's namespace — **do not import them**. Every verb that
returns a `DatasetInfo` can be chained.

## inspect(filepath) -> DatasetInfo
Reads metadata only (variables, dimensions, attributes); no bulk arrays. The
format is auto-detected by the adapter registry (see
`vislang://instructions/adapters`). Raises `UnsupportedFormatError` if nothing
can read it.

## load(info, variables=None, dimensions=None) -> DatasetInfo
Populates `info.data` with numpy arrays. `variables=None` loads all.
`dimensions` subselects:
- `{'particles': 0.1}` — random 10% of particles
- `{'particles': 5000}` — random 5000 particles
- `{'particles': slice(0, 1000)}` — first 1000
- `{'particles': slice(None, None, 10)}` — every 10th
- `{'grid': 64}` — stride a 3-D grid to ~64 cells per axis
- `{'grid': 0.25}` — keep ~25% of cells per axis

Selection is pushed into the read where the library supports it, else read-full
then sliced. Always stride large grids before `render` (see rendering doc).

## download(remote_source, local_path) -> path
Fetch a remote dataset to a local path; returns the local path string.

## compress(info, variables, error_bound) -> DatasetInfo
Error-bounded compression of selected variables.

## render(info, cmap=None, opacity=None, positions=None, subsample_factor=30, grid_size=128, reset_camera=False)
Serves a browser viewer and prints its URL. 3-D fields render as k3d volumes
(headless-safe); particle/point data uses the scene + render_server path.
- `cmap` — `'green'` (custom ramp), any matplotlib name (`'viridis'`, `'hot'`,
  `'plasma'`, …), or `None` (default per-field colormaps).
- `opacity` — override the k3d opacity transfer function (flat `[t, a, …]`).
- `positions` — `('x','y','z')` for particles when coords can't be auto-detected.
See `vislang://instructions/rendering` for the viewing model and SSH tunnel.

## Example spec
```python
# Inspect -> load (strided for the browser) -> render in green.
info   = inspect("/path/to/csafe_heptane_302x302x302_uint8.raw")
loaded = load(info, dimensions={'grid': 150})   # 302^3 -> ~151^3
render(loaded, cmap='green')
```
Re-running an edited spec replaces the view in place. Discuss changes with the
human at the spec level — change one line, re-run, look.
