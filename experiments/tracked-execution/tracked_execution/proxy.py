"""proxy.py — TrackedProxy for tracked_execution (PyVista-specific).

Re-exports TrackedProxy from tracked_core. The PyVista-specific dispatch
function is wired in here so callers don't need to pass it explicitly.

All code that creates TrackedProxy instances in tracked_execution should
use make_proxy() from this module (or go through _dag_call/dispatch from
tracked_execution.dispatch, which automatically passes the right dispatch_fn).
"""

from __future__ import annotations

from typing import Any

# TrackedProxy is entirely generic — imported from tracked_core
from tracked_core.proxy import TrackedProxy
from tracked_core.dag import DAG


def make_proxy(real_obj: Any, content_hash: str, dag: DAG) -> TrackedProxy:
    """Create a TrackedProxy wired to the PyVista-specific dispatch function.

    This is a convenience factory that ensures new proxies use the
    tracked_execution dispatch (with PyVista whitelist/blacklist) rather than
    requiring callers to import and pass dispatch explicitly.

    Most code in tracked_execution creates proxies through _dag_call() or
    dispatch() in tracked_execution.dispatch, which already handle this.
    Use make_proxy() only when you need to create a proxy directly.
    """
    from .dispatch import dispatch
    return TrackedProxy(real_obj, content_hash, dag, dispatch)


__all__ = ["TrackedProxy", "make_proxy"]
