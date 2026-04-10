"""dispatch.py — generic content-addressed dispatch for tracked proxies.

Provides:
  stable_hash()    — deterministic content hashing
  _should_wrap()   — predicate: should this object be wrapped in a proxy?
  _dag_call()      — shared cache-check / execute / store pattern
  dispatch()       — generic dispatch: whitelist check, hash, cache, execute
"""

from __future__ import annotations

import hashlib
import pickle
import reprlib
from typing import Any, Callable

import numpy as np

from .dag import DAG


# ---------------------------------------------------------------------------
# stable_hash
# ---------------------------------------------------------------------------

def stable_hash(obj) -> str:
    """Compute a deterministic SHA-256 hash for a Python object.

    Supports:
    - TrackedProxy instances: uses their ._hash attribute
    - Scalars (int, float, bool, str, bytes, None): hashes repr()
    - Tuples and lists: recursively hashes elements
    - dicts: recursively hashes sorted key-value pairs
    - Fallback: tries pickle, then repr

    Returns a hex string.
    """
    # TrackedProxy: use the pre-computed hash (handles recursive calls from tuple hashing)
    if hasattr(obj, '_hash') and hasattr(obj, '_real') and hasattr(obj, '_dag'):
        return obj._hash

    # Numpy scalars: convert to Python types for stable hashing
    if isinstance(obj, np.generic):
        obj = obj.item()

    if isinstance(obj, (int, float, bool, str, bytes, type(None))):
        raw = f"{type(obj).__qualname__}:{repr(obj)}"
        return hashlib.sha256(raw.encode()).hexdigest()

    if isinstance(obj, (tuple, list)):
        inner = ",".join(stable_hash(item) for item in obj)
        raw = f"{type(obj).__qualname__}:[{inner}]"
        return hashlib.sha256(raw.encode()).hexdigest()

    if isinstance(obj, dict):
        inner = ",".join(
            f"{stable_hash(k)}:{stable_hash(v)}"
            for k, v in sorted(obj.items(), key=lambda kv: repr(kv[0]))
        )
        raw = f"dict:{{{inner}}}"
        return hashlib.sha256(raw.encode()).hexdigest()

    # Fallback: try pickle for numpy arrays and other objects
    try:
        raw = pickle.dumps(obj, protocol=4)
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        pass

    # Last resort: repr-based hash (may not be stable across runs)
    raw = f"{type(obj).__qualname__}:{reprlib.repr(obj)}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Proxy helpers
# ---------------------------------------------------------------------------

def _should_wrap(obj: Any) -> bool:
    """Return True if obj should be wrapped in a TrackedProxy.

    Scalars (int, float, bool, str, bytes), None, tuples, and lists escape
    the proxy system. Complex objects (meshes, arrays) stay proxied.
    """
    if obj is None or isinstance(obj, (bool, int, float, str, bytes, tuple, list)):
        return False
    return True


def _unwrap(a):
    """Return the real object if ``a`` is a TrackedProxy, else ``a`` unchanged."""
    from .proxy import TrackedProxy
    if isinstance(a, TrackedProxy):
        return object.__getattribute__(a, '_real')
    return a


def _arg_hash(a) -> str:
    """Return the hash for an argument, unwrapping TrackedProxy if needed."""
    from .proxy import TrackedProxy
    if isinstance(a, TrackedProxy):
        return object.__getattribute__(a, '_hash')
    return stable_hash(a)


# ---------------------------------------------------------------------------
# _dag_call — shared cache-check / execute / store pattern
# ---------------------------------------------------------------------------

def _dag_call(
    dag: DAG,
    op_hash: str,
    execute_fn: Callable,
    dispatch_fn: Callable | None = None,
) -> Any:
    """Check the cache for *op_hash*; on miss, call *execute_fn()* and store.

    This is the common pattern shared by dispatch(), _TrackedNumpyNamespace._call(),
    tracked_read(), vtk_escape(), and vtk_escape_multi():
      1. Cache hit → record touch, return TrackedProxy (or raw scalar).
      2. Cache miss → execute_fn(), store result, return TrackedProxy (or raw scalar).

    Args:
        dag:         The active DAG.
        op_hash:     The content hash for this operation.
        execute_fn:  Zero-argument callable that computes the result on cache miss.
        dispatch_fn: Optional callable to store in new TrackedProxy instances.
                     Signature: ``dispatch_fn(proxy, method_name, args, kwargs) -> Any``.
                     Pass None to use the TrackedProxy default (tracked_core.dispatch.dispatch,
                     which is the generic parameterized dispatch with no whitelist).

    Returns:
        A TrackedProxy wrapping the result, or the raw value for scalars/None.
    """
    from .proxy import TrackedProxy

    if op_hash in dag.cache:
        dag.current_run.add(op_hash)
        dag.hits += 1
        cached = dag.cache[op_hash]
        if _should_wrap(cached):
            return TrackedProxy(cached, op_hash, dag, dispatch_fn)
        return cached

    dag.misses += 1
    result = execute_fn()
    dag.cache[op_hash] = result
    dag.current_run.add(op_hash)

    if _should_wrap(result):
        return TrackedProxy(result, op_hash, dag, dispatch_fn)
    return result


# ---------------------------------------------------------------------------
# dispatch — generic, whitelist/blacklist passed as parameters
# ---------------------------------------------------------------------------

def dispatch(
    proxy: Any,
    method_name: str,
    args: tuple,
    kwargs: dict,
    *,
    whitelist: frozenset,
    blacklist: frozenset,
    dispatch_fn: Callable,
    blacklist_reasons: dict | None = None,
    scalar_sensitive_methods: frozenset | None = None,
    _not_whitelisted_message_fn: Callable | None = None,
    _blacklist_message_fn: Callable | None = None,
) -> Any:
    """Generic dispatch: whitelist check, hash, cache, execute.

    Intercepts a method call on a TrackedProxy, checks the provided whitelist
    and blacklist, computes a content hash, and returns cached or freshly
    computed results.

    Args:
        proxy: The TrackedProxy on which the method is being called.
        method_name: Name of the method (e.g. "threshold").
        args: Positional arguments (may contain TrackedProxy instances).
        kwargs: Keyword arguments (may contain TrackedProxy instances).
        whitelist: Frozenset of (class, method_name) pairs that are allowed.
        blacklist: Frozenset of (class, method_name) pairs that are blocked.
        dispatch_fn: The callable to store in new TrackedProxy instances created
            by this operation. Typically the same function that called this (a
            closure binding the whitelist/blacklist). Passed through to _dag_call.
        blacklist_reasons: Optional dict mapping method_name to (reason, advice) tuples
            for improved error messages.
        scalar_sensitive_methods: Optional frozenset of method names that require
            explicit scalar selection (raises ValueError if scalars= not in kwargs).
        _not_whitelisted_message_fn: Optional callable(type_name, method_name) -> str
            for custom not-whitelisted error messages.
        _blacklist_message_fn: Optional callable(type_name, method_name) -> str
            for custom blacklist error messages.

    Returns:
        A new TrackedProxy wrapping the result, or the raw result if the
        operation returns a non-wrappable scalar/None.
    """
    real_obj = object.__getattribute__(proxy, '_real')
    dag = object.__getattribute__(proxy, '_dag')
    proxy_hash = object.__getattribute__(proxy, '_hash')

    type_name = type(real_obj).__name__

    # Build error message functions if not provided
    if _blacklist_message_fn is None:
        def _blacklist_message_fn(tn, mn):
            if blacklist_reasons and mn in blacklist_reasons:
                reason, advice = blacklist_reasons[mn]
                return f"{tn}.{mn}() is blocked ({reason}). {advice}"
            return f"{tn}.{mn}() is explicitly blocked."

    if _not_whitelisted_message_fn is None:
        def _not_whitelisted_message_fn(tn, mn):
            return (
                f"{tn}.{mn}() is not in the whitelist. "
                f"Use an escape hatch if available."
            )

    # 1. Whitelist check
    allowed = False
    for cls in type(real_obj).__mro__:
        if (cls, method_name) in blacklist:
            raise AttributeError(_blacklist_message_fn(type_name, method_name))
        if (cls, method_name) in whitelist:
            allowed = True
            break
    if not allowed:
        raise AttributeError(_not_whitelisted_message_fn(type_name, method_name))

    # 1b. Scalar-sensitive methods check
    if (
        scalar_sensitive_methods is not None
        and method_name in scalar_sensitive_methods
        and "scalars" not in kwargs
    ):
        raise ValueError(
            f"{type_name}.{method_name}() called without scalars= parameter. "
            f"This would use the active scalar field, which is hidden state not "
            f"captured in the cache hash. Always specify scalars= explicitly, e.g.: "
            f"mesh.{method_name}(..., scalars='FieldName')"
        )

    # 2. Compute content hash
    op_hash = stable_hash((
        type(real_obj).__qualname__,
        proxy_hash,
        method_name,
        tuple(_arg_hash(a) for a in args),
        tuple((k, _arg_hash(v)) for k, v in sorted(kwargs.items())),
    ))

    # 3 & 4. Cache check / execute / store
    def _execute():
        real_args = [_unwrap(a) for a in args]
        real_kwargs = {k: _unwrap(v) for k, v in kwargs.items()}
        attr_val = getattr(real_obj, method_name)
        return attr_val(*real_args, **real_kwargs) if callable(attr_val) else attr_val

    return _dag_call(dag, op_hash, _execute, dispatch_fn)
