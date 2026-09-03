"""VTK introspection helpers shared across siva modules.

Provides small, focused utilities for inspecting VTK objects so callers
don't duplicate the same hasattr/try patterns across filters.py and queries.py.
"""

import re

# Per-class cache of valid Set* property names (without the "Set" prefix).
# Populated lazily on first use.
_vtk_setter_cache: dict = {}  # vtk_class_name -> frozenset of property names

# Per-class cache of "generic" Set* names inherited from vtkObject/vtkAlgorithm
# -family plumbing base classes (see generic_algorithm_setter_names below).
_generic_setter_cache: dict = {}  # vtk_class_name -> frozenset of property names


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


def generic_algorithm_setter_names(vtk_class) -> frozenset:
    """Return the cached frozenset of 'Set*' names (without 'Set') inherited
    from generic vtkObject/vtkAlgorithm-family plumbing base classes.

    This covers ``vtkObject``/``vtkObjectBase`` themselves plus any
    intermediate "...Algorithm" base in the class's MRO (``vtkAlgorithm``,
    but also more specific plumbing bases like ``vtkPolyDataAlgorithm`` or
    ``vtkImageAlgorithm`` that every filter producing that output type
    inherits, e.g. ``SetInputData``). These names are executive/pipeline
    bookkeeping (``Debug``, ``ProgressText``, ``InputConnection``,
    ``GlobalWarningDisplay``, ...) rather than anything a spec author would
    set on a *specific* filter, so callers use this to exclude them from
    curated "valid property" listings (see ``filters._display_setter_names``).
    Membership is queried on the class object itself (not an instance),
    since several of these bases (e.g. ``vtkImageAlgorithm``) are abstract
    and cannot be instantiated directly.

    Args:
        vtk_class: An instantiated VTK object OR a VTK class object.

    Returns:
        A ``frozenset`` of property name strings (without ``"Set"`` prefix).
    """
    cls = vtk_class if isinstance(vtk_class, type) else type(vtk_class)
    class_name = cls.__name__

    if class_name in _generic_setter_cache:
        return _generic_setter_cache[class_name]

    generic = set()
    for base in cls.__mro__[1:]:
        base_name = base.__name__
        if base_name == "object":
            continue
        if base_name in ("vtkObject", "vtkObjectBase") or (
            base_name.startswith("vtk") and base_name.endswith("Algorithm")
        ):
            for name in dir(base):
                if name.startswith("Set") and len(name) > 3 and callable(getattr(base, name, None)):
                    generic.add(name[3:])

    result = frozenset(generic)
    _generic_setter_cache[class_name] = result
    return result


def is_shortcut_setter(vtk_class, setter_name: str) -> bool:
    """True if ``Set<setter_name>``'s first overload takes no arguments
    besides ``self``.

    VTK generates zero-arg convenience methods like
    ``SetRepresentationToWireframe()`` for enum-valued properties (the
    ``SetXxxToYyy()`` idiom); these show up in ``dir()`` alongside the real
    ``SetRepresentation(value)`` setter but can't be used as an ``Xxx=value``
    kwarg (calling them with an argument raises a VTK ``TypeError``). This
    inspects the bound method's docstring, which VTK's Python wrapping
    populates with the C++ signature (e.g. ``"SetRepresentationToWireframe(self) -> None"``),
    to detect the pattern without needing to hardcode name matching.

    Args:
        vtk_class: An instantiated VTK object OR a VTK class object.
        setter_name: Property name without the ``"Set"`` prefix.

    Returns:
        ``True`` if ``Set<setter_name>`` exists and takes no arguments beyond
        ``self`` in its first documented overload; ``False`` otherwise
        (including when the method doesn't exist).
    """
    cls = vtk_class if isinstance(vtk_class, type) else type(vtk_class)
    method = getattr(cls, f"Set{setter_name}", None)
    if method is None:
        return False
    doc = method.__doc__ or ""
    first_line = doc.splitlines()[0] if doc else ""
    return bool(re.match(r"^\w+\(self\)\s*->", first_line))
