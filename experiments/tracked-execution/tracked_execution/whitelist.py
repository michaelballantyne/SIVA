"""Whitelist and blacklist for tracked proxy dispatch.

WHITELIST is a set of (class, method_name) pairs that are allowed to be called
through dispatch(). BLACKLIST is a set of (class, method_name) pairs that are
explicitly blocked (filesystem/network operations and in-place mutations).

We use a manually curated whitelist for safety. The companion script
`experiments/tracked-execution/scripts/generate_whitelist.py` produces a
coverage report at WHITELIST-COVERAGE.md showing which methods are covered.
"""

import numpy as np

try:
    import pyvista as pv
    _pv_DataSet = pv.DataSet
    _pv_PolyData = pv.PolyData
    _pv_UnstructuredGrid = pv.UnstructuredGrid
    _pv_ImageData = pv.ImageData
    _pv_StructuredGrid = pv.StructuredGrid
    _pv_DataSetAttributes = pv.core.DataSetAttributes
    _PYVISTA_AVAILABLE = True
except ImportError:
    _PYVISTA_AVAILABLE = False
    _pv_DataSet = None
    _pv_PolyData = None
    _pv_UnstructuredGrid = None
    _pv_ImageData = None
    _pv_StructuredGrid = None
    _pv_DataSetAttributes = None


# ---------------------------------------------------------------------------
# Build whitelist entries
# ---------------------------------------------------------------------------

_WHITELIST_ENTRIES: list[tuple] = []
_BLACKLIST_ENTRIES: list[tuple] = []


def _add_methods(cls, methods):
    """Add (cls, method_name) pairs to the whitelist."""
    if cls is None:
        return
    for m in methods:
        _WHITELIST_ENTRIES.append((cls, m))


def _add_blacklist(cls, methods):
    """Add (cls, method_name) pairs to the blacklist."""
    if cls is None:
        return
    for m in methods:
        _BLACKLIST_ENTRIES.append((cls, m))


# --- numpy ndarray ---
_ndarray = np.ndarray

_add_methods(_ndarray, [
    # Statistical reductions
    "mean", "std", "min", "max", "sum", "var",
    "argmin", "argmax", "cumsum",
    "all", "any",
    "prod", "cumprod",
    # Shape/metadata (these return scalars or tuples that escape the proxy)
    "size", "ndim", "flatten", "reshape",
    # Sorting and searching
    "argsort", "sort", "argpartition", "searchsorted",
    # Type conversion
    "astype",
    # Transpose / shape manipulation
    "T", "transpose", "swapaxes", "squeeze", "ravel",
    # Indexing / slicing
    "__getitem__",
    "__len__",
    # Comparison operators
    "__gt__", "__lt__", "__ge__", "__le__", "__eq__", "__ne__",
    # Arithmetic operators
    "__add__", "__radd__", "__sub__", "__rsub__",
    "__mul__", "__rmul__", "__truediv__", "__rtruediv__",
    "__floordiv__", "__mod__", "__pow__",
    "__neg__", "__abs__",
    "__and__", "__or__", "__xor__", "__invert__",
    # Boolean
    "__bool__",
    # Repr / str (for debugging)
    "__repr__",
    # tolist for converting to Python scalars
    "tolist", "item",
    # Copy
    "copy",
    # __array__ — needed for numpy interop (np.percentile(proxy) calls this)
    "__array__",
    # shape/dtype as properties (accessed via dispatch)
    "shape", "dtype",
    # Mathematical / linear algebra
    "dot", "trace", "diagonal",
    "round", "clip",
    "real", "imag", "conj", "conjugate",
    # Iteration / element access
    "nonzero", "compress", "take", "repeat", "choose",
    # Memory info (read-only)
    "nbytes", "itemsize", "strides", "flags",
])

# In-place mutation is blocked for numpy
_add_blacklist(_ndarray, [
    "__setitem__", "__iadd__", "__isub__", "__imul__", "__itruediv__",
    "__ifloordiv__", "__imod__", "__ipow__",
    # Filesystem
    "tofile",
])

# --- PyVista DataSet (base class) ---
if _PYVISTA_AVAILABLE:
    _PYVISTA_DATASET_METHODS = [
        # --- Common filters ---
        "threshold", "threshold_percent", "clip", "clip_box", "clip_scalar", "clip_surface",
        "contour", "slice", "slice_orthogonal", "slice_along_axis",
        "slice_along_line", "slice_implicit", "slice_index",
        "extract_surface", "extract_geometry",
        "extract_all_edges", "extract_feature_edges",
        "extract_cells", "extract_cells_by_type", "extract_points", "extract_values",
        "cell_data_to_point_data", "point_data_to_cell_data",
        "compute_gradient", "compute_normals",
        "compute_cell_quality", "compute_cell_sizes", "compute_derivative",
        "compute_implicit_distance",
        "smooth", "subdivide", "decimate",
        "warp_by_scalar", "warp_by_vector",
        "extract_largest", "connectivity",
        "merge", "boolean_union", "boolean_difference",
        "select_enclosed_points", "select_interior_points",
        "transform", "translate", "scale",
        "rotate_x", "rotate_y", "rotate_z", "rotate", "rotate_vector",
        "reflect", "flip_x", "flip_y", "flip_z", "flip_normal",
        "elevation", "explode", "shrink", "glyph",
        "outline", "outline_corners",
        "delaunay_3d",
        "integrate_data", "interpolate", "sample", "probe",
        "sample_over_line", "sample_over_circular_arc",
        "sample_over_circular_arc_normal", "sample_over_multiple_lines",
        "streamlines", "streamlines_from_source", "streamlines_evenly_spaced_2D",
        "cell_quality", "cell_centers", "cell_neighbors",
        "concatenate",
        "find_cells_within_bounds", "find_containing_cell",
        "find_cells_along_line", "find_cells_intersecting_line",
        "texture_map_to_plane", "texture_map_to_sphere",
        "voxelize", "voxelize_binary_mask", "voxelize_rectilinear",
        "volume",

        # --- Metadata / properties ---
        "n_points", "n_cells", "n_arrays", "bounds", "center", "length",
        "area", "bounds_size", "is_empty", "has_nonlinear_cells",
        "actual_memory_size",
        "number_of_cells", "number_of_points",
        "distinct_cell_types",
        "points", "get_array",
        "active_scalars_name",
        "active_scalars", "active_scalars_info",
        "active_normals",
        "active_vectors", "active_vectors_name",
        "active_tensors", "active_tensors_name",
        # Field access
        "__getitem__",
        # Copy
        "copy", "deep_copy", "shallow_copy",
        # Conversions / casts
        "to_polydata", "cast_to_unstructured_grid", "cast_to_multiblock",
        # Array names / data
        "array_names", "point_data", "cell_data", "field_data",
        "get_array_association",
        # Sampling / probing (additional)
        "get_data_range",
        # Repr / str
        "__repr__",
        # Head (first N cells)
        "head",
        # Splitting / partitioning
        "split_bodies",
        # Bounding box
        "oriented_bounding_box",
    ]
    _add_methods(_pv_DataSet, _PYVISTA_DATASET_METHODS)

    # Also add to specific subclasses so MRO walk finds them
    for _cls in [_pv_PolyData, _pv_UnstructuredGrid, _pv_ImageData, _pv_StructuredGrid]:
        if _cls is not None:
            _add_methods(_cls, _PYVISTA_DATASET_METHODS + [
                # PolyData-specific
                "triangulate", "clean", "fill_holes",
                "strip", "tube", "ribbon", "extrude",
                "delaunay_2d",
                "curvature", "geodesic", "geodesic_distance",
                "edge_mask", "flip_normals",
                # ImageData/StructuredGrid specific
                "cast_to_rectilinear_grid", "cast_to_structured_grid",
                # ImageData-specific
                "dimensions", "spacing", "origin", "extent",
                "image_threshold", "gaussian_smooth", "median_smooth",
                "low_pass", "high_pass", "fft", "rfft",
                "pad_image", "resize", "resample",
                "number_of_scalar_components", "scalar_size", "scalar_type",
                "scalar_type_max", "scalar_type_min", "increments",
                "image_dilate_erode", "dilate", "erode",
                # UnstructuredGrid / StructuredGrid specific
                "extract_subset",
            ])

    # PyVista DataSetAttributes (point_data, cell_data result)
    if _pv_DataSetAttributes is not None:
        _add_methods(_pv_DataSetAttributes, [
            "__getitem__", "__setitem__", "__contains__",
            "keys", "values", "items", "__len__", "__iter__",
        ])

    # Blacklist for PyVista: no filesystem operations and no hidden state mutation
    for _cls in [_pv_DataSet, _pv_PolyData, _pv_UnstructuredGrid, _pv_ImageData, _pv_StructuredGrid]:
        if _cls is not None:
            _add_blacklist(_cls, [
                "save", "export", "write",
                "__setitem__",  # prevent mutation of dataset fields via proxy
                "set_active_scalars",   # hidden state mutation: use scalars= explicitly
                "set_active_vectors",   # hidden state mutation: use vectors= explicitly
                "set_active_tensors",   # hidden state mutation: use tensors= explicitly
            ])


# --- Built-in Python types that might appear as proxy targets ---
# dict (e.g., point_data might return dict-like)
_add_methods(dict, ["__getitem__", "__len__", "keys", "values", "items", "__contains__"])
_add_methods(list, ["__getitem__", "__len__", "__contains__"])
_add_methods(tuple, ["__getitem__", "__len__", "__contains__"])


# ---------------------------------------------------------------------------
# Freeze into sets for O(1) lookup
# ---------------------------------------------------------------------------

WHITELIST: frozenset[tuple] = frozenset(_WHITELIST_ENTRIES)
BLACKLIST: frozenset[tuple] = frozenset(_BLACKLIST_ENTRIES)
