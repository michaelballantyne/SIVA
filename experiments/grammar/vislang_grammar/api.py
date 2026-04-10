"""User-facing API for the 3D visualization grammar.

This module provides the top-level functions that a user calls to build a
visualization. All functions return declarative spec objects — nothing in VTK
is created or executed until compile_scene() is called.

Typical usage:

    from vislang_grammar import data, show, layer, encode, scale_color, scale_opacity
    from vislang_grammar import rep_volume, rep_isosurface, rep_surface, rep_outline
    from vislang_grammar import where

    bonsai = data("bonsai.vti")
    density_color = scale_color("density", range=[20, 200], colormap="terrain")

    scene = layer(
        show(bonsai | rep_volume("density"),
             encode(color=density_color,
                    opacity=scale_opacity("density", [(0, 0.0), (50, 0.2), (200, 0.8)]),
                    shade=True,
                    legend="Density")),

        show(bonsai | rep_isosurface("density", at=80),
             encode(color=(0.6, 0.4, 0.2), opacity=0.3)),

        show(bonsai | rep_outline(),
             encode(color=(1, 1, 1), opacity=0.2)),
    )
"""

from typing import Any, Optional, Union

from .core import (
    DataRef, Encoding, LayerSpec, RepSpec, ScaleColor, ScaleOpacity, ShowResult
)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def data(filename: str) -> DataRef:
    """Create a lazy reference to a data file.

    The VTK reader is inferred from the file extension:
      .vts -> vtkXMLStructuredGridReader
      .vti -> vtkXMLImageDataReader
      .vtp -> vtkXMLPolyDataReader
      .vtu -> vtkXMLUnstructuredGridReader
      .vtk -> vtkGenericDataObjectReader
      .nrrd, .nhdr -> vtkNrrdReader

    No I/O happens until compile_scene() is called.

    Args:
        filename: Path to the data file. Can be absolute or relative to the
                  working directory when compile_scene() runs.

    Returns:
        DataRef — a lazy file reference that supports the pipe operator.

    Example:
        bonsai = data("bonsai.vti")
        fire = data("/path/to/output.30000.vts")
    """
    return DataRef(filename=filename)


# ---------------------------------------------------------------------------
# Scales
# ---------------------------------------------------------------------------

def scale_color(field: str, range: Optional[list] = None,
                colormap: str = "cool_to_warm") -> ScaleColor:
    """Create a color scale: maps a data field to a color ramp.

    Color scales are first-class objects that can be defined once and reused
    across multiple show() calls. This avoids repeating color configuration
    in every layer.

    Args:
        field: The data field to color by.
        range: [lo, hi] data range for color mapping. If None, the compiler
               uses the field's actual min/max from the data.
        colormap: Colormap name. VisLang presets: "cool_to_warm", "fire",
                  "terrain", "wind", "heat", "grayscale", "blue_to_red".
                  Also accepts VTK named colormaps.

    Returns:
        ScaleColor — a reusable color scale specification.

    Example:
        density_color = scale_color("density", range=[20, 200], colormap="terrain")
        theta_color = scale_color("theta", range=[500, 2000], colormap="hot")
    """
    return ScaleColor(field=field, range=range, colormap=colormap)


def scale_opacity(field: str,
                  control_points: Optional[list] = None,
                  preset: Optional[str] = None,
                  gradient_modulation: bool = False) -> ScaleOpacity:
    """Create an opacity scale (transfer function for volume rendering).

    Opacity scales specify how data values map to transparency. They are the
    GoG representation of a volume rendering transfer function — a first-class
    object rather than a raw list of tuples buried in show() kwargs.

    Provide either control_points or preset, not both.

    Args:
        field: The data field controlling opacity.
        control_points: List of (value, opacity) pairs. Values are in data units;
                        opacity is in [0, 1] where 0 is transparent, 1 is opaque.
        preset: Named preset from vislang.colormaps.OPACITY_PRESETS:
                "fire", "vorticity", "o2_depletion", "ct_bone", "ct_tissue".
                Generic presets: "ramp_up", "gaussian", "step".
        gradient_modulation: If True, regions with high spatial gradient (edges)
                             are rendered more opaque. Useful for edge enhancement.

    Returns:
        ScaleOpacity — a reusable transfer function specification.

    Example:
        # Explicit control points
        opacity = scale_opacity("density",
            control_points=[(0, 0.0), (50, 0.2), (200, 0.8)])

        # Named preset
        opacity = scale_opacity("density", preset="ct_bone")
    """
    if control_points is None and preset is None:
        preset = "ramp_up"
    return ScaleOpacity(
        field=field,
        control_points=control_points,
        preset=preset,
        gradient_modulation=gradient_modulation,
    )


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def encode(color: Any = None, opacity: Any = None,
           specular: float = 0.0, specular_power: float = 100.0,
           shade: bool = False, legend: Optional[str] = None,
           line_width: float = 1.0, point_size: float = 5.0) -> Encoding:
    """Create a visual encoding specification.

    Encodings map data values to visual channels (color, opacity, shininess,
    etc.). They are first-class objects, separate from what they apply to —
    define once, reuse across many show() calls.

    Args:
        color: Color specification. One of:
               - ScaleColor from scale_color(): field-driven color mapping
               - (r, g, b) tuple with values in [0, 1]: constant RGB color
               - None: use a default gray
        opacity: Opacity specification. One of:
                 - ScaleOpacity from scale_opacity(): transfer function for volumes
                 - float in [0, 1]: constant opacity (1.0 = fully opaque)
                 - None: use default (1.0, fully opaque)
        specular: Specular highlight intensity in [0, 1]. 0 = matte, 1 = mirror.
        specular_power: Specular power (shininess). Higher = tighter highlight.
        shade: If True, enable lighting/shading for volume rendering.
        legend: Label for the scalar bar legend. None means no legend.
        line_width: Line width in pixels, for outline/wireframe representations.
        point_size: Point size in pixels, for glyph/scatter representations.

    Returns:
        Encoding — a reusable visual channel mapping.

    Example:
        fire_enc = encode(
            color=scale_color("theta", range=[500, 2000], colormap="hot"),
            opacity=scale_opacity("theta", [(500, 0), (800, 0.05), (2000, 0.6)]),
            shade=True,
            legend="Theta (K)",
        )
        surface_enc = encode(color=(0.8, 0.6, 0.4), opacity=0.6, specular=0.3)
    """
    return Encoding(
        color=color,
        opacity=opacity,
        specular=specular,
        specular_power=specular_power,
        shade=shade,
        legend=legend,
        line_width=line_width,
        point_size=point_size,
    )


# ---------------------------------------------------------------------------
# Scene assembly
# ---------------------------------------------------------------------------

def show(pipeline, encoding: Optional[Encoding] = None) -> ShowResult:
    """Pair a data pipeline (with representation) with a visual encoding.

    show() is the atomic unit of a scene: it says "render this representation
    of this data, with this visual encoding." Multiple show() calls are
    composed into a scene via layer().

    Args:
        pipeline: A RepSpec — the result of piping data through transforms into
                  a rep_*() function. E.g.: bonsai | rep_volume("density")
        encoding: An Encoding from encode(), specifying visual channel mappings.
                  If None, a default encoding is used (gray, fully opaque).

    Returns:
        ShowResult — a (representation, encoding) pair ready for the compiler.

    Example:
        show(bonsai | rep_volume("density"),
             encode(color=density_color, opacity=density_opacity, shade=True))

        show(bonsai | rep_outline(),
             encode(color=(1, 1, 1), opacity=0.2))
    """
    if not isinstance(pipeline, RepSpec):
        raise TypeError(
            f"show() expects a RepSpec (the result of data | rep_*(...)), "
            f"got {type(pipeline).__name__}. "
            "Did you forget to pipe into a representation? "
            "Example: show(bonsai | rep_volume('density'), ...)"
        )
    if encoding is None:
        encoding = Encoding()
    return ShowResult(rep=pipeline, encoding=encoding)


def layer(*shows: ShowResult) -> LayerSpec:
    """Compose multiple show() results into a scene.

    All representations in the layer are rendered in the same viewport with
    the same camera. The layer() call makes the scene structure explicit and
    readable: you can see at a glance what the scene contains.

    Args:
        *shows: ShowResult objects from show() calls.

    Returns:
        LayerSpec — a scene specification ready for compile_scene().

    Example:
        scene = layer(
            show(bonsai | rep_volume("density"), encode(color=density_color)),
            show(bonsai | rep_isosurface("density", at=80), encode(color=(0.6, 0.4, 0.2))),
            show(bonsai | rep_outline(), encode(color=(1, 1, 1), opacity=0.2)),
        )
    """
    for i, s in enumerate(shows):
        if not isinstance(s, ShowResult):
            raise TypeError(
                f"layer() argument {i} is {type(s).__name__}, expected ShowResult. "
                "All arguments to layer() must come from show()."
            )
    return LayerSpec(shows=list(shows))


# ---------------------------------------------------------------------------
# Seed helpers (for streamlines)
# ---------------------------------------------------------------------------

def near(field: str, range: list, n: int = 40):
    """Specify seed points near a field value range for streamlines.

    Returns a seed specification used by rep_streamlines(). Seeds are placed
    at points where the given field is in the specified range — useful for
    seeding streamlines in the regions of scientific interest.

    Args:
        field: The scalar field to check against the range.
        range: [lo, hi] — place seeds where field is in this range.
        n: Number of seed points to place.

    Returns:
        A seed specification dict (interpreted by the compiler).

    Example:
        seeds = near("theta", [500, 2000], n=40)
        show(fire | derive("velocity", ["u","v","w"])
                  | rep_streamlines("velocity", seeds=seeds),
             encode(color=scale_color("velocity", [0, 30], "wind")))
    """
    lo, hi = range
    return {"kind": "near", "field": field, "lo": lo, "hi": hi, "n": n}
