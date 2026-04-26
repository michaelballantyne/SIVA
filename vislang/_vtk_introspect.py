"""VTK introspection helpers shared across vislang modules.

Provides small, focused utilities for inspecting VTK objects so callers
don't duplicate the same hasattr/try patterns across filters.py and queries.py.
"""

# Per-class cache of valid Set* property names (without the "Set" prefix).
# Populated lazily on first use.
_vtk_setter_cache: dict = {}  # vtk_class_name -> frozenset of property names


def find_field_array(data, field_name):
    """Return (vtk_array, location) where location is 'point' or 'cell', or (None, None).

    Searches point data first, then cell data.

    Args:
        data: VTK dataset (vtkDataSet subclass).
        field_name: Name of the field array to look up.

    Returns:
        A ``(vtk_array, location)`` tuple where *location* is ``"point"`` or
        ``"cell"``, or ``(None, None)`` if the array is not found.
    """
    arr = data.GetPointData().GetArray(field_name)
    if arr is not None:
        return arr, "point"
    arr = data.GetCellData().GetArray(field_name)
    if arr is not None:
        return arr, "cell"
    return None, None


def get_algorithm_output(alg):
    """Return the vtkDataObject output of *alg*.

    Handles both ``vtkAlgorithm.GetOutput()`` (most filters/readers) and
    ``vtkTrivialProducer.GetOutputDataObject(0)`` (some composite sources).
    If *alg* is already a dataset (no GetOutput/GetOutputDataObject), it is
    returned as-is.  Returns ``None`` on failure.

    Args:
        alg: VTK algorithm, producer, or dataset.

    Returns:
        The output vtkDataObject, or ``None``.
    """
    if alg is None:
        return None
    try:
        if hasattr(alg, "GetOutput"):
            return alg.GetOutput()
        elif hasattr(alg, "GetOutputDataObject"):
            return alg.GetOutputDataObject(0)
    except Exception:
        pass
    return alg if hasattr(alg, "GetPointData") else None


def get_algorithm_input(alg):
    """Return the upstream output dataset, regardless of how it is connected.

    Tries ``GetInput()`` first (most filters), then falls back to
    ``GetInputDataObject(0, 0)`` and finally ``GetInputConnection``.
    Returns ``None`` if the upstream cannot be determined.

    Args:
        alg: VTK algorithm.

    Returns:
        The upstream vtkDataObject, or ``None``.
    """
    if alg is None:
        return None
    try:
        if hasattr(alg, "GetInput"):
            result = alg.GetInput()
            if result is not None:
                return result
        if hasattr(alg, "GetInputDataObject"):
            result = alg.GetInputDataObject(0, 0)
            if result is not None:
                return result
    except Exception:
        pass
    return None


def vtk_setter_names(vtk_class) -> frozenset:
    """Return a cached frozenset of valid 'Set*' kwarg names for *vtk_class*.

    Only names whose corresponding ``Set<Name>`` is callable are included,
    with the leading ``"Set"`` stripped.  Results are cached per class name so
    introspection only happens once per VTK class.

    Args:
        vtk_class: An instantiated VTK object (the class is inferred from it)
            OR a VTK class object.  Passing an instance is slightly more
            convenient for callers that already have one.

    Returns:
        A ``frozenset`` of property name strings (without ``"Set"`` prefix).
    """
    if isinstance(vtk_class, type):
        cls = vtk_class
    else:
        cls = type(vtk_class)
    class_name = cls.__name__

    if class_name in _vtk_setter_cache:
        return _vtk_setter_cache[class_name]

    instance = vtk_class if not isinstance(vtk_class, type) else vtk_class()
    valid = frozenset(
        name[3:]  # strip "Set"
        for name in dir(instance)
        if name.startswith("Set") and len(name) > 3 and callable(getattr(instance, name, None))
    )
    _vtk_setter_cache[class_name] = valid
    return valid
