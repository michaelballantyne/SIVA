"""Structured per-node status schema for SIVA pipelines.

Every node status dict has the shape::

    {
        "status": "ok" | "error" | "skipped" | "warning",
        "class":  str,          # VTK class or wrapper name (always present)
        "kind":   str,          # structured category (always present on non-ok)
        "message": str,         # human-readable summary (always present on non-ok)
        # ... kind-specific structured fields (see below)
    }

Kind-specific fields
--------------------
KIND_UNKNOWN_PROPERTY
    property     str   -- the typo'd property name
    vtk_class    str   -- the VTK class it was applied to
    similar      list  -- close matches (may be empty)
    valid        list  -- all valid property names for the class

KIND_MISSING_REQUIRED_ARG
    arg          str   -- missing argument name
    expected     str   -- human description of expected value/format

KIND_INVALID_ARG
    arg          str   -- the argument that was invalid
    value        any   -- the value supplied
    expected     str   -- description of what was expected

KIND_FIELD_NOT_FOUND
    field        str   -- the field name that was not found
    available    list  -- arrays actually present

KIND_FIELD_OUT_OF_RANGE
    field        str   -- field name
    range        list  -- [actual_min, actual_max]
    value        any   -- the user-supplied value (may be scalar or list)
    param        str   -- the parameter name (e.g. "ClipValue", "ThresholdRange")

KIND_UPSTREAM_FAILED
    upstream     int   -- node_id of the immediately-failed/skipped upstream

KIND_EMPTY_OUTPUT
    (no extra structured fields; hint is in message)

KIND_OTHER
    (no extra structured fields beyond message)
"""

# Status values
STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"
STATUS_WARNING = "warning"

# Kind values
KIND_UNKNOWN_PROPERTY = "unknown_property"
KIND_MISSING_REQUIRED_ARG = "missing_required_arg"
KIND_INVALID_ARG = "invalid_arg"
KIND_FIELD_NOT_FOUND = "field_not_found"
KIND_FIELD_OUT_OF_RANGE = "field_out_of_range"
KIND_UPSTREAM_FAILED = "upstream_failed"
KIND_EMPTY_OUTPUT = "empty_output"
KIND_OTHER = "other"


def ok(class_name: str, **info) -> dict:
    """Return a well-formed ok status dict.

    Args:
        class_name: VTK class or wrapper name.
        **info: Extra fields (num_points, bounds, point_arrays, etc.).
    """
    return {"status": STATUS_OK, "class": class_name, **info}


def error(class_name: str, kind: str, message: str, **structured) -> dict:
    """Return a well-formed error status dict.

    Args:
        class_name: VTK class or wrapper name.
        kind: One of the KIND_* constants.
        message: Human-readable error summary.
        **structured: Kind-specific structured fields.
    """
    return {
        "status": STATUS_ERROR,
        "class": class_name,
        "kind": kind,
        "message": message,
        **structured,
    }


def skipped(class_name: str, upstream_id: int, message: str = None) -> dict:
    """Return a well-formed skipped status dict.

    Args:
        class_name: VTK class or wrapper name.
        upstream_id: node_id of the immediately-preceding failed/skipped node.
        message: Optional human-readable summary (auto-generated if omitted).
    """
    if message is None:
        message = f"skipped: upstream node {upstream_id} failed"
    return {
        "status": STATUS_SKIPPED,
        "class": class_name,
        "kind": KIND_UPSTREAM_FAILED,
        "upstream": upstream_id,
        "message": message,
    }


def warning(class_name: str, kind: str, message: str, **structured) -> dict:
    """Return a well-formed warning status dict.

    Args:
        class_name: VTK class or wrapper name.
        kind: One of the KIND_* constants.
        message: Human-readable warning summary.
        **structured: Kind-specific structured fields.
    """
    return {
        "status": STATUS_WARNING,
        "class": class_name,
        "kind": kind,
        "message": message,
        **structured,
    }
