# Synthetic Volume Dataset

A small, procedurally generated 64x64x64 regular grid intended for testing
and development. Unlike the wildfire dataset (curvilinear StructuredGrid with
terrain-following coordinates), this is a uniform ImageData on the unit cube
with isotropic spacing.

## Fields

| Name        | Type   | Description                                    |
|-------------|--------|------------------------------------------------|
| temperature | scalar | Gaussian blob centred at (0.5, 0.5, 0.5), peak 1000 |
| density     | scalar | Linear gradient along Z, range [0, 1.225]      |
| velocity    | vector | Rigid-body rotation about Z axis (divergence-free swirl) |

## Generation

```bash
bash download.sh        # from project root: bash datasets/synthetic/download.sh
```

The script runs `generate.py` which requires only Python 3 and VTK. Output
lands in `data/output.vti` (gitignored), roughly 3-5 MB compressed.

## Why this dataset exists

- Structurally different from the wildfire data (ImageData vs StructuredGrid)
- Predictable, analytically defined fields (useful for verifying filters and queries)
- Small and fast to generate (no network download required)
