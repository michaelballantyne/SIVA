"""TrackedProxy — wraps any real object with a content hash and DAG reference.

All attribute accesses and method calls on a TrackedProxy go through dispatch(),
ensuring every operation is whitelisted, content-hashed, and cached in the DAG.
"""

from __future__ import annotations

import numpy as np

from .dispatch import dispatch, _should_wrap, stable_hash


class TrackedProxy:
    """A transparent proxy that wraps a real Python/VTK/numpy object.

    Every method call and attribute access goes through dispatch(), which:
    - Checks the whitelist
    - Computes a content hash for the operation
    - Returns a cached result or executes and caches

    Slots are used deliberately: only _real, _hash, _dag escape __getattr__.
    Everything else routes through __getattr__ → dispatch().
    """

    __slots__ = ("_real", "_hash", "_dag")

    def __init__(self, real_obj, content_hash: str, dag):
        object.__setattr__(self, "_real", real_obj)
        object.__setattr__(self, "_hash", content_hash)
        object.__setattr__(self, "_dag", dag)

    # ------------------------------------------------------------------
    # Core interception
    # ------------------------------------------------------------------

    def __getattr__(self, name: str):
        """Intercept attribute access.

        Returns a callable (for methods) or dispatches immediately (for
        properties). We return a bound-style callable so the user can
        write proxy.threshold(value) naturally.
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
            raise AttributeError(
                "TrackedProxy objects are immutable — "
                "modify the pipeline script instead"
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
        """Block item assignment — routes through dispatch which will reject it."""
        return self._op("__setitem__", key, value)

    def __len__(self):
        result = self._op("__len__")
        return int(result) if not isinstance(result, int) else result

    def __gt__(self, other):
        return self._op("__gt__", other)

    def __lt__(self, other):
        return self._op("__lt__", other)

    def __ge__(self, other):
        return self._op("__ge__", other)

    def __le__(self, other):
        return self._op("__le__", other)

    def __eq__(self, other):
        return self._op("__eq__", other)

    def __ne__(self, other):
        return self._op("__ne__", other)

    def __add__(self, other):
        return self._op("__add__", other)

    def __radd__(self, other):
        return self._op("__radd__", other)

    def __sub__(self, other):
        return self._op("__sub__", other)

    def __rsub__(self, other):
        return self._op("__rsub__", other)

    def __mul__(self, other):
        return self._op("__mul__", other)

    def __rmul__(self, other):
        return self._op("__rmul__", other)

    def __truediv__(self, other):
        return self._op("__truediv__", other)

    def __rtruediv__(self, other):
        return self._op("__rtruediv__", other)

    def __floordiv__(self, other):
        return self._op("__floordiv__", other)

    def __mod__(self, other):
        return self._op("__mod__", other)

    def __pow__(self, other):
        return self._op("__pow__", other)

    def __neg__(self):
        return self._op("__neg__")

    def __abs__(self):
        return self._op("__abs__")

    def __and__(self, other):
        return self._op("__and__", other)

    def __or__(self, other):
        return self._op("__or__", other)

    def __xor__(self, other):
        return self._op("__xor__", other)

    def __invert__(self):
        return self._op("__invert__")

    def __bool__(self):
        # For numpy arrays, bool conversion is often ambiguous. We allow it
        # but dispatch so it's tracked.
        result = self._op("__bool__")
        return bool(result) if not isinstance(result, bool) else result

    def __repr__(self):
        real = object.__getattribute__(self, "_real")
        h = object.__getattribute__(self, "_hash")
        return f"TrackedProxy({type(real).__name__}, hash={h[:8]}...)"

    def __iter__(self):
        """Iterate by yielding proxied items from __getitem__."""
        real = object.__getattribute__(self, "_real")
        dag = object.__getattribute__(self, "_dag")
        for i in range(len(real)):
            item = real[i]
            if _should_wrap(item):
                item_hash = stable_hash((
                    type(real).__qualname__,
                    object.__getattribute__(self, "_hash"),
                    "__iter__",
                    (stable_hash(i),),
                    (),
                ))
                dag.cache[item_hash] = item
                dag.current_run.add(item_hash)
                yield TrackedProxy(item, item_hash, dag)
            else:
                yield item
