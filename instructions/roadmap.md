# Roadmap

Where VisLang is heading, so today's choices stay consistent with it.

## Query DSL with predicate pushdown — now realized
The declarative DSL this roadmap used to anticipate is **built**. Specs are now
written from *forms* (`source/fields/region/subsample/timestep/filter/compress/
save/render`) that build an **AST**; an interpreter (`planner.py`) inspects the
source, static-checks the request against the schema *before any read*, **fuses**
the narrowing into one selection (`narrowing.py`), and lowers it to physical reads
— pushing crop+stride into HDF5/FITS hyperslabs where the library allows. `load`
left the spec grammar to become an executor step (`materialize`), exactly as
planned; `subset`/`load`/`download`/`establish_connection` are hidden physical ops.

The static check *is* the "verify the query against the schema before any read"
idea — typed against `DatasetInfo`, raising on a missing axis/variable or an
out-of-bounds range with no bulk read. See `vislang://instructions/soundness`.

## Remaining work (staged)
Forms that parse and static-check but aren't materialized yet (they raise a clear
message), and known optimizations:
- **Grid value-filter** (`filter` on a 3-D field) via voxel NaN-masking — point/
  table filtering works today.
- **In-file timesteps**: HDF5 step groups (`/Step#N`) and a leading time axis.
  Series-of-files (`source("…snap_*.h5")` + `timestep(i)`) works today.
- **World-space `region`** on grids (physical coords via origin/spacing/extent) —
  index-space works today. Keep `AxisRange` interpretation-agnostic so this is a
  converter in front, not a change to the physical layer.
- **yt cropped covering-grid**: build the covering grid over the cropped edges so a
  region pushes into yt instead of read-full-then-crop.
- **Remote timestep series** (a glob over `ssh://`) and **connection reuse** so a
  series authenticates/prompts once across many `transfer` calls.

## Rendering
- Port the particle/point path to a headless k3d renderer too (today it still uses
  the trame `render_server`, blank on GL-less nodes).
- Optionally restore live, camera-preserving updates on top of the k3d snapshot.

## Guiding constraints (unchanged)
Keep the soundness gate (`vislang://instructions/soundness`) in front of every new
LLM use, and keep `DatasetInfo` as the format boundary so new formats and new
query features compose without touching each other.
