"""Core grammar types for the 3D scientific visualization grammar.

These are pure data objects — no VTK is imported or instantiated here.
Everything is lazy/declarative until compile_scene() is called.

The grammar borrows from the Grammar of Graphics (Wilkinson 2005, Wickham 2010)
and adapts it for 3D scientific visualization over spatial fields.

Design:
  - DataRef: lazy reference to a data file
  - Transform: a single data transformation (threshold, gradient, etc.)
  - TransformChain: an ordered sequence of transforms applied to a data source
  - RepSpec: a representation algorithm (volume, isosurface, etc.) bound to a pipeline
  - Encoding: visual channel mappings (color, opacity, specular, ...)
  - ScaleColor: color scale specification
  - ScaleOpacity: opacity/transfer function specification
  - ShowResult: a (RepSpec, Encoding) pair ready for compilation
  - LayerSpec: a composed scene of multiple ShowResults
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional, Union


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class DataRef:
    """Lazy reference to a data file. The VTK reader is inferred from extension.

    Supports the pipe operator:
      data_ref | transform  ->  TransformChain
      data_ref | rep_*()    ->  RepSpec
    """
    filename: str

    def __or__(self, other):
        """Pipe: DataRef | Transform -> TransformChain, DataRef | RepSpec -> RepSpec."""
        if isinstance(other, Transform):
            return TransformChain(source=self, transforms=[other])
        elif isinstance(other, RepSpec):
            # Bind this data source to the representation
            return other._bind(self, [])
        else:
            raise TypeError(
                f"Cannot pipe DataRef into {type(other).__name__}. "
                "Expected a Transform or RepSpec."
            )

    def __repr__(self):
        return f"DataRef({self.filename!r})"


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------

@dataclass
class Transform:
    """A single data transformation.

    Transforms are the 3D analog of ggplot2's stats: they operate on field
    data before the representation algorithm runs. Examples: threshold,
    gradient, derived field computation.

    Each Transform has a kind (string) and params (dict). The compiler
    maps kind to specific VTK filters.
    """
    kind: str        # e.g. "where", "derive", "gradient", "slice_grid", "clip", "subsample"
    params: dict     # keyword arguments specific to the transform kind

    def __repr__(self):
        return f"Transform({self.kind!r}, {self.params!r})"


@dataclass
class TransformChain:
    """An ordered sequence of transforms applied to a data source.

    Supports the pipe operator:
      chain | transform  ->  new TransformChain (appended)
      chain | rep_*()    ->  RepSpec (representation bound to this pipeline)
    """
    source: DataRef
    transforms: list  # list of Transform

    def __or__(self, other):
        """Pipe: Chain | Transform -> new Chain, Chain | RepSpec -> RepSpec."""
        if isinstance(other, Transform):
            return TransformChain(
                source=self.source,
                transforms=self.transforms + [other]
            )
        elif isinstance(other, RepSpec):
            return other._bind(self.source, self.transforms)
        else:
            raise TypeError(
                f"Cannot pipe TransformChain into {type(other).__name__}. "
                "Expected a Transform or RepSpec."
            )

    def __repr__(self):
        return f"TransformChain(source={self.source!r}, transforms={self.transforms!r})"


# ---------------------------------------------------------------------------
# Representations
# ---------------------------------------------------------------------------

@dataclass
class RepSpec:
    """Specification of a representation algorithm.

    Representations are the 3D analog of geoms: they define how field data
    is turned into visible geometry. Unlike 2D geoms (which map rows to marks),
    3D reps run algorithms over the full field:
      - Volume rendering casts rays through a 3D grid
      - Isosurface runs marching cubes
      - Streamlines integrate trajectories through a vector field

    A RepSpec is initially "unbound" (no data source attached). Piping a
    DataRef or TransformChain into it binds the data.

    kind: "volume", "isosurface", "streamlines", "glyphs", "surface", "outline"
    params: representation-specific parameters (field name, isovalue, etc.)
    source: the DataRef (set when bound via pipe)
    transforms: the list of Transform objects (set when bound via pipe)
    """
    kind: str
    params: dict
    source: Optional[DataRef] = field(default=None, repr=False)
    transforms: list = field(default_factory=list, repr=False)

    def _bind(self, source: DataRef, transforms: list) -> RepSpec:
        """Return a new RepSpec with data source and transforms attached."""
        return RepSpec(
            kind=self.kind,
            params=self.params,
            source=source,
            transforms=transforms,
        )

    def __repr__(self):
        bound = f", source={self.source!r}" if self.source else ""
        return f"RepSpec({self.kind!r}, {self.params!r}{bound})"


# ---------------------------------------------------------------------------
# Scales
# ---------------------------------------------------------------------------

@dataclass
class ScaleColor:
    """Color scale specification: maps a data field to a color ramp.

    Args:
        field: The data field to color by.
        range: [min, max] data range. If None, uses the field's full range.
        colormap: Name of the colormap preset (see vislang.colormaps.PRESETS)
                  or a VTK-recognized colormap name like "viridis", "hot", etc.
    """
    field: str
    range: Optional[list]   # [lo, hi] or None for auto
    colormap: str = "cool_to_warm"

    def __repr__(self):
        return f"ScaleColor({self.field!r}, range={self.range!r}, colormap={self.colormap!r})"


@dataclass
class ScaleOpacity:
    """Opacity scale specification: maps a data field to opacity values.

    This is the GoG representation of a volume rendering transfer function.
    Instead of a raw list of tuples buried in show() kwargs, it is a
    first-class, inspectable, reusable object.

    Args:
        field: The data field controlling opacity.
        control_points: List of (value, opacity) pairs defining the transfer
                        function. Opacity in [0, 1].
        preset: Named opacity preset (e.g. "fire", "ct_bone") from
                vislang.colormaps.OPACITY_PRESETS. Used if control_points is None.
        gradient_modulation: If True, edges (high gradient) are made more opaque.
    """
    field: str
    control_points: Optional[list] = None   # [(value, opacity), ...]
    preset: Optional[str] = None
    gradient_modulation: bool = False

    def __repr__(self):
        if self.preset:
            return f"ScaleOpacity({self.field!r}, preset={self.preset!r})"
        return f"ScaleOpacity({self.field!r}, control_points={self.control_points!r})"


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

@dataclass
class Encoding:
    """Visual channel mapping for a representation.

    Encodings are first-class objects that can be defined once and reused
    across multiple show() calls. This is the key GoG insight: separate
    the encoding (how data maps to visual channels) from the representation
    (what geometry is produced from the field).

    Args:
        color: Color specification. One of:
               - ScaleColor: field-driven color mapping
               - (r, g, b) tuple: constant RGB color
               - None: use default
        opacity: Opacity specification. One of:
                 - ScaleOpacity: field-driven opacity (transfer function for volumes)
                 - float in [0, 1]: constant opacity
                 - None: use default (1.0)
        specular: Specular highlight intensity in [0, 1].
        specular_power: Specular power (shininess).
        shade: If True, enable shading (lighting) for volume rendering.
        legend: Label for the scalar bar/legend. None means no legend.
        line_width: Line width (for wireframe / outline representations).
        point_size: Point size (for glyph representations).
    """
    color: Any = None          # ScaleColor | (r, g, b) | None
    opacity: Any = None        # ScaleOpacity | float | None
    specular: float = 0.0
    specular_power: float = 100.0
    shade: bool = False
    legend: Optional[str] = None
    line_width: float = 1.0
    point_size: float = 5.0

    def __repr__(self):
        parts = []
        if self.color is not None:
            parts.append(f"color={self.color!r}")
        if self.opacity is not None:
            parts.append(f"opacity={self.opacity!r}")
        if self.specular:
            parts.append(f"specular={self.specular!r}")
        if self.shade:
            parts.append("shade=True")
        if self.legend:
            parts.append(f"legend={self.legend!r}")
        return f"Encoding({', '.join(parts)})"


# ---------------------------------------------------------------------------
# Scene composition
# ---------------------------------------------------------------------------

@dataclass
class ShowResult:
    """Result of show(pipeline, encoding): a representation paired with an encoding.

    This is the atomic unit of a scene layer. It carries everything needed
    to build one VTK actor (or volume): what data to use, what transforms to
    apply, what representation algorithm to run, and how to visually encode it.
    """
    rep: RepSpec
    encoding: Encoding

    def __repr__(self):
        return f"ShowResult(rep={self.rep!r}, encoding={self.encoding!r})"


@dataclass
class LayerSpec:
    """A composed scene of multiple ShowResults.

    layer() takes N show() results and composes them into a single scene
    where all representations are rendered together in the same viewport.

    This makes scene structure explicit and readable: you can see at a glance
    how many layers there are and what each one does.
    """
    shows: list    # list of ShowResult

    def __repr__(self):
        return f"LayerSpec([{len(self.shows)} layers])"
