"""DSL interpreter for declarative VTK pipeline specifications.

Re-execution is incremental: nodes are content-addressed by (kind, params,
parent hashes), so unchanged subgraphs are reused. See `vislang/build_cache.py`
for details.
"""

import inspect
import vtk
from .build_cache import BuildCache, stable_hash, _file_fingerprint
from . import diagnostics as _diag


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

from .filters import (
    create_vtk_filter,
    create_show,
    extract_component,
    _UnknownPropertyError,
    physical_bounds_to_voi,
    COMPONENT_INDEX_MAP,
    SCALAR_TYPE_MAP,
)


class NodeRef:
    """Reference to a pipeline node."""

    def __init__(self, builder, node_id, vtk_class, properties, input_ref=None):
        self._builder = builder
        self._node_id = node_id
        self.vtk_class = vtk_class
        self.properties = properties
        self.input_ref = input_ref


class PipelineBuilder:
    """Builds VTK pipelines from DSL declarations."""

    def __init__(self):
        self._nodes = []  # (node_id, NodeRef)
        self._shows = []  # (node_ref, show_name, display_props)
        self._camera = None
        self._background = None
        self._title = None
        self._axes = None
        self._annotations = []  # list of {x, y, z, text, color, font_size}
        self._node_counter = 0

    def source(self, vtk_class, **props):
        """Load data from a file or create a geometric source.

        This is the entry point for every pipeline — it creates the root node
        that all downstream filters connect to.

        ``vtk_class`` must be a whitelisted VTK reader or source class name.
        Common readers:

        - ``"vtkXMLStructuredGridReader"`` — .vts (curvilinear structured grids, e.g. fire/CFD simulations)
        - ``"vtkXMLImageDataReader"`` — .vti (regular image/volume data, CT scans, etc.)
        - ``"vtkXMLPolyDataReader"`` — .vtp (surface/polydata)
        - ``"vtkXMLUnstructuredGridReader"`` — .vtu (unstructured meshes)
        - ``"vtkXMLRectilinearGridReader"`` — .vtr (rectilinear grids)

        Common geometry sources (no FileName needed):

        - ``"vtkArrowSource"`` — arrow glyph (TipResolution, ShaftResolution)
        - ``"vtkSphereSource"`` — sphere (Radius, ThetaResolution)
        - ``"vtkLineSource"`` — line between two points (Point1, Point2, Resolution)
        - ``"vtkPointSource"`` — random point cloud (NumberOfPoints, Radius)

        All ``**props`` are passed as VTK ``SetXxx(value)`` calls on the created
        object.  The most important property for readers is ``FileName``.

        Args:
            vtk_class: Whitelisted VTK class name string.
            **props: Keyword arguments forwarded to the VTK object via SetXxx().
                     ``FileName`` is required for file readers.

        Returns:
            A ``NodeRef`` that can be passed as ``input=`` to filter forms or
            to ``show()``.

        Example::

            data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
            vol  = source("vtkXMLImageDataReader", FileName="scan.vti")
            arrow = source("vtkArrowSource", TipResolution=8, ShaftResolution=8)

        Notes:
            - Use ``get_dsl_overview()`` to see all whitelisted class names.
            - For raw binary volumes, prefer ``raw_source()`` which handles type
              and dimension parameters more conveniently.
            - The node name in pipeline status reports is taken from the Python
              variable the return value is assigned to.
        """
        self._node_counter += 1
        node_id = self._node_counter
        ref = NodeRef(self, node_id, vtk_class, props)
        self._nodes.append((node_id, ref))
        return ref

    def filter(self, vtk_class, input=None, **props):
        """Apply any whitelisted VTK filter to an input node.

        This is the generic escape hatch for VTK classes that do not have a
        dedicated DSL convenience form (``threshold``, ``contour``, etc.).
        Use it when you need direct access to a VTK filter's properties.

        The ``vtk_class`` must be one of the whitelisted class names returned
        by ``get_dsl_overview()``.  All ``**props`` are applied via the
        special-case property handler in ``filters.py``, which understands
        VTK idioms (e.g. ``VOI``, ``SampleRate``, ``IntegrationDirection``).
        Any property not handled specially is forwarded as ``SetXxx(value)``.

        Args:
            vtk_class: Whitelisted VTK class name string.
            input: Input ``NodeRef`` produced by ``source()`` or another filter.
                   Pass ``None`` for filters that create their own geometry.
            **props: Properties to configure the filter.  Named properties with
                     special handling include ``VOI``, ``SampleRate``,
                     ``IntegrationDirection``, ``GlyphSource``, ``SeedSource``,
                     ``CutFunction``, ``AddScalarArrayName``, etc.
                     All others are forwarded as ``SetXxx(value)``.

        Returns:
            A ``NodeRef`` that can be passed to further filters or to ``show()``.

        Example::

            # Pass arrays through (keep only specific fields)
            trimmed = filter("vtkPassArrays", input=data,
                             PointDataArrays=["temperature", "pressure"])

        Notes:
            - Prefer the named convenience forms (``threshold``, ``contour``,
              ``stream_tracer``, etc.) when available — they have cleaner APIs.
            - Use ``get_dsl_reference('filter')`` to check this form's docs.
            - Use ``get_dsl_overview()`` to see all whitelisted VTK classes.
        """
        self._node_counter += 1
        node_id = self._node_counter
        ref = NodeRef(self, node_id, vtk_class, props, input_ref=input)
        self._nodes.append((node_id, ref))
        return ref

    def contour(self, input=None, **props):
        """Extract isosurfaces (contour surfaces) from a scalar field.

        Finds all points where a scalar field equals given values and connects
        them into a surface mesh.  Use this to visualize "shells" in volumetric
        data — e.g. flame fronts, pressure surfaces, density iso-contours.

        Args:
            input: Input ``NodeRef`` containing the scalar field.
            ContourBy (str): Name of the scalar array to extract isosurfaces from.
                             Must be a point array on the input dataset.
            Isosurfaces (list or float): Isosurface value(s) to extract.
                             Can be a single float or a list of floats.
                             All values must lie within the field's data range;
                             out-of-range values produce empty output.
            **props: Additional VTK properties forwarded to ``vtkContourFilter``.

        Returns:
            A ``NodeRef`` containing the extracted surface (vtkPolyData).

        Example::

            # Single isosurface
            iso = contour(input=data, ContourBy="temperature", Isosurfaces=[800.0])
            show(iso, "flame", color_by="temperature",
                 scalar_range=(300, 1200), lut="fire")

            # Multiple isosurfaces
            iso = contour(input=data, ContourBy="pressure",
                          Isosurfaces=[0.25, 0.5, 0.75])
            show(iso, "shells", color_by="pressure", opacity=0.5)

        Notes:
            - Always call ``describe_data()`` first to find valid value ranges.
            - Use ``suggest_isosurface()`` for histogram-guided value suggestions.
            - Empty output means values are outside the field range.
            - The output is polydata (surface mesh), not a volume.
            - Related: ``threshold()`` keeps a volume region; ``contour()`` extracts
              only the boundary surface.
        """
        return self.filter("vtkContourFilter", input=input, **props)

    def calculator(self, input=None, **props):
        """Evaluate a mathematical expression on field data to create a new array.

        Uses ``vtkArrayCalculator`` to compute a new scalar or vector array
        from existing arrays using a mathematical expression string.  This is
        the low-level building block used by ``make_vector()``,
        ``compute_magnitude()``, ``curl_vector()``, ``curl_magnitude()``, etc.

        The expression syntax is a subset of C math:  ``+``, ``-``, ``*``, ``/``,
        ``sqrt()``, ``mag()``, ``iHat``, ``jHat``, ``kHat`` (unit vectors), etc.
        Array names referenced in the expression must be registered with
        ``AddScalarArrayName`` or ``AddVectorArrayName``.

        Args:
            input: Input ``NodeRef`` containing the source arrays.
            Function (str): Math expression to evaluate.  References registered
                            array names.
            ResultArrayName (str): Name for the output array.
            AddScalarArrayName (list): List of scalar array names to make
                                       available in the expression.
            AddVectorArrayName (list): List of vector array names to make
                                       available in the expression.
            **props: Additional VTK properties forwarded to ``vtkArrayCalculator``.

        Returns:
            A ``NodeRef`` with the new array added to the dataset.

        Example::

            # Scale temperature by a factor
            scaled = calculator(input=data,
                                Function="temperature * 1.8 + 32",
                                ResultArrayName="temp_fahrenheit",
                                AddScalarArrayName=["temperature"])
            show(scaled, "temp", color_by="temp_fahrenheit")

            # Build a vector from scalars (same as make_vector)
            vel = calculator(input=data,
                             Function="u*iHat + v*jHat + w*kHat",
                             ResultArrayName="velocity",
                             AddScalarArrayName=["u", "v", "w"])

        Notes:
            - For vector assembly, prefer ``make_vector()`` — it is simpler.
            - For vector magnitude, prefer ``compute_magnitude()``.
            - For curl, prefer ``curl_vector()`` or ``curl_magnitude()``.
            - All arrays referenced in ``Function`` must be registered.
        """
        return self.filter("vtkArrayCalculator", input=input, **props)

    def threshold(self, input=None, **props):
        """Keep only cells where a field value falls within a given range.

        Extracts the subset of the input data where the specified scalar field
        is within ``[ThresholdRange[0], ThresholdRange[1]]``.  The result is an
        unstructured grid.  Use this to focus on regions of interest before
        further processing or volume rendering.

        Args:
            input: Input ``NodeRef`` to threshold.
            ThresholdBy (str): Name of the scalar array to threshold on.
                               Must be a point or cell array on the input.
            ThresholdRange (list): ``[min, max]`` — cells where the field value
                                   lies within this range are kept.
            **props: Additional VTK properties forwarded to ``vtkThreshold``.

        Returns:
            A ``NodeRef`` containing only the cells that passed the threshold
            (vtkUnstructuredGrid).

        Example::

            # Keep only cells where temperature is between 500 and 2000 K
            hot = threshold(input=data,
                            ThresholdBy="temperature",
                            ThresholdRange=[500, 2000])
            show(hot, "fire", color_by="temperature",
                 scalar_range=(500, 2000), lut="fire")

            # Then volume-render the thresholded region
            show(hot, "fire_vol", representation="Volume",
                 color_by="temperature", scalar_range=(500, 2000),
                 lut="fire", opacity_function=[(500,0),(1000,0.1),(2000,0.5)])

        Notes:
            - Always call ``describe_data()`` first to find valid field ranges.
            - Empty output means ``ThresholdRange`` doesn't overlap the field's
              actual data range.
            - Unlike ``contour()``, threshold keeps a volume region, not just the
              boundary surface.
            - To threshold to a spatial region instead of a field value, use
              ``extract_region()``, ``clip_box()``, or ``clip()``.
        """
        return self.filter("vtkThreshold", input=input, **props)

    def extract_grid(self, input=None, **props):
        """Extract a sub-volume of a structured grid by extent indices.

        Passes VOI directly to ``vtkExtractGrid`` in extent coordinates,
        matching ParaView's "Extract Subset" behavior.  Use
        ``get_node_info()`` or ``describe_data()`` to see the dataset's
        extent range.

        For extraction by physical coordinates, use ``extract_region()``
        instead.

        Args:
            input: Input ``NodeRef`` (vtkStructuredGrid or vtkRectilinearGrid).
            VOI (list): ``[imin, imax, jmin, jmax, kmin, kmax]`` in extent
                        coordinates (not zero-based — must fall within the
                        dataset's actual extent range).
            SampleRate (list): ``[si, sj, sk]`` — subsample every N-th point
                               along each axis (default ``[1, 1, 1]``).
            **props: Additional VTK properties forwarded to ``vtkExtractGrid``.

        Returns:
            A ``NodeRef`` containing the extracted sub-grid.

        Example::

            # Extract the ground surface (k=kmin)
            terrain = extract_grid(input=data, VOI=[251, 850, 0, 499, 0, 0])

            # Subsample every other point
            sub = extract_grid(input=data, VOI=[251, 850, 0, 499, 0, 60],
                               SampleRate=[2, 2, 1])

        Notes:
            - VOI uses extent coordinates, not zero-based indices.
            - Out-of-range values are clamped by VTK, but relying on this
              is discouraged — use the actual extent.
            - Related: ``extract_region()`` for physical coordinates.
        """
        return self.filter("vtkExtractGrid", input=input, **props)

    def extract_region(self, input=None, bounds=None, **props):
        """Extract a sub-region of a structured grid by physical coordinates.

        The high-level way to crop a structured grid.  Specify the region in
        physical coordinates and this form auto-converts to grid indices and
        picks the correct VTK filter:

        - ``vtkExtractVOI`` for ``vtkImageData`` / ``vtkUniformGrid``
        - ``vtkExtractGrid`` for ``vtkStructuredGrid`` / ``vtkRectilinearGrid``

        Args:
            input: Input structured grid ``NodeRef``.
            bounds (list): Physical coordinate extents
                           ``[xmin, xmax, ymin, ymax, zmin, zmax]``.
                           Required.
            **props: Additional properties forwarded to the underlying VTK filter
                     (e.g. ``SampleRate=[2, 2, 1]`` for subsampling).

        Returns:
            A ``NodeRef`` containing the extracted sub-region, preserving the
            grid structure.

        Raises:
            ValueError: If ``bounds`` is not provided.

        Example::

            # Crop to a physical sub-region
            region = extract_region(input=data,
                                    bounds=[400, 600, 300, 500, 0, 100])
            show(region, "crop", color_by="temperature")

            # With subsampling to reduce data density
            sub = extract_region(input=data,
                                 bounds=[400, 600, 300, 500, 0, 100],
                                 SampleRate=[2, 2, 1])
            show(sub, "sparse", color_by="temperature")

        Notes:
            - For non-structured data, use ``clip_box()`` instead.
            - Use ``get_bounds()`` to find your dataset's spatial extent.
            - Related: ``clip_box()``.
        """
        self._node_counter += 1
        node_id = self._node_counter
        ref = NodeRef(self, node_id, "_extract_region", {
            "input_ref": input,
            "bounds": bounds,
            "extra_props": props,
        }, input_ref=input)
        self._nodes.append((node_id, ref))
        return ref

    def stream_tracer(self, input=None, **props):
        """Trace streamlines through a vector field from seed points.

        Integrates the vector field starting from every point in ``SeedSource``
        and traces curves forward, backward, or both directions through the flow.
        Used to visualize wind patterns, fluid flow, vortex structures, fire plumes, etc.

        Before calling this you usually need to:
        1. Assemble a velocity vector field with ``make_vector()``
        2. Create seed points with a ``vtkLineSource``, ``vtkPointSource``, or ``vtkPlaneSource``

        Pass the result directly to ``show()`` — it renders as lines by default.
        Use ``tube()`` only when you want shaded 3D tubes.

        Args:
            input: Input ``NodeRef`` containing the vector field (must have an
                   active 3-component vector array).
            SeedSource: A ``NodeRef`` providing the seed points (e.g.
                        ``source("vtkLineSource", Point1=..., Point2=..., Resolution=30)``,
                        ``source("vtkPointSource", ...)``, or
                        ``source("vtkPlaneSource", Origin=..., Point1=..., Point2=..., XResolution=10, YResolution=10)``).
            Vectors (str): Name of the vector array to trace.  Required if the
                           dataset has more than one vector array.
            IntegrationDirection (str): ``"Forward"``, ``"Backward"``, or
                                         ``"Both"`` (default ``"Both"``).
            MaximumNumberOfSteps (int): Maximum integration steps per line
                                        (default 2000).  Increase for longer lines.
            MaximumPropagation (float): Maximum physical distance a streamline
                                        may travel (default unbounded).
            InitialIntegrationStep (float): Initial step size (relative to cell size).
            IntegratorType (str): ``"RungeKutta2"``, ``"RungeKutta4"``,
                                   ``"RungeKutta45"`` (default).
            **props: Additional VTK properties forwarded to ``vtkStreamTracer``.

        Returns:
            A ``NodeRef`` containing streamline polylines (vtkPolyData).

        Example::

            # Assemble velocity vector
            vel = make_vector(input=data, components=("u", "v", "w"), result="velocity")

            # Create seed points along a line
            seeds = source("vtkLineSource",
                           Point1=[450, 400, 10], Point2=[550, 400, 10], Resolution=40)

            # Trace streamlines
            streams = stream_tracer(input=vel, SeedSource=seeds, Vectors="velocity",
                                    IntegrationDirection="Both",
                                    MaximumNumberOfSteps=2000,
                                    MaximumPropagation=500)

            show(streams, "flow", color_by="velocity", opacity=0.8)

        Notes:
            - Empty output usually means seed points are outside the grid.
              Use ``get_ground_z(node, x, y)`` to find valid z-coordinates on
              terrain-following structured grids.
            - Make sure the input has active vectors — ``make_vector()`` sets them.
            - Output includes a ``Vorticity`` array; color by it to highlight
              rotational structure.
            - Related: ``tube()`` adds thickness. Use ``source("vtkPlaneSource", ...)`` for broad planar seed coverage.
        """
        return self.filter("vtkStreamTracer", input=input, **props)

    def tube(self, input=None, **props):
        """Add volumetric tubes around line/streamline geometry.

        Wraps each polyline (e.g. a streamline) with a cylindrical tube, giving
        streamlines visible thickness and enabling depth-cuing.  Optional — for
        most streamline plots, passing ``streams`` directly to ``show()`` renders
        clean 3px-wide lines that read well. Reach for ``tube()`` only when you
        want shaded 3D structure (e.g. to show how streamlines twist around a
        plume).

        Args:
            input: Input ``NodeRef`` containing polylines (e.g. streamline output).
            Radius (float): Tube radius in world coordinates (VTK default 0.5).
                            Set explicitly — what matters is seed spacing, not domain
                            size. A good starting point is ~1/4 of the mean distance
                            between adjacent seeds; too thick and neighboring tubes
                            merge into a solid mass that hides the flow structure.
            NumberOfSides (int): Number of polygonal sides per tube (default 6).
                                  8–12 gives smooth tubes; 4–6 is faster.
            Capping (bool): Close the tube ends with caps (default True).
            **props: Additional VTK properties forwarded to ``vtkTubeFilter``.

        Returns:
            A ``NodeRef`` containing tube surfaces (vtkPolyData).

        Example::

            streams = stream_tracer(input=vel, SeedSource=seeds,
                                    Vectors="velocity", IntegrationDirection="Both")
            tubes = tube(input=streams, Radius=0.5, NumberOfSides=8)
            show(tubes, "flow", color_by="velocity", opacity=0.8,
                 scalar_range=(0, 50), lut="wind")

        Notes:
            - Radius scales with seed spacing, not domain size.
            - Use ``scalar_bar`` in ``show()`` to add a color legend.
            - Related: ``stream_tracer()`` produces the input lines; often you can
              skip ``tube()`` entirely and just ``show(streams, ...)``.
        """
        return self.filter("vtkTubeFilter", input=input, **props)

    def glyph(self, input=None, **props):
        """Place a glyph shape at every input point, optionally oriented and scaled by data.

        Replicates a source geometry (arrow, sphere, cone, etc.) at every point
        of the input dataset, optionally rotating each glyph to align with a
        vector field and scaling it by a scalar field.  Use this to visualize
        wind direction, vector magnitudes, sample density, etc.

        Pair with ``mask_points()`` first to subsample the input — placing glyphs
        at every grid point usually produces an illegible, slow image.

        Args:
            input: Input ``NodeRef`` whose points receive glyphs.
            GlyphSource: A ``NodeRef`` for the glyph shape (e.g.
                         ``source("vtkArrowSource", TipResolution=8, ShaftResolution=8)``).
            OrientationArray (str): Name of a vector array to orient each glyph.
                                    Glyphs are rotated to align with this vector.
            ScaleArray (str): Name of a scalar array to scale glyph size.
                              Each glyph's size is proportional to this value.
            ScaleFactor (float): Global scale multiplier applied on top of
                                  ``ScaleArray`` (default 1.0).
            **props: Additional VTK properties forwarded to ``vtkGlyph3D``.

        Returns:
            A ``NodeRef`` containing the glyph surface geometry (vtkPolyData).

        Example::

            # Subsample the grid first
            sparse = mask_points(input=data, OnRatio=20, RandomMode=True)

            # Create the arrow shape
            arrow = source("vtkArrowSource", TipResolution=8, ShaftResolution=8)

            # Build velocity vector and magnitude
            vel = make_vector(input=sparse, components=("u","v","w"), result="velocity")
            speed = compute_magnitude(input=vel, components=("u","v","w"), result="speed")

            # Place oriented, scaled arrows
            arrows = glyph(input=speed, GlyphSource=arrow,
                           OrientationArray="velocity",
                           ScaleArray="speed", ScaleFactor=5.0)
            show(arrows, "wind", color_by="speed",
                 scalar_range=(0, 30), lut="wind")

        Notes:
            - Always subsample with ``mask_points()`` first.
            - ``OrientationArray`` must be a 3-component vector.
            - ``ScaleArray`` must be a scalar (1-component).
            - Related: ``mask_points()`` for subsampling, ``compute_magnitude()`` for speed.
        """
        return self.filter("vtkGlyph3D", input=input, **props)

    def warp_vector(self, input=None, **props):
        """Displace mesh points by a vector field (structural deformation, etc.).

        Moves every point of the input mesh by its vector array value multiplied
        by ``ScaleFactor``.  Useful for showing structural deformations, mode shapes,
        or displacement fields.  The output has the same topology as the input but
        with displaced point positions.

        Args:
            input: Input ``NodeRef`` whose geometry will be displaced.
            ScaleFactor (float): Multiplier applied to the displacement vector
                                  before adding to point coordinates (default 1.0).
                                  Set < 1.0 to reduce exaggeration.
            **props: Additional VTK properties forwarded to ``vtkWarpVector``.

        Returns:
            A ``NodeRef`` with the warped mesh (same topology, displaced positions).

        Example::

            # Exaggerate deformation by a factor of 10
            warped = warp_vector(input=data, ScaleFactor=10.0)
            show(warped, "deformed", color_by="displacement_mag")

        Notes:
            - The input must have an active vector array (set via ``make_vector()``
              or by having a 3-component array in the data).
            - Compare warped and unwarped views using overlay with different opacities.
            - Related: ``warp_scalar()`` for height-field warping.
        """
        return self.filter("vtkWarpVector", input=input, **props)

    def warp_scalar(self, input=None, **props):
        """Displace mesh points along their normals by a scalar field (relief map).

        Moves each surface point along its normal by the value of the active scalar
        multiplied by ``ScaleFactor``.  Creates a 3-D relief effect from a 2-D
        surface, e.g. terrain elevation maps, pressure fields on surfaces, etc.

        Args:
            input: Input ``NodeRef`` with a surface mesh (vtkPolyData recommended).
            ScaleFactor (float): Multiplier applied to the scalar value before
                                  displacing (default 1.0).  Increase to exaggerate.
            **props: Additional VTK properties forwarded to ``vtkWarpScalar``.

        Returns:
            A ``NodeRef`` with the warped surface mesh.

        Example::

            # Create terrain relief from elevation data
            surf = surface(input=data)
            elev = elevation(input=surf, low_point=(0,0,0), high_point=(0,0,100))
            relief = warp_scalar(input=elev, ScaleFactor=5.0)
            show(relief, "terrain", color_by="Elevation", lut="terrain")

        Notes:
            - ``warp_scalar()`` works best on surface (polydata) input.
            - Related: ``elevation()`` to compute elevation scalars,
              ``warp_vector()`` for vector-field displacement.
        """
        return self.filter("vtkWarpScalar", input=input, **props)

    def cell_to_point(self, input=None, **props):
        """Interpolate cell-centered data arrays to point-centered arrays.

        Some VTK filters require point data; others output cell data.  Use this
        to convert so downstream filters (e.g. ``contour()``, ``stream_tracer()``)
        can access the field values, or to enable smooth per-vertex color shading
        instead of flat per-cell shading.

        Args:
            input: Input ``NodeRef`` whose cell data arrays should be converted.
            **props: Additional VTK properties forwarded to
                     ``vtkCellDataToPointData``.

        Returns:
            A ``NodeRef`` with the same dataset but cell arrays promoted to
            point arrays (original cell arrays are preserved alongside new ones).

        Example::

            # Convert cell data before contouring
            pts = cell_to_point(input=data)
            iso = contour(input=pts, ContourBy="pressure", Isosurfaces=[0.5])
            show(iso, "shell", color_by="pressure")

        Notes:
            - Interpolation averages values from surrounding cells at each point.
            - Related: ``point_to_cell()`` for the reverse direction.
        """
        return self.filter("vtkCellDataToPointData", input=input, **props)

    def point_to_cell(self, input=None, **props):
        """Average point-centered data arrays to cell-centered arrays.

        The inverse of ``cell_to_point()``.  Use when you need cell data for a
        downstream filter that requires it, such as cell-based thresholds.

        Args:
            input: Input ``NodeRef`` whose point data arrays should be converted.
            **props: Additional VTK properties forwarded to
                     ``vtkPointDataToCellData``.

        Returns:
            A ``NodeRef`` with the same dataset but point arrays averaged to
            cell arrays.

        Example::

            cells = point_to_cell(input=data)
            region = threshold(input=cells, ThresholdBy="temperature",
                               ThresholdRange=[500, 2000])

        Notes:
            - Values are averaged over all points belonging to each cell.
            - Related: ``cell_to_point()`` for the reverse direction.
        """
        return self.filter("vtkPointDataToCellData", input=input, **props)

    def outline(self, input=None, **props):
        """Draw a wireframe bounding box around a dataset.

        Useful as a spatial reference frame — adds a box that shows the full
        extent of the data even when only parts of it are visualized.

        Args:
            input: Input ``NodeRef`` whose bounds define the box.
            **props: Additional VTK properties forwarded to ``vtkOutlineFilter``.

        Returns:
            A ``NodeRef`` containing the box edges (vtkPolyData).

        Example::

            box = outline(input=data)
            show(box, "bbox", color=(1, 1, 1), opacity=0.3)
            show(iso, "flame", color_by="temperature", lut="fire")

        Notes:
            - Use with low opacity to avoid cluttering the view.
            - Related: ``clip_box()`` for clipping data to a box region.
        """
        return self.filter("vtkOutlineFilter", input=input, **props)

    def elevation(self, input=None, low_point=None, high_point=None, **props):
        """Add a scalar "Elevation" array that encodes height between two points.

        Computes a scalar value at each point proportional to its signed distance
        along the axis from ``low_point`` to ``high_point``.  Output range is
        always [0, 1] — use ``scalar_range`` in ``show()`` to map to real values.
        Useful for height-based colorization or creating terrain-like coloring.

        Args:
            input: Input ``NodeRef`` to add the elevation array to.
            low_point: (x, y, z) — the point mapped to elevation 0.0.
                       Defaults to the dataset's minimum Z extent if None.
            high_point: (x, y, z) — the point mapped to elevation 1.0.
                        Defaults to the dataset's maximum Z extent if None.
            **props: Additional VTK properties forwarded to ``vtkElevationFilter``.

        Returns:
            A ``NodeRef`` with a new point array named ``"Elevation"`` added.

        Example::

            # Color surface by Z height
            elev = elevation(input=surf,
                             low_point=(0, 0, 0),
                             high_point=(0, 0, 200))
            show(elev, "height", color_by="Elevation",
                 scalar_range=(0, 200), lut="terrain")

        Notes:
            - The output array is named ``"Elevation"`` (capital E).
            - ``low_point`` and ``high_point`` don't need to be along Z —
              any axis or diagonal works.
            - Related: ``warp_scalar()`` to extrude geometry by elevation.
        """
        if low_point is not None:
            props["LowPoint"] = low_point
        if high_point is not None:
            props["HighPoint"] = high_point
        return self.filter("vtkElevationFilter", input=input, **props)

    def surface(self, input=None, **props):
        """Extract the outer surface (skin) of a volumetric dataset.

        Converts a volume (structured grid, unstructured grid, image data, etc.)
        to a surface mesh (vtkPolyData).  Needed before smooth shading, glyph
        placement, or any filter that requires polydata input.

        Args:
            input: Input ``NodeRef`` to extract the surface of.
            **props: Additional VTK properties forwarded to
                     ``vtkDataSetSurfaceFilter``.

        Returns:
            A ``NodeRef`` containing the outer surface (vtkPolyData).

        Example::

            surf = surface(input=data)
            show(surf, "skin", color_by="temperature",
                 scalar_range=(300, 1200), opacity=0.3)

            # Smooth before displaying
            smooth_surf = smooth(input=surf, iterations=50)
            show(smooth_surf, "shell", color=(0.8, 0.8, 0.8))

        Notes:
            - Only the boundary faces are kept; interior cells are discarded.
            - Use before ``smooth()`` for better smoothing results.
            - Related: ``smooth()`` to reduce surface noise, ``outline()``
              for just the bounding box.
        """
        return self.filter("vtkDataSetSurfaceFilter", input=input, **props)

    def smooth(self, input=None, iterations=20, **props):
        """Smooth a polydata surface to reduce noise and improve appearance.

        Applies windowed-sinc smoothing to a surface mesh, reducing jagged edges
        and producing a cleaner geometry.  More iterations = smoother but slower.

        Args:
            input: Input ``NodeRef`` (vtkPolyData — run through ``surface()`` first
                   if starting from a volume).
            iterations (int): Number of smoothing iterations (default 20).
                               Typical values: 10–100.
            **props: Additional VTK properties forwarded to
                     ``vtkWindowedSincPolyDataFilter`` (e.g.
                     ``PassBand=0.1``, ``BoundarySmoothing=False``).

        Returns:
            A ``NodeRef`` containing the smoothed surface (vtkPolyData).

        Example::

            surf = surface(input=iso)
            polished = smooth(input=surf, iterations=50)
            show(polished, "surface", color=(0.85, 0.65, 0.4), specular=0.4,
                 specular_power=20)

        Notes:
            - Only works on polydata; call ``surface()`` first on volumetric data.
            - More iterations preserve less boundary detail; start with 20–50.
            - Related: ``surface()`` to generate polydata from volumes.
        """
        props["NumberOfIterations"] = iterations
        return self.filter("vtkWindowedSincPolyDataFilter", input=input, **props)

    def mask_points(self, input=None, **props):
        """Subsample a point cloud, keeping every N-th point or a random subset.

        Reduces the number of points before placing glyphs or creating seed lines,
        which would otherwise be too dense to render efficiently or interpret visually.

        Args:
            input: Input ``NodeRef`` to subsample.
            OnRatio (int): Keep every N-th point (default 2).  Higher values
                           produce sparser output.
            RandomMode (bool): If True, select points randomly instead of
                               uniformly (default False).
            **props: Additional VTK properties forwarded to ``vtkMaskPoints``.

        Returns:
            A ``NodeRef`` containing the subsampled point cloud.

        Example::

            # Keep every 20th point, randomly selected
            sparse = mask_points(input=data, OnRatio=20, RandomMode=True)

            # Place glyphs at the subsampled points
            arrows = glyph(input=sparse, GlyphSource=arrow,
                           OrientationArray="velocity",
                           ScaleArray="speed", ScaleFactor=5.0)

        Notes:
            - Always apply before ``glyph()`` to prevent overplotting.
            - ``RandomMode=True`` produces less grid-aligned patterns.
            - Related: ``glyph()`` for placing glyphs at the resulting points.
        """
        return self.filter("vtkMaskPoints", input=input, **props)

    def gradient(self, input=None, **props):
        """Compute the gradient of a scalar or vector field.

        Computes per-point spatial derivatives, producing a new vector array.
        Used as input to ``compute_gradient_magnitude()`` for edge detection,
        or directly as a vector field for further analysis.

        Args:
            input: Input ``NodeRef`` containing the field to differentiate.
            GradientField (str): Name of the scalar (or vector) array to
                                  differentiate.  Required.
            ResultArrayName (str): Name for the output gradient array
                                   (default ``"Gradients"``).
            **props: Additional VTK properties forwarded to ``vtkGradientFilter``.

        Returns:
            A ``NodeRef`` with the gradient array added to the dataset.

        Example::

            # Compute gradient of pressure
            grad = gradient(input=data, GradientField="pressure",
                            ResultArrayName="pressure_grad")
            show(data, "grad_mag", color_by="pressure_grad")

            # Use compute_gradient_magnitude() as a simpler wrapper
            gm = compute_gradient_magnitude(input=data, field="temperature",
                                            result="temp_edges")
            show(data, "edges", color_by="temp_edges", scalar_range=(0, 50))

        Notes:
            - For a scalar field the output is a 3-component vector at each point.
            - Use ``compute_gradient_magnitude()`` to collapse the vector to a
              magnitude (useful for edge detection / boundary finding).
            - Related: ``compute_gradient_magnitude()``, ``curl_magnitude()``.
        """
        return self.filter("vtkGradientFilter", input=input, **props)

    def extract_component(self, input=None, field=None, component=0, result_name=None):
        """Extract a single component from a multi-component (vector) field.

        Creates a new 1-component scalar array from one component of a vector or
        multi-component field.  Use this to isolate a single velocity component
        (e.g. the vertical wind ``w``), colorize by a specific vector direction,
        or feed a scalar field to a contour/threshold that expects a scalar.

        Args:
            input: Input ``NodeRef`` containing the vector field.
            field (str): Name of the vector array to extract from.
            component (int or str): Component to extract: ``0``, ``1``, ``2``
                                     or ``"x"``, ``"y"``, ``"z"``.
            result_name (str): Name for the new scalar array.  Defaults to
                               ``"{field}_x"``, ``"{field}_y"``, ``"{field}_z"``.

        Returns:
            A ``NodeRef`` with the new scalar array added to the dataset.
            The original vector array is preserved.

        Example::

            # Isolate the vertical (Z) component of velocity
            vel = make_vector(input=data, components=("u","v","w"), result="velocity")
            w = extract_component(input=vel, field="velocity",
                                  component=2, result_name="w_component")
            show(data, "updraft", color_by="w_component",
                 scalar_range=(-5, 20), lut="cool_to_warm",
                 scalar_bar="W (m/s)")

        Notes:
            - ``field`` must already be a multi-component array on the dataset.
            - If the field doesn't exist, you'll get a clear error listing
              the available arrays.
            - Related: ``make_vector()`` to build a vector from scalars,
              ``component=`` parameter of ``show()`` for a lightweight alternative.
        """
        if result_name is None:
            if isinstance(component, str):
                result_name = f"{field}_{component.lower()}"
            else:
                result_name = f"{field}_{COMPONENT_INDEX_MAP.get(int(component), str(component))}"
        self._node_counter += 1
        node_id = self._node_counter
        ref = NodeRef(self, node_id, "_extract_component", {
            "input_ref": input,
            "field": field,
            "component": component,
            "result_name": result_name,
        }, input_ref=input)
        self._nodes.append((node_id, ref))
        return ref

    def clip(self, input=None, origin=None, normal=None, inside_out=False, **props):
        """Clip data by a plane, keeping one half-space.

        Cuts the dataset with an infinite plane defined by a point and normal
        vector.  By default keeps the half-space in the direction the normal
        points; set ``inside_out=True`` to keep the opposite half.

        Args:
            input: Input ``NodeRef`` to clip.
            origin (list): A point ``[x, y, z]`` on the clipping plane.
            normal (list): Normal vector ``[nx, ny, nz]`` defining which side
                           to keep (points toward the kept side).
            inside_out (bool): If True, keep the opposite half (default False).
            **props: Additional VTK properties forwarded to ``vtkClipDataSet``.

        Returns:
            A ``NodeRef`` containing the clipped dataset.

        Example::

            # Keep everything to the right of x=500
            right = clip(input=data, origin=(500, 0, 0), normal=(1, 0, 0))
            show(right, "right_half", color_by="temperature")

            # Cross-section: keep half to the left of x=500
            left = clip(input=data, origin=(500, 0, 0), normal=(-1, 0, 0))

        Notes:
            - ``clip()`` removes geometry; ``slice()`` creates a 2-D cross-section.
            - Related: ``clip_box()``, ``clip_sphere()``, ``slice()``.
        """
        plane_dict = dict(type="Plane", Origin=origin, Normal=normal)
        props["CutFunction"] = plane_dict
        if inside_out:
            props["InsideOut"] = 1
        return self.filter("vtkClipDataSet", input=input, **props)

    def clip_sphere(self, input=None, center=None, radius=100, inside_out=True, **props):
        """Clip data by a sphere, keeping everything inside (by default).

        Cuts the dataset with a sphere and retains only the region inside the
        sphere (or outside, with ``inside_out=False``).  Useful for focusing on
        a spherical region of interest.

        Args:
            input: Input ``NodeRef`` to clip.
            center (list): Sphere center ``[x, y, z]``.
            radius (float): Sphere radius in world coordinates (default 100).
            inside_out (bool): If True (default), keep the region inside the
                               sphere.  False keeps the outside.
            **props: Additional VTK properties forwarded to ``vtkClipDataSet``.

        Returns:
            A ``NodeRef`` containing the clipped dataset.

        Example::

            # Keep only data within 200 units of the fire center
            local = clip_sphere(input=data, center=(500, 400, 50), radius=200)
            show(local, "plume", color_by="temperature", lut="fire")

        Notes:
            - Related: ``clip()``, ``clip_box()``.
        """
        props["CutFunction"] = dict(type="Sphere", Center=center, Radius=radius)
        if inside_out:
            props["InsideOut"] = 1
        return self.filter("vtkClipDataSet", input=input, **props)

    def clip_box(self, input=None, bounds=None, inside_out=True, **props):
        """Clip data by an axis-aligned box, keeping everything inside (by default).

        Removes everything outside an axis-aligned rectangular region.
        The region is defined by physical coordinate bounds.

        Args:
            input: Input ``NodeRef`` to clip.
            bounds (list): ``[xmin, xmax, ymin, ymax, zmin, zmax]`` in world
                           coordinates.
            inside_out (bool): If True (default), keep the region inside the box.
                               False keeps everything outside the box.
            **props: Additional VTK properties forwarded to ``vtkClipDataSet``.

        Returns:
            A ``NodeRef`` containing the clipped dataset.

        Example::

            # Crop to a 200x200x100 sub-region
            crop = clip_box(input=data,
                            bounds=[400, 600, 300, 500, 0, 100])
            show(crop, "zoom", color_by="temperature", scalar_range=(300, 1200))

        Notes:
            - For structured grids, ``extract_region()`` is more efficient because
              it preserves grid structure.
            - Related: ``extract_region()``, ``clip()``, ``clip_sphere()``.
        """
        props["CutFunction"] = dict(type="Box", Bounds=bounds)
        if inside_out:
            props["InsideOut"] = 1
        return self.filter("vtkClipDataSet", input=input, **props)

    def probe(self, input=None, source=None, **props):
        """Sample data from a source dataset at the geometry of the input dataset.

        Probes the ``source`` dataset at every point of ``input``, interpolating
        the source arrays and attaching them to the output geometry.  The input
        defines *where* to sample; the source defines *what* to sample.

        Common patterns:
        - Sample a volume at a set of points (input=point cloud, source=volume)
        - Sample a volume along a line (input=line source, source=volume)

        Prefer the higher-level ``line_probe()`` for line sampling — it wraps
        ``source("vtkLineSource", ...) + probe()`` in one call.

        Args:
            input: Input ``NodeRef`` whose geometry provides the sample locations.
            source: ``NodeRef`` of the dataset to sample from.
            **props: Additional VTK properties forwarded to ``vtkProbeFilter``.

        Returns:
            A ``NodeRef`` with the input's geometry and the source's field values
            interpolated at each input point.

        Example::

            # Sample volume data at a set of scattered points
            pts = source("vtkPointSource", NumberOfPoints=100, Radius=50)
            sampled = probe(input=pts, source=data)
            show(sampled, "samples", color_by="temperature")

            # Line probe (use line_probe() instead — it's cleaner)
            line = source("vtkLineSource",
                          Point1=[0,0,0], Point2=[100,100,50],
                          Resolution=200)
            profile_data = probe(input=line, source=data)

        Notes:
            - Related: ``line_probe()`` is a simpler wrapper for the common line-probe pattern.
            - The ``profile()`` MCP tool does the same as a line probe and formats the result.
        """
        if source is not None:
            props["_probe_source"] = source
        return self.filter("vtkProbeFilter", input=input, **props)

    def resample_to_image(self, input=None, dimensions=None, **props):
        """Resample any dataset to a regular axis-aligned image grid.

        Converts structured or unstructured volumetric data to a regular
        ``vtkImageData`` grid.  Required for volume rendering of non-image data
        (the volume renderer ``_create_volume()`` does this automatically, but
        calling this explicitly gives you direct control over resolution).

        Args:
            input: Input ``NodeRef`` — any VTK dataset type.
            dimensions (list): ``[nx, ny, nz]`` grid dimensions for the output.
                               If None, VTK chooses based on the input bounds.
            **props: Additional VTK properties forwarded to ``vtkResampleToImage``.

        Returns:
            A ``NodeRef`` containing regular image data (``vtkImageData``).

        Example::

            # Resample to a coarse grid for fast volume rendering
            img = resample_to_image(input=data, dimensions=[64, 64, 32])
            show(img, "vol", representation="Volume",
                 color_by="temperature", scalar_range=(300, 1200),
                 opacity_function=[(300,0),(800,0.1),(1200,0.4)])

            # Resample to higher resolution for detail
            img_hi = resample_to_image(input=region, dimensions=[256, 256, 128])
            show(img_hi, "vol_hi", representation="Volume",
                 color_by="pressure", lut="cool_to_warm")

        Notes:
            - Larger dimensions give finer detail but use more memory.
            - For non-image data, ``show(..., representation="Volume")`` already
              resamples automatically via ``volume_resolution`` parameter.
            - Related: ``show(..., volume_resolution=N)`` for an implicit version.
        """
        if dimensions is not None:
            props["SamplingDimensions"] = dimensions
        return self.filter("vtkResampleToImage", input=input, **props)

    def make_vector(self, components=("u", "v", "w"), result="velocity", input=None):
        """Assemble three scalar arrays into a single 3-component vector array.

        Many datasets store vector field components as separate scalar arrays
        (e.g. ``u``, ``v``, ``w`` for wind velocity).  VTK filters like
        ``stream_tracer()`` and ``glyph()`` require a true vector array.
        This form creates one from named scalar arrays.

        The assembled vector is also set as the active vector on the dataset,
        so it is immediately usable by vector-consuming filters.

        Args:
            components (tuple): Names of the three scalar arrays for the X, Y,
                                 and Z components (default ``("u", "v", "w")``).
            result (str): Name for the new vector array (default ``"velocity"``).
            input: Input ``NodeRef`` containing all three component arrays.

        Returns:
            A ``NodeRef`` with the new vector array added and set as active vectors.

        Example::

            # Assemble velocity from U, V, W scalars
            vel = make_vector(input=data, components=("u", "v", "w"),
                              result="velocity")

            # Trace streamlines through it
            seed_line = source("vtkLineSource", Point1=[x0, y0, z0], Point2=[x1, y0, z0], Resolution=30)
            streams = stream_tracer(input=vel, SeedSource=seed_line,
                                    Vectors="velocity", IntegrationDirection="Both")

        Notes:
            - All three component arrays must already exist as point arrays.
            - Related: ``compute_magnitude()`` to get a scalar speed array.
            - Related: ``curl_vector()`` or ``curl_magnitude()`` to compute vorticity from the vector.
        """
        cx, cy, cz = components[0], components[1], components[2]
        return self.filter("vtkArrayCalculator", input=input,
            AddScalarArrayName=list(components),
            Function=f"{cx}*iHat + {cy}*jHat + {cz}*kHat",
            ResultArrayName=result)

    def curl_vector(self, vector_field, field=None, output_field=None):
        """Compute the full 3-component curl (∇ × F) of a vector field.

        Returns the vorticity as a 3-component vector array (one (x, y, z) tuple
        per point).  In fluid dynamics the curl of velocity is the vorticity —
        high-magnitude regions indicate spinning or twisting flow structures.

        Internally uses ``vtkCellDerivatives`` → ``vtkCellDataToPointData``.
        VTK names the intermediate per-cell array ``Vorticity`` (capital V);
        this wrapper renames the result to snake_case before returning so agents
        never need to know the VTK-internal name.

        Args:
            vector_field: Input ``NodeRef`` whose active vector array is used.
                          Must have a 3-component vector (set with ``make_vector()``).
            field (str): Name of the vector array to differentiate.  Optional — if
                         omitted the dataset's active vector attribute is used.
            output_field (str): Name for the output 3-component array.
                                Defaults to ``"vorticity"``.

        Returns:
            A ``NodeRef`` with the ``output_field`` 3-component vector array added.

        Example::

            vel = make_vector(input=data, components=("u","v","w"), result="velocity")

            # Full 3-component vorticity vector
            vort = curl_vector(vector_field=vel)
            show(vort, "vort_z", color_by="vorticity", component="z",
                 lut="cool_to_warm")

            # Custom output name
            omega = curl_vector(vector_field=vel, output_field="omega")
            show(omega, "spin", color_by="omega", component="z")

        Notes:
            - The result uses cell-derivative accuracy; smooth the data first for
              cleaner results.
            - For the scalar rotation intensity use ``curl_magnitude()``.
            - Related: ``gradient()``.
        """
        if output_field is None:
            output_field = "vorticity"
        vort = self.filter("vtkCellDerivatives", input=vector_field,
            VectorMode="ComputeVorticity", TensorMode="PassTensors")
        vort_pts = self.filter("vtkCellDataToPointData", input=vort)
        # Rename VTK's capital-V "Vorticity" intermediate array to snake_case
        return self.filter("vtkArrayCalculator", input=vort_pts,
            AddVectorArrayName=["Vorticity"],
            Function="Vorticity",
            ResultArrayName=output_field)

    def curl_magnitude(self, vector_field, field=None, output_field=None):
        """Compute the scalar magnitude |∇ × F| of a vector field's curl.

        Returns the vorticity magnitude as a scalar (1-component) array.
        Useful for visualising total rotational intensity without needing to
        pick a specific axis component.

        Internally uses ``vtkCellDerivatives`` → ``vtkCellDataToPointData``
        then a magnitude calculator.  VTK names the intermediate per-cell array
        ``Vorticity`` (capital V); this wrapper hides that name entirely.

        Args:
            vector_field: Input ``NodeRef`` whose active vector array is used.
                          Must have a 3-component vector (set with ``make_vector()``).
            field (str): Name of the vector array to differentiate.  Optional — if
                         omitted the dataset's active vector attribute is used.
            output_field (str): Name for the output scalar array.
                                Defaults to ``"vorticity_magnitude"``.

        Returns:
            A ``NodeRef`` with the ``output_field`` scalar array added.

        Example::

            vel = make_vector(input=data, components=("u","v","w"), result="velocity")

            # Scalar magnitude of rotation
            vort_mag = curl_magnitude(vector_field=vel)
            show(vort_mag, "spinning", color_by="vorticity_magnitude",
                 scalar_range=(0, 0.5))

            # Custom output name
            mag = curl_magnitude(vector_field=vel, output_field="spin_intensity")
            show(mag, "spin", color_by="spin_intensity", scalar_range=(0, 5))

        Notes:
            - The result uses cell-derivative accuracy; smooth the data first for
              cleaner results.
            - For the full 3-component vorticity vector use ``curl_vector()``.
            - Related: ``gradient()``, ``compute_gradient_magnitude()``.
        """
        if output_field is None:
            output_field = "vorticity_magnitude"
        vort = self.filter("vtkCellDerivatives", input=vector_field,
            VectorMode="ComputeVorticity", TensorMode="PassTensors")
        vort_pts = self.filter("vtkCellDataToPointData", input=vort)
        return self.filter("vtkArrayCalculator", input=vort_pts,
            AddVectorArrayName=["Vorticity"],
            Function="mag(Vorticity)",
            ResultArrayName=output_field)

    def compute_gradient_magnitude(self, input=None, field=None, result=None):
        """Compute the gradient magnitude of a scalar field to highlight boundaries.

        Computes ``|∇f| = sqrt((∂f/∂x)² + (∂f/∂y)² + (∂f/∂z)²)`` for the named
        field and adds it as a new scalar array.  High values indicate regions where
        the field changes rapidly — useful for edge detection, highlighting flame
        fronts, density discontinuities, etc.

        Args:
            input: Input ``NodeRef`` containing the scalar field.
            field (str): Name of the scalar array to differentiate.
            result (str): Name for the output magnitude array.
                          Defaults to ``"{field}_gradient_mag"``.

        Returns:
            A ``NodeRef`` with the gradient-magnitude scalar added.

        Example::

            # Find temperature boundaries (e.g. fire front)
            gm = compute_gradient_magnitude(input=data, field="temperature",
                                            result="temp_edges")
            show(data, "edges", color_by="temp_edges",
                 scalar_range=(0, 50), lut="fire",
                 scalar_bar="∇T magnitude")

        Notes:
            - Internally builds a ``gradient()`` node then a ``calculator()``
              to take the magnitude.
            - Related: ``gradient()`` for the raw vector gradient,
              ``contour()`` for isosurface at a specific value.
        """
        if result is None:
            result = f"{field}_gradient_mag"
        grad = self.filter("vtkGradientFilter", input=input,
            GradientField=field, ResultArrayName=f"{field}_gradient")
        return self.filter("vtkArrayCalculator", input=grad,
            AddVectorArrayName=[f"{field}_gradient"],
            Function=f"mag({field}_gradient)",
            ResultArrayName=result)

    def compute_magnitude(self, input=None, components=("u", "v", "w"), result="speed"):
        """Compute the Euclidean magnitude of three scalar components.

        Creates a new scalar array ``sqrt(cx^2 + cy^2 + cz^2)`` from the named
        component arrays.  Useful for computing wind speed from U/V/W, total
        displacement magnitude from component displacements, etc.

        Args:
            input: Input ``NodeRef`` containing the scalar component arrays.
            components (tuple): Names of the three scalar arrays
                                 (default ``("u", "v", "w")``).
            result (str): Name for the new magnitude array (default ``"speed"``).

        Returns:
            A ``NodeRef`` with the magnitude scalar added.

        Example::

            # Compute wind speed from U, V, W components
            speed = compute_magnitude(input=data,
                                      components=("u", "v", "w"),
                                      result="speed")
            show(data, "wind_speed", color_by="speed",
                 scalar_range=(0, 30), lut="wind")

        Notes:
            - The three component arrays must exist as point scalars.
            - Related: ``make_vector()`` to assemble the vector itself,
              ``curl_magnitude()`` for vorticity magnitude.
        """
        expr = "+".join(f"{c}*{c}" for c in components)
        return self.filter("vtkArrayCalculator", input=input,
            AddScalarArrayName=list(components),
            Function=f"sqrt({expr})",
            ResultArrayName=result)

    def slice(self, input=None, origin=None, normal=None, **props):
        """Cut a 2-D cross-section through a dataset with a plane.

        Intersects the dataset with an infinite plane, producing a 2-D surface
        of polydata.  Unlike ``clip()``, which removes half the dataset, ``slice()``
        produces only the thin cross-section at the cut plane.  Use it to show
        internal structure in an otherwise opaque volume.

        Args:
            input: Input ``NodeRef`` to cut through.
            origin (list): A point ``[x, y, z]`` on the cutting plane.
            normal (list): Normal vector ``[nx, ny, nz]`` of the cutting plane.
                           E.g. ``(0, 0, 1)`` for a horizontal XY cut.
            **props: Additional VTK properties forwarded to ``vtkCutter``.

        Returns:
            A ``NodeRef`` containing the cross-section surface (vtkPolyData).

        Example::

            # Horizontal cross-section at mid-altitude
            xsec = slice(input=data, origin=(500, 400, 50), normal=(0, 0, 1))
            show(xsec, "slice", color_by="temperature",
                 scalar_range=(300, 1200), opacity=0.8)

            # Vertical cross-section through a plume
            vert = slice(input=data, origin=(500, 400, 0), normal=(1, 0, 0))
            show(vert, "vert_cut", color_by="w", lut="cool_to_warm")

        Notes:
            - The output is always 2-D polydata, even if the input is 3-D.
            - Multiple slices can be shown simultaneously with transparency.
            - Related: ``clip()`` to keep half the volume, ``contour()`` to
              extract an isosurface.
        """
        props["CutFunction"] = dict(type="Plane", Origin=origin, Normal=normal)
        return self.filter("vtkCutter", input=input, **props)

    def raw_source(self, filename, dimensions=(1, 1, 1), scalar_type="unsigned_char",
                   header_size=0, num_components=1):
        """Load a raw binary volume file (e.g. CT scans, MRI, simulation dumps).

        Reads a headerless binary volume file directly into a ``vtkImageData``
        regular grid.  You must supply the grid dimensions and data type manually
        because raw files have no metadata.

        Args:
            filename (str): Path to the .raw binary file.
            dimensions (tuple): ``(nx, ny, nz)`` — number of voxels along each axis.
            scalar_type (str or int): Data type string.  Supported values:
                ``"unsigned_char"`` (8-bit uint), ``"unsigned_short"`` (16-bit uint),
                ``"short"`` (16-bit int), ``"int"`` (32-bit int), ``"float"``
                (32-bit float), ``"double"`` (64-bit float), and ``"char"``,
                ``"unsigned_int"``.  Or pass a raw VTK type constant.
            header_size (int): Bytes to skip at the start of the file (default 0).
            num_components (int): Number of scalar components per voxel
                                   (default 1 for grayscale; 3 for RGB).

        Returns:
            A ``NodeRef`` containing the loaded ``vtkImageData`` volume.

        Raises:
            ValueError: If ``scalar_type`` string is not recognized.

        Example::

            # 16-bit CT scan (256x256x128 voxels)
            ct = raw_source("scan.raw", dimensions=(256, 256, 128),
                            scalar_type="unsigned_short")
            show(ct, "ct_vol", representation="Volume",
                 opacity_function="ct_bone", lut="grayscale")

            # Float32 simulation data with a 256-byte header
            sim = raw_source("sim_output.raw", dimensions=(512, 512, 256),
                             scalar_type="float", header_size=256)
            show(sim, "sim", representation="Volume",
                 color_by=None, scalar_range=(0.0, 1.0))

        Notes:
            - For standard VTK files (.vts, .vti, etc.) use ``source()`` instead.
            - ``describe_data()`` is very helpful after loading to inspect the data.
        """
        if isinstance(scalar_type, str):
            vtk_type = SCALAR_TYPE_MAP.get(scalar_type)
            if vtk_type is None:
                raise ValueError(
                    f"Unknown scalar type '{scalar_type}'. "
                    f"Available: {sorted(SCALAR_TYPE_MAP.keys())}"
                )
        else:
            vtk_type = scalar_type

        nx, ny, nz = dimensions
        return self.source("vtkImageReader2",
                           FileName=filename,
                           DataExtent=[0, nx - 1, 0, ny - 1, 0, nz - 1],
                           DataScalarType=vtk_type,
                           FileDimensionality=3,
                           HeaderSize=header_size,
                           NumberOfScalarComponents=num_components)

    def line_probe(self, input=None, point1=None, point2=None, resolution=100):
        """Create a 1-D probe that samples field values along a line.

        Places ``resolution`` evenly spaced sample points between ``point1``
        and ``point2``, then probes the input dataset at each location.  The
        result is a polydata line with field values interpolated at each point.

        After creating a ``line_probe`` node, use the MCP tool ``profile()``
        to visualize the field values as a formatted table with statistics.

        Args:
            input: Input ``NodeRef`` — the volumetric dataset to sample from.
            point1 (list): Start point ``[x, y, z]``.
            point2 (list): End point ``[x, y, z]``.
            resolution (int): Number of sample points along the line (default 100).

        Returns:
            A ``NodeRef`` containing the probed line (vtkPolyData with field values
            at each sample point).

        Example::

            # Vertical temperature profile through a fire plume
            prof = line_probe(input=data,
                              point1=[500, 400, 0],
                              point2=[500, 400, 200],
                              resolution=200)
            # Then use the profile() MCP tool to read the values:
            # profile("prof", [500,400,0], [500,400,200], fields=["temperature"])

        Notes:
            - For ad-hoc profiling without modifying the pipeline, use the
              ``profile()`` MCP tool directly instead.
            - Related: ``probe()``, ``slice()``.
        """
        self._node_counter += 1
        node_id = self._node_counter
        ref = NodeRef(self, node_id, "_line_probe", {
            "input_ref": input,
            "point1": point1,
            "point2": point2,
            "resolution": resolution,
        }, input_ref=input)
        self._nodes.append((node_id, ref))
        return ref

    def show(self, node, name=None, **display_props):
        """Add a pipeline node to the rendered scene.

        This is the most important DSL form — every visualization layer needs
        a ``show()`` call to become visible.  It creates either a standard surface
        actor (default) or a volume actor (when ``representation="Volume"``).

        Args:
            node: A ``NodeRef`` returned by ``source()``, ``filter()``,
                  ``threshold()``, ``contour()``, or any other filter form.
            name (str): Unique name for this actor in the scene.
                        Defaults to the node's auto-name.

        Keyword Display Properties (surface / actor):
            color_by (str): Name of a point or cell array to color by.
                            When omitted, VTK uses whatever the active scalar is.
            scalar_range (tuple): ``(min, max)`` — the value range mapped to the
                                   full colormap.  Values outside this range are
                                   clamped to the colormap endpoints.
                                   Use percentiles from ``describe_data(field=)`` to choose good defaults.
            lut (str): Colormap preset name.  Available presets: ``"fire"``,
                       ``"terrain"``, ``"wind"``, ``"cool_to_warm"``,
                       ``"blue_to_red"``, ``"grayscale"``,
                       ``"oxygen"``, ``"heat"``.
                       Use ``get_dsl_overview()`` for the complete list.
            opacity (float): Actor opacity from 0.0 (invisible) to 1.0 (opaque).
            color (tuple): Solid RGB color ``(r, g, b)`` as floats 0–1.
                           Used instead of ``color_by`` for uniform coloring.
            component (int or str): For multi-component (vector) fields: which
                                     component to color by.  0/1/2 or
                                     ``"x"``/``"y"``/``"z"``.
            representation (str): ``"Surface"`` (default), ``"Wireframe"``,
                                   ``"Points"``, or ``"Volume"`` for volume rendering.
            specular (float): Specular highlight intensity (0.0–1.0).  Adds
                               shininess to surfaces.
            specular_power (float): Specular exponent — higher = smaller, sharper
                                     highlights (default 1.0).
            line_width (float): Line width in pixels for wireframe or streamline actors.
            scalar_bar (bool or str): Add a color legend to the scene.  Pass
                                       ``True`` to use the field name as the title,
                                       or a string for a custom title.

        Keyword Display Properties (volume rendering — ``representation="Volume"``):
            opacity_function (list or str): Opacity transfer function control
                points: ``[(value, opacity), ...]``.  Or a preset string such as
                ``"fire"``, ``"ct_bone"``, ``"ct_soft"``, ``"ramp_up"``,
                ``"gaussian"``.
            gradient_opacity (bool or list): Edge-enhanced opacity.  ``True``
                applies a default gradient ramp; a list of ``(gradient, opacity)``
                tuples defines a custom curve.
            volume_resolution (int or list): Resampling resolution for non-image
                data (default 256, capped at 512).  Pass an integer (longest-axis
                resolution, proportional for others) or a ``[nx, ny, nz]`` list.
                Reduce for faster rendering; increase for detail.
            shade (bool): Phong shading for the volume (default True).  Gives
                          directional lighting effects.
            sample_distance (float): Ray-casting step size; smaller = higher
                quality but slower.
            clip_planes (list): Clipping planes as a list of
                ``{"origin": [x,y,z], "normal": [nx,ny,nz]}`` dicts.

        Example::

            # Surface — color by field
            show(data, "field", color_by="temperature",
                 scalar_range=(300, 1200), lut="heat",
                 scalar_bar="Temperature (K)")

            # Isosurface with solid color
            show(iso, "flame", color=(1.0, 0.4, 0.0), opacity=0.8,
                 specular=0.5, specular_power=30)

            # Volume rendering
            show(region, "vol",
                 representation="Volume",
                 color_by="temperature",
                 scalar_range=(300, 1200),
                 lut="fire",
                 opacity_function=[(300,0),(600,0.02),(1200,0.5)],
                 gradient_opacity=True,
                 volume_resolution=200)

            # Color by a single vector component
            show(data, "w_vel", color_by="velocity", component="z",
                 scalar_range=(-10, 10), lut="cool_to_warm")

        Notes:
            - Call ``describe_data(node=node, field=field)`` before choosing ``scalar_range``.
            - ``scalar_bar`` adds a 2-D color legend overlay to the scene.
            - Multiple ``show()`` calls create multiple layers composited together.
        """
        self._shows.append((node, name, display_props))

    def camera(self, position=None, focal_point=None, up=None, zoom=None):
        """Set the scene camera position and orientation.

        Camera state is saved alongside the pipeline and restored on every
        ``run_pipeline()`` run.  Call ``suggest_camera()`` from the MCP layer
        to get good starting values, then paste them here.

        Args:
            position (list): Camera world-space position ``[x, y, z]``.
            focal_point (list): The point the camera looks toward ``[x, y, z]``.
            up (list): Camera up vector ``[x, y, z]`` (default ``[0, 0, 1]``).
            zoom (float): Zoom factor applied after positioning.  Values > 1.0
                           zoom in; values < 1.0 zoom out.

        Example::

            # Explicit position from suggest_camera()
            camera(position=(500, -800, 300),
                   focal_point=(500, 500, 50),
                   up=(0, 0, 1))

            # Just zoom without moving
            camera(zoom=1.5)

        Notes:
            - Use this form only for reproducible exports where a fixed camera angle
              is required. For interactive sessions, use ``set_suggested_camera()``
              or ``set_camera()`` MCP tools instead — those preserve human adjustments.
            - All four parameters are optional — pass only what you want to change.
        """
        self._camera = {
            "position": position,
            "focal_point": focal_point,
            "up": up,
            "zoom": zoom,
        }

    def title(self, text, position="top", font_size=24, color=(1, 1, 1)):
        """Add a text annotation overlay to the scene.

        Renders a billboard text label fixed in 2-D screen space, useful for
        labeling screenshots with dataset names, timestamps, or parameter values.

        Args:
            text (str): The text to display.
            position (str): Screen anchor — ``"top"`` (default), ``"bottom"``,
                            ``"left"``, ``"right"``, or ``"center"``.
            font_size (int): Font size in points (default 24).
            color (tuple): RGB color as floats 0–1 (default white ``(1,1,1)``).

        Example::

            title("Wildfire Simulation — t = 30 s",
                  position="top", font_size=20, color=(1, 1, 1))

        Notes:
            - Only one title per scene is supported (the last call wins).
            - For individual data labels in 3-D space, use the DSL
              ``annotate()`` form instead.
        """
        self._title = {"text": text, "position": position, "font_size": font_size, "color": color}

    def annotate(self, x, y, z, text, color="white", font_size=14):
        """Add a 3-D billboard text annotation at a world-space position.

        Renders a ``vtkBillboardTextActor3D`` that always faces the camera, so
        the label remains readable from any viewing angle.  Multiple calls
        accumulate — each ``annotate()`` call in a pipeline spec adds one label.
        Because pipeline specs are declarative, simply omitting ``annotate()``
        calls in the next rebuild removes all previous labels.

        Args:
            x (float): World-space X coordinate for the label.
            y (float): World-space Y coordinate for the label.
            z (float): World-space Z coordinate for the label.
            text (str): Text to display.
            color: Text color — named CSS color (``"white"``, ``"red"``,
                   ``"yellow"``, …), hex string (``"#ff8800"``), or an RGB
                   float tuple ``(r, g, b)`` with components in 0–1.
                   Defaults to ``"white"``.
            font_size (int): Font size in points.  Defaults to 14.

        Example::

            annotate(0, 0, 0, "origin")
            annotate(1, 0, 0, "x-axis", color="red")
            annotate(0, 0, 50, "fire front", color="#ff8800", font_size=16)

        Notes:
            - Multiple annotations with the same text are allowed — text is
              not used as a unique key.
            - For a single scene-title overlay in screen space, use
              ``title()`` instead.
        """
        self._annotations.append(
            {"x": x, "y": y, "z": z, "text": text, "color": color, "font_size": font_size}
        )

    def axes(self, color=(1, 1, 1), font_size=14,
             labels=("X", "Y", "Z")):
        """Add labeled X/Y/Z axes with tick marks to the scene.

        Draws a cube-axes actor around the scene bounds showing physical
        (world) coordinates with tick marks and axis labels.  Useful for
        orienting the human viewer and cross-referencing coordinates reported
        by describe_data() and get_spatial_extent().

        Args:
            color (tuple): RGB color for axes, labels, and ticks (default white).
            font_size (int): Label font size in points (default 14).
            labels (tuple): Axis label strings, e.g. ``("X (m)", "Y (m)", "Z (m)")``.

        Example::

            axes(color=(1, 1, 1), labels=("X (m)", "Y (m)", "Z (m)"))
            scene_preset("dark")
        """
        self._axes = {"color": color, "font_size": font_size, "labels": labels}

    def background(self, r, g, b):
        """Set the scene background color.

        Provides direct RGB control over the background.  For convenience presets
        (dark, light, black, white), use ``scene_preset()`` instead.

        Args:
            r (float): Red channel (0.0–1.0).
            g (float): Green channel (0.0–1.0).
            b (float): Blue channel (0.0–1.0).

        Example::

            background(0.05, 0.05, 0.1)   # dark blue
            background(1.0, 1.0, 1.0)     # white (publication-ready)
            background(0.0, 0.0, 0.0)     # pure black

        Notes:
            - Calling ``scene_preset()`` after ``background()`` will override it.
            - Related: ``scene_preset()`` for named presets.
        """
        self._background = (r, g, b)

    def scene_preset(self, name="dark"):
        """Apply a named background color preset for the scene.

        Convenience wrapper around ``background()`` with curated values for
        common use cases.  Always place this at the end of a pipeline so it
        does not get overridden by a later ``background()`` call.

        Args:
            name (str): Preset name — one of:
                - ``"dark"`` — dark blue/charcoal (default; great for colorful data)
                - ``"light"`` — soft light gray (good for solid objects/surfaces)
                - ``"black"`` — pure black (maximum contrast)
                - ``"white"`` — pure white (publication/paper figures)

        Raises:
            ValueError: If ``name`` is not one of the available presets.

        Example::

            show(iso, "flame", color_by="temperature", lut="fire")
            show(outline(input=data), "bbox", color=(1,1,1), opacity=0.2)
            camera(position=(500,-800,300), focal_point=(500,500,50))
            scene_preset("dark")

        Notes:
            - Overrides any earlier ``background()`` call in the same pipeline.
            - Related: ``background()`` for a custom RGB value.
        """
        presets = {
            "dark": (0.02, 0.02, 0.06),
            "light": (0.85, 0.85, 0.9),
            "black": (0.0, 0.0, 0.0),
            "white": (1.0, 1.0, 1.0),
        }
        if name not in presets:
            raise ValueError(f"Unknown scene preset '{name}'. Available: {sorted(presets.keys())}")
        self._background = presets[name]

    # ------------------------------------------------------------------
    # Content hashing for incremental caching
    # ------------------------------------------------------------------

    def _compute_content_hash(self, ref, node_hash_map):
        """Return a stable sha256 hex string for a pipeline node.

        Hashes: node kind, scalar params (with file fingerprints for FileName),
        and sorted input hashes from node_hash_map.
        """
        import hashlib

        def _hash_props(props):
            parts = {}
            for k, v in props.items():
                if k == "FileName" and isinstance(v, str):
                    parts[k] = _file_fingerprint(v)
                elif isinstance(v, NodeRef):
                    # NodeRef resolved at build time — hash the input's hash
                    h = node_hash_map.get(v._node_id, "missing")
                    parts[k] = h
                else:
                    parts[k] = stable_hash(v)
            return stable_hash(parts)

        kind_hash = stable_hash(ref.vtk_class)
        props_hash = _hash_props(ref.properties)
        input_hash = node_hash_map.get(ref.input_ref._node_id, "none") if ref.input_ref else "none"

        raw = f"kind:{kind_hash}|props:{props_hash}|input:{input_hash}"
        return hashlib.sha256(raw.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Private helpers for build(): one method per special node type
    # ------------------------------------------------------------------

    def _build_extract_region_node(self, node_id, ref, vtk_objects, node_statuses):
        """Handle the _extract_region pseudo-class.

        Picks vtkExtractVOI or vtkExtractGrid based on the input data type,
        converts physical bounds to grid indices automatically.
        """
        bounds = ref.properties.get("bounds")
        if bounds is None:
            msg = (
                "extract_region: missing required 'bounds' argument; "
                "expected (xmin, xmax, ymin, ymax, zmin, zmax)"
            )
            node_statuses[node_id] = _diag.error(
                "_extract_region",
                _diag.KIND_MISSING_REQUIRED_ARG,
                msg,
                arg="bounds",
                expected="(xmin, xmax, ymin, ymax, zmin, zmax)",
            )
            return

        input_alg = vtk_objects.get(ref.input_ref._node_id)
        if input_alg is None:
            node_statuses[node_id] = _diag.error(
                "_extract_region",
                _diag.KIND_OTHER,
                "Input node not built",
            )
            return

        try:
            input_alg.Update()
            input_data = input_alg.GetOutput()

            cls_name = input_data.GetClassName()
            if cls_name in ("vtkImageData", "vtkUniformGrid"):
                filter_class = "vtkExtractVOI"
            else:
                filter_class = "vtkExtractGrid"

            extra_props = dict(ref.properties.get("extra_props", {}))
            voi = physical_bounds_to_voi(input_data, ref.properties["bounds"])
            extra_props["VOI"] = voi

            vtk_obj, status = create_vtk_filter(filter_class, input_alg, **extra_props)
            status["extract_filter"] = filter_class
            status["physical_bounds"] = ref.properties["bounds"]
            status["computed_voi"] = voi
            vtk_objects[node_id] = vtk_obj
            node_statuses[node_id] = status
        except Exception as e:
            node_statuses[node_id] = _diag.error(
                "_extract_region", _diag.KIND_OTHER, str(e)
            )

    def _build_extract_component_node(self, node_id, ref, vtk_objects, node_statuses):
        """Handle the _extract_component pseudo-class."""
        input_alg = vtk_objects.get(ref.input_ref._node_id)
        if input_alg is None:
            node_statuses[node_id] = _diag.error(
                "_extract_component", _diag.KIND_OTHER, "Input node not built"
            )
            return

        try:
            result_alg, status = extract_component(
                input_alg,
                field=ref.properties["field"],
                component=ref.properties["component"],
                result_name=ref.properties["result_name"],
            )
            vtk_objects[node_id] = result_alg
            node_statuses[node_id] = status
        except Exception as e:
            node_statuses[node_id] = _diag.error(
                "_extract_component", _diag.KIND_OTHER, str(e)
            )

    def _build_line_probe_node(self, node_id, ref, vtk_objects, node_statuses):
        """Handle the _line_probe pseudo-class.

        Validates point1/point2, then creates a vtkLineSource + vtkProbeFilter
        pipeline inline, recording a node error status if endpoints are missing.
        """
        from .filters import create_vtk_filter

        point1 = ref.properties.get("point1")
        point2 = ref.properties.get("point2")

        if point1 is None or point2 is None:
            missing = []
            if point1 is None:
                missing.append("point1")
            if point2 is None:
                missing.append("point2")
            msg = (
                f"line_probe: missing required argument(s) {missing}; "
                "expected point1=[x, y, z] and point2=[x, y, z]"
            )
            node_statuses[node_id] = _diag.error(
                "_line_probe",
                _diag.KIND_MISSING_REQUIRED_ARG,
                msg,
                arg=missing[0] if len(missing) == 1 else "point1,point2",
                expected="[x, y, z]",
            )
            return

        input_alg = vtk_objects.get(ref.input_ref._node_id)
        if input_alg is None:
            node_statuses[node_id] = _diag.error(
                "_line_probe", _diag.KIND_OTHER, "Input node not built"
            )
            return

        try:
            resolution = ref.properties.get("resolution", 100)
            line_alg, _ = create_vtk_filter(
                "vtkLineSource",
                None,
                Point1=list(point1),
                Point2=list(point2),
                Resolution=resolution,
            )
            probe_alg, status = create_vtk_filter(
                "vtkProbeFilter",
                line_alg,
                _probe_source=input_alg,
            )
            status["point1"] = list(point1)
            status["point2"] = list(point2)
            status["resolution"] = resolution
            vtk_objects[node_id] = probe_alg
            node_statuses[node_id] = status
        except Exception as e:
            node_statuses[node_id] = _diag.error(
                "_line_probe", _diag.KIND_OTHER, str(e)
            )

    def _build_generic_node(self, node_id, ref, input_alg, vtk_objects, node_statuses):
        """Handle all standard VTK filter/source nodes."""
        try:
            # Resolve any NodeRef values in properties to actual VTK objects
            props = dict(ref.properties)
            for k, v in props.items():
                if isinstance(v, NodeRef):
                    if v._node_id not in vtk_objects:
                        raise ValueError(
                            f"Property '{k}' references node that failed to build"
                        )
                    props[k] = vtk_objects[v._node_id]

            vtk_obj, status = create_vtk_filter(ref.vtk_class, input_alg, **props)
            vtk_objects[node_id] = vtk_obj
            node_statuses[node_id] = status
        except _UnknownPropertyError as e:
            s = e.structured
            node_statuses[node_id] = _diag.error(
                ref.vtk_class,
                _diag.KIND_UNKNOWN_PROPERTY,
                s["message"],
                property=s["property"],
                vtk_class=s["vtk_class"],
                similar=s["similar"],
                valid=s["valid"],
            )
        except Exception as e:
            node_statuses[node_id] = _diag.error(
                ref.vtk_class, _diag.KIND_OTHER, str(e)
            )

    def _build_show_directives(self, vtk_objects, renderer):
        """Create actors/volumes from show() directives and add them to the renderer.

        Returns show_statuses dict.
        """
        show_statuses = {}
        for node_ref, show_name, display_props in self._shows:
            vtk_alg = vtk_objects.get(node_ref._node_id)
            if vtk_alg is None:
                key = show_name or "?"
                show_statuses[key] = _diag.error(
                    key, _diag.KIND_OTHER, "Node not built (dependency error)"
                )
                continue
            try:
                result = create_show(vtk_alg, **display_props)
                if isinstance(result, tuple):
                    actor, bar_actor = result
                else:
                    actor, bar_actor = result, None

                actor_name = show_name or f"show_{node_ref._node_id}"
                if isinstance(actor, vtk.vtkVolume):
                    renderer.add_volume(actor_name, actor)
                else:
                    renderer.add_actor(actor_name, actor)
                if bar_actor:
                    sb = display_props.get("scalar_bar")
                    title_text = sb if isinstance(sb, str) else display_props.get("color_by", "")
                    title_actor = vtk.vtkTextActor()
                    title_actor.SetInput(title_text)
                    tp = title_actor.GetTextProperty()
                    tp.SetFontSize(15)
                    tp.SetColor(1, 1, 1)
                    tp.SetJustificationToRight()
                    tp.SetVerticalJustificationToCentered()
                    tp.BoldOn()
                    tp.ShadowOff()
                    renderer.add_scalar_bar(actor_name, bar_actor, title_actor)
                show_statuses[actor_name] = {"status": "ok"}
            except Exception as e:
                key = show_name or "?"
                show_statuses[key] = _diag.error(key, _diag.KIND_OTHER, str(e))
        return show_statuses

    def _apply_scene_settings(self, renderer):
        """Apply background, camera, and title settings to the renderer."""
        if self._background:
            renderer.set_background(*self._background)

        if self._camera:
            renderer.set_camera(**{k: v for k, v in self._camera.items() if v is not None})
        elif not renderer._camera_positioned:
            result = renderer.suggest_camera("overview")
            if result:
                renderer.set_camera(**result)
            else:
                renderer.reset_camera()
            renderer._camera_positioned = True

        if self._title:
            text_actor = vtk.vtkTextActor()
            text_actor.SetInput(self._title["text"])
            tp = text_actor.GetTextProperty()
            tp.SetFontSize(self._title["font_size"])
            tp.SetColor(*self._title["color"])
            tp.SetFontFamilyToArial()
            tp.SetBold(True)
            tp.SetShadow(True)

            pos = self._title.get("position", "top")
            if pos == "top":
                text_actor.SetPosition(20, renderer._render_window.GetSize()[1] - 50)
            elif pos == "bottom":
                text_actor.SetPosition(20, 20)
            elif isinstance(pos, tuple):
                text_actor.SetPosition(*pos)

            renderer.add_overlay_actor(text_actor)

        for ann in self._annotations:
            actor = vtk.vtkBillboardTextActor3D()
            actor.SetInput(ann["text"])
            actor.SetPosition(ann["x"], ann["y"], ann["z"])
            # Exclude annotation actors from ComputeVisiblePropBounds() so that
            # labels placed far from the data do not stretch the cube-axes bounds.
            actor.UseBoundsOff()
            tp = actor.GetTextProperty()
            r, g, b = _coerce_color(ann["color"])
            tp.SetColor(r, g, b)
            tp.SetFontSize(ann["font_size"])
            tp.SetBold(False)
            tp.SetItalic(False)
            tp.SetShadow(True)
            renderer.add_overlay_actor(actor)

        if self._axes:
            renderer._ensure_initialized()
            cube_axes = vtk.vtkCubeAxesActor()
            cube_axes.SetBounds(renderer._renderer.ComputeVisiblePropBounds())
            cube_axes.SetCamera(renderer._renderer.GetActiveCamera())
            r, g, b = self._axes["color"]
            cube_axes.GetTitleTextProperty(0).SetColor(r, g, b)
            cube_axes.GetTitleTextProperty(1).SetColor(r, g, b)
            cube_axes.GetTitleTextProperty(2).SetColor(r, g, b)
            cube_axes.GetLabelTextProperty(0).SetColor(r, g, b)
            cube_axes.GetLabelTextProperty(1).SetColor(r, g, b)
            cube_axes.GetLabelTextProperty(2).SetColor(r, g, b)
            cube_axes.GetXAxesLinesProperty().SetColor(r, g, b)
            cube_axes.GetYAxesLinesProperty().SetColor(r, g, b)
            cube_axes.GetZAxesLinesProperty().SetColor(r, g, b)
            fs = self._axes["font_size"]
            for i in range(3):
                cube_axes.GetTitleTextProperty(i).SetFontSize(fs)
                cube_axes.GetLabelTextProperty(i).SetFontSize(fs)
            labels = self._axes["labels"]
            cube_axes.SetXTitle(labels[0])
            cube_axes.SetYTitle(labels[1])
            cube_axes.SetZTitle(labels[2])
            cube_axes.SetFlyModeToOuterEdges()
            cube_axes.DrawXGridlinesOff()
            cube_axes.DrawYGridlinesOff()
            cube_axes.DrawZGridlinesOff()
            renderer.add_actor("__axes__", cube_axes)

    # ------------------------------------------------------------------
    # Main build entry point
    # ------------------------------------------------------------------

    def _build_pipeline(self, cache=None):
        """Build VTK filter graph and run Update() on all nodes.

        This is the expensive compute step (data I/O, filter execution).
        It does NOT touch the renderer and is safe to call from any thread.

        When *cache* is provided (a BuildCache), nodes whose content hash
        matches a previous run are reused without rebuilding.

        Returns (vtk_objects, node_statuses).
        """
        vtk_objects = {}     # node_id -> vtk_algorithm
        node_statuses = {}
        node_hash_map = {}   # node_id -> content hash (for cache + child hashing)
        failed_nodes = {}    # node_id -> first upstream failure id (or self) for cascade tracking

        if cache is not None:
            cache.begin_run()

        # Build nodes in declaration order (inputs always precede dependents)
        for node_id, ref in self._nodes:
            input_alg = None
            if ref.input_ref is not None:
                input_alg = vtk_objects.get(ref.input_ref._node_id)

            # Compute content hash for this node (used regardless of cache)
            h = self._compute_content_hash(ref, node_hash_map)
            node_hash_map[node_id] = h

            if cache is not None:
                cached = cache.get(h)
                if cached is not None:
                    vtk_objects[node_id] = cached
                    cache.touch(h)
                    cache.hits += 1
                    node_statuses[node_id] = {"cached": True, "class": ref.vtk_class}
                    continue
                cache.misses += 1

            # --- Cascade-skip: if any direct dependency failed, skip this node ---
            # Check both the pipeline input and any NodeRef-valued properties.
            failed_upstream_id = None
            if ref.input_ref is not None and ref.input_ref._node_id in failed_nodes:
                failed_upstream_id = ref.input_ref._node_id
            else:
                for prop_val in ref.properties.values():
                    if isinstance(prop_val, NodeRef) and prop_val._node_id in failed_nodes:
                        failed_upstream_id = prop_val._node_id
                        break
            if failed_upstream_id is not None:
                node_statuses[node_id] = _diag.skipped(ref.vtk_class, failed_upstream_id)
                failed_nodes[node_id] = failed_upstream_id
                continue

            if ref.vtk_class == "_extract_region":
                self._build_extract_region_node(node_id, ref, vtk_objects, node_statuses)
            elif ref.vtk_class == "_extract_component":
                self._build_extract_component_node(node_id, ref, vtk_objects, node_statuses)
            elif ref.vtk_class == "_line_probe":
                self._build_line_probe_node(node_id, ref, vtk_objects, node_statuses)
            else:
                self._build_generic_node(node_id, ref, input_alg, vtk_objects, node_statuses)

            # If the node failed (error recorded but no vtk object produced), track it
            if node_id not in vtk_objects and node_id in node_statuses:
                if node_statuses[node_id].get("status") == _diag.STATUS_ERROR:
                    failed_nodes[node_id] = node_id

            if cache is not None and node_id in vtk_objects:
                cache.put(h, vtk_objects[node_id])
                cache.touch(h)

        if cache is not None:
            stats = cache.end_run()
            import logging
            logging.getLogger("vislang").info(
                "Cache: %d hits, %d misses, %d evicted, %d kept",
                stats["hits"], stats["misses"], stats["evictions"], stats["kept"],
            )

        return vtk_objects, node_statuses

    def _apply_to_renderer(self, vtk_objects, renderer):
        """Swap actors into the renderer and render.

        This is the cheap scene-update step that must run on the main thread.

        Returns show_statuses.
        """
        renderer.clear()
        show_statuses = self._build_show_directives(vtk_objects, renderer)
        self._apply_scene_settings(renderer)
        renderer.render()
        return show_statuses

    def _build(self, renderer, cache=None):
        """Build the VTK pipeline and add actors to the renderer.

        Convenience method that calls _build_pipeline() then _apply_to_renderer().
        When both steps run on the same thread, this is equivalent to the
        original single-step build.
        """
        vtk_objects, node_statuses = self._build_pipeline(cache=cache)
        renderer.clear()
        show_statuses = self._build_show_directives(vtk_objects, renderer)
        self._apply_scene_settings(renderer)
        renderer.render()
        return vtk_objects, {}, node_statuses, show_statuses


def _make_namespace(builder):
    """Create the restricted namespace for DSL pipeline execution.

    Builder methods are auto-populated via inspect so that any new method
    added to PipelineBuilder is automatically available in the DSL without a
    manual mapping update.  Only public DSL-facing methods are included;
    infrastructure methods used to *execute* the pipeline (``_build``,
    ``_build_pipeline``, ``_apply_to_renderer``) are private and excluded
    by the underscore filter below.
    """
    namespace = {
        name: method
        for name, method in inspect.getmembers(builder, predicate=inspect.ismethod)
        if not name.startswith("_")
    }

    # Safe builtins
    namespace.update({
        "range": range,
        "zip": zip,
        "enumerate": enumerate,
        "len": len,
        "min": min,
        "max": max,
        "True": True,
        "False": False,
        "None": None,
        "dict": dict,
        "list": list,
        "tuple": tuple,
        "float": float,
        "int": int,
        "abs": abs,
        "round": round,
        "sorted": sorted,
        "sum": sum,
        "math": __import__("math"),
        "print": print,  # Allow debug output
        "__builtins__": {},
    })

    return namespace


def interpret(code, renderer, cache=None):
    """Interpret a DSL code string and build the pipeline.

    Returns (vtk_objects_by_name, node_statuses, show_statuses, builder).
    """
    builder = PipelineBuilder()

    namespace = _make_namespace(builder)
    exec(code, namespace)

    # Build the pipeline
    vtk_objects, _, node_statuses, show_statuses = builder._build(renderer, cache=cache)

    # Extract variable names from namespace
    vtk_objects_by_name = {}
    for var_name, var_value in namespace.items():
        if isinstance(var_value, NodeRef) and var_value._node_id in vtk_objects:
            vtk_objects_by_name[var_name] = vtk_objects[var_value._node_id]
            # Update node status with the variable name
            if var_value._node_id in node_statuses:
                node_statuses[var_value._node_id]["name"] = var_name

    return vtk_objects_by_name, node_statuses, show_statuses, builder


def interpret_build(code, cache=None):
    """Parse DSL code and execute the VTK pipeline (expensive compute step).

    Does NOT touch the renderer. Safe to call from any thread.
    Returns (builder, vtk_objects_by_name, vtk_objects, node_statuses).
    """
    builder = PipelineBuilder()

    namespace = _make_namespace(builder)
    exec(code, namespace)

    # Run all VTK filters
    vtk_objects, node_statuses = builder._build_pipeline(cache=cache)

    # Extract variable names from namespace
    vtk_objects_by_name = {}
    for var_name, var_value in namespace.items():
        if isinstance(var_value, NodeRef) and var_value._node_id in vtk_objects:
            vtk_objects_by_name[var_name] = vtk_objects[var_value._node_id]
            if var_value._node_id in node_statuses:
                node_statuses[var_value._node_id]["name"] = var_name

    return builder, vtk_objects, vtk_objects_by_name, node_statuses
