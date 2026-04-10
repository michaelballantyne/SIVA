"""proxy.py — TrackedProxy for tracked_execution (PyVista-specific).

Re-exports TrackedProxy from tracked_core and registers the PyVista-specific
dispatch function as the default so callers can create proxies with 3 args:
``TrackedProxy(real_obj, content_hash, dag)`` — no dispatch_fn needed.

Importing this module (or tracked_execution) is sufficient to register the
default dispatch.
"""

from __future__ import annotations

# TrackedProxy is entirely generic — imported from tracked_core
from tracked_core.proxy import TrackedProxy, set_default_dispatch


def _register_default():
    """Register the PyVista dispatch as TrackedProxy's default dispatch_fn."""
    from .dispatch import dispatch
    set_default_dispatch(dispatch)


# Register immediately on import of tracked_execution.proxy
_register_default()


__all__ = ["TrackedProxy", "set_default_dispatch"]
