"""Whitelist and blacklist for the pandas tracked proxy dispatch.

WHITELIST is a set of (class, method_name) pairs allowed through dispatch().
BLACKLIST is a set of (class, method_name) pairs that are explicitly blocked
(filesystem writes and in-place mutations).
"""

import pandas as pd

# ---------------------------------------------------------------------------
# Build whitelist entries
# ---------------------------------------------------------------------------

_WHITELIST_ENTRIES: list[tuple] = []
_BLACKLIST_ENTRIES: list[tuple] = []


def _add_methods(cls, methods):
    """Add (cls, method_name) pairs to the whitelist."""
    for m in methods:
        _WHITELIST_ENTRIES.append((cls, m))


def _add_blacklist(cls, methods):
    """Add (cls, method_name) pairs to the blacklist."""
    for m in methods:
        _BLACKLIST_ENTRIES.append((cls, m))


# --- pandas DataFrame ---
_df_methods = [
    # Selection
    "query", "head", "tail", "__getitem__", "__len__",
    "loc", "iloc",
    # Aggregation
    "groupby", "agg", "sum", "mean", "std", "min", "max", "count",
    "describe", "value_counts",
    # Transformation
    "sort_values", "sort_index", "reset_index", "set_index",
    "rename", "drop", "dropna", "fillna", "replace",
    "astype", "copy",
    # Reshaping
    "pivot", "pivot_table", "melt", "merge", "join",
    # Properties
    "columns", "dtypes", "shape", "index", "values",
    "T", "empty",
    # String/apply
    "apply", "map",
    # Repr
    "__repr__", "to_string",
]

_df_blacklist = [
    "__setitem__", "__delitem__",
    "to_csv", "to_parquet", "to_excel", "to_sql", "to_json",
    "to_clipboard", "to_pickle",
]

_add_methods(pd.DataFrame, _df_methods)
_add_blacklist(pd.DataFrame, _df_blacklist)

# --- pandas Series ---
_series_methods = [
    # Selection
    "__getitem__", "__len__",
    # Aggregation
    "agg", "sum", "mean", "std", "min", "max", "count",
    "describe", "value_counts",
    # Transformation
    "sort_values", "sort_index", "reset_index",
    "rename", "drop", "dropna", "fillna", "replace",
    "astype", "copy",
    # Properties
    "dtype", "shape", "index", "values", "name", "empty",
    # Apply/map
    "apply", "map",
    # Repr
    "__repr__", "to_string",
    # Comparison operators
    "__gt__", "__lt__", "__ge__", "__le__", "__eq__", "__ne__",
    # Arithmetic
    "__add__", "__radd__", "__sub__", "__rsub__",
    "__mul__", "__rmul__", "__truediv__", "__rtruediv__",
]

_series_blacklist = [
    "__setitem__", "__delitem__",
    "to_csv", "to_json", "to_pickle",
]

_add_methods(pd.Series, _series_methods)
_add_blacklist(pd.Series, _series_blacklist)

# --- pandas GroupBy ---
_groupby_methods = [
    "agg", "aggregate", "sum", "mean", "std", "min", "max", "count",
    "describe", "first", "last", "size",
    "__getitem__", "__len__",
]

_add_methods(pd.core.groupby.DataFrameGroupBy, _groupby_methods)
_add_methods(pd.core.groupby.SeriesGroupBy, _groupby_methods)

# ---------------------------------------------------------------------------
# Freeze into sets for O(1) lookup
# ---------------------------------------------------------------------------

WHITELIST: frozenset[tuple] = frozenset(_WHITELIST_ENTRIES)
BLACKLIST: frozenset[tuple] = frozenset(_BLACKLIST_ENTRIES)
