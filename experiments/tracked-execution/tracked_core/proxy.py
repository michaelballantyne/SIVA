"""TrackedProxy — transparent proxy that routes all operations through the DAG cache.

Every attribute access and method call goes through a dispatch function, which
checks a whitelist, computes a content hash, and returns a cached or freshly
computed result.

This module is domain-independent: TrackedProxy works for any kind of object
(PyVista meshes, numpy arrays, plain Python objects). The dispatch function
(and thus whitelist/blacklist) is provided at proxy-creation time via the
``dispatch_fn`` argument, allowing different domains to use different whitelists
with the same TrackedProxy class.

When ``dispatch_fn`` is None (the default), TrackedProxy falls back to importing
``tracked_core.dispatch.dispatch`` — which is the generic dispatch that requires
whitelist/blacklist to be passed explicitly.  Domain-specific code should always
pass a concrete ``dispatch_fn``.
"""

from __future__ import annotations

from typing import Any, Callable

from .dag import DAG


# Module-level default dispatch function. Domain-specific code (e.g. tracked_execution)
# calls set_default_dispatch() at import time so TrackedProxy(real, hash, dag) works
# without explicitly passing dispatch_fn.
_default_dispatch_fn: Callable | None = None


def set_default_dispatch(fn: Callable) -> None:
    """Register a default dispatch function for TrackedProxy.

    Domain-specific code calls this once at import time so that
    ``TrackedProxy(real_obj, content_hash, dag)`` (3-arg form) works without
    passing ``dispatch_fn`` explicitly.

    The registered function must have the signature:
        ``fn(proxy, method_name, args, kwargs) -> Any``

    Args:
        fn: The dispatch callable to use as the default.
    """
    global _default_dispatch_fn
    _default_dispatch_fn = fn


class TrackedProxy:
    """Transparent proxy wrapping a real Python object.

    Every method call and attribute access goes through a dispatch function
    (stored as ``_dispatch_fn``), which checks the whitelist, computes a
    content hash, and returns a cached or fresh result.

    Uses ``__slots__`` so that only ``_real``, ``_hash``, ``_dag``, and
    ``_dispatch_fn`` bypass ``__getattr__``; everything else routes through
    the dispatch function.
    """

    __slots__ = ("_real", "_hash", "_dag", "_dispatch_fn")

    def __init__(
        self,
        real_obj: Any,
        content_hash: str,
        dag: DAG,
        dispatch_fn: Callable | None = None,
    ) -> None:
        object.__setattr__(self, "_real", real_obj)
        object.__setattr__(self, "_hash", content_hash)
        object.__setattr__(self, "_dag", dag)
        if dispatch_fn is None:
            # Try to get the registered default dispatch (set by domain-specific code).
            dispatch_fn = _default_dispatch_fn
        if dispatch_fn is None:
            raise TypeError(
                "TrackedProxy requires a dispatch_fn. "
                "Either pass dispatch_fn explicitly, or register a default via "
                "tracked_core.proxy.set_default_dispatch(fn)."
            )
        object.__setattr__(self, "_dispatch_fn", dispatch_fn)

    # ------------------------------------------------------------------
    # Core interception
    # ------------------------------------------------------------------

    def _call_dispatch(self, method_name: str, args: tuple, kwargs: dict):
        """Invoke the stored dispatch function for this method call."""
        fn = object.__getattribute__(self, "_dispatch_fn")
        return fn(self, method_name, args, kwargs)

    def __getattr__(self, name: str):
        """Intercept attribute access: return a dispatch wrapper for methods,
        or dispatch immediately for properties and plain data attributes.
        """
        # Avoid recursion on our own slots
        if name.startswith("__") and name.endswith("__"):
            # Special dunder — try to get it from the real object.
            # If it doesn't exist, raise AttributeError (normal Python behaviour).
            real = object.__getattribute__(self, "_real")
            try:
                attr = getattr(type(real), name)
            except AttributeError:
                raise AttributeError(
                    f"'{type(real).__name__}' object has no attribute '{name}'"
                )
            # Non-data descriptors (methods) — return a dispatch wrapper
            if callable(attr):
                def _dunder_call(*args, **kwargs):
                    return self._call_dispatch(name, args, kwargs)
                return _dunder_call
            # Data descriptor (property) — dispatch with no args
            return self._call_dispatch(name, (), {})

        real = object.__getattribute__(self, "_real")

        # Check if it's a method or property/data attribute on the real type
        type_attr = None
        for cls in type(real).__mro__:
            if name in cls.__dict__:
                type_attr = cls.__dict__[name]
                break

        if type_attr is not None and callable(type_attr):
            # It's a method — return a callable that goes through dispatch
            def _method(*args, **kwargs):
                return self._call_dispatch(name, args, kwargs)
            return _method

        # It's a property or plain attribute — dispatch with no args
        # (dispatch will fetch it from the real object)
        return self._call_dispatch(name, (), {})

    def __setattr__(self, name: str, value):
        if name in ("_real", "_hash", "_dag", "_dispatch_fn"):
            object.__setattr__(self, name, value)
        else:
            real = object.__getattribute__(self, "_real")
            raise AttributeError(
                f"{type(real).__name__}.{name} cannot be set on a TrackedProxy. "
                f"Cached objects are immutable — setting attributes directly would "
                f"corrupt the content-addressed cache. "
                f"To create a modified version, use vtk_escape(proxy, lambda m: ...) "
                f"and return a new object from your function."
            )

    # ------------------------------------------------------------------
    # Operators (must be defined explicitly — can't go through __getattr__
    # for dunder methods because Python looks them up on the type, not instance)
    # ------------------------------------------------------------------

    def _op(self, method_name, *args, **kwargs):
        return self._call_dispatch(method_name, args, kwargs)

    def __getitem__(self, key):
        return self._op("__getitem__", key)

    def __setitem__(self, key, value):
        """Block item assignment (raises AttributeError via dispatch blacklist)."""
        return self._op("__setitem__", key, value)

    def __len__(self):
        result = self._op("__len__")
        return int(result) if not isinstance(result, int) else result

    def __bool__(self):
        # For numpy arrays, bool conversion is often ambiguous. We allow it
        # but dispatch so it's tracked.
        result = self._op("__bool__")
        return bool(result) if not isinstance(result, bool) else result

    def __int__(self):
        real = object.__getattribute__(self, "_real")
        return int(real)

    def __float__(self):
        real = object.__getattribute__(self, "_real")
        return float(real)

    def __format__(self, format_spec: str) -> str:
        """Delegate format() calls to the underlying real value.

        This makes f-string format specs like ``f"{proxy:.2f}"`` work the same
        as they would on the underlying numpy scalar or Python number.
        """
        real = object.__getattribute__(self, "_real")
        return format(real, format_spec)

    def __array__(self, dtype=None, copy=False):
        """Implement the numpy array protocol so proxies can be used as arrays.

        This allows a TrackedProxy wrapping an ndarray to be passed anywhere
        numpy expects a real array — for example, assigning ``np.sqrt(arr)``
        result to a PyVista mesh field:

            mesh_copy["Derived"] = np.sqrt(proxy_arr)  # works via __array__

        The underlying real object is returned as a numpy array, preserving the
        caching benefit (the proxy still tracks the operation in the DAG).

        The ``copy`` keyword is accepted for numpy 2.x compatibility but
        ignored — we always return a view where possible (via ``np.asarray``).
        """
        import numpy as _np
        real = object.__getattribute__(self, "_real")
        if dtype is None:
            return _np.asarray(real)
        return _np.asarray(real, dtype=dtype)

    def __array_wrap__(self, array, context=None, return_scalar=False):
        """Return the raw numpy array after a ufunc call.

        When numpy ufuncs (e.g. ``np.sqrt``, ``np.log``) operate on a proxy
        via ``__array__``, numpy calls ``__array_wrap__`` on the original
        object to let it "re-wrap" the result.  We intentionally return the
        plain ndarray rather than a new TrackedProxy — the caller can wrap it
        via the tracked numpy namespace (``np.sqrt(proxy)`` in a pipeline
        script) if they want a tracked result.

        Without this method, numpy falls through to ``__getattr__``, which
        routes to dispatch().  Since ``__array_wrap__`` is not in the
        whitelist, that raises AttributeError.
        """
        return array

    def __repr__(self):
        real = object.__getattribute__(self, "_real")
        h = object.__getattribute__(self, "_hash")
        return f"TrackedProxy({type(real).__name__}, hash={h[:8]}...)"

    def __iter__(self):
        """Yield TrackedProxy-wrapped items from the underlying sequence.

        Routes through dispatch() so whitelist/blacklist checks apply and
        cache accounting stays consistent.
        """
        length = self._call_dispatch("__len__", (), {})
        for i in range(int(length)):
            yield self._call_dispatch("__getitem__", (i,), {})


# ---------------------------------------------------------------------------
# Generate operator methods via a loop so each op is one line, not ten.
# Binary ops take one argument; unary ops take none.
# ---------------------------------------------------------------------------

_BINARY_OPS = (
    "__gt__", "__lt__", "__ge__", "__le__",
    "__eq__", "__ne__",
    "__add__", "__radd__", "__sub__", "__rsub__",
    "__mul__", "__rmul__",
    "__truediv__", "__rtruediv__", "__floordiv__",
    "__mod__", "__pow__",
    "__and__", "__or__", "__xor__",
)

_UNARY_OPS = ("__neg__", "__abs__", "__invert__")


def _make_binary_op(name):
    def op(self, other):
        return self._call_dispatch(name, (other,), {})
    op.__name__ = name
    return op


def _make_unary_op(name):
    def op(self):
        return self._call_dispatch(name, (), {})
    op.__name__ = name
    return op


for _name in _BINARY_OPS:
    setattr(TrackedProxy, _name, _make_binary_op(_name))

for _name in _UNARY_OPS:
    setattr(TrackedProxy, _name, _make_unary_op(_name))
