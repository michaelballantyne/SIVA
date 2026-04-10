"""PyVista-specific dispatch: thin wrapper around tracked_core with PyVista's whitelist/blacklist."""

from __future__ import annotations

from typing import Any

from tracked_core.dag import DAG
from tracked_core.dispatch import (
    stable_hash,
    _should_wrap,
    _unwrap,
    _arg_hash,
    _dag_call as _core_dag_call,
    dispatch as _core_dispatch_fn,
)


# Methods that silently use the active scalar when scalars= is omitted — a purity hazard.
_SCALAR_SENSITIVE_METHODS = frozenset([
    "threshold", "threshold_percent", "clip_scalar",
    "contour", "warp_by_scalar",
    "compute_gradient", "compute_derivative",
    "image_threshold",
])

_BLACKLIST_REASONS: dict[str, tuple[str, str]] = {
    "save":        ("filesystem write", "Pipeline outputs are managed by the execution framework."),
    "export":      ("filesystem write", "Pipeline outputs are managed by the execution framework."),
    "write":       ("filesystem write", "Pipeline outputs are managed by the execution framework."),
    "tofile":      ("filesystem write", "Use vtk_escape() if you need to write array data to disk."),
    "set_active_scalars": ("hidden state mutation", "Always pass scalars= explicitly to threshold(), contour(), etc."),
    "set_active_vectors": ("hidden state mutation", "Always pass vectors= explicitly."),
    "set_active_tensors": ("hidden state mutation", "Always pass tensors= explicitly."),
    "__setitem__": ("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
    "__iadd__":    ("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
    "__isub__":    ("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
    "__imul__":    ("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
    "__itruediv__":("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
    "__ifloordiv__":("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
    "__imod__":    ("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
    "__ipow__":    ("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
}


def _blacklist_message(type_name: str, method_name: str) -> str:
    """Return a clear, actionable error for a blacklisted method call."""
    reason, advice = _BLACKLIST_REASONS.get(
        method_name,
        ("explicitly forbidden", "Use vtk_escape() for operations that are not allowed through the proxy."),
    )
    return f"{type_name}.{method_name}() is blocked ({reason}). {advice}"


def _not_whitelisted_message(type_name: str, method_name: str) -> str:
    """Return a clear, actionable error for a method not in the whitelist."""
    return (
        f"{type_name}.{method_name}() is not in the whitelist. "
        f"Workaround: use vtk_escape(proxy, lambda m: m.{method_name}(...)) "
        f"to call it through the escape hatch. "
        f"If this method should be whitelisted, open an issue or add it to "
        f"tracked_execution/whitelist.py."
    )


def _dag_call(dag: DAG, op_hash: str, execute_fn) -> Any:
    """Thin wrapper around tracked_core._dag_call that binds the PyVista dispatch function."""
    return _core_dag_call(dag, op_hash, execute_fn, dispatch)


def dispatch(proxy: Any, method_name: str, args: tuple, kwargs: dict) -> Any:
    """Whitelist check, hash, cache lookup, execute — returns a TrackedProxy."""
    from .whitelist import WHITELIST, BLACKLIST

    return _core_dispatch_fn(
        proxy,
        method_name,
        args,
        kwargs,
        whitelist=WHITELIST,
        blacklist=BLACKLIST,
        dispatch_fn=dispatch,
        blacklist_reasons=_BLACKLIST_REASONS,
        scalar_sensitive_methods=_SCALAR_SENSITIVE_METHODS,
        _blacklist_message_fn=_blacklist_message,
        _not_whitelisted_message_fn=_not_whitelisted_message,
    )
