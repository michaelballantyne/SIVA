"""tracked_execution — content-addressed caching for PyVista pipelines.

Core entry points: execute_pipeline, inspect_exec, tracked_read, Session, run_session.
Escape hatch for raw VTK: vtk_escape, vtk_escape_multi.
"""

from .core import DAG
from .proxy import TrackedProxy
from .dispatch import dispatch, stable_hash
from .executor import (
    execute_pipeline,
    tracked_read,
    inspect_exec,
    ExecutionResult,
    InspectResult,
)
from .reconciler import SceneReconciler, ReconcileResult, ActorRecord
from .runner import Session, run_session
from .vtk_escape import vtk_escape, vtk_escape_multi

__all__ = [
    "DAG",
    "TrackedProxy",
    "dispatch",
    "stable_hash",
    "execute_pipeline",
    "tracked_read",
    "inspect_exec",
    "ExecutionResult",
    "InspectResult",
    "SceneReconciler",
    "ReconcileResult",
    "ActorRecord",
    "Session",
    "run_session",
    "vtk_escape",
    "vtk_escape_multi",
]
