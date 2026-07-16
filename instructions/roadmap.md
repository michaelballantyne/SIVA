# Roadmap

Where VisLang is heading, so today's choices stay consistent with it.

## Query DSL with predicate pushdown — now realized
The declarative DSL this roadmap used to anticipate is **built**. Specs are now
written from *forms* (`source/fields/region/subsample/threshold/compress/
save/render`) that build an **AST**; an interpreter (`planner.py`) inspects the
source, static-checks the request against the schema *before any read*, classifies
every form on two axes (structural-vs-computed for pushdown; absolute-vs-relative
for reordering — `subsample` is the lone order-sensitive form), **fuses** the
structural narrowing into one selection (`narrowing.py`), and applies the computed
cuts post-read in written order — pushing crop+stride into HDF5/FITS hyperslabs
where the library allows. `load` left the spec grammar to become an executor step
(`materialize`); `subset`/`load`/`download`/`establish_connection` are hidden
physical ops.

The static check *is* the "verify the query against the schema before any read"
idea — typed against `DatasetInfo`, raising on a missing axis/variable or an
out-of-bounds range with no bulk read. See `vislang://instructions/soundness`.

## Remaining work (staged)
Forms that parse and static-check but aren't materialized yet (they raise a clear
message), and known optimizations:
- **World-space `region`** on grids (physical coords via origin/spacing/extent) —
  index-space works today. Keep `AxisRange` interpretation-agnostic so this is a
  converter in front, not a change to the physical layer.
- **yt cropped covering-grid**: build the covering grid over the cropped edges so a
  region pushes into yt instead of read-full-then-crop.
- **Remote compute** (`REMOTE_COMPUTE_PLAN.md`): push the narrowing prefix to the
  data over ssh (Apptainer reducer), keep a local extent catalog so incremental
  requests fetch only the delta. Timesteps (in-file step groups or a series
  form) are out for now — `source` is strictly single-file.

## Rendering
- Port the particle/point path to a headless k3d renderer too (today it still uses
  the trame `render_server`, blank on GL-less nodes).
- Optionally restore live, camera-preserving updates on top of the k3d snapshot.

## Guiding constraints (unchanged)
Keep the soundness gate (`vislang://instructions/soundness`) in front of every new
LLM use, and keep `DatasetInfo` as the format boundary so new formats and new
query features compose without touching each other.
