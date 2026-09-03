"""DSL interpreter for declarative VTK pipeline specifications.

Re-execution is incremental: nodes are content-addressed by (kind, params,
parent hashes), so unchanged subgraphs are reused. See `siva/build_cache.py`
for details.
"""

import inspect
from dataclasses import dataclass, field

from .colormaps import _coerce_color
from .colors import resolve_color
from .spec import (
    Annotation,
    AxesSpec,
    CameraSpec,
    Node,
    Ref,
    SceneSpec,
    Show,
    Spec,
    TitleSpec,
)
from .filters import (
    COMPONENT_INDEX_MAP,
    SCALAR_TYPE_MAP,
)


@dataclass
class NodeRef:
    """Construction-time handle for a pipeline node.

    This is the value spec code passes around (``t = threshold(src, ...)``;
    ``show(t)``). It survives only inside ``construct``; at freeze time it is
    converted to a frozen :class:`~siva.spec.Ref` value inside the owning
    :class:`~siva.spec.Node`'s params. The primary input, when present, lives in
    ``properties`` under the conventional ``"input"`` key (as a ``NodeRef``),
    alongside every other param — there is no privileged separate input slot.

    It is host-only: it never crosses the sandbox boundary. The sandbox holds
    an opaque :class:`~siva.sandbox.NodeHandle` (just the ``node_id``) in its
    place, and the marshalling layer resolves handles back to the canonical
    ``NodeRef`` by id. See :mod:`siva.sandbox` for that model — a full
    ``NodeRef`` embeds its input ``NodeRef`` (and so a whole filter chain),
    which is exactly why only the slim handle is marshalled.
    """

    node_id: int
    vtk_class: str
    properties: dict = field(default_factory=dict)


def _dsl_param_names_from_stack() -> tuple:
    """Collect the keyword-parameter names of every ``PipelineBuilder`` method
    currently on the call stack above ``_add_node`` (which calls this).

    E.g. for ``contour(input=..., ContourBy=...)`` this returns ``("input",)``;
    for ``elevation(input=..., low_point=..., **props)`` it also picks up
    ``low_point``/``high_point`` because ``elevation`` is on the stack between
    the spec code and ``self.filter(...)`` -> ``_add_node``. It's a snapshot of
    "every DSL-level argument name in scope for this call", regardless of how
    many wrapper layers deep the actual node-creating call is.

    Purely a best-effort hint used to widen the 'did you mean' suggestion pool
    when a ``**props`` kwarg mistypes a DSL-level (snake_case) argument name
    instead of the VTK (PascalCase) property it was meant to become -- see
    ``_validate_vtk_kwargs_structured`` in ``siva/filters.py``, which is the
    sole consumer (via the ``"_dsl_param_names"`` key stashed in node params
    by ``_add_node`` below). Never raises; degrades to an empty tuple if the
    stack can't be walked for any reason (e.g. under an interpreter that
    doesn't support frame introspection).
    """
    names: set = set()
    frame = inspect.currentframe()
    try:
        # frame is this function's own; its caller is _add_node -- skip both
        # and start from _add_node's caller (the outermost form the spec
        # code invoked directly, or an intermediate wrapper like filter()).
        frame = frame.f_back
        frame = frame.f_back if frame is not None else None
        while frame is not None:
            code = frame.f_code
            if code.co_filename != __file__:
                break  # left dsl.py -- either the spec code or somewhere unrelated
            self_obj = frame.f_locals.get("self")
            if not isinstance(self_obj, PipelineBuilder):
                break
            method = getattr(type(self_obj), code.co_name, None)
            if method is not None:
                try:
                    for pname, param in inspect.signature(method).parameters.items():
                        if pname == "self":
                            continue
                        if param.kind in (
                            inspect.Parameter.VAR_KEYWORD,
                            inspect.Parameter.VAR_POSITIONAL,
                        ):
                            continue
                        names.add(pname)
                except (TypeError, ValueError):
                    pass
            frame = frame.f_back
    except Exception:
        pass
    finally:
        del frame
    return tuple(sorted(names))


class PipelineBuilder:
    """Records DSL declarations into nodes, shows, and scene state.

    The builder is a pure construction recorder: the build/compute logic lives
    in :mod:`siva.compute` as free functions over the frozen
    :class:`~siva.spec.Spec` that ``_freeze_spec`` produces from a builder.
    """

    def __init__(self):
        self._nodes = []  # list[NodeRef], in declaration order
        self._shows = []  # list[(NodeRef, name, props)], in actor add order
        self._camera = None
        self._background = None
        self._title = None
        self._axes = None
        self._annotations = []  # list of {x, y, z, text, color, font_size}
        self._node_counter = 0

    def _add_node(self, vtk_class, properties, input_ref=None):
        """Allocate a node id, register the node, and return its ``NodeRef``.

        Single owner of id allocation and registration for every builder form.
        Nodes are stored in declaration order, which is a valid topological
        order (inputs are declared before the forms that consume them).

        The primary input (``input_ref``) is folded into ``properties`` under the
        conventional ``"input"`` key, so every edge — primary and secondary —
        lives uniformly in the params. There is no separate input slot.

        Also stashes the calling form's snake_case DSL argument names under
        the internal ``"_dsl_param_names"`` key (see
        ``_dsl_param_names_from_stack``) -- not a VTK property, exempted from
        Set{key} handling in ``filters.py``, and used only to widen "did you
        mean" suggestions for a **props kwarg that mistypes a DSL argument
        name (e.g. ``filter("vtkX", inpt=...)`` suggesting ``input``).
        """
        self._node_counter += 1
        props = dict(properties)
        if input_ref is not None:
            props["input"] = input_ref
        dsl_names = _dsl_param_names_from_stack()
        if dsl_names:
            props["_dsl_param_names"] = dsl_names
        ref = NodeRef(self._node_counter, vtk_class, props)
        self._nodes.append(ref)
        return ref

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
        return self._add_node(vtk_class, props)

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
            - Use ``get_dsl_reference(form="filter")`` to check this form's docs.
            - Use ``get_dsl_overview()`` to see all whitelisted VTK classes.
        """
        return self._add_node(vtk_class, props, input_ref=input)

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

        Uses ``vtkArrayCalculator`` (with the default ``vtkExprTkFunctionParser``,
        a wrapper around the ExprTk expression library) to compute a new scalar
        or vector array from existing arrays using a single-expression string.
        This is the low-level building block used by ``make_vector()``,
        ``compute_magnitude()``, ``curl_vector()``, ``curl_magnitude()``, etc.

        Array names referenced in the expression must be registered with
        ``AddScalarArrayName`` (1-component arrays) or ``AddVectorArrayName``
        (3-component arrays).  The result can be a scalar or a 3-component vector
        depending on the expression.

        EXPRESSION LANGUAGE
        -------------------
        Operators:
            ``+ - * /``         arithmetic (works on scalars, vectors, and
                                scalar*vector / vector/scalar mixes)
            ``^``               power (e.g. ``x^2``)
            ``%``               modulo
            ``< > <= >= == !=`` comparisons (return 1.0 or 0.0)
            ``and or not``      boolean (also ``&& || !``)
            ``cond ? a : b``    ternary

        Scalar functions:
            ``abs, sign, ceil, floor, trunc, round, roundn, frac, hypot``
            ``sqrt, exp, pow(x,y), ln, log10, log2``  (``ln`` is natural log)
            ``sin, cos, tan, asin, acos, atan, atan2, sinh, cosh, tanh``
            ``erf, erfc``
            ``clamp(low, x, high)``       clamp x to [low, high]
            ``inrange(low, x, high)``     1.0 if low <= x <= high else 0.0
            ``min, max, avg, sum, mul``   multi-argument (2+ args each)
            ``if(cond, then, else)``      conditional expression

        Vector functions (require a 3-component array argument):
            ``dot(a, b)``     scalar dot product  a · b
            ``cross(a, b)``   vector cross product  a × b
            ``mag(v)``        scalar magnitude  |v|
            ``norm(v)``       unit vector  v / |v|

            Vectors compose with scalars naturally:
                ``2*v + w``, ``v - w``, ``v / 2``, ``s*v + t*w``

        Predefined names:
            ``pi``                          3.14159…
            ``iHat, jHat, kHat``            unit vectors (1,0,0), (0,1,0), (0,0,1)

        NOT supported (will fail to parse or build):
            - Multi-statement expressions, ``;`` separators, ``var`` declarations
            - ``for``, ``while``, ``switch`` blocks
            - ``inf``, ``epsilon``, ``nan`` constants (use a large finite number)
            - String operations or I/O

        Args:
            input: Input ``NodeRef`` containing the source arrays.
            Function (str): A single math expression that references registered
                            array names.
            ResultArrayName (str): Name for the output array.
            AddScalarArrayName (list): Names of 1-component arrays referenced in
                                       ``Function``.
            AddVectorArrayName (list): Names of 3-component arrays referenced in
                                       ``Function``.
            **props: Additional VTK properties forwarded to ``vtkArrayCalculator``.

        Returns:
            A ``NodeRef`` with the new array added to the dataset.

        Example::

            # Scale temperature K -> F
            scaled = calculator(input=data,
                                Function="temperature * 1.8 + 32",
                                ResultArrayName="temp_fahrenheit",
                                AddScalarArrayName=["temperature"])

            # Build a vector from scalars (same as make_vector)
            vel = calculator(input=data,
                             Function="u*iHat + v*jHat + w*kHat",
                             ResultArrayName="velocity",
                             AddScalarArrayName=["u", "v", "w"])

            # Decompose a vector by subtracting its component along a direction.
            # Here: extract the horizontal part of a velocity field by removing
            # the vertical (kHat-aligned) component:
            #     v_horiz = v - (v · kHat) kHat
            # Substitute any other unit vector (e.g. a surface normal) for kHat
            # to project along that direction instead. Single calculator call —
            # no per-component split.
            horizontal = calculator(input=data,
                                    Function="velocity - dot(velocity,kHat)*kHat",
                                    ResultArrayName="velocity_horizontal",
                                    AddVectorArrayName=["velocity"])

            # Conditional masking: keep speed where temperature > 500, else 0
            masked = calculator(input=data,
                                Function="if(temperature > 500, mag(velocity), 0)",
                                ResultArrayName="hot_speed",
                                AddScalarArrayName=["temperature"],
                                AddVectorArrayName=["velocity"])

        Notes:
            - For simple vector assembly, prefer ``make_vector()``.
            - For simple vector magnitude, prefer ``compute_magnitude()``.
            - For curl, prefer ``curl_vector()`` or ``curl_magnitude()``.
            - Stay in vector form when you can — ``dot``/``cross``/``mag``/``norm``
              and vector arithmetic are usually clearer than per-component splits.
            - If a calculator expression fails to parse, the node may build with
              no error but the ``ResultArrayName`` will be missing from the output;
              the next consumer of that array will be the one that errors.
            - The complete reference for the underlying parser (uncommon edge cases,
              precedence details) is the ExprTk documentation:
              https://github.com/ArashPartow/exprtk — but the listing above
              already covers everything ``vtkArrayCalculator`` exposes.
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
        params = {"bounds": bounds}
        params.update(props)
        return self._add_node("_extract_region", params, input_ref=input)

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
        return self._add_node("_extract_component", {
            "field": field,
            "component": component,
            "result_name": result_name,
        }, input_ref=input)

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
            - note: opposite of ParaView's Clip default (ParaView's "Invert" is on
              by default and keeps the side the normal points *away* from); use
              ``inside_out=True`` to match ParaView's default behavior.
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
        return self._add_node("_line_probe", {
            "point1": point1,
            "point2": point2,
            "resolution": resolution,
        }, input_ref=input)

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

        Keyword Display Properties (both surfaces and volumes):
            color_by (str): Name of a point or cell array to color by.
                            When omitted, VTK uses whatever the active scalar is.
                            **Smart defaults applied automatically** (Vega-lite style):
                            a scalar bar is added with the field name as title
                            (underscores replaced by spaces); if the field range
                            crosses zero (signed field), the ``"cool_to_warm"``
                            diverging colormap is selected and the scalar range is
                            made symmetric (``±max(|min|, |max|)``).  Pass explicit
                            ``scalar_bar=False``, ``lut=``, or ``scalar_range=`` to
                            override any of these defaults.
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
                              For volumes it scales the whole opacity transfer
                              function.
            representation (str): ``"Surface"`` (default), ``"Wireframe"``,
                                   ``"Points"``, or ``"Volume"`` for volume rendering.
                                   This choice selects which of the two
                                   representation-specific prop sets below applies.
            scalar_bar (bool or str): Add a color legend to the scene.  Pass
                                       ``True`` to use the field name as the title,
                                       a string for a custom title, or ``False`` to
                                       suppress the auto-added bar.  When ``color_by``
                                       is set and ``scalar_bar`` is not passed, a bar
                                       is added automatically.
            ambient (float): Ambient lighting coefficient (0.0–1.0) — how much
                              the actor is lit independent of light direction
                              (volume default 0.3).
            diffuse (float): Diffuse lighting coefficient (0.0–1.0) — strength of
                              direction-dependent matte shading (volume default 0.6).
            specular (float): Specular highlight intensity (0.0–1.0).  Adds
                               shininess to surfaces (volume default 0.2).
            specular_power (float): Specular exponent — higher = smaller, sharper
                                     highlights (default 1.0).

        Keyword Display Properties (surface actors only — ``representation``
        ``"Surface"``, ``"Wireframe"``, or ``"Points"``):
            color (tuple or str): Solid color used instead of ``color_by`` for
                           uniform coloring. Either an RGB triple ``(r, g, b)``
                           as floats 0–1, or a name — any vtkNamedColors name
                           such as ``"wheat"`` or ``"slate_gray"``, or ``"#rrggbb"``.
            component (int or str): For multi-component (vector) fields: which
                                     component to color by.  0/1/2 or
                                     ``"x"``/``"y"``/``"z"``.
            line_width (float): Line width in pixels for wireframe or streamline actors.
            lighting (bool): ``False`` disables lighting entirely, drawing the
                              actor in flat unshaded color — useful for glyphs,
                              streamlines, and reference geometry.
            smooth_shading (bool): ``True`` uses Phong interpolation (smooth
                                    surfaces); ``False`` uses flat per-facet
                                    shading.  When the surface carries no point
                                    normals, a ``vtkPolyDataNormals`` filter is
                                    inserted automatically before the mapper.
            split_sharp_edges (bool): ``True`` splits vertices along edges sharper
                                       than ``feature_angle`` so creases stay crisp
                                       under smooth shading (as in pyvista).
            feature_angle (float): Sharp-edge threshold in degrees for the
                                    generated normals (VTK default 30).  Only
                                    applies together with ``split_sharp_edges``
                                    or ``smooth_shading``.

        Keyword Display Properties (volume rendering only — ``representation="Volume"``):
            opacity_function (list or str): Opacity transfer function control
                points: ``[(value, opacity), ...]``.  Or a preset string such as
                ``"fire"``, ``"ct_bone"``, ``"ct_soft"``, ``"ramp_up"``,
                ``"gaussian"``.
            color_function (list): Color transfer function control points
                ``[(value, r, g, b), ...]`` at absolute scalar values (no
                rescale).  Takes precedence over ``lut`` for volume rendering
                and the scalar bar.  Use this to replicate Slicer/OsiriX
                clinical presets exactly.
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

            # Smooth-shaded mesh with crisp creases, softened lighting
            show(mesh, "terrain", color_by="elevation",
                 smooth_shading=True, split_sharp_edges=True,
                 feature_angle=45, ambient=0.2, diffuse=0.8)

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
            - Display-prop keys are validated: an unknown key fails this ``show()``
              directive with the list of accepted keys (and a near-name
              suggestion), and a key belonging to the *other* representation
              (e.g. ``opacity_function`` on a surface actor) is reported as a
              warning in the build report saying it was ignored.  An
              unrecognized ``representation`` value is an error too.
        """
        self._shows.append((node, name, display_props))

    def camera(self, position=None, focal_point=None, up=None, zoom=None):
        """Set the scene camera position and orientation.

        Camera state is saved alongside the pipeline and restored on every
        ``wait_for_pipeline()`` run.  Call ``suggest_camera()`` from the MCP layer
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

    def title(self, text, position="top", font_size=24, color=(1, 1, 1), show_view_name=True):
        """Add a text annotation overlay to the scene.

        Renders a billboard text label fixed in 2-D screen space, useful for
        labeling screenshots with dataset names, timestamps, or parameter values.

        Args:
            text (str): The text to display.
            position (str or tuple): Screen anchor — ``"top"`` (default) or
                            ``"bottom"``, both fixed-margin placements in
                            screen pixels; or an explicit ``(x, y)`` pixel
                            tuple for custom placement.
            font_size (int): Font size in points (default 24).
            color (tuple): RGB color as floats 0–1 (default white ``(1,1,1)``).

        Example::

            title("Wildfire Simulation — t = 30 s",
                  position="top", font_size=20, color=(1, 1, 1))

        Notes:
            - The view name is automatically prefixed (e.g. "flanks: <text>") so
              every screenshot is self-identifying. Pass ``show_view_name=False``
              to suppress it.
            - If ``title()`` is not called at all, the view name alone is rendered
              as a default title.
            - Only one title per scene is supported (the last call wins).
            - For individual data labels in 3-D space, use the DSL
              ``annotate()`` form instead.
        """
        self._title = {"text": text, "position": position, "font_size": font_size,
                       "color": color, "show_view_name": show_view_name}

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
        """
        self._axes = {"color": color, "font_size": font_size, "labels": labels}

    def background(self, *args):
        """Set the scene background color.

        Accepts either a named color or an explicit RGB triple.

        Args:
            *args: Either a single color name/hex string, or three floats
                (r, g, b) in the range 0.0–1.0. The name may be one of the
                built-in presets, any vtkNamedColors name such as ``"wheat"``
                or ``"slate_gray"``, or a ``"#rrggbb"`` hex string. Preset names:

                - ``"dark"`` — dark blue/charcoal (default; great for colorful data)
                - ``"light"`` — soft light gray (good for solid objects/surfaces)
                - ``"black"`` — pure black (maximum contrast)
                - ``"white"`` — pure white (publication/paper figures)

        Example::

            background("white")           # publication-ready
            background("slate_gray")      # any vtkNamedColors name
            background(0.05, 0.05, 0.1)   # custom dark blue

        Raises:
            ValueError: If the name is not a known preset/named color/hex
                string, or arguments are neither a single name nor three floats.
        """
        if len(args) == 1 and isinstance(args[0], str):
            self._background = resolve_color(args[0])
        elif len(args) == 3:
            self._background = resolve_color((float(args[0]), float(args[1]), float(args[2])))
        else:
            raise ValueError(
                "background() expects either a color name (e.g. background('dark')) "
                "or three floats (r, g, b)."
            )


def _make_namespace(builder):
    """Create the restricted namespace for DSL pipeline execution.

    Builder methods are auto-populated via inspect so that any new method
    added to PipelineBuilder is automatically available in the DSL without a
    manual mapping update.  Only public DSL-facing forms are included; the
    underscore filter below excludes builder internals. The compute phase
    (build/hash/cache) lives in ``siva.compute`` and the render phase in
    ``siva.scene``, both as free functions over the frozen :class:`~siva.spec.Spec`
    — not on the builder.
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


def _construct_builder(code):
    """Run the spec code against a fresh builder + namespace.

    Returns ``(builder, bindings)``. Declaring the pipeline populates the
    builder; ``bindings`` is a name -> value mapping (with pipeline handles as
    ``NodeRef``) recovered by the sandbox, used to name nodes at freeze time.
    Spec code runs inside the Monty sandbox; see :mod:`siva.sandbox` for the
    safety story and the marshalling model.
    """
    from .sandbox import execute

    builder = PipelineBuilder()
    namespace = _make_namespace(builder)
    bindings = execute(code, namespace)
    return builder, bindings


def _coerce_vec3(v):
    """Return a 3-tuple of floats, or ``None`` if *v* is ``None``.

    Used at freeze time to normalize camera positions/vectors from whatever
    sequence type DSL code passed (list, tuple, generator) into the canonical
    tuple-of-floats form the frozen spec records carry.
    """
    if v is None:
        return None
    return tuple(float(x) for x in v)


def _freeze_scene(builder):
    """Freeze the builder's accumulated scene slots into a :class:`SceneSpec`.

    Called at end-of-construct: the singleton slots (``camera``, ``background``,
    ``title``, ``axes``) and the annotation list become immutable value records.
    Nothing downstream of construct touches the builder's scene state again.

    This is also where color and position/vector values are normalized to
    their canonical frozen-spec form (RGB float tuples via ``_coerce_color``,
    positions/vectors via ``_coerce_vec3``) — freeze time is the single place
    this coercion happens, so every consumer downstream (``siva.scene``) can
    trust the field types without re-coercing.
    """
    camera = None
    if builder._camera is not None:
        c = builder._camera
        camera = CameraSpec(
            position=_coerce_vec3(c["position"]),
            focal_point=_coerce_vec3(c["focal_point"]),
            up=_coerce_vec3(c["up"]),
            zoom=c["zoom"],
        )

    title = None
    if builder._title is not None:
        t = builder._title
        pos = t["position"]
        title = TitleSpec(
            text=t["text"],
            position=pos if isinstance(pos, str) else _coerce_vec3(pos),
            font_size=t["font_size"],
            color=_coerce_color(t["color"]),
            show_view_name=t["show_view_name"],
        )

    axes = None
    if builder._axes is not None:
        a = builder._axes
        axes = AxesSpec(
            color=_coerce_color(a["color"]),
            font_size=a["font_size"],
            labels=tuple(a["labels"]),
        )

    annotations = tuple(
        Annotation(
            x=float(a["x"]), y=float(a["y"]), z=float(a["z"]), text=a["text"],
            color=_coerce_color(a["color"]), font_size=a["font_size"],
        )
        for a in builder._annotations
    )

    return SceneSpec(
        camera=camera,
        background=builder._background,
        title=title,
        axes=axes,
        annotations=annotations,
    )


def _freeze_node(ref):
    """Convert a construction-time :class:`NodeRef` into a frozen
    :class:`~siva.spec.Node`.

    Every ``NodeRef`` value in the params (the primary ``"input"`` and any
    secondary sources) becomes a frozen :class:`~siva.spec.Ref`; all other params
    are carried through unchanged. Edges thereafter live in exactly one place —
    the ``Ref``-valued params — and are discoverable by type.
    """
    params = {}
    for key, value in ref.properties.items():
        if isinstance(value, NodeRef):
            params[key] = Ref(value.node_id)
        else:
            params[key] = value
    return Node(node_id=ref.node_id, op=ref.vtk_class, params=params)


def _freeze_spec(builder, namespace=None):
    """Freeze a builder's accumulated state into an immutable :class:`~siva.spec.Spec`.

    Converts the recorded nodes (with their ``NodeRef`` handles demoted to
    ``Ref`` values), the show directives, the scene settings, and — from the
    construction *namespace* — the variable-name bindings (name -> node id). The
    bindings walk needs only the namespace and handles, not built outputs, so it
    happens here at freeze time rather than after compute. Nothing downstream of
    construct ever touches the builder.
    """
    nodes = tuple(_freeze_node(ref) for ref in builder._nodes)
    shows = tuple(
        # Fresh dict copy: props is otherwise a live reference into the
        # builder's recorded state. Deep-freeze remains the convention for
        # frozen spec values; a shallow copy here is enough since display
        # props are flat scalar/tuple values, not nested mutable containers.
        Show(node=Ref(node_ref.node_id), name=name, props=dict(props))
        for node_ref, name, props in builder._shows
    )
    bindings = {}
    if namespace is not None:
        for var_name, var_value in namespace.items():
            if isinstance(var_value, NodeRef):
                bindings[var_name] = var_value.node_id
    return Spec(
        nodes=nodes,
        shows=shows,
        scene=_freeze_scene(builder),
        bindings=bindings,
    )


def construct(code):
    """Construct phase: execute the spec code and freeze the result into a
    :class:`~siva.spec.Spec`.

    Spec code runs inside the Monty sandbox (see :mod:`siva.sandbox`). This is
    the only site where the mutable ``PipelineBuilder`` exists; it does not
    escape. The returned ``Spec`` is a frozen value safe to pass to
    ``siva.compute.compute`` on any thread.
    """
    builder, bindings = _construct_builder(code)
    return _freeze_spec(builder, bindings)
