"""Re-exports TrackedProxy and registers the PyVista dispatch as the module-level default."""

from __future__ import annotations

from tracked_core.proxy import TrackedProxy, set_default_dispatch


def _register_default():
    from .dispatch import dispatch
    set_default_dispatch(dispatch)


_register_default()


__all__ = ["TrackedProxy", "set_default_dispatch"]
