"""dispatch.py — content-addressed cache (DAG) and the core interception point.

DAG: content-addressed cache with per-run GC.
stable_hash(): deterministic content hashing.
dispatch(): intercepts TrackedProxy method calls, checks the whitelist,
            and returns cached or freshly computed results.
"""

from __future__ import annotations

import hashlib
import pickle
import reprlib
import warnings
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Error message helpers
# ---------------------------------------------------------------------------

# Methods that use the active scalar when scalars= is not given — a purity hazard.
_SCALAR_SENSITIVE_METHODS = frozenset([
    "threshold", "threshold_percent", "clip_scalar",
    "contour", "warp_by_scalar",
    "compute_gradient", "compute_derivative",
    "image_threshold",
])

# Categorise blacklisted methods so error messages can explain WHY.
_BLACKLIST_REASONS: dict[str, tuple[str, str]] = {
    # (reason_phrase, what_to_do)
    "save":        ("filesystem write", "Pipeline outputs are managed by the execution framework."),
    "export":      ("filesystem write", "Pipeline outputs are managed by the execution framework."),
    "write":       ("filesystem write", "Pipeline outputs are managed by the execution framework."),
    "tofile":      ("filesystem write", "Use vtk_escape() if you need to write array data to disk."),
    "__setitem__": ("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
    "__iadd__":    ("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
    "__isub__":    ("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
    "__imul__":    ("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
    "__itruediv__":("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
    "__ifloordiv__":("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
    "__imod__":    ("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
    "__ipow__":    ("in-place mutation", "Cached objects are immutable. Use vtk_escape() to create a modified copy."),
}


def _blacklist_message(type_name: str, method_name: str) -> str:
    """Return a clear, actionable error for a blacklisted method call."""
    reason, advice = _BLACKLIST_REASONS.get(
        method_name,
        ("explicitly forbidden", "Use vtk_escape() for operations that are not allowed through the proxy."),
    )
    return (
        f"{type_name}.{method_name}() is blocked ({reason}). "
        f"{advice}"
    )


def _not_whitelisted_message(type_name: str, method_name: str) -> str:
    """Return a clear, actionable error for a method not in the whitelist."""
    return (
        f"{type_name}.{method_name}() is not in the whitelist. "
        f"Workaround: use vtk_escape(proxy, lambda m: m.{method_name}(...)) "
        f"to call it through the escape hatch. "
        f"If this method should be whitelisted, open an issue or add it to "
        f"tracked_execution/whitelist.py."
    )


# ---------------------------------------------------------------------------
# DAG — content-addressed cache
# ---------------------------------------------------------------------------

class DAG:
    """Content-addressed cache for pipeline execution.

    Stores a mapping from content hashes to live Python/VTK/numpy objects.
    Call begin_run() before each execution and end_run() after to evict stale
    entries.  Hit/miss/eviction counts are available via stats() after end_run().

    Attributes:
        cache:       content_hash → real object.
        current_run: Set of hashes touched during the current execution.
        names:       variable_name → content_hash, populated by execute_pipeline.
    """

    def __init__(self):
        self.cache: dict[str, Any] = {}
        self.current_run: set[str] = set()
        self.names: dict[str, str] = {}  # variable_name → hash

        # Stats from the last completed run (read via stats())
        self.hits: int = 0
        self.misses: int = 0
        self.evictions: int = 0

    def begin_run(self) -> None:
        """Start a new execution run, resetting the tracking set and counters."""
        self.current_run = set()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def end_run(self) -> None:
        """Finish the current run: evict entries not touched this run.

        After this call:
        - cache only contains entries in current_run
        - stats() reflects the completed run
        """
        stale = set(self.cache.keys()) - self.current_run
        for key in stale:
            del self.cache[key]
            self.evictions += 1

    def stats(self) -> dict[str, int]:
        """Return hit/miss/eviction counts from the last completed run."""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
        }


# ---------------------------------------------------------------------------
# stable_hash
# ---------------------------------------------------------------------------

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

def _dag_call(dag: DAG, op_hash: str, execute_fn: Callable) -> Any:
    """Check the cache for *op_hash*; on miss, call *execute_fn()* and store.

    This is the common pattern shared by dispatch(), _TrackedNumpyNamespace._call(),
    tracked_read(), vtk_escape(), and vtk_escape_multi():
      1. Cache hit → record touch, return TrackedProxy (or raw scalar).
      2. Cache miss → execute_fn(), store result, return TrackedProxy (or raw scalar).

    Args:
        dag:        The active DAG.
        op_hash:    The content hash for this operation.
        execute_fn: Zero-argument callable that computes the result on cache miss.

    Returns:
        A TrackedProxy wrapping the result, or the raw value for scalars/None.
    """
    from .proxy import TrackedProxy

    if op_hash in dag.cache:
        dag.current_run.add(op_hash)
        dag.hits += 1
        cached = dag.cache[op_hash]
        if _should_wrap(cached):
            return TrackedProxy(cached, op_hash, dag)
        return cached

    dag.misses += 1
    result = execute_fn()
    dag.cache[op_hash] = result
    dag.current_run.add(op_hash)

    if _should_wrap(result):
        return TrackedProxy(result, op_hash, dag)
    return result


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

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

    real_obj = object.__getattribute__(proxy, '_real')
    dag = object.__getattribute__(proxy, '_dag')
    proxy_hash = object.__getattribute__(proxy, '_hash')

    # 1. Whitelist check
    allowed = False
    for cls in type(real_obj).__mro__:
        if (cls, method_name) in BLACKLIST:
            raise AttributeError(
                _blacklist_message(type(real_obj).__name__, method_name)
            )
        if (cls, method_name) in WHITELIST:
            allowed = True
            break
    if not allowed:
        raise AttributeError(
            _not_whitelisted_message(type(real_obj).__name__, method_name)
        )

    # 1b. Scalar-sensitive method warning: warn if scalars= is missing
    if method_name in _SCALAR_SENSITIVE_METHODS and "scalars" not in kwargs:
        warnings.warn(
            f"{type(real_obj).__name__}.{method_name}() called without scalars= parameter. "
            f"This uses the active scalar field, which may cause incorrect cache hits "
            f"if the active scalar changes between runs. "
            f"Always specify scalars= explicitly, e.g.: "
            f"mesh.{method_name}(..., scalars='FieldName')",
            stacklevel=3,
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

    return _dag_call(dag, op_hash, _execute)
