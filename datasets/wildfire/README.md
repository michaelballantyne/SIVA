# Wildfire Simulation Dataset

HIGRAD/FIRETEC coupled fire-atmosphere simulation from the 2022 IEEE SciVis Contest.

## Files

- `output.30000.vts` — Single timestep, 600x500x61 structured grid, ~1.1 GB, originally from https://oceans11.lanl.gov/firetec/mountain_backcurve40/output.30000.vts
- `scivis-report_8947f.pdf` — Contest challenge description
- `2022_IEEE_Scientific_Visualization_Contest_Winner_...pdf` — Winning entry report

## Download

```bash
./download.sh
```

Files are downloaded to `data/` (gitignored).

> **Note:** `output.30000.vts` is ~1.1 GB, so this is a large download (and a
> sizable file to load). Expect it to take a while on a slow connection, and
> make sure you have the disk space and memory headroom before fetching it.

## Domain guide

See `domains/wildfire.md` for field interpretations, key thresholds, and
visualization patterns specific to this dataset.
