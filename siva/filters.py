"""VTK filter creation with property mapping and whitelisting."""

import difflib
import os
import vtk
from vtk.util.numpy_support import vtk_to_numpy, numpy_to_vtk

from siva._vtk_introspect import (
    find_field_array,
    get_algorithm_output as _get_algorithm_output,
    get_algorithm_input as _get_algorithm_input,
    vtk_setter_names as _vtk_setter_names,
    _vtk_setter_cache,
)
from siva import diagnostics as _diag

# Reader cache: avoids re-reading large files on pipeline rebuild
_reader_cache = {}  # (class_name, filename) -> vtk_algorithm

# Keys handled by _apply_properties via special-case logic (not generic Set{key}).
# These are exempt from property-typo checking since they don't use Set{key} at all.
_SPECIAL_CASE_KEYS = frozenset({
    "Isosurfaces", "ContourBy", "Vectors", "ThresholdRange", "ThresholdBy",
    "AddScalarArrayName", "AddVectorArrayName", "VOI", "SampleRate",
    "IntegrationDirection", "IntegratorType", "GlyphSource", "ScaleArray",
    "OrientationArray", "GlyphMode", "VectorMode", "TensorMode", "CutFunction",
    "_probe_source", "SamplingDimensions", "LowPoint", "HighPoint", "SeedSource",
    "OnRatio", "RandomMode", "GradientField", "DataExtent", "DataScalarType",
    "FileDimensionality", "NumberOfScalarComponents", "HeaderSize",
    # Always-valid internal/framework keys
    "FileName",
})


def clear_reader_cache():
    """Clear the reader cache (for testing)."""
    _reader_cache.clear()


class _UnknownPropertyError(ValueError):
    """Raised by create_vtk_filter when a kwarg name is not a valid VTK setter.

    Carries ``structured`` — a dict with keys: property, vtk_class, similar, valid, message.
    """

    def __init__(self, structured: dict):
        super().__init__(structured["message"])
        self.structured = structured


def _get_vtk_valid_setters(vtk_instance) -> frozenset:
    """Return the set of property names (without 'Set' prefix) available on *vtk_instance*.

    Delegates to ``siva._vtk_introspect.vtk_setter_names`` which caches
    results per class.  Kept as a thin shim so existing imports from
    ``siva.filters`` continue to work.
    """
    return _vtk_setter_names(vtk_instance)


def _validate_vtk_kwargs(vtk_instance, kwargs: dict, vtk_class_name: str) -> str | None:
    """Validate that kwargs intended for Set<Key> methods exist on *vtk_instance*.

    Only keys that are not handled by special-case logic in ``_apply_properties``
    are checked (special cases use their own dispatch, not Set<Key>).

    Args:
        vtk_instance: An instantiated VTK object.
        kwargs: The properties dict as passed to ``create_vtk_filter``.
        vtk_class_name: Class name string used only in the error message.

    Returns:
        ``None`` if all checked kwargs are valid, or a prose error string describing
        the first unknown property found (with similar names and a full property list).
        Callers that need structured info should use _validate_vtk_kwargs_structured.
    """
    result = _validate_vtk_kwargs_structured(vtk_instance, kwargs, vtk_class_name)
    if result is None:
        return None
    return result["message"]


def _validate_vtk_kwargs_structured(vtk_instance, kwargs: dict, vtk_class_name: str) -> dict | None:
    """Like _validate_vtk_kwargs but returns a structured error info dict or None.

    The returned dict has keys: property, vtk_class, similar, valid, message.
    Returns None if all kwargs are valid.
    """
    valid_setters = _get_vtk_valid_setters(vtk_instance)

    for key in kwargs:
        if key in _SPECIAL_CASE_KEYS:
            continue  # handled by special-case dispatch, not Set{key}
        if key in valid_setters:
            continue  # valid generic setter

        # Unknown property — build a helpful error message
        similar = difflib.get_close_matches(key, valid_setters, n=3, cutoff=0.6)
        sorted_valid = sorted(valid_setters)
        preview = sorted_valid[:30]
        more = len(sorted_valid) - len(preview)
        valid_str = ", ".join(preview)
        if more:
            valid_str += f", ... ({more} more)"

        msg = f"unknown property '{key}' on {vtk_class_name}\n"
        if similar:
            msg += f"similar: {', '.join(similar)}\n"
        msg += f"valid: [{valid_str}]"

        return {
            "property": key,
            "vtk_class": vtk_class_name,
            "similar": similar,
            "valid": sorted_valid,
            "message": msg,
        }

    return None


# ---------------------------------------------------------------------------
# Shared constants (imported by dsl.py and server.py)
# ---------------------------------------------------------------------------

# Map human-readable component names to VTK component indices
COMPONENT_NAME_MAP = {"x": 0, "y": 1, "z": 2}

# Inverse: VTK component indices to human-readable names
COMPONENT_INDEX_MAP = {0: "x", 1: "y", 2: "z"}

# Map scalar type strings to VTK type constants
SCALAR_TYPE_MAP = {
    "unsigned_char": vtk.VTK_UNSIGNED_CHAR,
    "char": vtk.VTK_CHAR,
    "unsigned_short": vtk.VTK_UNSIGNED_SHORT,
    "short": vtk.VTK_SHORT,
    "unsigned_int": vtk.VTK_UNSIGNED_INT,
    "int": vtk.VTK_INT,
    "float": vtk.VTK_FLOAT,
    "double": vtk.VTK_DOUBLE,
}


WHITELISTED_CLASSES = {
    # Sources / Readers
    "vtkXMLStructuredGridReader": vtk.vtkXMLStructuredGridReader,
    "vtkXMLImageDataReader": vtk.vtkXMLImageDataReader,
    "vtkXMLPolyDataReader": vtk.vtkXMLPolyDataReader,
    "vtkXMLUnstructuredGridReader": vtk.vtkXMLUnstructuredGridReader,
    "vtkXMLRectilinearGridReader": vtk.vtkXMLRectilinearGridReader,
    "vtkGenericDataObjectReader": vtk.vtkGenericDataObjectReader,
    "vtkNrrdReader": vtk.vtkNrrdReader,
    "vtkArrowSource": vtk.vtkArrowSource,
    "vtkConeSource": vtk.vtkConeSource,
    "vtkCubeSource": vtk.vtkCubeSource,
    "vtkCylinderSource": vtk.vtkCylinderSource,
    "vtkDiskSource": vtk.vtkDiskSource,
    "vtkLineSource": vtk.vtkLineSource,
    "vtkPlaneSource": vtk.vtkPlaneSource,
    "vtkPointSource": vtk.vtkPointSource,
    "vtkRegularPolygonSource": vtk.vtkRegularPolygonSource,
    "vtkSphereSource": vtk.vtkSphereSource,
    "vtkSuperquadricSource": vtk.vtkSuperquadricSource,
    "vtkTexturedSphereSource": vtk.vtkTexturedSphereSource,
    "vtkParametricFunctionSource": vtk.vtkParametricFunctionSource,
    # Filters -- geometry and topology
    "vtkAppendFilter": vtk.vtkAppendFilter,
    "vtkAppendPolyData": vtk.vtkAppendPolyData,
    "vtkArrayCalculator": vtk.vtkArrayCalculator,
    "vtkCellDataToPointData": vtk.vtkCellDataToPointData,
    "vtkCellDerivatives": vtk.vtkCellDerivatives,
    "vtkCleanPolyData": vtk.vtkCleanPolyData,
    "vtkClipDataSet": vtk.vtkClipDataSet,
    "vtkConnectivityFilter": vtk.vtkConnectivityFilter,
    "vtkContourFilter": vtk.vtkContourFilter,
    "vtkCutter": vtk.vtkCutter,
    "vtkDataSetSurfaceFilter": vtk.vtkDataSetSurfaceFilter,
    "vtkDecimatePro": vtk.vtkDecimatePro,
    "vtkDelaunay2D": vtk.vtkDelaunay2D,
    "vtkDelaunay3D": vtk.vtkDelaunay3D,
    "vtkElevationFilter": vtk.vtkElevationFilter,
    "vtkExtractEdges": vtk.vtkExtractEdges,
    "vtkExtractGrid": vtk.vtkExtractGrid,
    "vtkExtractVOI": vtk.vtkExtractVOI,
    "vtkFeatureEdges": vtk.vtkFeatureEdges,
    "vtkFillHolesFilter": vtk.vtkFillHolesFilter,
    "vtkGaussianSplatter": vtk.vtkGaussianSplatter,
    "vtkGeometryFilter": vtk.vtkGeometryFilter,
    "vtkGlyph3D": vtk.vtkGlyph3D,
    "vtkGradientFilter": vtk.vtkGradientFilter,
    "vtkMaskPoints": vtk.vtkMaskPoints,
    "vtkOutlineFilter": vtk.vtkOutlineFilter,
    "vtkPassArrays": vtk.vtkPassArrays,
    "vtkPointDataToCellData": vtk.vtkPointDataToCellData,
    "vtkPolyDataConnectivityFilter": vtk.vtkPolyDataConnectivityFilter,
    "vtkPolyDataNormals": vtk.vtkPolyDataNormals,
    "vtkProbeFilter": vtk.vtkProbeFilter,
    "vtkQuadricDecimation": vtk.vtkQuadricDecimation,
    "vtkResampleToImage": vtk.vtkResampleToImage,
    "vtkResampleWithDataSet": vtk.vtkResampleWithDataSet,
    "vtkRibbonFilter": vtk.vtkRibbonFilter,
    "vtkSelectEnclosedPoints": vtk.vtkSelectEnclosedPoints,
    "vtkSmoothPolyDataFilter": vtk.vtkSmoothPolyDataFilter,
    "vtkStreamTracer": vtk.vtkStreamTracer,
    "vtkStripper": vtk.vtkStripper,
    "vtkThreshold": vtk.vtkThreshold,
    "vtkThresholdPoints": vtk.vtkThresholdPoints,
    "vtkTransformFilter": vtk.vtkTransformFilter,
    "vtkTriangleFilter": vtk.vtkTriangleFilter,
    "vtkTubeFilter": vtk.vtkTubeFilter,
    "vtkVertexGlyphFilter": vtk.vtkVertexGlyphFilter,
    "vtkWarpScalar": vtk.vtkWarpScalar,
    "vtkWarpVector": vtk.vtkWarpVector,
    "vtkWindowedSincPolyDataFilter": vtk.vtkWindowedSincPolyDataFilter,
    # Filters -- image processing
    "vtkImageCast": vtk.vtkImageCast,
    "vtkImageGaussianSmooth": vtk.vtkImageGaussianSmooth,
    "vtkImageMathematics": vtk.vtkImageMathematics,
    "vtkImageShiftScale": vtk.vtkImageShiftScale,
    "vtkImageReader2": vtk.vtkImageReader2,
    # Readers -- additional file formats
    "vtkPLYReader": vtk.vtkPLYReader,
    "vtkSTLReader": vtk.vtkSTLReader,
    "vtkOBJReader": vtk.vtkOBJReader,
    # Sources -- additional geometry primitives
    "vtkFrustumSource": vtk.vtkFrustumSource,
    "vtkOutlineSource": vtk.vtkOutlineSource,
    "vtkTessellatedBoxSource": vtk.vtkTessellatedBoxSource,
    # Filters -- geometry and topology (additional)
    "vtkClipPolyData": vtk.vtkClipPolyData,
    "vtkTransformPolyDataFilter": vtk.vtkTransformPolyDataFilter,
    "vtkLoopSubdivisionFilter": vtk.vtkLoopSubdivisionFilter,
    "vtkButterflySubdivisionFilter": vtk.vtkButterflySubdivisionFilter,
    "vtkLinearSubdivisionFilter": vtk.vtkLinearSubdivisionFilter,
    "vtkReverseSense": vtk.vtkReverseSense,
    "vtkMarchingCubes": vtk.vtkMarchingCubes,
    "vtkFlyingEdges3D": vtk.vtkFlyingEdges3D,
    "vtkBooleanOperationPolyDataFilter": vtk.vtkBooleanOperationPolyDataFilter,
    "vtkIntersectionPolyDataFilter": vtk.vtkIntersectionPolyDataFilter,
    "vtkHull": vtk.vtkHull,
    "vtkShrinkFilter": vtk.vtkShrinkFilter,
    "vtkShrinkPolyData": vtk.vtkShrinkPolyData,
    "vtkExtractCells": vtk.vtkExtractCells,
    "vtkExtractGeometry": vtk.vtkExtractGeometry,
    "vtkTableBasedClipDataSet": vtk.vtkTableBasedClipDataSet,
    "vtkRectilinearGridToTetrahedra": vtk.vtkRectilinearGridToTetrahedra,
    "vtkMassProperties": vtk.vtkMassProperties,
    "vtkTableToPolyData": vtk.vtkTableToPolyData,
    "vtkRectilinearGridGeometryFilter": vtk.vtkRectilinearGridGeometryFilter,
    "vtkStructuredGridGeometryFilter": vtk.vtkStructuredGridGeometryFilter,
    "vtkProjectSphereFilter": vtk.vtkProjectSphereFilter,
    "vtkRandomAttributeGenerator": vtk.vtkRandomAttributeGenerator,
    "vtkSampleImplicitFunctionFilter": vtk.vtkSampleImplicitFunctionFilter,
    "vtkImplicitModeller": vtk.vtkImplicitModeller,
    # Filters -- point cloud and sampling
    "vtkPointInterpolator": vtk.vtkPointInterpolator,
    "vtkSPHInterpolator": vtk.vtkSPHInterpolator,
    "vtkStatisticalOutlierRemoval": vtk.vtkStatisticalOutlierRemoval,
    "vtkRadiusOutlierRemoval": vtk.vtkRadiusOutlierRemoval,
    "vtkVoxelGrid": vtk.vtkVoxelGrid,
    "vtkPoissonDiskSampler": vtk.vtkPoissonDiskSampler,
    # Filters -- image processing (additional)
    "vtkImageResample": vtk.vtkImageResample,
    "vtkImageReslice": vtk.vtkImageReslice,
    "vtkImageFlip": vtk.vtkImageFlip,
    "vtkImageExtractComponents": vtk.vtkImageExtractComponents,
    "vtkImageNormalize": vtk.vtkImageNormalize,
    "vtkImageClip": vtk.vtkImageClip,
    "vtkImageMedian3D": vtk.vtkImageMedian3D,
    "vtkImageGradient": vtk.vtkImageGradient,
    "vtkImageGradientMagnitude": vtk.vtkImageGradientMagnitude,
}


def extract_component(input_algorithm, field, component, result_name):
    """Extract a single component from a vector field, creating a new scalar array.

    Args:
        input_algorithm: VTK algorithm or dataset containing the vector field.
        field: Name of the vector field to extract from.
        component: Component index (0, 1, 2) or name ("x", "y", "z").
        result_name: Name for the new scalar array.

    Returns:
        (vtk_algorithm, status_dict) -- the input algorithm is returned with
        the new scalar array added to its output.
    """
    if isinstance(component, str):
        comp_idx = COMPONENT_NAME_MAP.get(component.lower())
        if comp_idx is None:
            raise ValueError(
                f"extract_component: unknown component name '{component}'; "
                "expected 0/1/2 or 'x'/'y'/'z'."
            )
    else:
        comp_idx = int(component)

    # Get the output dataset
    if hasattr(input_algorithm, "GetOutput"):
        input_algorithm.Update()
        data = input_algorithm.GetOutput()
    else:
        data = input_algorithm

    if data is None:
        raise ValueError("extract_component: input has no output data.")

    # Find the array in point data or cell data
    arr, location = find_field_array(data, field)
    if arr is None:
        available_pd = [data.GetPointData().GetArrayName(i)
                        for i in range(data.GetPointData().GetNumberOfArrays())]
        available_cd = [data.GetCellData().GetArrayName(i)
                        for i in range(data.GetCellData().GetNumberOfArrays())]
        raise ValueError(
            f"extract_component: field '{field}' not found in upstream data; "
            f"available point arrays: {available_pd}, cell arrays: {available_cd}"
        )
    is_point_data = (location == "point")

    num_comp = arr.GetNumberOfComponents()
    if num_comp == 1:
        raise ValueError(
            f"extract_component: field '{field}' is a scalar (1 component); "
            "extract_component requires a vector field with multiple components."
        )
    if comp_idx >= num_comp:
        raise ValueError(
            f"extract_component: component index {comp_idx} out of range for "
            f"field '{field}' which has {num_comp} components; "
            f"expected 0..{num_comp - 1}."
        )

    # Extract component using numpy
    np_arr = vtk_to_numpy(arr)
    comp_data = np_arr[:, comp_idx].copy()

    new_arr = numpy_to_vtk(comp_data, deep=True)
    new_arr.SetName(result_name)

    if is_point_data:
        data.GetPointData().AddArray(new_arr)
    else:
        data.GetCellData().AddArray(new_arr)

    status = _diag.ok(
        "extract_component",
        source_field=field,
        component=comp_idx,
        result_name=result_name,
        num_tuples=new_arr.GetNumberOfTuples(),
        range=list(new_arr.GetRange()),
    )
    return input_algorithm, status


def physical_bounds_to_voi(data, bounds):
    """Convert physical coordinate bounds to grid index VOI for structured/image data.

    For vtkImageData/vtkUniformGrid, uses the regular grid spacing formula.
    For vtkStructuredGrid and vtkRectilinearGrid, scans grid points to find
    the closest indices (handles curvilinear and non-uniform grids).

    Args:
        data: A VTK dataset with GetDimensions() (structured/image/rectilinear).
        bounds: [xmin, xmax, ymin, ymax, zmin, zmax] in physical coordinates.

    Returns:
        [imin, imax, jmin, jmax, kmin, kmax] clamped to grid extent.

    Raises:
        ValueError: if the dataset does not have structured dimensions.
    """
    if not hasattr(data, "GetDimensions"):
        raise ValueError(
            "physical_bounds_to_voi requires a structured dataset (vtkImageData, "
            "vtkStructuredGrid, or vtkRectilinearGrid). "
            f"Got: {data.GetClassName()}"
        )

    dims = [0, 0, 0]
    data.GetDimensions(dims)
    nx, ny, nz = dims

    xmin, xmax, ymin, ymax, zmin, zmax = bounds

    class_name = data.GetClassName()

    if class_name in ("vtkImageData", "vtkUniformGrid"):
        # Regular grid: use spacing and origin for exact arithmetic
        origin = data.GetOrigin()
        spacing = data.GetSpacing()

        def _coord_to_idx(lo_phys, hi_phys, origin_c, spacing_c, n):
            if spacing_c == 0:
                return 0, n - 1
            # Convert physical coordinates to float indices
            lo_f = (lo_phys - origin_c) / spacing_c
            hi_f = (hi_phys - origin_c) / spacing_c
            # Round outward to include the entire requested region
            lo_i = max(0, int(lo_f))
            hi_i = min(n - 1, int(hi_f + 0.9999))
            return lo_i, hi_i

        imin, imax = _coord_to_idx(xmin, xmax, origin[0], spacing[0], nx)
        jmin, jmax = _coord_to_idx(ymin, ymax, origin[1], spacing[1], ny)
        kmin, kmax = _coord_to_idx(zmin, zmax, origin[2], spacing[2], nz)

    elif class_name == "vtkRectilinearGrid":
        # Non-uniform but axis-aligned grid: binary-search each axis array
        import bisect
        from vtk.util.numpy_support import vtk_to_numpy as _vtk_to_numpy

        def _axis_to_range(arr_vtk, lo_phys, hi_phys):
            arr = _vtk_to_numpy(arr_vtk)
            n = len(arr)
            lo_idx = bisect.bisect_left(arr, lo_phys)
            hi_idx = bisect.bisect_right(arr, hi_phys) - 1
            # Expand one step outward to include boundary cells
            lo_idx = max(0, lo_idx - 1)
            hi_idx = min(n - 1, hi_idx + 1)
            return lo_idx, hi_idx

        imin, imax = _axis_to_range(data.GetXCoordinates(), xmin, xmax)
        jmin, jmax = _axis_to_range(data.GetYCoordinates(), ymin, ymax)
        kmin, kmax = _axis_to_range(data.GetZCoordinates(), zmin, zmax)

    else:
        # vtkStructuredGrid: curvilinear — use VTK's spatial locator via
        # FindPoint() on the bounding box corners, then convert flat point
        # IDs to (i,j,k) extent indices.
        extent = data.GetExtent()  # (imin_ext, imax_ext, jmin_ext, jmax_ext, kmin_ext, kmax_ext)
        ei0, _, ej0, _, ek0, _ = extent

        corner_points = [
            (xmin, ymin, zmin), (xmin, ymin, zmax),
            (xmin, ymax, zmin), (xmin, ymax, zmax),
            (xmax, ymin, zmin), (xmax, ymin, zmax),
            (xmax, ymax, zmin), (xmax, ymax, zmax),
        ]

        imin, imax = nx - 1, 0
        jmin, jmax = ny - 1, 0
        kmin, kmax = nz - 1, 0

        for corner in corner_points:
            pt_id = data.FindPoint(corner)
            if pt_id < 0:
                continue
            # FindPoint returns a flat ID in local (0-based) ordering.
            # Convert to local ijk, then offset to extent indices.
            k = pt_id // (nx * ny) + ek0
            j = (pt_id % (nx * ny)) // nx + ej0
            i = pt_id % nx + ei0
            imin = min(imin, i)
            imax = max(imax, i)
            jmin = min(jmin, j)
            jmax = max(jmax, j)
            kmin = min(kmin, k)
            kmax = max(kmax, k)

        # If no corners were found, fall back to full extent
        if imin > imax or jmin > jmax or kmin > kmax:
            imin, imax = extent[0], extent[1]
            jmin, jmax = extent[2], extent[3]
            kmin, kmax = extent[4], extent[5]
        else:
            # Pad by one index to include cells straddling the boundary
            imin = max(extent[0], imin - 1)
            imax = min(extent[1], imax + 1)
            jmin = max(extent[2], jmin - 1)
            jmax = min(extent[3], jmax + 1)
            kmin = max(extent[4], kmin - 1)
            kmax = min(extent[5], kmax + 1)

    return [imin, imax, jmin, jmax, kmin, kmax]


def _get_output_array_names(algorithm):
    """Return (point_arrays, cell_arrays) from an algorithm's current output.

    Calls Update() on the algorithm to ensure its output is populated before
    querying array names.  Returns empty lists if the algorithm has no output.

    Args:
        algorithm: A VTK algorithm with GetOutput() or GetOutputDataObject(),
                   or a VTK dataset directly.

    Returns:
        (point_arrays, cell_arrays) -- lists of array name strings.
    """
    if algorithm is None:
        return [], []

    # Ensure the upstream pipeline is executed so array names are available.
    if hasattr(algorithm, "Update"):
        algorithm.Update()

    data = _get_algorithm_output(algorithm)
    if data is None:
        # Assume the argument is already a dataset.
        data = algorithm if hasattr(algorithm, "GetPointData") else None

    if data is None:
        return [], []

    if not hasattr(data, "GetPointData"):
        return [], []

    pd = data.GetPointData()
    point_arrays = [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]

    cd = data.GetCellData()
    cell_arrays = [cd.GetArrayName(i) for i in range(cd.GetNumberOfArrays())]

    return point_arrays, cell_arrays


# Map: filter class -> list of (property_key, search_scope)
# search_scope: "point" = point arrays only, "cell" = cell arrays only,
#               "both" = point or cell arrays are acceptable.
_FIELD_NAME_PROPERTIES = {
    "vtkContourFilter":    [("ContourBy", "point")],
    "vtkThreshold":        [("ThresholdBy", "both")],
    "vtkGradientFilter":   [("GradientField", "both")],
    "vtkArrayCalculator":  [
        ("AddScalarArrayName", "both"),   # value is a list
        ("AddVectorArrayName", "both"),   # value is a list
    ],
    "vtkGlyph3D":          [
        ("ScaleArray", "both"),
        ("OrientationArray", "both"),
    ],
    "vtkWarpScalar":       [("Vectors", "both")],
    "vtkWarpVector":       [("Vectors", "both")],
    "vtkStreamTracer":     [("Vectors", "point")],
}


def _validate_field_names(vtk_class_name, properties, input_algorithm):
    """Check that field names referenced by *properties* exist on the upstream data.

    Called before executing the expensive ``Update()`` so typos are caught early.
    Calls Update() on the input to ensure arrays are available for inspection.

    Args:
        vtk_class_name: The VTK class name string (e.g. ``"vtkContourFilter"``).
        properties: Dict of filter properties as passed to ``create_vtk_filter``.
        input_algorithm: Upstream VTK algorithm or ``None``.

    Raises:
        ValueError: When a referenced field name is not found in the upstream
            point or cell arrays.  The error message includes the available
            array names.
    """
    spec = _FIELD_NAME_PROPERTIES.get(vtk_class_name)
    if not spec or input_algorithm is None:
        return  # Nothing to validate

    point_arrays, cell_arrays = _get_output_array_names(input_algorithm)
    all_arrays = list(dict.fromkeys(point_arrays + cell_arrays))  # deduped, order preserved

    if not all_arrays:
        # If we can't enumerate arrays (e.g. source not yet run), skip validation
        return

    for prop_key, scope in spec:
        if prop_key not in properties:
            continue

        value = properties[prop_key]
        # Some props hold a single string; others (AddScalarArrayName) hold a list.
        field_names = value if isinstance(value, (list, tuple)) else [value]

        for field in field_names:
            if not isinstance(field, str):
                continue  # skip non-string values

            if scope == "point":
                available = point_arrays
            elif scope == "cell":
                available = cell_arrays
            else:  # "both"
                available = all_arrays

            if field not in available:
                raise ValueError(
                    f"Field '{field}' not found in upstream data for "
                    f"{vtk_class_name} property '{prop_key}'. "
                    f"Available arrays: {available}"
                )


def _format_field_range_hint(dataset, field_name, user_value=None, kind="clip") -> str:
    """Return a human-readable hint with the field's actual range and the user's chosen value.

    Args:
        dataset: VTK dataset (vtkDataSet subclass).
        field_name: Name of the scalar field to look up.
        user_value: The value the user supplied (ClipValue, IsoValue, ThresholdRange, etc.).
            For "threshold" kind, pass the [lo, hi] list.
        kind: One of "clip", "threshold", "iso", "probe", "generic" — controls the verb.

    Returns:
        A short hint string. Falls back to a generic message if the field is not found.
    """
    if dataset is None or not field_name:
        return "Use describe_data() to verify field ranges."

    arr, _loc = find_field_array(dataset, field_name)
    if arr is None:
        return "Use describe_data() to verify field ranges."

    rng = arr.GetRange()
    base = f"'{field_name}' range is [{rng[0]:.4g}, {rng[1]:.4g}]"

    if user_value is not None:
        verbs = {
            "clip": "ClipValue",
            "threshold": "ThresholdRange",
            "iso": "IsoValue",
            "probe": "probe value",
            "generic": "value",
        }
        label = verbs.get(kind, "value")
        if kind == "clip" and (user_value < rng[0] or user_value > rng[1]):
            return f"{base} but your {label} was {user_value:.4g} (outside range)"
        if kind == "threshold" and isinstance(user_value, (list, tuple)) and len(user_value) == 2:
            lo, hi = user_value
            if lo > rng[1] or hi < rng[0]:
                return f"{base} but your {label} {list(user_value)} doesn't overlap"
            return f"{base}; your {label} was {list(user_value)}"
        return f"{base}; your {label} was {user_value:.4g}"

    return base


def _get_active_scalar_hint(input_algorithm) -> str:
    """Return a field-range hint string for the upstream's active scalar.

    Used as a generic fallback for filter types with no special-case handling.
    Returns an empty string if the upstream has no active scalar.
    """
    inp = _get_algorithm_output(input_algorithm)
    if inp is None:
        return ""
    pd = inp.GetPointData()
    scalars = pd.GetScalars()
    if scalars is None:
        # Try cell data
        cd = inp.GetCellData()
        scalars = cd.GetScalars()
    if scalars is None:
        return ""
    field_name = scalars.GetName()
    hint = _format_field_range_hint(inp, field_name, kind="generic")
    if "describe_data" in hint:
        return ""
    return hint


# Properties that name a filesystem path a reader will open. Currently only
# ``FileName`` is used by any whitelisted reader (see WHITELISTED_CLASSES);
# revisit this set if a reader taking ``FileNames``/``FilePrefix`` etc. is
# ever whitelisted.
FILE_PATH_PROPS = frozenset({"FileName"})


def confine_to_workdir(path: str, workdir: str | None = None) -> str:
    """Confine *path* to lie within the server's working directory.

    The confinement rule (see ``siva/sandbox.py`` module docstring for the
    security rationale):

    1. Absolute paths are rejected outright.
    2. The path is normalized *lexically* (no symlink resolution) and must not
       escape *workdir* -- ``../x`` or ``a/../../x`` are rejected, but an
       internal ``a/../b.vts`` that stays inside is fine.
    3. Symlinks are deliberately NOT resolved here: a symlink placed *inside*
       the working directory that points elsewhere on disk is allowed to
       load. The untrusted actor is the spec (sandboxed, can't create files or
       symlinks); a symlink in the working directory was placed by the
       trusted human, so following it is an authorization, not an escape.

    Returns *path* unchanged if it passes. Raises ``ValueError`` naming the
    offending path and the working directory otherwise.
    """
    if workdir is None:
        workdir = os.getcwd()

    if os.path.isabs(path):
        raise ValueError(
            f"FileName '{path}' is outside the working directory '{workdir}'. "
            "Pipelines may only read files within it (symlinks placed inside "
            "it are allowed). Call list_data_files() to see available files."
        )

    # Lexical normalization: join under workdir and normalize without
    # touching the filesystem (no realpath/symlink resolution).
    normalized = os.path.normpath(os.path.join(workdir, path))
    workdir_normalized = os.path.normpath(workdir)
    if normalized != workdir_normalized and not normalized.startswith(
        workdir_normalized + os.sep
    ):
        raise ValueError(
            f"FileName '{path}' is outside the working directory '{workdir}'. "
            "Pipelines may only read files within it (symlinks placed inside "
            "it are allowed). Call list_data_files() to see available files."
        )

    return path


def create_vtk_filter(vtk_class_name, input_algorithm=None, **properties):
    """Create a VTK filter/source, connect input, apply properties, update."""
    if vtk_class_name not in WHITELISTED_CLASSES:
        raise ValueError(
            f"VTK class '{vtk_class_name}' not in whitelist. "
            f"Available: {sorted(WHITELISTED_CLASSES.keys())}"
        )

    # Confine any filesystem-path property to the server's working directory
    # BEFORE the path is used (cache lookup, object construction, or Update()).
    for _path_prop in FILE_PATH_PROPS:
        if _path_prop in properties and isinstance(properties[_path_prop], str):
            confine_to_workdir(properties[_path_prop])

    # Use reader cache for file readers to avoid re-reading large files
    _cacheable_readers = {"vtkXMLStructuredGridReader", "vtkXMLImageDataReader",
                          "vtkXMLPolyDataReader", "vtkXMLUnstructuredGridReader",
                          "vtkXMLRectilinearGridReader"}
    if vtk_class_name in _cacheable_readers and "FileName" in properties:
        cache_key = (vtk_class_name, properties["FileName"])
        if cache_key in _reader_cache:
            cached = _reader_cache[cache_key]
            cached.Update()
            output = cached.GetOutput()
            _num_pts = output.GetNumberOfPoints() if output else 0
            _num_cells = output.GetNumberOfCells() if output else 0
            _extra = {"num_points": _num_pts, "num_cells": _num_cells, "cached": True}
            if output and _num_pts > 0:
                _extra["bounds"] = list(output.GetBounds())
            if output:
                arrays = []
                pd = output.GetPointData()
                for i in range(pd.GetNumberOfArrays()):
                    arrays.append(pd.GetArrayName(i))
                if arrays:
                    _extra["point_arrays"] = arrays
            return cached, _diag.ok(vtk_class_name, **_extra)

    vtk_obj = WHITELISTED_CLASSES[vtk_class_name]()

    if input_algorithm is not None:
        if hasattr(input_algorithm, "GetOutputPort"):
            vtk_obj.SetInputConnection(input_algorithm.GetOutputPort())
        else:
            vtk_obj.SetInputData(input_algorithm)

    # For vtkExtractGrid: convert physical Bounds to VOI indices if provided.
    # The user may pass Bounds=[xmin,xmax,ymin,ymax,zmin,zmax] (physical coords)
    # instead of VOI=[imin,imax,jmin,jmax,kmin,kmax] (grid indices).
    # Both Bounds and VOI are mutually exclusive; Bounds takes precedence.
    properties = dict(properties)  # copy to avoid mutating caller's dict
    if vtk_class_name in ("vtkExtractGrid", "vtkExtractVOI") and "Bounds" in properties:
        if "VOI" in properties:
            raise ValueError(
                "Specify either 'Bounds' (physical coords) or 'VOI' (grid indices), not both."
            )
        phys_bounds = properties.pop("Bounds")
        if input_algorithm is not None:
            # Update input to get its output data for coordinate conversion
            if hasattr(input_algorithm, "Update"):
                input_algorithm.Update()
            if hasattr(input_algorithm, "GetOutput"):
                input_data = input_algorithm.GetOutput()
            else:
                input_data = input_algorithm
            voi = physical_bounds_to_voi(input_data, phys_bounds)
            properties["VOI"] = voi
        else:
            raise ValueError(
                "'Bounds' requires an input dataset for coordinate conversion. "
                "Connect an input node before using Bounds."
            )

    # Validate field names against upstream metadata BEFORE the expensive Update().
    # This catches typos early without waiting for large data to process.
    _validate_field_names(vtk_class_name, properties, input_algorithm)

    # Validate that any generic Set{key} kwargs actually exist on the VTK object.
    # This catches property name typos (e.g. 'ScalarArrays' vs 'InputScalarsSelection')
    # before the opaque error that would come from _apply_properties.
    kwarg_error_info = _validate_vtk_kwargs_structured(vtk_obj, properties, vtk_class_name)
    if kwarg_error_info:
        # Raise a structured error that callers can catch and convert to a node status
        raise _UnknownPropertyError(kwarg_error_info)

    _apply_properties(vtk_obj, vtk_class_name, properties)

    # For filters that need active vectors, set them on input
    if vtk_class_name in ("vtkStreamTracer", "vtkGlyph3D", "vtkCellDerivatives") and input_algorithm is not None:
        if hasattr(input_algorithm, "GetOutput"):
            input_data = input_algorithm.GetOutput()
            pd = input_data.GetPointData()
            for i in range(pd.GetNumberOfArrays()):
                arr = pd.GetArray(i)
                if arr and arr.GetNumberOfComponents() == 3:
                    pd.SetActiveVectors(arr.GetName())
                    break

    vtk_obj.Update()

    # Post-update validation for vtkArrayCalculator: if ResultArrayName is
    # missing from the output, the Function string almost certainly failed to
    # parse. Raise so this surfaces on the calculator node itself (and triggers
    # cascade-skip downstream) rather than appearing two nodes later as a
    # confusing "field not found" error on the next consumer.
    if vtk_class_name == "vtkArrayCalculator":
        result_name = properties.get("ResultArrayName")
        if result_name:
            calc_output = vtk_obj.GetOutput()
            if calc_output:
                result_arr = (
                    calc_output.GetPointData().GetArray(result_name)
                    or calc_output.GetCellData().GetArray(result_name)
                )
                if result_arr is None:
                    function = properties.get("Function", "")
                    raise RuntimeError(
                        f"vtkArrayCalculator failed to produce ResultArrayName "
                        f"'{result_name}'. The Function expression likely failed "
                        f"to parse. Function: {function!r}. "
                        f'See get_dsl_reference(form="calculator") for supported syntax.'
                    )

    # Cache readers for reuse
    if vtk_class_name in _cacheable_readers and "FileName" in properties:
        cache_key = (vtk_class_name, properties["FileName"])
        _reader_cache[cache_key] = vtk_obj

    output = vtk_obj.GetOutput()
    _num_pts = output.GetNumberOfPoints() if output else 0
    _num_cells = output.GetNumberOfCells() if output else 0
    _ok_extra: dict = {"num_points": _num_pts, "num_cells": _num_cells}
    if output and _num_pts > 0:
        _ok_extra["bounds"] = list(output.GetBounds())

    if output:
        arrays = []
        pd = output.GetPointData()
        for i in range(pd.GetNumberOfArrays()):
            arrays.append(pd.GetArrayName(i))
        if arrays:
            _ok_extra["point_arrays"] = arrays
        cd = output.GetCellData()
        cell_arrays = []
        for i in range(cd.GetNumberOfArrays()):
            cell_arrays.append(cd.GetArrayName(i))
        if cell_arrays:
            _ok_extra["cell_arrays"] = cell_arrays

    if _num_pts == 0:
        # Diagnose why output is empty
        warning = "Filter produced empty output"
        if vtk_class_name in (
            "vtkClipDataSet", "vtkTableBasedClipDataSet", "vtkClipPolyData"
        ):
            clip_value = properties.get("Value")
            inp = _get_algorithm_output(input_algorithm)
            if inp:
                clip_func = properties.get("CutFunction") or properties.get("ClipFunction")
                if clip_func is None:
                    # Scalar clip — find the active scalar
                    pd = inp.GetPointData()
                    scalars = pd.GetScalars()
                    field_name = scalars.GetName() if scalars else None
                    hint = _format_field_range_hint(
                        inp, field_name, user_value=clip_value, kind="clip")
                    warning += f". {hint}"
                else:
                    warning += ". Clip function may not intersect the data domain"
        elif vtk_class_name in ("vtkExtractVOI", "vtkExtractGrid"):
            voi = properties.get("VOI")
            inp = _get_algorithm_output(input_algorithm)
            if inp and voi is not None:
                ext = inp.GetExtent() if hasattr(inp, "GetExtent") else None
                if ext is not None:
                    warning += (
                        f". VOI {list(voi)} lies outside the dataset extent "
                        f"[{ext[0]}, {ext[1]}, {ext[2]}, {ext[3]}, {ext[4]}, {ext[5]}]"
                    )
        elif vtk_class_name == "vtkContourFilter":
            # Check if contour values are in range
            contour_by = properties.get("ContourBy", "")
            iso_vals = properties.get("Isosurfaces", [])
            inp = _get_algorithm_output(input_algorithm)
            if inp and contour_by:
                arr, _loc = find_field_array(inp, contour_by)
                if arr is None:
                    available = [inp.GetPointData().GetArrayName(i) for i in range(inp.GetPointData().GetNumberOfArrays())]
                    warning += f". Field '{contour_by}' not found. Available: {available}"
                else:
                    rng = arr.GetRange()
                    out_of_range = [v for v in iso_vals if v < rng[0] or v > rng[1]]
                    if out_of_range:
                        warning += (
                            f". Isosurface values {out_of_range} are outside "
                            f"'{contour_by}' range [{rng[0]:.4g}, {rng[1]:.4g}]"
                        )
                    else:
                        warning += f". Values {iso_vals} are within range [{rng[0]:.4g}, {rng[1]:.4g}] but produced no surface"
        elif vtk_class_name == "vtkThreshold":
            thresh_by = properties.get("ThresholdBy", "")
            thresh_range = properties.get("ThresholdRange", [])
            if thresh_by:
                inp = _get_algorithm_output(input_algorithm)
                if inp:
                    arr, _loc = find_field_array(inp, thresh_by)
                    if arr:
                        rng = arr.GetRange()
                        if thresh_range and (thresh_range[0] > rng[1] or thresh_range[1] < rng[0]):
                            warning += (
                                f". ThresholdRange {thresh_range} doesn't overlap "
                                f"'{thresh_by}' range [{rng[0]:.4g}, {rng[1]:.4g}]"
                            )
                        elif thresh_range:
                            warning += (
                                f". '{thresh_by}' range is [{rng[0]:.4g}, {rng[1]:.4g}]"
                                f"; your ThresholdRange {thresh_range} overlaps but produced no cells"
                                " — check cell vs point data association"
                            )
                        else:
                            warning += f". '{thresh_by}' range is [{rng[0]:.4g}, {rng[1]:.4g}]"
        elif vtk_class_name == "vtkProbeFilter":
            # Probe produces empty output when input and source don't overlap spatially
            inp = _get_algorithm_output(input_algorithm)
            if inp:
                active_hint = _get_active_scalar_hint(input_algorithm)
                if active_hint:
                    warning += f". {active_hint}"
                src = properties.get("_probe_source")
                if src is not None:
                    src_data = _get_algorithm_output(src)
                    if src_data and src_data.GetNumberOfPoints() > 0:
                        src_bounds = src_data.GetBounds()
                        inp_bounds = inp.GetBounds() if inp.GetNumberOfPoints() > 0 else None
                        if inp_bounds is not None:
                            overlap = all(
                                src_bounds[2 * i] <= inp_bounds[2 * i + 1] and
                                src_bounds[2 * i + 1] >= inp_bounds[2 * i]
                                for i in range(3)
                            )
                            if not overlap:
                                warning += ". Source and input datasets don't overlap spatially"
        elif vtk_class_name == "vtkStreamTracer":
            seed_source = properties.get("SeedSource")
            seed_bounds = None
            grid_bounds = None
            try:
                if seed_source is not None and hasattr(seed_source, "GetOutput"):
                    seed_source.Update()
                    seed_bounds = seed_source.GetOutput().GetBounds()
                if input_algorithm is not None and hasattr(input_algorithm, "GetOutput"):
                    input_algorithm.Update()
                    grid_bounds = input_algorithm.GetOutput().GetBounds()
            except Exception:
                pass

            if seed_bounds is not None and grid_bounds is not None:
                sx_min, sx_max, sy_min, sy_max = seed_bounds[0], seed_bounds[1], seed_bounds[2], seed_bounds[3]
                gx_min, gx_max, gy_min, gy_max = grid_bounds[0], grid_bounds[1], grid_bounds[2], grid_bounds[3]
                xy_overlap = (sx_min < gx_max and sx_max > gx_min and
                              sy_min < gy_max and sy_max > gy_min)
                if xy_overlap:
                    warning += (
                        ". Seeds are within the data XY extent but produced no traces"
                        " — on a terrain-following grid, seeds may be below the terrain surface."
                        " Call get_ground_z(node, x, y) to find the correct Z at your seed location."
                    )
                else:
                    warning += (
                        f". Seed points appear to be outside the data domain entirely."
                        f" Check coordinates against the data bounds: {grid_bounds}."
                    )
            else:
                warning += (
                    ". Check: (1) seed points are inside the data bounds "
                    "(use get_ground_z to find valid z-coordinates), "
                    "(2) velocity vectors exist on the input data"
                )
        else:
            # Generic fallback: include the active scalar range from upstream
            active_hint = _get_active_scalar_hint(input_algorithm)
            if active_hint:
                warning += f". {active_hint}"
            else:
                warning += ". Use describe_data() to verify field ranges."
        return vtk_obj, _diag.warning(
            vtk_class_name, _diag.KIND_EMPTY_OUTPUT, warning, **_ok_extra
        )

    return vtk_obj, _diag.ok(vtk_class_name, **_ok_extra)


# Extension -> VTK reader class name (canonical map, used by server.py and load_file)
EXT_TO_READER = {
    "vts": "vtkXMLStructuredGridReader",
    "vti": "vtkXMLImageDataReader",
    "vtp": "vtkXMLPolyDataReader",
    "vtu": "vtkXMLUnstructuredGridReader",
    "vtr": "vtkXMLRectilinearGridReader",
    "vtk": "vtkGenericDataObjectReader",
    "nrrd": "vtkNrrdReader",
    "nhdr": "vtkNrrdReader",
}


def load_file(file_path: str):
    """Load a VTK file directly, returning (data, error_message).

    Detects the appropriate reader from the file extension.
    Returns (vtk_data_object, None) on success.
    Returns (None, error_str) on failure.

    Supported extensions: .vts, .vti, .vtp, .vtu, .vtr
    """
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    reader_class = EXT_TO_READER.get(ext)
    if reader_class is None:
        supported = sorted(EXT_TO_READER.keys())
        return None, (
            f"Cannot read '{file_path}': unknown extension '.{ext}'. "
            f"Supported extensions: {supported}"
        )

    try:
        reader, _ = create_vtk_filter(reader_class, FileName=file_path)
        reader.Update()
        data = reader.GetOutput()
    except Exception as e:
        return None, f"Error reading '{file_path}': {e}"

    if data is None or data.GetNumberOfPoints() == 0:
        return None, f"File '{file_path}' loaded but contains no points."

    return data, None


def _apply_properties(vtk_obj, vtk_class_name, properties):
    """Apply properties to a VTK object with special-case handling."""
    for key, value in properties.items():
        if key == "Isosurfaces":
            # Accept single value or list
            if isinstance(value, (int, float)):
                value = [value]
            for i, v in enumerate(value):
                vtk_obj.SetValue(i, v)
            vtk_obj.SetNumberOfContours(len(value))
        elif key == "ContourBy":
            vtk_obj.SetInputArrayToProcess(0, 0, 0, 0, value)
        elif key == "Vectors":
            vtk_obj.SetInputArrayToProcess(0, 0, 0, 0, value)
        elif key == "ThresholdRange":
            # VTK 9.x threshold API
            vtk_obj.SetLowerThreshold(value[0])
            vtk_obj.SetUpperThreshold(value[1])
            vtk_obj.SetThresholdFunction(vtk.vtkThreshold.THRESHOLD_BETWEEN)
        elif key == "ThresholdBy":
            vtk_obj.SetInputArrayToProcess(0, 0, 0, 0, value)
        elif key == "AddScalarArrayName":
            for name in value:
                vtk_obj.AddScalarArrayName(name)
        elif key == "AddVectorArrayName":
            for name in value:
                vtk_obj.AddVectorArrayName(name)
        elif key == "VOI":
            vtk_obj.SetVOI(*value)
        elif key == "SampleRate":
            vtk_obj.SetSampleRate(*value)
        elif key == "IntegrationDirection":
            directions = {
                "Forward": vtk_obj.FORWARD if hasattr(vtk_obj, "FORWARD") else 0,
                "Backward": vtk_obj.BACKWARD if hasattr(vtk_obj, "BACKWARD") else 1,
                "Both": vtk_obj.BOTH if hasattr(vtk_obj, "BOTH") else 2,
            }
            vtk_obj.SetIntegrationDirection(directions.get(value, 2))
        elif key == "IntegratorType":
            integrators = {"RungeKutta2": 0, "RungeKutta4": 1, "RungeKutta45": 2}
            vtk_obj.SetIntegratorType(integrators.get(value, 2))
        elif key == "GlyphSource":
            # value is a vtk algorithm (source for glyphs)
            vtk_obj.SetSourceConnection(value.GetOutputPort())
        elif key == "ScaleArray":
            vtk_obj.SetInputArrayToProcess(0, 0, 0, 0, value)
            vtk_obj.SetScaleModeToScaleByScalar()
        elif key == "OrientationArray":
            vtk_obj.SetInputArrayToProcess(1, 0, 0, 0, value)
            vtk_obj.OrientOn()
            vtk_obj.SetVectorModeToUseVector()
        elif key == "GlyphMode":
            modes = {
                "AllPoints": 0,
                "EveryNthPoint": 1,
                "UniformSpatialDistribution": 2,
            }
            if hasattr(vtk_obj, "SetGlyphMode"):
                vtk_obj.SetGlyphMode(modes.get(value, 0))
        elif key == "VectorMode":
            mode_setter = f"SetVectorModeTo{value}"
            if hasattr(vtk_obj, mode_setter):
                getattr(vtk_obj, mode_setter)()
            else:
                raise ValueError(f"Unknown VectorMode '{value}'")
        elif key == "TensorMode":
            mode_setter = f"SetTensorModeTo{value}"
            if hasattr(vtk_obj, mode_setter):
                getattr(vtk_obj, mode_setter)()
            else:
                raise ValueError(f"Unknown TensorMode '{value}'")
        elif key == "CutFunction":
            # value is a dict like {"type": "Plane", "Origin": (x,y,z), "Normal": (x,y,z)}
            func_type = value.get("type", "Plane")
            if func_type == "Plane":
                func = vtk.vtkPlane()
                if value.get("Origin") is not None:
                    func.SetOrigin(*value["Origin"])
                if value.get("Normal") is not None:
                    func.SetNormal(*value["Normal"])
            elif func_type == "Sphere":
                func = vtk.vtkSphere()
                if value.get("Center") is not None:
                    func.SetCenter(*value["Center"])
                if value.get("Radius") is not None:
                    func.SetRadius(value["Radius"])
            elif func_type == "Box":
                func = vtk.vtkBox()
                if value.get("Bounds") is not None:
                    func.SetBounds(*value["Bounds"])
            else:
                raise ValueError(f"Unsupported CutFunction type: '{func_type}'. "
                                 "Available: Plane, Sphere, Box")
            if hasattr(vtk_obj, "SetCutFunction"):
                vtk_obj.SetCutFunction(func)
            elif hasattr(vtk_obj, "SetClipFunction"):
                vtk_obj.SetClipFunction(func)
        elif key == "_probe_source":
            # Internal: set source for vtkProbeFilter
            if hasattr(value, "GetOutputPort"):
                vtk_obj.SetSourceConnection(value.GetOutputPort())
            elif hasattr(value, "GetOutput"):
                vtk_obj.SetSourceData(value.GetOutput())
        elif key == "SamplingDimensions":
            vtk_obj.SetSamplingDimensions(*value)
        elif key == "LowPoint":
            vtk_obj.SetLowPoint(*value)
        elif key == "HighPoint":
            vtk_obj.SetHighPoint(*value)
        elif key == "SeedSource":
            # value is a vtk algorithm providing seed points
            if hasattr(value, "GetOutputPort"):
                vtk_obj.SetSourceConnection(value.GetOutputPort())
            else:
                vtk_obj.SetSourceData(value)
        elif key == "OnRatio":
            vtk_obj.SetOnRatio(value)
        elif key == "RandomMode":
            if value:
                vtk_obj.RandomModeOn()
            else:
                vtk_obj.RandomModeOff()
        elif key == "GradientField":
            vtk_obj.SetInputArrayToProcess(0, 0, 0, 0, value)
        elif key == "DataExtent":
            vtk_obj.SetDataExtent(*value)
        elif key == "DataScalarType":
            if isinstance(value, str):
                scalar_type = SCALAR_TYPE_MAP.get(value)
                if scalar_type is None:
                    raise ValueError(
                        f"Unknown scalar type '{value}'. "
                        f"Available: {sorted(SCALAR_TYPE_MAP.keys())}"
                    )
                vtk_obj.SetDataScalarType(scalar_type)
            else:
                # Assume it's already a VTK type constant (int)
                vtk_obj.SetDataScalarType(value)
        elif key == "FileDimensionality":
            vtk_obj.SetFileDimensionality(value)
        elif key == "NumberOfScalarComponents":
            vtk_obj.SetNumberOfScalarComponents(value)
        elif key == "HeaderSize":
            vtk_obj.SetHeaderSize(value)
        else:
            # Default: try Set{Key}(value)
            setter = f"Set{key}"
            if hasattr(vtk_obj, setter):
                getattr(vtk_obj, setter)(value)
            else:
                raise ValueError(
                    f"VTK class '{vtk_class_name}' has no method '{setter}'"
                )


def _auto_opacity(arr, scalar_range, num_bins=50, num_points=8, max_opacity=0.6):
    """Generate histogram-guided opacity control points.

    Delegates to :func:`queries._histogram_opacity_points`.
    """
    from .queries import _histogram_opacity_points
    return _histogram_opacity_points(arr, scalar_range, n_bins=num_bins,
                                     num_points=num_points, max_opacity=max_opacity)


def _volume_prepare_data(vtk_algorithm, color_by, scalar_range):
    """Extract and validate data from the algorithm; auto-detect color_by and scalar_range.

    Returns (data, color_by, scalar_range) with any auto-detected values filled in.
    Raises ValueError if the data has no points.
    """
    if hasattr(vtk_algorithm, "GetOutput") or hasattr(vtk_algorithm, "GetOutputDataObject"):
        vtk_algorithm.Update()
    data = _get_algorithm_output(vtk_algorithm)
    if data is None and hasattr(vtk_algorithm, "GetPointData"):
        data = vtk_algorithm  # already a dataset

    if data is None or data.GetNumberOfPoints() == 0:
        # Try to provide an inline range hint using the upstream input
        upstream = _get_algorithm_input(vtk_algorithm)

        hint_parts = []
        if upstream is not None:
            pd = upstream.GetPointData()
            scalars = pd.GetScalars()
            field_name = scalars.GetName() if scalars else None
            if field_name and color_by and field_name != color_by:
                field_name = color_by
            if field_name:
                hint = _format_field_range_hint(upstream, field_name, kind="threshold")
                if "describe_data" not in hint:
                    hint_parts.append(hint)

        range_msg = (
            f" Upstream {hint_parts[0]}." if hint_parts
            else " Use describe_data() to verify field ranges."
        )
        raise ValueError(
            "Volume rendering input has 0 points. "
            "Check your threshold/filter — the data may be empty."
            + range_msg
        )

    # Auto-detect color_by from active scalars if not specified
    if not color_by and data:
        pd = data.GetPointData()
        scalars = pd.GetScalars()
        if scalars:
            color_by = scalars.GetName()

    # Set active scalars if color_by is specified
    if color_by and data:
        pd = data.GetPointData()
        if pd.GetArray(color_by) is not None:
            pd.SetActiveScalars(color_by)

    # Determine scalar range from data if not provided
    if scalar_range is None and data and color_by:
        arr = data.GetPointData().GetArray(color_by)
        if arr:
            scalar_range = arr.GetRange()
    if scalar_range is None and data:
        pd = data.GetPointData()
        if pd.GetScalars():
            scalar_range = pd.GetScalars().GetRange()
    if scalar_range is None:
        scalar_range = (0.0, 1.0)

    return data, color_by, scalar_range


def _volume_build_mapper(vtk_algorithm, data, color_by, volume_resolution):
    """Create a vtkSmartVolumeMapper, resampling to image data if necessary.

    Returns a configured mapper.
    """
    need_resample = True
    if data is not None:
        data_class = data.GetClassName()
        if data_class in ("vtkImageData", "vtkUniformGrid"):
            need_resample = False

    if need_resample:
        resampler = vtk.vtkResampleToImage()
        if hasattr(vtk_algorithm, "GetOutputPort"):
            resampler.SetInputConnection(vtk_algorithm.GetOutputPort())
        else:
            resampler.SetInputDataObject(vtk_algorithm)

        if isinstance(volume_resolution, (list, tuple)):
            resampler.SetSamplingDimensions(*volume_resolution)
        else:
            # Scale dimensions proportionally, using resolution as the longest axis
            if data is not None:
                bounds = data.GetBounds()
                dx = bounds[1] - bounds[0]
                dy = bounds[3] - bounds[2]
                dz = bounds[5] - bounds[4]
                max_dim = max(dx, dy, dz)
                if max_dim > 0:
                    nx = max(2, int(volume_resolution * dx / max_dim))
                    ny = max(2, int(volume_resolution * dy / max_dim))
                    nz = max(2, int(volume_resolution * dz / max_dim))
                    resampler.SetSamplingDimensions(nx, ny, nz)
                else:
                    resampler.SetSamplingDimensions(
                        volume_resolution, volume_resolution, volume_resolution)
            else:
                resampler.SetSamplingDimensions(
                    volume_resolution, volume_resolution, volume_resolution)

        resampler.Update()
        image_data = resampler.GetOutput()

        if color_by and image_data:
            rpd = image_data.GetPointData()
            if rpd.GetArray(color_by) is not None:
                rpd.SetActiveScalars(color_by)

        mapper = vtk.vtkSmartVolumeMapper()
        mapper.SetInputConnection(resampler.GetOutputPort())
    else:
        mapper = vtk.vtkSmartVolumeMapper()
        if hasattr(vtk_algorithm, "GetOutputPort"):
            mapper.SetInputConnection(vtk_algorithm.GetOutputPort())
        else:
            mapper.SetInputData(vtk_algorithm)

    if color_by:
        mapper.SetScalarModeToUsePointFieldData()
        mapper.SelectScalarArray(color_by)

    return mapper


def _volume_build_color_function(color_function, lut_config, scalar_range):
    """Build and return a vtkColorTransferFunction for volume rendering.

    ``color_function`` (an explicit list of ``(value, r, g, b)`` control points)
    takes precedence over ``lut_config`` (a preset name).
    """
    from .colormaps import build_color_transfer_function

    if color_function is not None:
        return build_color_transfer_function(color_function, scalar_range=scalar_range)

    if lut_config is not None:
        return build_color_transfer_function(lut_config, scalar_range=scalar_range)

    # Default: grayscale ramp
    ctf = vtk.vtkColorTransferFunction()
    ctf.AddRGBPoint(scalar_range[0], 0.0, 0.0, 0.0)
    ctf.AddRGBPoint(scalar_range[1], 1.0, 1.0, 1.0)
    return ctf


def _volume_build_opacity_function(opacity_function, data, color_by, scalar_range, opacity_scale):
    """Build and return a vtkPiecewiseFunction for scalar opacity.

    Auto-generates a histogram-guided function when none is supplied.
    """
    from .colormaps import build_opacity_function

    if opacity_function is None and data and color_by:
        arr = data.GetPointData().GetArray(color_by)
        if arr is not None:
            opacity_function = _auto_opacity(arr, scalar_range)

    return build_opacity_function(
        opacity_function, scalar_range=scalar_range, opacity_scale=opacity_scale)


def _volume_build_property(ctf, otf, display_props):
    """Build and return a vtkVolumeProperty from transfer functions and display options."""
    vol_prop = vtk.vtkVolumeProperty()
    vol_prop.SetColor(ctf)
    vol_prop.SetScalarOpacity(otf)
    vol_prop.SetInterpolationTypeToLinear()
    vol_prop.SetAmbient(display_props.get("ambient", 0.3))
    vol_prop.SetDiffuse(display_props.get("diffuse", 0.6))
    vol_prop.SetSpecular(display_props.get("specular", 0.2))
    if display_props.get("specular_power") is not None:
        vol_prop.SetSpecularPower(display_props["specular_power"])

    # Gradient opacity: enhances edges/surfaces by modulating opacity on gradient magnitude
    gradient_opacity = display_props.get("gradient_opacity")
    if gradient_opacity is True:
        gotf = vtk.vtkPiecewiseFunction()
        gotf.AddPoint(0.0, 0.0)
        gotf.AddPoint(0.5, 0.1)
        gotf.AddPoint(1.0, 1.0)
        vol_prop.SetGradientOpacity(gotf)
    elif isinstance(gradient_opacity, list):
        gotf = vtk.vtkPiecewiseFunction()
        for gval, gop in gradient_opacity:
            gotf.AddPoint(gval, gop)
        vol_prop.SetGradientOpacity(gotf)

    shade = display_props.get("shade", True)
    if shade:
        vol_prop.ShadeOn()
    else:
        vol_prop.ShadeOff()

    return vol_prop


def _volume_configure_mapper(mapper, display_props):
    """Apply sample distance and clipping plane settings to the mapper."""
    sample_distance = display_props.get("sample_distance")
    if sample_distance is not None:
        mapper.SetSampleDistance(sample_distance)

    clip_planes = display_props.get("clip_planes")
    if clip_planes:
        planes = vtk.vtkPlaneCollection()
        for cp in clip_planes:
            plane = vtk.vtkPlane()
            if "origin" in cp:
                plane.SetOrigin(*cp["origin"])
            if "normal" in cp:
                plane.SetNormal(*cp["normal"])
            planes.AddItem(plane)
        mapper.SetClippingPlanes(planes)


def _volume_build_scalar_bar(color_function, lut_config, scalar_range, color_by, scalar_bar_prop):
    """Build and return a vtkScalarBarActor, or None if not requested.

    ``color_function`` (explicit control points) takes precedence over
    ``lut_config`` (preset name); a vtkColorTransferFunction is fed directly
    to the bar since it is itself a vtkScalarsToColors.
    """
    if not (scalar_bar_prop and color_by):
        return None

    from .colormaps import build_color_transfer_function, build_lut

    if color_function is not None:
        scalars_to_colors = build_color_transfer_function(
            color_function, scalar_range=scalar_range)
    elif lut_config is not None:
        scalars_to_colors = build_lut(lut_config, scalar_range=scalar_range)
    else:
        scalars_to_colors = vtk.vtkLookupTable()
        scalars_to_colors.SetNumberOfTableValues(256)
        scalars_to_colors.SetTableRange(*scalar_range)
        scalars_to_colors.Build()

    bar = vtk.vtkScalarBarActor()
    bar.SetLookupTable(scalars_to_colors)
    bar.SetTitle(scalar_bar_prop if isinstance(scalar_bar_prop, str) else color_by)
    _style_scalar_bar(bar)
    return bar


def _style_scalar_bar(bar):
    """Apply consistent styling to a vtkScalarBarActor.

    Horizontal orientation with labels above the color strip. The built-in
    title is suppressed — the pipeline renders a separate right-aligned
    vtkTextActor to the left of the bar so long titles can extend leftward
    into the scene without colliding with neighboring bars.

    Pixel-based sizing and positioning is handled by the renderer's
    StartEvent callback (see Renderer._reposition_scalar_bars); the values
    set here are placeholders overwritten at render time.
    """
    bar.SetOrientationToHorizontal()
    bar.SetBarRatio(0.7)  # chunkier color strip (default 0.375) without squeezing labels
    bar.SetTextPositionToPrecedeScalarBar()  # labels above bar
    bar.SetTitle("")
    bar.SetNumberOfLabels(3)
    bar.SetLabelFormat("%.3g")
    bar.SetTextPad(4)  # pixels between bar and tick labels
    bar.UnconstrainedFontSizeOn()
    label = bar.GetLabelTextProperty()
    label.SetFontSize(12)
    label.SetColor(1, 1, 1)
    label.BoldOff()
    label.ItalicOff()
    label.ShadowOff()


def _create_volume(vtk_algorithm, **display_props):
    """Create a vtkVolume for volume rendering.

    Returns (vtkVolume, scalar_bar_or_None).
    """
    color_by = display_props.get("color_by")
    scalar_range = display_props.get("scalar_range")
    lut_config = display_props.get("lut")
    color_function = display_props.get("color_function")
    opacity = display_props.get("opacity", 1.0)
    opacity_function = display_props.get("opacity_function")
    volume_resolution = display_props.get("volume_resolution", 256)

    # Cap volume resolution to prevent OOM
    max_res = 512
    if isinstance(volume_resolution, (int, float)) and volume_resolution > max_res:
        import logging
        logging.getLogger("siva").warning(
            f"volume_resolution={volume_resolution} capped to {max_res}")
        volume_resolution = max_res

    # 1. Validate input data and resolve auto-detected color_by / scalar_range
    data, color_by, scalar_range = _volume_prepare_data(
        vtk_algorithm, color_by, scalar_range)

    # Keep display_props in sync with any auto-detected color_by
    if color_by != display_props.get("color_by"):
        display_props = dict(display_props, color_by=color_by)

    # 2. Build the volume mapper (resamples to image data if needed)
    mapper = _volume_build_mapper(vtk_algorithm, data, color_by, volume_resolution)

    # 3. Build color and opacity transfer functions
    ctf = _volume_build_color_function(color_function, lut_config, scalar_range)
    opacity_scale = opacity if opacity is not None else 1.0
    otf = _volume_build_opacity_function(
        opacity_function, data, color_by, scalar_range, opacity_scale)

    # Warn if user-supplied opacity_function control points are all outside scalar_range.
    # This makes the volume look completely invisible without an obvious error.
    if isinstance(opacity_function, list) and len(opacity_function) >= 1:
        max_op = max(op for _, op in opacity_function)
        if max_op == 0.0:
            field_hint = _format_field_range_hint(data, color_by, kind="generic") if color_by else ""
            hint = (
                f"All opacity_function control points have opacity 0 — "
                f"the volume will be invisible. {field_hint}"
                if field_hint
                else "All opacity_function control points have opacity 0 — the volume will be invisible."
            )
            raise ValueError(hint)
        op_lo = min(v for v, _ in opacity_function)
        op_hi = max(v for v, _ in opacity_function)
        sr_lo, sr_hi = scalar_range
        if op_lo > sr_hi or op_hi < sr_lo:
            field_hint = _format_field_range_hint(data, color_by, kind="generic") if color_by else ""
            range_str = f"[{op_lo:.4g}, {op_hi:.4g}]"
            hint = (
                f"opacity_function value range {range_str} is outside scalar_range "
                f"[{sr_lo:.4g}, {sr_hi:.4g}] — volume will be invisible."
            )
            if field_hint and "describe_data" not in field_hint:
                hint += f" {field_hint}"
            raise ValueError(hint)

    # 4. Assemble the volume property
    vol_prop = _volume_build_property(ctf, otf, display_props)

    # 5. Apply mapper-level settings (sample distance, clipping planes)
    _volume_configure_mapper(mapper, display_props)

    # 6. Assemble the volume actor
    volume = vtk.vtkVolume()
    volume.SetMapper(mapper)
    volume.SetProperty(vol_prop)

    # 7. Optionally build a scalar bar
    bar = _volume_build_scalar_bar(
        color_function, lut_config, scalar_range, color_by, display_props.get("scalar_bar"))

    return volume, bar


def _humanize_field_name(name):
    """Return a human-readable version of a field name (underscores → spaces)."""
    return name.replace("_", " ")


def _infer_display_defaults(vtk_algorithm, display_props):
    """Apply Vega-lite-style inference to fill in smart display defaults.

    Fills in, when not explicitly provided:
    - ``scalar_bar``: auto-enabled (title = humanized field name) whenever
      ``color_by`` is set and the caller did not pass ``scalar_bar`` at all.
      Pass ``scalar_bar=False`` to explicitly suppress it.
    - ``lut``: ``"cool_to_warm"`` (diverging) when the field crosses zero
      (min < 0 < max) and no explicit ``lut`` was given.
    - ``scalar_range``: symmetric around zero when the field is signed and
      no explicit range was given.

    Returns an updated *display_props* dict (shallow-copies only when changes
    are needed, leaving the original unchanged).
    """
    color_by = display_props.get("color_by")
    if not color_by:
        return display_props

    updates = {}

    # Auto scalar_bar: trigger only when the key was never passed by the caller
    if "scalar_bar" not in display_props:
        updates["scalar_bar"] = _humanize_field_name(color_by)

    # Try to read the field range from the upstream data
    field_rng = None
    try:
        data = None
        if hasattr(vtk_algorithm, "GetOutput"):
            vtk_algorithm.Update()
            data = vtk_algorithm.GetOutput()
        elif hasattr(vtk_algorithm, "GetOutputDataObject"):
            data = vtk_algorithm.GetOutputDataObject(0)
        else:
            data = vtk_algorithm
        if data is not None:
            arr, _loc = find_field_array(data, color_by)
            if arr is not None:
                field_rng = arr.GetRange()
    except Exception as exc:
        import logging
        logging.getLogger("siva").debug(
            f"display-default inference: could not read range for '{color_by}': {exc}"
        )

    # Diverging colormap + symmetric range for signed fields
    if (field_rng is not None
            and field_rng[0] < 0 < field_rng[1]
            and "lut" not in display_props
            and "scalar_range" not in display_props):
        abs_max = max(abs(field_rng[0]), abs(field_rng[1]))
        updates["lut"] = "cool_to_warm"
        updates["scalar_range"] = (-abs_max, abs_max)

    if updates:
        display_props = dict(display_props, **updates)
    return display_props


# ---------------------------------------------------------------------------
# show() display-property registry
# ---------------------------------------------------------------------------

# Scope tags for display props: which kind of actor a prop actually reaches.
SCOPE_BOTH = "both"
SCOPE_SURFACE = "surface"
SCOPE_VOLUME = "volume"

#: Every keyword ``show()`` accepts, tagged with the representation it applies
#: to.  ``show()`` reads its ``**display_props`` key by key, so a key that is
#: not in this registry would otherwise be silently dropped; validation against
#: this table turns typos into show-directive errors and mis-scoped props
#: (volume-only keys on a surface actor, or vice versa) into build warnings.
DISPLAY_PROPS = {
    # --- both representations --------------------------------------------
    "color_by": SCOPE_BOTH,
    "scalar_range": SCOPE_BOTH,
    "lut": SCOPE_BOTH,
    "opacity": SCOPE_BOTH,
    "representation": SCOPE_BOTH,
    "scalar_bar": SCOPE_BOTH,
    "ambient": SCOPE_BOTH,
    "diffuse": SCOPE_BOTH,
    "specular": SCOPE_BOTH,
    "specular_power": SCOPE_BOTH,
    # --- surface / wireframe / points actors only -------------------------
    "color": SCOPE_SURFACE,
    "component": SCOPE_SURFACE,
    "line_width": SCOPE_SURFACE,
    "lighting": SCOPE_SURFACE,
    "smooth_shading": SCOPE_SURFACE,
    "split_sharp_edges": SCOPE_SURFACE,
    "feature_angle": SCOPE_SURFACE,
    # --- volume rendering only (representation="Volume") ------------------
    "opacity_function": SCOPE_VOLUME,
    "color_function": SCOPE_VOLUME,
    "gradient_opacity": SCOPE_VOLUME,
    "volume_resolution": SCOPE_VOLUME,
    "shade": SCOPE_VOLUME,
    "sample_distance": SCOPE_VOLUME,
    "clip_planes": SCOPE_VOLUME,
}

#: The accepted ``representation`` values.  "Volume" selects the volume path in
#: ``create_show``; the other three map to ``vtkProperty`` representations.
REPRESENTATIONS = ("Surface", "Wireframe", "Points", "Volume")

#: Props that only take effect when another prop is set.  Maps a prop name to
#: ``(required_prop_names, human explanation)``.
_DISPLAY_PROP_REQUIRES = {
    "feature_angle": (
        ("split_sharp_edges", "smooth_shading"),
        "feature_angle only applies when surface normals are generated",
    ),
}


def display_props_for_scope(scope):
    """Return the sorted prop names that apply to *scope* (shared props included)."""
    return sorted(k for k, s in DISPLAY_PROPS.items()
                  if s == SCOPE_BOTH or s == scope)


def check_display_props(display_props):
    """Check ``show()`` display-prop keys against :data:`DISPLAY_PROPS`.

    Returns ``(error_info, warnings)``:

    - ``error_info`` is ``None`` when every key is a known display prop.
      Otherwise it is a dict with ``property``, ``similar``, ``valid`` and
      ``message`` keys describing the unknown key(s) — mirroring the structured
      info ``_validate_vtk_kwargs_structured`` returns for VTK property typos.
    - ``warnings`` is a list of dicts (``property``, ``scope``, ``message``) for
      keys that are known but do not apply to the representation being built:
      volume-only props on a surface actor and surface-only props on a volume
      (plus props whose effect depends on another prop that wasn't set).  These
      are dropped by the renderer, so the caller reports them rather than
      failing the build.
    """
    scope = (SCOPE_VOLUME if display_props.get("representation") == "Volume"
             else SCOPE_SURFACE)
    other_scope = SCOPE_SURFACE if scope == SCOPE_VOLUME else SCOPE_VOLUME
    valid = sorted(DISPLAY_PROPS)

    unknown = [k for k in display_props if k not in DISPLAY_PROPS]
    if unknown:
        similar = []
        for key in unknown:
            similar += [m for m in difflib.get_close_matches(key, valid, n=3, cutoff=0.6)
                        if m not in similar]
        quoted = ", ".join(f"'{k}'" for k in unknown)
        msg = (
            f"unknown show() display "
            f"propert{'y' if len(unknown) == 1 else 'ies'}: {quoted}\n"
        )
        if similar:
            msg += f"similar: {', '.join(similar)}\n"
        msg += f"valid: [{', '.join(valid)}]"
        return (
            {
                "property": unknown[0] if len(unknown) == 1 else unknown,
                "similar": similar,
                "valid": valid,
                "message": msg,
            },
            [],
        )

    # An unrecognized representation would otherwise fall through to a plain
    # surface actor, making the mistake invisible.
    representation = display_props.get("representation")
    if representation is not None and representation not in REPRESENTATIONS:
        similar = difflib.get_close_matches(str(representation), REPRESENTATIONS,
                                            n=3, cutoff=0.5)
        msg = f"unknown show() representation '{representation}'\n"
        if similar:
            msg += f"similar: {', '.join(similar)}\n"
        msg += f"valid: [{', '.join(REPRESENTATIONS)}]"
        return (
            {
                "property": "representation",
                "similar": similar,
                "valid": list(REPRESENTATIONS),
                "message": msg,
            },
            [],
        )

    if scope == SCOPE_VOLUME:
        rep_desc = "volume rendering"
        other_desc = "representation='Surface'/'Wireframe'/'Points'"
    else:
        rep_desc = f"representation='{display_props.get('representation') or 'Surface'}'"
        other_desc = "representation='Volume'"

    warnings = []
    for key in display_props:
        if DISPLAY_PROPS[key] == other_scope:
            warnings.append({
                "property": key,
                "scope": other_scope,
                "message": (
                    f"'{key}' is a {other_scope}-only display property and was "
                    f"ignored for {rep_desc} — it only applies to {other_desc}"
                ),
            })
    for key, (required, explanation) in _DISPLAY_PROP_REQUIRES.items():
        if key in display_props and not any(display_props.get(r) for r in required):
            warnings.append({
                "property": key,
                "scope": DISPLAY_PROPS[key],
                "message": (
                    f"'{key}' was ignored: {explanation} — set "
                    f"{' or '.join(f'{r}=True' for r in required)}"
                ),
            })
    return None, warnings


def validate_display_props(display_props):
    """Raise ``ValueError`` on unknown ``show()`` display props; return warnings.

    Convenience wrapper around :func:`check_display_props` for callers that want
    exception semantics (``create_show`` itself).  The returned list holds the
    human-readable warning messages for props that were ignored.
    """
    error_info, warnings = check_display_props(display_props)
    if error_info is not None:
        raise ValueError(error_info["message"])
    return [w["message"] for w in warnings]


def _updated_output(vtk_algorithm):
    """Return the (updated) output data object of *vtk_algorithm*, or None."""
    try:
        if hasattr(vtk_algorithm, "Update"):
            vtk_algorithm.Update()
    except Exception as exc:
        import logging
        logging.getLogger("siva").debug(f"shading: could not update input: {exc}")
    return _get_algorithm_output(vtk_algorithm)


def _connect_input(vtk_filter, upstream):
    """Connect *upstream* (algorithm or data object) as the input of *vtk_filter*."""
    if hasattr(upstream, "GetOutputPort"):
        vtk_filter.SetInputConnection(upstream.GetOutputPort())
    else:
        vtk_filter.SetInputData(upstream)


def _apply_surface_shading(mapper, prop, vtk_algorithm, *, smooth_shading,
                           split_sharp_edges, feature_angle):
    """Apply ``smooth_shading`` / ``split_sharp_edges`` to a surface actor.

    Sets the interpolation mode on *prop* (Phong for smooth shading, flat
    otherwise) and, when point normals are required — smooth shading of a
    surface that carries none, or edge splitting — inserts a
    ``vtkPolyDataNormals`` filter between *vtk_algorithm* and *mapper*, the way
    pyvista's ``smooth_shading``/``split_sharp_edges`` do.  Non-polydata input
    is converted with a ``vtkGeometryFilter`` first, since
    ``vtkPolyDataNormals`` only accepts polydata.

    ``feature_angle`` (degrees) is the sharp-edge threshold used by the normals
    filter; it has no effect unless normals are generated (``check_display_props``
    warns in that case).

    Returns the inserted ``vtkPolyDataNormals``, or ``None`` when none was needed.
    """
    if smooth_shading is None and not split_sharp_edges:
        return None

    if smooth_shading:
        prop.SetInterpolationToPhong()
    elif smooth_shading is not None:
        prop.SetInterpolationToFlat()

    data = _updated_output(vtk_algorithm)
    has_normals = False
    try:
        has_normals = (data is not None
                       and data.GetPointData().GetNormals() is not None)
    except Exception:
        has_normals = False

    # Splitting always needs the filter; smooth shading only when the surface
    # doesn't already carry point normals.
    if not split_sharp_edges and has_normals:
        return None

    normals = vtk.vtkPolyDataNormals()
    normals.ComputePointNormalsOn()
    normals.ComputeCellNormalsOff()
    normals.ConsistencyOn()
    if feature_angle is not None:
        normals.SetFeatureAngle(feature_angle)
    if split_sharp_edges:
        normals.SplittingOn()
    else:
        normals.SplittingOff()

    upstream = vtk_algorithm
    if not isinstance(data, vtk.vtkPolyData):
        geometry = vtk.vtkGeometryFilter()
        _connect_input(geometry, vtk_algorithm)
        upstream = geometry
    _connect_input(normals, upstream)
    mapper.SetInputConnection(normals.GetOutputPort())
    return normals


def create_show(vtk_algorithm, **display_props):
    """Create a vtkActor (or vtkVolume) from a filter's output with display properties.

    Returns (renderable, scalar_bar_or_None) where renderable is either a
    vtkActor or a vtkVolume (for representation="Volume").

    Display defaults are inferred automatically (Vega-lite style) when
    ``color_by`` is set and the caller did not provide explicit overrides:

    - **scalar_bar** is added automatically with a title derived from the
      field name (underscores replaced by spaces).  Pass ``scalar_bar=False``
      to suppress it.
    - **Diverging colormap** (``cool_to_warm``) is selected automatically
      when the field range crosses zero (min < 0 < max) and no explicit
      ``lut`` was given.  The scalar range is made symmetric:
      ``(-max(|min|, |max|), +max(|min|, |max|))``.
    - Explicit ``lut``, ``scalar_range``, or ``scalar_bar`` values always
      override inference.

    Display-prop keys are validated against :data:`DISPLAY_PROPS`: an unknown
    key raises ``ValueError``, and a key that does not apply to the
    representation being built is logged as a warning (callers that build a
    report should use :func:`check_display_props` to surface those instead).
    """
    for _warning in validate_display_props(display_props):
        import logging
        logging.getLogger("siva").warning(f"show(): {_warning}")

    representation = display_props.get("representation")

    # Apply field-specific defaults if no lut/scalar_range provided
    color_by_field = display_props.get("color_by")
    if color_by_field and (display_props.get("lut") is None or display_props.get("scalar_range") is None):
        from .colormaps import FIELD_DEFAULTS
        defaults = FIELD_DEFAULTS.get(color_by_field, {})
        if display_props.get("lut") is None and "lut" in defaults:
            display_props = dict(display_props, lut=defaults["lut"])
        if display_props.get("scalar_range") is None and "scalar_range" in defaults:
            display_props = dict(display_props, scalar_range=defaults["scalar_range"])

    # Vega-lite-style inference: auto scalar_bar + diverging for signed fields
    display_props = _infer_display_defaults(vtk_algorithm, display_props)

    # Volume rendering path
    if representation == "Volume":
        return _create_volume(vtk_algorithm, **display_props)

    # Standard actor path
    mapper = vtk.vtkDataSetMapper()
    if hasattr(vtk_algorithm, "GetOutputPort"):
        mapper.SetInputConnection(vtk_algorithm.GetOutputPort())
    else:
        mapper.SetInputData(vtk_algorithm)

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)

    prop = actor.GetProperty()

    color_by = display_props.get("color_by")
    scalar_range = display_props.get("scalar_range")
    color = display_props.get("color")
    opacity = display_props.get("opacity")
    specular = display_props.get("specular")
    specular_power = display_props.get("specular_power")
    lut_config = display_props.get("lut")
    line_width = display_props.get("line_width", 3.0)
    component = display_props.get("component")

    # Resolve component names to indices
    if component is not None:
        if isinstance(component, str):
            component = COMPONENT_NAME_MAP.get(component.lower())
            if component is None:
                raise ValueError(
                    f"Unknown component name '{display_props.get('component')}'. "
                    "Use 0/1/2 or 'x'/'y'/'z'."
                )
        component = int(component)

    # Auto-detect scalar_range for a specific vector component
    if color_by and component is not None and scalar_range is None:
        if hasattr(vtk_algorithm, "GetOutput"):
            vtk_algorithm.Update()
            _data = vtk_algorithm.GetOutput()
        else:
            _data = vtk_algorithm
        if _data:
            arr = _data.GetPointData().GetArray(color_by)
            if arr is None:
                arr = _data.GetCellData().GetArray(color_by)
            if arr and arr.GetNumberOfComponents() > component:
                scalar_range = arr.GetRange(component)

    if color_by:
        mapper.SetScalarModeToUsePointFieldData()
        mapper.SelectColorArray(color_by)
        mapper.SetColorModeToMapScalars()
        mapper.ScalarVisibilityOn()
        if scalar_range:
            mapper.SetScalarRange(*scalar_range)

        if lut_config:
            from .colormaps import build_lut
            lut = build_lut(lut_config, scalar_range=scalar_range)
            mapper.SetLookupTable(lut)

        # Vector component coloring: color by a single component instead of magnitude
        if component is not None:
            lut = mapper.GetLookupTable()
            lut.SetVectorModeToComponent()
            lut.SetVectorComponent(component)
    elif color:
        mapper.ScalarVisibilityOff()
        prop.SetColor(*color)
    else:
        mapper.ScalarVisibilityOn()

    if opacity is not None:
        prop.SetOpacity(opacity)
    if specular is not None:
        prop.SetSpecular(specular)
    if specular_power is not None:
        prop.SetSpecularPower(specular_power)
    if line_width is not None:
        prop.SetLineWidth(line_width)

    # Lighting / shading model
    lighting = display_props.get("lighting")
    ambient = display_props.get("ambient")
    diffuse = display_props.get("diffuse")
    if lighting is not None:
        prop.SetLighting(bool(lighting))
    if ambient is not None:
        prop.SetAmbient(ambient)
    if diffuse is not None:
        prop.SetDiffuse(diffuse)
    _apply_surface_shading(
        mapper, prop, vtk_algorithm,
        smooth_shading=display_props.get("smooth_shading"),
        split_sharp_edges=display_props.get("split_sharp_edges"),
        feature_angle=display_props.get("feature_angle"),
    )

    if representation:
        rep_map = {
            "Surface": vtk.VTK_SURFACE,
            "Wireframe": vtk.VTK_WIREFRAME,
            "Points": vtk.VTK_POINTS,
        }
        if representation in rep_map:
            prop.SetRepresentation(rep_map[representation])

    # Scalar bar (color legend)
    scalar_bar_prop = display_props.get("scalar_bar")
    if scalar_bar_prop and color_by:
        bar = vtk.vtkScalarBarActor()
        bar.SetLookupTable(mapper.GetLookupTable())
        bar.SetTitle(scalar_bar_prop if isinstance(scalar_bar_prop, str) else color_by)
        _style_scalar_bar(bar)
        return actor, bar
    return actor, None
