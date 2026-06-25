"""Inspect a data file and return its metadata as a DatasetInfo.

Dispatch lives in adapters.py: the registry picks the adapter that recognizes
the file. Unknown formats raise UnsupportedFormatError.

inspect also resolves semantic roles that downstream verbs need — currently the
spatial-coordinate variables (`info.positions`), auto-detected from the variable
names. Pass `positions=('x','y','z')` to override when detection can't tell.
"""

from adapters import get_adapter, detect_positions, UnsupportedFormatError  # noqa: F401


def inspect_file(filepath, positions=None):
    info = get_adapter(filepath).inspect(filepath)
    # Resolve which variables are spatial coordinates once, at the format
    # boundary, so render/etc. stay meaning-blind. None when there are no
    # explicit coordinate variables (e.g. a grid).
    info.positions = positions if positions is not None else detect_positions(info.variables)
    return info
