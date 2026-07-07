# SIVA demos

This folder holds **runnable example pipelines** that reproduce the examples
from the SIVA VIS 2026 paper using **current** SIVA syntax. Unlike a frozen
archive, these specs track the live DSL: if a form is renamed or removed, the
demos are updated to match so they keep constructing and rendering.

The exact version of the pipelines as described in the published paper is
preserved at the paper's release git tag; check `git tag` for the tag name.
Use that tag if you want the code exactly as the paper describes it. The copy
here is the maintained, up-to-date version.

## Flagship: `wildfire-video-demo/`

`wildfire-video-demo/` reproduces the paper's demo video — a set of views of
the HIGRAD/FIRETEC wildfire simulation (`datasets/wildfire/`, a ~1.1 GB
StructuredGrid). Each `view-*.py` file is a self-contained SIVA spec:

- `view-main.py` — terrain fuel density, a temperature fire isosurface, and
  wind-vector glyphs.
- `view-omega-x.py` — a longitudinal (ω_x) vorticity slice with a fire outline.
- `view-omega-x-plume.py` — vertical vorticity (ω_z) on a temperature plume.
- `view-omega-y.py` — a lateral (ω_y) vorticity slice.
- `view-vorticity.py` — a vertical vorticity (ω_z) slab near the ground.

The committed `latest_*.png` images and `report.md` are the demo's rendered
output and writeup.

To run a view, point the SIVA server at this directory (so it can find the
dataset) and load the spec; see the top-level `README.md` and `CLAUDE.md` for
launch instructions. The dataset must be downloaded first
(`datasets/wildfire/download.sh`).

## Planned: paper-figures

A `paper-figures/` folder reproducing the static figures from the paper is
planned but not yet added.

## CI coverage

Every `demos/**/view-*.py` spec is construct-tested by `tests/test_demos.py`.
`construct()` parses and freezes a spec without building VTK or reading data,
so the test is fast and data-free — it catches DSL drift (a spec referencing a
removed or renamed form) on every commit, which is what keeps these demos from
silently rotting.
