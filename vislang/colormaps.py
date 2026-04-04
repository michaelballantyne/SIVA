"""Color map presets for visualization."""

import vtk

# Named presets that can be used in show() via lut="preset_name"
PRESETS = {
    "cool_to_warm": {
        "colors": [
            (0.0, 0.23, 0.30, 0.75),   # cool blue
            (0.5, 0.87, 0.87, 0.87),   # white
            (1.0, 0.71, 0.02, 0.15),   # warm red
        ]
    },
    "fire": {
        "colors": [
            (0.0, 0.0, 0.0, 0.0),      # black
            (0.2, 0.5, 0.0, 0.0),      # dark red
            (0.4, 1.0, 0.2, 0.0),      # red-orange
            (0.6, 1.0, 0.6, 0.0),      # orange-yellow
            (0.8, 1.0, 1.0, 0.3),      # bright yellow
            (1.0, 1.0, 1.0, 1.0),      # white
        ]
    },
    "terrain": {
        "colors": [
            (0.0, 0.10, 0.05, 0.02),   # dark brown (burned)
            (0.1, 0.30, 0.15, 0.05),   # brown
            (0.3, 0.20, 0.45, 0.10),   # olive green
            (0.5, 0.15, 0.55, 0.10),   # green
            (0.7, 0.10, 0.40, 0.08),   # darker green
            (1.0, 0.05, 0.30, 0.05),   # forest green
        ]
    },
    "blue_to_red": {
        "colors": [
            (0.0, 0.0, 0.0, 1.0),      # blue
            (0.25, 0.0, 0.5, 1.0),     # cyan-blue
            (0.5, 0.0, 1.0, 0.5),      # green-cyan
            (0.75, 1.0, 1.0, 0.0),     # yellow
            (1.0, 1.0, 0.0, 0.0),      # red
        ]
    },
    "wind": {
        "colors": [
            (0.0, 0.0, 0.2, 0.6),      # dark blue (reverse/slow)
            (0.3, 0.2, 0.6, 0.8),      # light blue
            (0.5, 0.6, 0.9, 0.6),      # light green
            (0.7, 0.9, 0.9, 0.2),      # yellow
            (1.0, 0.9, 0.2, 0.0),      # orange (fast)
        ]
    },
    "grayscale": {
        "colors": [
            (0.0, 0.0, 0.0, 0.0),
            (1.0, 1.0, 1.0, 1.0),
        ]
    },
}


def build_lut(config, scalar_range=None):
    """Build a vtkLookupTable from either a preset name or a config dict.

    Args:
        config: Either a string (preset name) or a dict with:
            - hue_range, saturation_range, value_range (HSV-based), or
            - colors: list of (position, r, g, b) control points
        scalar_range: (min, max) tuple for the table range
    """
    if isinstance(config, str):
        if config not in PRESETS:
            raise ValueError(f"Unknown preset '{config}'. Available: {sorted(PRESETS.keys())}")
        config = PRESETS[config]

    if "colors" in config:
        # Build a color transfer function and sample it
        ctf = vtk.vtkColorTransferFunction()
        for pos, r, g, b in config["colors"]:
            ctf.AddRGBPoint(pos, r, g, b)

        n = 256
        lut = vtk.vtkLookupTable()
        lut.SetNumberOfTableValues(n)
        for i in range(n):
            t = i / (n - 1)
            rgb = ctf.GetColor(t)
            alpha = config.get("alpha", 1.0) if isinstance(config.get("alpha"), (int, float)) else 1.0
            lut.SetTableValue(i, rgb[0], rgb[1], rgb[2], alpha)
        if scalar_range:
            lut.SetTableRange(*scalar_range)
        lut.Build()
        return lut
    else:
        # HSV-based LUT
        lut = vtk.vtkLookupTable()
        lut.SetNumberOfTableValues(256)
        if "hue_range" in config:
            lut.SetHueRange(*config["hue_range"])
        if "saturation_range" in config:
            lut.SetSaturationRange(*config["saturation_range"])
        if "value_range" in config:
            lut.SetValueRange(*config["value_range"])
        if "alpha_range" in config:
            lut.SetAlphaRange(*config["alpha_range"])
        if scalar_range:
            lut.SetTableRange(*scalar_range)
        lut.Build()
        return lut
