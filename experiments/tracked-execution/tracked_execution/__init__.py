"""tracked_execution — content-addressed caching for PyVista pipelines.

Public API:
    DAG             — cache store, current-run tracker, GC manager
    TrackedProxy    — transparent proxy wrapping real objects
    dispatch        — method interception (whitelist, hash, cache)
    stable_hash     — deterministic SHA-256 hash for operations/values
    execute_pipeline  — run a pipeline script in a restricted namespace
    inspect_exec    — run a read-only inspection snippet against cached DAG state
    tracked_read    — load a PyVista file with mtime-based identity
    SceneReconciler — diff old vs new actor sets, apply minimal plotter updates
    ReconcileResult — counts from a single reconcile() pass
    ActorRecord     — record of a tracked actor
    Session         — encapsulates DAG, Plotter, reconciler, and watcher
    run_session     — convenience factory to create and initialize a Session
"""

from .core import DAG
from .proxy import TrackedProxy
from .dispatch import dispatch, stable_hash
from .executor import execute_pipeline, tracked_read
from .inspect import inspect_exec
from .reconciler import SceneReconciler, ReconcileResult, ActorRecord
from .runner import Session, run_session

__all__ = [
    "DAG",
    "TrackedProxy",
    "dispatch",
    "stable_hash",
    "execute_pipeline",
    "tracked_read",
    "inspect_exec",
    "SceneReconciler",
    "ReconcileResult",
    "ActorRecord",
    "Session",
    "run_session",
]
