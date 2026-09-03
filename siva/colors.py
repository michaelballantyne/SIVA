"""Named-color resolution shared by ``background()`` and ``show(color=)``.

Both DSL forms take a color that can be an explicit ``(r, g, b)`` triple in
0.0-1.0, one of SIVA's own background presets, a ``vtkNamedColors`` name (case-
and separator-insensitive), or a ``#rrggbb`` hex string. :func:`resolve_color`
is the single place that turns any of those into a validated ``(r, g, b)``
float triple -- errors are explicit rather than silently falling back to a
default color, so a typo'd name is caught at build time instead of quietly
rendering the wrong color.

This is distinct from ``siva.colormaps._coerce_color``, an older, more
forgiving helper used by ``text()``/``annotate()``/``axes()`` colors that falls
back to white on anything it doesn't recognize.
"""

import difflib

import vtk

#: ``background()``'s built-in presets. Kept here (rather than duplicated in
#: ``siva.dsl.PipelineBuilder.background``) so both ``background()`` and
#: ``show(color=)`` resolve the same names, and so ``scripts/gen_spec_api.py``
#: can read the preset list without parsing method source.
BACKGROUND_PRESETS = {
    "dark": (0.02, 0.02, 0.06),
    "light": (0.85, 0.85, 0.9),
    "black": (0.0, 0.0, 0.0),
    "white": (1.0, 1.0, 1.0),
}

_NAMED_COLORS = vtk.vtkNamedColors()

_normalized_names = None  # lazily-built: normalized name -> vtkNamedColors canonical name


def _normalize(name):
    """Lowercase and strip spaces/underscores so 'Slate Gray' == 'slate_gray'."""
    return name.strip().lower().replace(" ", "").replace("_", "")


def _named_color_index():
    """Build (and cache) the normalized-name -> canonical vtkNamedColors name map."""
    global _normalized_names
    if _normalized_names is None:
        names = [n for n in _NAMED_COLORS.GetColorNames().split("\n") if n]
        index = {}
        for name in names:
            index.setdefault(_normalize(name), name)
        _normalized_names = index
    return _normalized_names


def _parse_hex(value):
    """Return an (r, g, b) float triple for a '#rrggbb' string, or None."""
    if not value.startswith("#"):
        return None
    digits = value[1:]
    if len(digits) != 6 or any(c not in "0123456789abcdefABCDEF" for c in digits):
        return None
    return tuple(int(digits[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def resolve_color(value):
    """Resolve a color to an ``(r, g, b)`` float triple in 0.0-1.0.

    Accepts:
        - An ``(r, g, b)`` sequence of three floats already in 0.0-1.0.
        - A ``background()`` preset name: ``"dark"``, ``"light"``, ``"black"``,
          ``"white"``.
        - Any ``vtkNamedColors`` name, case- and separator-insensitive
          (``"wheat"``, ``"Slate Gray"``, ``"slate_gray"`` all resolve).
        - A ``"#rrggbb"`` hex string.

    Returns:
        tuple[float, float, float]: The color as ``(r, g, b)`` in 0.0-1.0.

    Raises:
        ValueError: If *value* is not a valid triple, preset, named color, or
            hex string. Unknown strings get up to three did-you-mean
            suggestions drawn from the ``vtkNamedColors`` name list.
    """
    if isinstance(value, (tuple, list)):
        if len(value) != 3:
            raise ValueError(
                f"Color triple must have exactly 3 components (r, g, b), got {len(value)}: {value!r}."
            )
        try:
            components = tuple(float(c) for c in value)
        except (TypeError, ValueError):
            raise ValueError(
                f"Color triple components must be floats in 0.0-1.0, got {value!r}."
            )
        if any(c < 0.0 or c > 1.0 for c in components):
            raise ValueError(
                f"Color triple components must be floats in 0.0-1.0 (not 0-255), got {value!r}."
            )
        return components

    if not isinstance(value, str):
        raise ValueError(
            "Color must be an (r, g, b) float triple or a color name/hex string, "
            f"got {value!r}."
        )

    if value in BACKGROUND_PRESETS:
        return BACKGROUND_PRESETS[value]

    index = _named_color_index()
    normalized = _normalize(value)
    if normalized in index:
        canonical = index[normalized]
        c = _NAMED_COLORS.GetColor3d(canonical)
        return (c[0], c[1], c[2])

    hex_rgb = _parse_hex(value)
    if hex_rgb is not None:
        return hex_rgb

    suggestions = difflib.get_close_matches(normalized, index.keys(), n=3, cutoff=0.6)
    suggestion_names = [index[s] for s in suggestions]
    msg = (
        f"Unknown color '{value}'. Available presets: {sorted(BACKGROUND_PRESETS)}."
    )
    if suggestion_names:
        msg += f" Did you mean: {', '.join(suggestion_names)}?"
    msg += (
        " Any vtkNamedColors name (e.g. 'wheat', 'slate_gray') or a '#rrggbb' "
        "hex string also works."
    )
    raise ValueError(msg)
