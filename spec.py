render(
    subsample(
        fields(
            region(
                source("ssh://cn623/projects/exasky/data/nyx/highz/512/NVB_C009_l10n512_S12345T692_z42.hdf5"),
                x=(128, 384), y=(128, 384), z=(128, 384)),
            ["baryon_density"]),
        4),
    cmap="viridis")
