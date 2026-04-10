"""Transparent proxy that routes all method calls through the DAG cache."""

from __future__ import annotations

from typing import Any, Callable

from .dag import DAG


# Domain-specific code calls set_default_dispatch() at import time so
# TrackedProxy(real, hash, dag) works without passing dispatch_fn explicitly.
_default_dispatch_fn: Callable | None = None


def set_default_dispatch(fn: Callable) -> None:
    """Register fn as the module-level default dispatch for TrackedProxy."""
    global _default_dispatch_fn
    _default_dispatch_fn = fn


class TrackedProxy:
    """Transparent proxy: every attribute access/method call goes through dispatch.

    Uses __slots__ so only ``_real``, ``_hash``, ``_dag``, and ``_dispatch_fn``
    bypass ``__getattr__``; everything else routes through the dispatch function.
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
        """Return a dispatch wrapper for methods, or dispatch directly for properties."""
        if name.startswith("__") and name.endswith("__"):
            real = object.__getattribute__(self, "_real")
            try:
                attr = getattr(type(real), name)
            except AttributeError:
                raise AttributeError(
                    f"'{type(real).__name__}' object has no attribute '{name}'"
                )
            if callable(attr):
                def _dunder_call(*args, **kwargs):
                    return self._call_dispatch(name, args, kwargs)
                return _dunder_call
            return self._call_dispatch(name, (), {})

        real = object.__getattribute__(self, "_real")

        type_attr = None
        for cls in type(real).__mro__:
            if name in cls.__dict__:
                type_attr = cls.__dict__[name]
                break

        if type_attr is not None and callable(type_attr):
            def _method(*args, **kwargs):
                return self._call_dispatch(name, args, kwargs)
            return _method

        # Property or plain attribute — dispatch with no args
        return self._call_dispatch(name, (), {})

    def __setattr__(self, name: str, value):
        if name in ("_real", "_hash", "_dag", "_dispatch_fn"):
            object.__setattr__(self, name, value)
        else:
            real = object.__getattribute__(self, "_real")
            raise AttributeError(
                f"{type(real).__name__}.{name} cannot be set on a TrackedProxy. "
                "Cached objects are immutable — setting attributes directly would "
                "corrupt the content-addressed cache. "
                "To create a modified version, use vtk_escape(proxy, lambda m: ...) "
                "and return a new object from your function."
            )

    # ------------------------------------------------------------------
    # Operators — must be defined explicitly because Python looks up dunders
    # on the type, not the instance, so __getattr__ is never called for them.
    # ------------------------------------------------------------------

    def _op(self, method_name, *args, **kwargs):
        return self._call_dispatch(method_name, args, kwargs)

    def __getitem__(self, key):
        return self._op("__getitem__", key)

    def __setitem__(self, key, value):
        return self._op("__setitem__", key, value)

    def __len__(self):
        result = self._op("__len__")
        return int(result) if not isinstance(result, int) else result

    def __bool__(self):
        result = self._op("__bool__")
        return bool(result) if not isinstance(result, bool) else result

    def __int__(self):
        real = object.__getattribute__(self, "_real")
        return int(real)

    def __float__(self):
        real = object.__getattribute__(self, "_real")
        return float(real)

    def __format__(self, format_spec: str) -> str:
        """Delegate format() to the real value so f-strings work correctly."""
        real = object.__getattribute__(self, "_real")
        return format(real, format_spec)

    def __array__(self, dtype=None, copy=False):
        """Implement the numpy array protocol so proxies can be passed to numpy functions.

        The ``copy`` argument is accepted for numpy 2.x compatibility but ignored.
        """
        import numpy as _np
        real = object.__getattribute__(self, "_real")
        if dtype is None:
            return _np.asarray(real)
        return _np.asarray(real, dtype=dtype)

    def __array_wrap__(self, array, context=None, return_scalar=False):
        """Return the raw array after a ufunc, rather than re-wrapping in a proxy.

        Without this, numpy falls through to __getattr__, which routes to
        dispatch() and raises AttributeError because __array_wrap__ is not
        in the whitelist.
        """
        return array

    def __repr__(self):
        real = object.__getattribute__(self, "_real")
        h = object.__getattribute__(self, "_hash")
        return f"TrackedProxy({type(real).__name__}, hash={h[:8]}...)"

    def __iter__(self):
        """Yield dispatch-wrapped items from the underlying sequence."""
        length = self._call_dispatch("__len__", (), {})
        for i in range(int(length)):
            yield self._call_dispatch("__getitem__", (i,), {})


# ---------------------------------------------------------------------------
# Generate operator methods via a loop rather than repeating the pattern.
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
