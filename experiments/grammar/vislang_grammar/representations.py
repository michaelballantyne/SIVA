"""Representation functions for the 3D visualization grammar.

Representations are the grammar's analog of ggplot2's geoms, but for 3D
scientific visualization. Instead of mapping data rows to visual marks, they
run field algorithms (marching cubes, ray casting, streamline integration)
to produce visible geometry from field data.

Each function returns an unbound RepSpec. It becomes bound to a data source
when piped: `data | rep_surface()` or `data | where(...) | rep_volume("density")`.

The compiler (compiler.py) maps each RepSpec.kind to the VTK pipeline that
executes the algorithm.

Usage:
    bonsai | rep_volume("density")
    fire   | where("theta", between=[500, 2000]) | rep_isosurface("theta", at=800)
    data   | rep_surface()
    data   | rep_outline()
    data   | subsample(every_nth=20) | rep_glyphs("velocity", shape="arrow")
"""

from .core import RepSpec


def rep_volume(field: str) -> RepSpec:
    """Volume rendering: cast rays through the dataset, accumulating color/opacity.

    This is direct volume rendering — the full 3D field is rendered without
    extracting geometry. Color and opacity are controlled by the encoding's
    scale_color and scale_opacity objects.

    Maps to vtkSmartVolumeMapper + vtkVolume. For non-ImageData datasets,
    a vtkResampleToImage step is inserted to produce a regular grid.

    Args:
        field: The scalar field to render volumetrically.

    Example:
        show(bonsai | rep_volume("density"),
             encode(color=scale_color("density", [20, 200], "terrain"),
                    opacity=scale_opacity("density", [(0, 0.0), (100, 0.5), (200, 0.9)])))
    """
    return RepSpec(kind="volume", params={"field": field})


def rep_isosurface(field: str, at) -> RepSpec:
    """Isosurface extraction: produce a surface at one or more scalar values.

    Runs the marching cubes algorithm (vtkContourFilter) to extract a
    triangulated surface where the field equals the specified value(s).

    Maps to vtkContourFilter -> vtkPolyDataMapper -> vtkActor.

    Args:
        field: The scalar field to contour.
        at: A single isovalue (float) or list of isovalues.

    Example:
        show(bonsai | rep_isosurface("density", at=80),
             encode(color=(0.6, 0.4, 0.2), opacity=0.3))

        show(fire | rep_isosurface("theta", at=[500, 800, 1200]),
             encode(color=scale_color("theta", [500, 1200], "hot")))
    """
    if isinstance(at, (int, float)):
        at = [at]
    return RepSpec(kind="isosurface", params={"field": field, "at": at})


def rep_streamlines(field: str, seeds=None, tube_radius: float = 1.0,
                    max_steps: int = 2000, direction: str = "both") -> RepSpec:
    """Streamlines: numerically integrate trajectories through a vector field.

    Integrates particle trajectories using RK4 from seed points placed in
    the dataset. Optionally wraps each streamline in a tube for visual clarity.

    Maps to vtkStreamTracer -> vtkTubeFilter -> vtkPolyDataMapper -> vtkActor.

    Args:
        field: The vector field to integrate through. Must be a 3-component array.
        seeds: Seed point specification. Either:
               - A near() result: seeds near regions of high field values
               - A list of (x, y, z) tuples: explicit seed positions
               - None: seeds from a default point source in the center
        tube_radius: Radius of tubes around each streamline. Use 0 to disable tubes.
        max_steps: Maximum number of integration steps per streamline.
        direction: Integration direction: "forward", "backward", or "both".

    Example:
        show(fire | derive("velocity", from_components=["u", "v", "w"])
                  | rep_streamlines("velocity",
                                    seeds=near("theta", [500, 2000], n=40),
                                    tube_radius=1.5),
             encode(color=scale_color("velocity", [0, 30], "wind")))
    """
    return RepSpec(kind="streamlines", params={
        "field": field,
        "seeds": seeds,
        "tube_radius": tube_radius,
        "max_steps": max_steps,
        "direction": direction,
    })


def rep_glyphs(field: str, shape: str = "arrow", every_nth: int = 1,
               scale_factor: float = 1.0) -> RepSpec:
    """Glyphs: place a shape at each sample point, oriented/scaled by the field.

    This is the 3D analog of ggplot2's geom_point: one mark (glyph) per sample,
    positioned in 3D space. Glyphs can be arrows (for vectors), spheres (for
    scalars), or other shapes.

    Maps to vtkGlyph3D -> vtkPolyDataMapper -> vtkActor. For vector fields,
    orientation and magnitude scaling are applied automatically.

    Args:
        field: The field to drive glyph orientation (vector) or scale (scalar).
        shape: Glyph shape: "arrow", "sphere", "cone", "cube".
        every_nth: Subsample rate — place a glyph every Nth point.
        scale_factor: Global scale multiplier for glyph size.

    Example:
        show(data | rep_glyphs("velocity", shape="arrow", every_nth=10),
             encode(color=scale_color("velocity", [0, 30], "wind"), opacity=0.8))
    """
    return RepSpec(kind="glyphs", params={
        "field": field,
        "shape": shape,
        "every_nth": every_nth,
        "scale_factor": scale_factor,
    })


def rep_surface() -> RepSpec:
    """Outer surface: extract the exterior boundary of the dataset as geometry.

    Converts any VTK dataset type to a surface mesh by extracting boundary
    faces. Useful for showing the outer boundary of a volume or structured grid.

    Maps to vtkDataSetSurfaceFilter -> vtkPolyDataMapper -> vtkActor.

    Example:
        show(data | rep_surface(),
             encode(color=(0.8, 0.7, 0.5), opacity=0.5))
    """
    return RepSpec(kind="surface", params={})


def rep_outline() -> RepSpec:
    """Bounding box: draw the axis-aligned bounding box of the dataset.

    Produces a wireframe box showing the spatial extent of the dataset.
    Useful as a spatial reference frame alongside other representations.

    Maps to vtkOutlineFilter -> vtkPolyDataMapper -> vtkActor.

    Example:
        show(data | rep_outline(),
             encode(color=(1, 1, 1), opacity=0.2))
    """
    return RepSpec(kind="outline", params={})
