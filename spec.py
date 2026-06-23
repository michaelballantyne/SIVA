# Render Nyx dataset: baryon_density, dark_matter_density, temperature
# Target: under 256 MB browser payload
info = inspect('/projects/exasky/data/nyx/highz/512/NVB_C009_l10n512_S12345T692_z42.hdf5')

# Subset to the three fields we want, and stride to ~256 grid cells per axis
# 3 fields × 256³ × 4 bytes ≈ 201 MB
narrowed = subset(info,
    variables=[
        'native_fields/baryon_density',
        'native_fields/dark_matter_density',
        'native_fields/temperature'
    ],
    dimensions={'grid': 256}
)

# Load and render
render(load(narrowed))
