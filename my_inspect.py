"""Inspect a data file and return its metadata as a DatasetInfo.

Dispatch lives in adapters.py: the registry picks the adapter that recognizes
the file. Unknown formats raise UnsupportedFormatError.
"""

from adapters import get_adapter, UnsupportedFormatError  # noqa: F401


def inspect_file(filepath):
    return get_adapter(filepath).inspect(filepath)
