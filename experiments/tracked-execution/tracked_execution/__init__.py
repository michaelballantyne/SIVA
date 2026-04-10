"""tracked_execution — content-addressed caching for PyVista pipelines.

Public API:
    DAG           — cache store, current-run tracker, GC manager
    TrackedProxy  — transparent proxy wrapping real objects
    dispatch      — method interception (whitelist, hash, cache)
    stable_hash   — deterministic SHA-256 hash for operations/values
    execute_pipeline — run a pipeline script in a restricted namespace
    inspect_exec  — run a read-only inspection snippet against cached DAG state
    tracked_read  — load a PyVista file with mtime-based identity
"""

from .core import DAG
from .proxy import TrackedProxy
from .dispatch import dispatch, stable_hash
from .executor import execute_pipeline, tracked_read
from .inspect import inspect_exec

__all__ = [
    "DAG",
    "TrackedProxy",
    "dispatch",
    "stable_hash",
    "execute_pipeline",
    "tracked_read",
    "inspect_exec",
]
