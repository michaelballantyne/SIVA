"""Content-addressed per-view cache for realized VTK pipeline objects."""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

logger = logging.getLogger("vislang")


def _file_fingerprint(path: str) -> str:
    try:
        st = os.stat(path)
        raw = f"file:{path}:{st.st_mtime_ns}:{st.st_size}"
        return hashlib.sha256(raw.encode()).hexdigest()
    except OSError:
        return hashlib.sha256(f"file:{path}:missing".encode()).hexdigest()


def stable_hash(obj) -> str:
    """Return a stable sha256 hex string for a scalar/container value."""
    if isinstance(obj, (bool, int, float, str, bytes, type(None))):
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
        import numpy as np
        if isinstance(obj, np.ndarray):
            shape_dtype = f"ndarray:{obj.shape}:{obj.dtype}"
            arr_hash = hashlib.sha256(obj.tobytes()).hexdigest()
            return hashlib.sha256(f"{shape_dtype}:{arr_hash}".encode()).hexdigest()
        if isinstance(obj, np.generic):
            return stable_hash(obj.item())
    except ImportError:
        pass

    # Fall back to repr — may not be stable across runs for complex objects
    raw = f"{type(obj).__qualname__}:{repr(obj)}"
    logger.debug("build_cache: repr-fallback hash for %s", type(obj).__qualname__)
    return hashlib.sha256(raw.encode()).hexdigest()


class BuildCache:
    """Per-view content-addressed cache: node-content-hash -> realized VTK object.

    Content hash = sha256(node_kind, params_hash, sorted_input_hashes).
    File params include a fingerprint so mtime/size changes bust the cache.
    """

    def __init__(self):
        self._cache: dict[str, Any] = {}
        self._touched: set[str] = set()
        self.hits: int = 0
        self.misses: int = 0
        self.evictions: int = 0

    def get(self, h: str) -> Any | None:
        return self._cache.get(h)

    def put(self, h: str, vtk_output: Any) -> None:
        self._cache[h] = vtk_output

    def begin_run(self) -> None:
        self._touched = set()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def touch(self, h: str) -> None:
        self._touched.add(h)

    def end_run(self) -> dict:
        stale = set(self._cache.keys()) - self._touched
        for h in stale:
            del self._cache[h]
            self.evictions += 1
        kept = len(self._touched)
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "kept": kept,
        }
