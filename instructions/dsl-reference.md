# DSL Reference

A spec is a plain Python file — always `spec.py`, edited in place — executed by
`run_pipeline("spec.py")`. The form names below are injected into the spec's
namespace — **do not import them**. Each form takes a node and returns a node, so
they chain. Calling a form runs nothing; it builds an AST node. Only a **sink**
(`render`, `save`) triggers execution.

How a run works: the interpreter walks the chain, inspects the `source`,
**static-checks** the request against the schema (raising before any bulk read),
**fuses** the narrowing forms into one read, then materializes and runs the sink.
A spec with no sink is dry-run (the inferred plan is reported; nothing is read).

## source(uri, positions=None) -> node
Starts a chain; names the dataset.
- `uri` — a local path; a **glob** like `".../snap_*.h5"` (a timestep series —
  see `timestep`); or remote `ssh://[user@]host/path` / `user@host:/path`
  (fetched to a local cache first).
- `positions` — `('x','y','z')` override naming the coordinate variables when
  auto-detection can't tell (point data with unusual names).

## fields(node, keep) -> node
Keep only `keep` (a name or list of names); the rest are dropped. Validated
against the schema (unknown names raise).

## region(node, x=(a,b), y=(c,d), …) -> node
Crop to a per-axis range.
- **Grids:** index-space `[a:b]` per named axis (`x`,`y`,`z` → axes 0,1,2). `None`
  is an open end. Pushed into the read as a hyperslab where the format allows
  (HDF5/FITS); else read-then-crop.
- **Point data:** a **world-coordinate** bounding box on the coordinate variables
  (`info.positions`) — keep points whose coords fall in the box.

## subsample(node, factor) | subsample(node, x=…, y=…, z=…) -> node
Reduce resolution. A factor is an **int stride** (keep every f-th) or a **float
fraction** in (0,1]. A single `factor` is uniform; per-axis `x=/y=/z=` is for
grids. On point data use a single factor (stride or fraction of rows).
`region` and `subsample` on the same grid compose into one strided crop.

## timestep(node, index) -> node
Select timestep `index` of a series. The `source` must be a glob that matched
multiple files (each file is one timestep); a single-file source raises. The
chosen file is read in place of step 0.

## filter(node, "var op value") -> node
Keep rows where the predicate holds, e.g. `"density > 0.5"`. Operators:
`< <= > >= == !=`; the right-hand side is a scalar. Point/table data only (the
predicate variable must still be present — don't `fields` it away first).
Multiple `filter`s AND together; combine with `region` (bbox) freely.

## compress(node, variables, error_bound, mode="auto") -> node
Error-bounded compression of the named variables (SPERR/Zstd, in-memory).
Storage only — it does **not** cheapen a render.

## save(node, path) -> (sink)
Write the materialized result to disk (currently `.npz` of the arrays).

## render(node, cmap=None, opacity=None) -> (sink)
Serve the headless k3d browser viewer; prints its URL. Renders everything the
node describes — narrow upstream to show less. `cmap` is `'green'`, a matplotlib
name, or `None`; `opacity` overrides the k3d transfer function. 3-D fields render
as volumes; point data as a cloud + density volume (coords from `info.positions`).
See `vislang://instructions/rendering`.

## MCP tools (called directly, not in the spec)
- `inspect(filepath, positions=None)` — the schema (variables, dimensions),
  metadata only. Use it to write the spec. Same engine as `source()`.
- `estimate_render_cost(filepath)` — predicted browser payload + disk cost.
- `run_pipeline(spec_path)` — execute the spec.

## Example spec
```python
# Crop a sub-box, stride it for the browser, color green.
render(subsample(region(source("/abs/heptane_302x302x302_uint8.raw"),
                        x=(0, 200), y=(0, 200)), 2),
       cmap="green")
```
Re-running an edited spec replaces the view in place. Discuss at the spec level —
change one line, re-run, look.

## Staged — parse and static-check but not yet materialized
These build valid nodes but raise a clear message at lowering until a later phase:
- `filter` on **grid** fields (voxel NaN-masking) — works on point/table data today.
- **in-file** timesteps (HDF5 step groups / a leading time axis) — series-of-files works today.
- **world-space** `region` on grids (physical coords via origin/spacing) — index-space today.
- remote **timestep series** (a glob over `ssh://`).
