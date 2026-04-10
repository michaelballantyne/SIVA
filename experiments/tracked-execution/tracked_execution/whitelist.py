"""Whitelist and blacklist for tracked proxy dispatch.

WHITELIST is a set of (class, method_name) pairs that are allowed to be called
through dispatch(). BLACKLIST is a set of (class, method_name) pairs that are
explicitly blocked (filesystem/network operations and in-place mutations).

We use a manually curated whitelist for safety. Auto-generation from introspection
is a future Phase 4 item.
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
    # Shape/metadata (these return scalars or tuples that escape the proxy)
    "size", "ndim", "flatten", "reshape",
    # Type conversion
    "astype",
    # Transpose
    "T",
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
])

# In-place mutation is blocked for numpy
_add_blacklist(_ndarray, [
    "__setitem__", "__iadd__", "__isub__", "__imul__", "__itruediv__",
    "__ifloordiv__", "__imod__", "__ipow__",
])

# --- PyVista DataSet (base class) ---
if _PYVISTA_AVAILABLE:
    _PYVISTA_DATASET_METHODS = [
        # Common filters
        "threshold", "threshold_percent", "clip", "clip_box",
        "contour", "slice", "slice_orthogonal", "slice_along_axis",
        "extract_surface", "extract_geometry",
        "cell_data_to_point_data", "point_data_to_cell_data",
        "compute_gradient", "compute_normals",
        "smooth", "subdivide", "decimate",
        "warp_by_scalar", "warp_by_vector",
        "extract_largest", "connectivity",
        "merge", "boolean_union", "boolean_difference",
        "select_enclosed_points",
        "transform", "translate", "scale", "rotate_x", "rotate_y", "rotate_z",
        # Metadata / properties
        "n_points", "n_cells", "bounds", "center", "length",
        "points", "get_array", "set_active_scalars",
        "active_scalars_name",
        # Field access
        "__getitem__",
        # Copy
        "copy", "deep_copy",
        # Conversions
        "to_polydata", "cast_to_unstructured_grid",
        # Array names
        "array_names", "point_data", "cell_data",
        # Sampling / probing
        "sample", "probe",
        # Stats
        "get_data_range",
        # Repr / str
        "__repr__",
    ]
    _add_methods(_pv_DataSet, _PYVISTA_DATASET_METHODS)

    # Also add to specific subclasses so MRO walk finds them
    for _cls in [_pv_PolyData, _pv_UnstructuredGrid, _pv_ImageData, _pv_StructuredGrid]:
        if _cls is not None:
            _add_methods(_cls, _PYVISTA_DATASET_METHODS + [
                # PolyData-specific
                "triangulate", "clean", "fill_holes",
                # ImageData/StructuredGrid specific
                "cast_to_rectilinear_grid", "cast_to_structured_grid",
            ])

    # PyVista DataSetAttributes (point_data, cell_data result)
    if _pv_DataSetAttributes is not None:
        _add_methods(_pv_DataSetAttributes, [
            "__getitem__", "__setitem__", "__contains__",
            "keys", "values", "items", "__len__", "__iter__",
        ])

    # Blacklist for PyVista: no filesystem operations
    for _cls in [_pv_DataSet, _pv_PolyData, _pv_UnstructuredGrid, _pv_ImageData, _pv_StructuredGrid]:
        if _cls is not None:
            _add_blacklist(_cls, [
                "save", "export", "write",
                "__setitem__",  # prevent mutation of dataset fields via proxy
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
