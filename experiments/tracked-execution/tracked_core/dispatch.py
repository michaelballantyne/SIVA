"""Generic content-addressed dispatch for tracked proxies."""

from __future__ import annotations

import hashlib
import pickle
import reprlib
import time
from typing import Any, Callable

import numpy as np

from .dag import DAG


def stable_hash(obj) -> str:
    """Compute a deterministic SHA-256 hash for a Python object.

    Handles TrackedProxy (uses pre-computed hash), scalars, tuples/lists/dicts
    (recursively), numpy arrays (via pickle), and falls back to repr.
    """
    if hasattr(obj, '_hash') and hasattr(obj, '_real') and hasattr(obj, '_dag'):
        return obj._hash

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

    try:
        raw = pickle.dumps(obj, protocol=4)
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        pass

    # Last resort — may not be stable across runs
    raw = f"{type(obj).__qualname__}:{reprlib.repr(obj)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _should_wrap(obj: Any) -> bool:
    """Return True if obj should be wrapped in a TrackedProxy.

    Scalars, None, tuples, and lists escape the proxy. Complex objects stay proxied.
    """
    if obj is None or isinstance(obj, (bool, int, float, str, bytes, tuple, list)):
        return False
    return True


def _unwrap(a):
    """Return the real object behind a TrackedProxy, or a unchanged."""
    from .proxy import TrackedProxy
    if isinstance(a, TrackedProxy):
        return object.__getattribute__(a, '_real')
    return a


def _arg_hash(a) -> str:
    """Return a stable hash for an argument, using the proxy hash if available."""
    from .proxy import TrackedProxy
    if isinstance(a, TrackedProxy):
        return object.__getattribute__(a, '_hash')
    return stable_hash(a)


def _dag_call(
    dag: DAG,
    op_hash: str,
    execute_fn: Callable,
    dispatch_fn: Callable | None = None,
) -> Any:
    """Check the cache for op_hash; on miss, call execute_fn() and store the result.

    Returns a TrackedProxy wrapping the result, or the raw value for scalars/None.
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
    t0 = time.perf_counter()
    result = execute_fn()
    elapsed = time.perf_counter() - t0

    dag.cache[op_hash] = result
    dag.current_run.add(op_hash)
    dag.timings[op_hash] = elapsed

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
    """Whitelist check, content hash, cache lookup, execute, return TrackedProxy."""
    real_obj = object.__getattribute__(proxy, '_real')
    dag = object.__getattribute__(proxy, '_dag')
    proxy_hash = object.__getattribute__(proxy, '_hash')
    type_name = type(real_obj).__name__

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

    allowed = False
    for cls in type(real_obj).__mro__:
        if (cls, method_name) in blacklist:
            raise AttributeError(_blacklist_message_fn(type_name, method_name))
        if (cls, method_name) in whitelist:
            allowed = True
            break
    if not allowed:
        raise AttributeError(_not_whitelisted_message_fn(type_name, method_name))

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

    op_hash = stable_hash((
        type(real_obj).__qualname__,
        proxy_hash,
        method_name,
        tuple(_arg_hash(a) for a in args),
        tuple((k, _arg_hash(v)) for k, v in sorted(kwargs.items())),
    ))

    def _execute():
        real_args = [_unwrap(a) for a in args]
        real_kwargs = {k: _unwrap(v) for k, v in kwargs.items()}
        attr_val = getattr(real_obj, method_name)
        return attr_val(*real_args, **real_kwargs) if callable(attr_val) else attr_val

    return _dag_call(dag, op_hash, _execute, dispatch_fn)
