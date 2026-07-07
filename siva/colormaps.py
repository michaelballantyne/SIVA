"""Color map presets for visualization."""

import vtk


def _coerce_color(c):
    """Return (r, g, b) floats in [0,1] from a color name, hex string, or tuple.

    Accepts:
        - A string color name (e.g. "white", "red", "yellow").
        - A hex string (e.g. "#ff8800").
        - An RGB tuple/list of three floats (e.g. (1, 0, 0)).

    Tuple/list inputs must have exactly 3 elements; values are clamped to [0, 1].
    A 4-tuple (r, g, b, a) is accepted but the alpha component is silently dropped.
    Sequences with fewer than 3 elements fall back to white.
    Unknown strings, None, and other non-string/non-sequence inputs fall back to white.
    """
    if c is None:
        return (1, 1, 1)
    if isinstance(c, (tuple, list)):
        if len(c) < 3:
            return (1, 1, 1)
        r, g, b = c[0], c[1], c[2]
        return (max(0.0, min(1.0, float(r))),
                max(0.0, min(1.0, float(g))),
                max(0.0, min(1.0, float(b))))
    named = {
        "white": (1, 1, 1),
        "black": (0, 0, 0),
        "red": (1, 0, 0),
        "green": (0, 1, 0),
        "blue": (0, 0, 1),
        "yellow": (1, 1, 0),
        "cyan": (0, 1, 1),
        "magenta": (1, 0, 1),
        "orange": (1, 0.5, 0),
        "purple": (0.5, 0, 0.5),
        "gray": (0.5, 0.5, 0.5),
        "grey": (0.5, 0.5, 0.5),
        "pink": (1, 0.75, 0.8),
        "lime": (0, 1, 0),
        "brown": (0.65, 0.16, 0.16),
    }
    s = c.strip().lower()
    if s in named:
        return named[s]
    if s.startswith("#") and len(s) == 7:
        r = int(s[1:3], 16) / 255.0
        g = int(s[3:5], 16) / 255.0
        b = int(s[5:7], 16) / 255.0
        return (r, g, b)
    return (1, 1, 1)


# Named presets that can be used in show() via lut="preset_name"
PRESETS = {
    "cool_to_warm": {
        "colors": [
            (0.0, 0.23, 0.30, 0.75),   # cool blue
            (0.5, 0.87, 0.87, 0.87),   # neutral gray
            (1.0, 0.71, 0.02, 0.15),   # warm red
        ]
    },
    "oxygen": {
        "colors": [
            (0.0, 0.8, 0.0, 0.0),      # red (depleted)
            (0.3, 0.9, 0.5, 0.0),      # orange
            (0.5, 0.9, 0.9, 0.3),      # yellow
            (0.7, 0.3, 0.7, 0.3),      # green
            (1.0, 0.1, 0.3, 0.6),      # blue (ambient)
        ]
    },
    "heat": {
        "colors": [
            (0.0, 0.1, 0.1, 0.5),      # cool blue (negative)
            (0.4, 0.2, 0.2, 0.3),      # dark
            (0.5, 0.3, 0.3, 0.3),      # neutral gray
            (0.6, 0.6, 0.3, 0.1),      # warm
            (1.0, 1.0, 0.8, 0.2),      # hot yellow
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


# Opacity presets for volume rendering of specific fields
OPACITY_PRESETS = {
    "fire": [(298, 0.0), (340, 0.0), (400, 0.03), (500, 0.08), (700, 0.2), (1000, 0.5), (1200, 0.7)],
    "vorticity": [(0.0, 0.0), (0.5, 0.0), (1.0, 0.005), (2.0, 0.02), (3.5, 0.1), (5.0, 0.3)],
    "o2_depletion": [(0.086, 0.6), (0.15, 0.3), (0.20, 0.1), (0.22, 0.02)],
    "ct_bone": [(0, 0.0), (30, 0.0), (80, 0.01), (120, 0.05), (180, 0.2), (255, 0.6)],
    "ct_tissue": [(0, 0.0), (20, 0.0), (40, 0.02), (80, 0.05), (120, 0.1), (200, 0.3), (255, 0.5)],
}


# Field-specific defaults: suggested colormap and scalar range for known fields.
# When color_by matches a known field and no lut/scalar_range is given, these
# are used as intelligent defaults. Kept empty by default — domain-specific
# defaults belong in domain documentation (see domains/ directory).
FIELD_DEFAULTS = {}


def build_color_transfer_function(config, scalar_range=None):
    """Build a vtkColorTransferFunction from a preset name, config dict, or
    explicit list of (value, r, g, b) control points.

    Args:
        config: One of:
            - A string preset name.
            - A list of (value, r, g, b) tuples — absolute scalar values; no
              rescale by ``scalar_range`` is applied.
            - A dict with ``colors`` (positions 0-1, rescaled to ``scalar_range``)
              or ``hue_range`` / ``saturation_range`` / ``value_range`` (HSV).
        scalar_range: (min, max) tuple; positions in dict configs are mapped
            from [0,1] to this range.

    Returns:
        vtkColorTransferFunction
    """
    if isinstance(config, list):
        ctf = vtk.vtkColorTransferFunction()
        ctf.SetColorSpaceToRGB()  # explicit linear-RGB interpolation between control points
        for value, r, g, b in config:
            ctf.AddRGBPoint(value, r, g, b)
        return ctf

    if isinstance(config, str):
        if config not in PRESETS:
            raise ValueError(f"Unknown preset '{config}'. Available: {sorted(PRESETS.keys())}")
        config = PRESETS[config]

    ctf = vtk.vtkColorTransferFunction()
    lo = scalar_range[0] if scalar_range else 0.0
    hi = scalar_range[1] if scalar_range else 1.0

    if "colors" in config:
        for pos, r, g, b in config["colors"]:
            val = lo + pos * (hi - lo)
            ctf.AddRGBPoint(val, r, g, b)
    else:
        # HSV-based: sample an HSV ramp and add RGB points
        hue_range = config.get("hue_range", (0.0, 0.67))
        sat_range = config.get("saturation_range", (1.0, 1.0))
        val_range = config.get("value_range", (1.0, 1.0))
        n = 64
        import colorsys
        for i in range(n):
            t = i / (n - 1)
            h = hue_range[0] + t * (hue_range[1] - hue_range[0])
            s = sat_range[0] + t * (sat_range[1] - sat_range[0])
            v = val_range[0] + t * (val_range[1] - val_range[0])
            r, g, b = colorsys.hsv_to_rgb(h, s, v)
            scalar_val = lo + t * (hi - lo)
            ctf.AddRGBPoint(scalar_val, r, g, b)

    return ctf


def build_opacity_function(config, scalar_range=None, opacity_scale=1.0):
    """Build a vtkPiecewiseFunction for volume opacity.

    Args:
        config: One of:
            - A list of (value, opacity) control points
            - A string preset: "ramp_up", "gaussian", "step", or field-specific
              presets like "fire", "vorticity", "o2_depletion", "ct_bone", "ct_tissue"
            - None for a default ramp
        scalar_range: (min, max) tuple for the data range.
        opacity_scale: Global multiplier applied to all opacity values.

    Returns:
        vtkPiecewiseFunction
    """
    otf = vtk.vtkPiecewiseFunction()
    lo = scalar_range[0] if scalar_range else 0.0
    hi = scalar_range[1] if scalar_range else 1.0

    if config is None:
        # Default: linear ramp from 0 to opacity_scale
        otf.AddPoint(lo, 0.0)
        otf.AddPoint(hi, 1.0 * opacity_scale)
    elif isinstance(config, str):
        if config == "ramp_up":
            otf.AddPoint(lo, 0.0)
            otf.AddPoint(hi, 1.0 * opacity_scale)
        elif config == "gaussian":
            mid = (lo + hi) / 2.0
            quarter = (hi - lo) / 4.0
            otf.AddPoint(lo, 0.0)
            otf.AddPoint(mid - quarter, 0.05 * opacity_scale)
            otf.AddPoint(mid, 1.0 * opacity_scale)
            otf.AddPoint(mid + quarter, 0.05 * opacity_scale)
            otf.AddPoint(hi, 0.0)
        elif config == "step":
            mid = (lo + hi) / 2.0
            otf.AddPoint(lo, 0.0)
            otf.AddPoint(mid - 0.001 * (hi - lo), 0.0)
            otf.AddPoint(mid, 1.0 * opacity_scale)
            otf.AddPoint(hi, 1.0 * opacity_scale)
        elif config in OPACITY_PRESETS:
            # Field-specific opacity preset - use control points directly
            for value, opacity in OPACITY_PRESETS[config]:
                otf.AddPoint(value, opacity * opacity_scale)
        else:
            available = ["ramp_up", "gaussian", "step"] + sorted(OPACITY_PRESETS.keys())
            raise ValueError(f"Unknown opacity preset '{config}'. Available: {available}")
    elif isinstance(config, list):
        for value, opacity in config:
            otf.AddPoint(value, opacity * opacity_scale)
    else:
        raise ValueError(f"Invalid opacity_function config: {config}")

    return otf


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
