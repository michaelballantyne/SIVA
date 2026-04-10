"""dispatch() — the core interception point for TrackedProxy method calls.

Also provides stable_hash() for deterministic content hashing of operations.
"""

from __future__ import annotations

import hashlib
import pickle
import reprlib
from typing import Any

import numpy as np


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


def dispatch(proxy: Any, method_name: str, args: tuple, kwargs: dict) -> Any:
    """Intercept a method call on a TrackedProxy.

    Steps:
    1. Whitelist check — raises AttributeError if blocked
    2. Compute a content hash from the operation
    3. Cache hit → return TrackedProxy wrapping cached result
    4. Cache miss → execute, cache result, return new TrackedProxy

    Args:
        proxy: The TrackedProxy on which the method is being called.
        method_name: Name of the method (e.g. "threshold").
        args: Positional arguments (may contain TrackedProxy instances).
        kwargs: Keyword arguments (may contain TrackedProxy instances).

    Returns:
        A new TrackedProxy wrapping the result, or the raw result if the
        operation returns a non-wrappable scalar/None.
    """
    from .whitelist import WHITELIST, BLACKLIST
    from .proxy import TrackedProxy

    real_obj = object.__getattribute__(proxy, '_real')
    dag = object.__getattribute__(proxy, '_dag')
    proxy_hash = object.__getattribute__(proxy, '_hash')

    # 1. Whitelist check
    allowed = False
    for cls in type(real_obj).__mro__:
        if (cls, method_name) in BLACKLIST:
            raise AttributeError(
                f"{type(real_obj).__name__}.{method_name} is blacklisted"
            )
        if (cls, method_name) in WHITELIST:
            allowed = True
            break
    if not allowed:
        raise AttributeError(
            f"{type(real_obj).__name__}.{method_name} is not whitelisted"
        )

    # 2. Compute content hash
    op_hash = stable_hash((
        type(real_obj).__qualname__,
        proxy_hash,
        method_name,
        tuple(_arg_hash(a) for a in args),
        tuple((k, _arg_hash(v)) for k, v in sorted(kwargs.items())),
    ))

    # 3. Cache check
    if op_hash in dag.cache:
        dag.current_run.add(op_hash)
        dag.hits += 1
        cached = dag.cache[op_hash]
        if _should_wrap(cached):
            return TrackedProxy(cached, op_hash, dag)
        return cached

    dag.misses += 1

    # 4. Execute
    real_args = [_unwrap(a) for a in args]
    real_kwargs = {k: _unwrap(v) for k, v in kwargs.items()}
    attr_val = getattr(real_obj, method_name)
    if callable(attr_val):
        result = attr_val(*real_args, **real_kwargs)
    else:
        # Property or data attribute — the value IS the result
        result = attr_val

    # 5. Cache and record
    dag.cache[op_hash] = result
    dag.current_run.add(op_hash)

    if _should_wrap(result):
        return TrackedProxy(result, op_hash, dag)
    return result
