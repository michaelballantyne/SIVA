"""Transform functions for the 3D visualization grammar.

Transforms are the grammar's equivalent of ggplot2's stats: data processing
steps applied before the representation algorithm runs. They are composable
via the pipe operator and remain lazy until compile_scene() executes them.

Each function returns a Transform object. The compiler (compiler.py) maps
each Transform.kind to the appropriate VTK filter class and configuration.

Usage:
    fire_data | where("theta", between=[500, 2000]) | rep_volume("theta")
    data | derive("velocity", from_components=["u", "v", "w"]) | rep_streamlines("velocity")
    data | slice_grid(k=0) | rep_surface()
"""

from .core import Transform


def where(field: str, between: list) -> Transform:
    """Threshold: keep only the region where field is in [lo, hi].

    Maps to vtkThreshold with ThresholdBetween mode.

    Args:
        field: The scalar field to threshold on.
        between: [lo, hi] — the value range to keep.

    Example:
        fire | where("theta", between=[500, 2000]) | rep_volume("theta")
    """
    lo, hi = between
    return Transform(kind="where", params={"field": field, "lo": lo, "hi": hi})


def derive(name: str, from_components: list) -> Transform:
    """Create a vector field from scalar component arrays.

    Maps to vtkArrayCalculator. Useful when u, v, w velocity components are
    stored as separate scalar arrays and need to be combined into a vector
    for streamline integration or glyph orientation.

    Args:
        name: The name for the resulting vector array.
        from_components: List of 3 scalar field names [fx, fy, fz].

    Example:
        data | derive("velocity", from_components=["u", "v", "w"]) | rep_streamlines("velocity")
    """
    if len(from_components) != 3:
        raise ValueError(
            f"derive() requires exactly 3 component fields, got {len(from_components)}"
        )
    return Transform(
        kind="derive",
        params={"name": name, "components": from_components}
    )


def gradient(field: str) -> Transform:
    """Compute the gradient of a scalar field.

    Maps to vtkGradientFilter. The result is a vector field named
    'Gradient' containing the spatial gradient of the input scalar field.

    Args:
        field: The scalar field to differentiate.

    Example:
        data | gradient("density") | rep_glyphs("Gradient", shape="arrow")
    """
    return Transform(kind="gradient", params={"field": field})


def slice_grid(k: int = None, j: int = None, i: int = None) -> Transform:
    """Extract a 2D slice from a structured grid at a given index.

    Maps to vtkExtractGrid with a collapsed VOI. Exactly one of k, j, i
    must be specified; the others are left as full range.

    Args:
        k: Index along the k (z) axis to extract.
        j: Index along the j (y) axis to extract.
        i: Index along the i (x) axis to extract.

    Example:
        data | slice_grid(k=0) | rep_surface()   # terrain / ground slice
    """
    specified = {ax: val for ax, val in [("k", k), ("j", j), ("i", i)] if val is not None}
    if len(specified) != 1:
        raise ValueError("slice_grid() requires exactly one of k, j, or i.")
    return Transform(kind="slice_grid", params=specified)


def clip(normal: tuple, origin: tuple) -> Transform:
    """Clip the dataset by a plane defined by normal and origin.

    Maps to vtkClipDataSet with a vtkPlane implicit function. Keeps the
    half-space in the direction of the normal vector.

    Args:
        normal: The plane normal vector as (nx, ny, nz).
        origin: A point on the plane as (ox, oy, oz).

    Example:
        data | clip(normal=(0, 0, 1), origin=(0, 0, 100)) | rep_surface()
    """
    return Transform(kind="clip", params={"normal": normal, "origin": origin})


def subsample(every_nth: int = 10) -> Transform:
    """Keep every Nth point, reducing the dataset density.

    Maps to vtkMaskPoints. Useful for reducing glyph or scatter plot density
    to manageable levels without changing the data structure.

    Args:
        every_nth: Keep 1 out of every N points.

    Example:
        data | subsample(every_nth=20) | rep_glyphs("velocity", shape="arrow")
    """
    return Transform(kind="subsample", params={"every_nth": every_nth})
