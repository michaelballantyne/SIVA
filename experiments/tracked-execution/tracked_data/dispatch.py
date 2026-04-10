"""dispatch.py — pandas-specific dispatch wrapping the generic tracked_core."""

from __future__ import annotations

from typing import Any

from tracked_core.dispatch import dispatch as _generic_dispatch
from .whitelist import WHITELIST, BLACKLIST


def dispatch(proxy: Any, method_name: str, args: tuple, kwargs: dict) -> Any:
    """Intercept a method call on a TrackedProxy wrapping a pandas object.

    Steps:
    1. Whitelist check — raises AttributeError if not allowed or blacklisted.
    2. Compute a content hash from the operation and its inputs.
    3. Cache hit → return TrackedProxy wrapping cached result.
    4. Cache miss → execute, cache result, return new TrackedProxy.

    Args:
        proxy: The TrackedProxy on which the method is being called.
        method_name: Name of the method (e.g. "query", "groupby").
        args: Positional arguments (may contain TrackedProxy instances).
        kwargs: Keyword arguments (may contain TrackedProxy instances).

    Returns:
        A new TrackedProxy wrapping the result, or the raw result if the
        operation returns a non-wrappable scalar/None.
    """
    return _generic_dispatch(
        proxy,
        method_name,
        args,
        kwargs,
        whitelist=WHITELIST,
        blacklist=BLACKLIST,
        dispatch_fn=dispatch,
    )
