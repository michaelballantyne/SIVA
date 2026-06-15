# ParaView example datasets

ParaView ships a folder of small reference datasets under
`Contents/examples` inside its app bundle. Notable for SIVA use:

- `headsq.vti` — 256×256×94 head CT volume, the classic Slicer/VTK demo
  scan. **HU calibration is empirical** — see
  [../headsq-calibration.md](../headsq-calibration.md).
- `disk_out_ref.ex2` — Exodus II flow data over a disk
- `can.ex2` — Exodus II crushed-can simulation
- `bake.e` — small Exodus thermal example

## Setup

Install ParaView from <https://www.paraview.org/download/>, then symlink
the examples folder into `data/`:

```bash
ln -s /Applications/ParaView-X.Y.Z.app/Contents/examples \
      datasets/paraview-examples/data
```

(Adjust the source path for your ParaView version and platform — Linux
typically lives under `/opt/paraview-*/share/paraview-*/examples`,
Windows under `C:\Program Files\ParaView X.Y.Z\examples`.)

After symlinking, files are addressable as
`datasets/paraview-examples/data/headsq.vti` etc. The `data/` symlink is
gitignored.
