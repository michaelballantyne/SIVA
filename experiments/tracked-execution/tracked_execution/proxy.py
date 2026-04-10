"""TrackedProxy — transparent proxy that routes all operations through the DAG cache.

Every attribute access and method call goes through dispatch(), which checks the
whitelist, computes a content hash, and returns a cached or freshly computed result.
"""

from __future__ import annotations

from typing import Any

from .dispatch import DAG, dispatch


class TrackedProxy:
    """Transparent proxy wrapping a real Python/VTK/numpy object.

    Every method call and attribute access goes through dispatch(), which checks
    the whitelist, computes a content hash, and returns a cached or fresh result.
    Uses ``__slots__`` so that only ``_real``, ``_hash``, and ``_dag`` bypass
    ``__getattr__``; everything else routes through dispatch().
    """

    __slots__ = ("_real", "_hash", "_dag")

    def __init__(self, real_obj: Any, content_hash: str, dag: DAG) -> None:
        object.__setattr__(self, "_real", real_obj)
        object.__setattr__(self, "_hash", content_hash)
        object.__setattr__(self, "_dag", dag)

    # ------------------------------------------------------------------
    # Core interception
    # ------------------------------------------------------------------

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
                    return dispatch(self, name, args, kwargs)
                return _dunder_call
            # Data descriptor (property) — dispatch with no args
            return dispatch(self, name, (), {})

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
                return dispatch(self, name, args, kwargs)
            return _method

        # It's a property or plain attribute — dispatch with no args
        # (dispatch will fetch it from the real object)
        return dispatch(self, name, (), {})

    def __setattr__(self, name: str, value):
        if name in ("_real", "_hash", "_dag"):
            object.__setattr__(self, name, value)
        else:
            real = object.__getattribute__(self, "_real")
            raise AttributeError(
                f"{type(real).__name__}.{name} cannot be set on a TrackedProxy. "
                f"Cached objects are immutable — setting attributes directly would "
                f"corrupt the content-addressed cache. "
                f"To create a modified version, use vtk_escape(proxy, lambda m: ...) "
                f"and return a new mesh from your function."
            )

    # ------------------------------------------------------------------
    # Operators (must be defined explicitly — can't go through __getattr__
    # for dunder methods because Python looks them up on the type, not instance)
    # ------------------------------------------------------------------

    def _op(self, method_name, *args, **kwargs):
        return dispatch(self, method_name, args, kwargs)

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

    def __repr__(self):
        real = object.__getattribute__(self, "_real")
        h = object.__getattribute__(self, "_hash")
        return f"TrackedProxy({type(real).__name__}, hash={h[:8]}...)"

    def __iter__(self):
        """Yield TrackedProxy-wrapped items from the underlying sequence.

        Routes through dispatch() so whitelist/blacklist checks apply and
        cache accounting stays consistent.
        """
        length = dispatch(self, "__len__", (), {})
        for i in range(int(length)):
            yield dispatch(self, "__getitem__", (i,), {})


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
        return dispatch(self, name, (other,), {})
    op.__name__ = name
    return op


def _make_unary_op(name):
    def op(self):
        return dispatch(self, name, (), {})
    op.__name__ = name
    return op


for _name in _BINARY_OPS:
    setattr(TrackedProxy, _name, _make_binary_op(_name))

for _name in _UNARY_OPS:
    setattr(TrackedProxy, _name, _make_unary_op(_name))
