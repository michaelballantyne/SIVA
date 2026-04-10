"""Grammar of 3D Scientific Visualization — a GoG-inspired DSL on VTK.

Usage:
    from vislang_grammar import data, show, layer, encode, scale_color, scale_opacity
    from vislang_grammar import rep_volume, rep_isosurface, rep_surface, rep_outline
    from vislang_grammar import where, derive, clip, subsample
"""

# API
from .api import data, show, layer, encode, scale_color, scale_opacity, near

# Representations
from .representations import (
    rep_volume, rep_isosurface, rep_streamlines, rep_glyphs,
    rep_surface, rep_outline,
)

# Transforms
from .transforms import where, derive, gradient, slice_grid, clip, subsample

# Core types (for isinstance checks, type annotations)
from .core import (
    DataRef, TransformChain, Transform, RepSpec, Encoding,
    ScaleColor, ScaleOpacity, ShowResult, LayerSpec,
)

# Compiler
from .compiler import compile_scene
