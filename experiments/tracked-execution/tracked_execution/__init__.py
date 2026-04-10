"""tracked_execution — content-addressed caching for PyVista pipelines.

Primary entry points:
    execute_pipeline  — run a pipeline script with content-addressed caching
    inspect_pipeline  — read-only queries against cached DAG state
    Session           — high-level object combining DAG, Plotter, and reconciler
    run_session       — convenience factory that creates and initialises a Session
    vtk_escape        — escape hatch for raw VTK operations within a pipeline
    vtk_escape_multi  — multi-input variant of vtk_escape

Supporting types:
    DAG               — content-addressed cache (pass to execute_pipeline)
    ExecutionResult   — returned by execute_pipeline and Session.execute
    InspectResult     — returned by inspect_pipeline and Session.inspect

Internal symbols (available for advanced use but not part of the stable API):
    TrackedProxy, dispatch, stable_hash, tracked_read,
    SceneReconciler, ReconcileResult, ActorRecord
"""

from .dispatch import DAG
from .executor import (
    execute_pipeline,
    inspect_pipeline,
    tracked_read,
    ExecutionResult,
    InspectResult,
)
from .runner import Session, run_session
from .vtk_escape import vtk_escape, vtk_escape_multi

# Internal symbols: importable by name but not advertised in __all__.
from .proxy import TrackedProxy
from .dispatch import dispatch, stable_hash
from .reconciler import SceneReconciler, ReconcileResult, ActorRecord

__all__ = [
    # Core entry points
    "execute_pipeline",
    "inspect_pipeline",
    "Session",
    "run_session",
    "vtk_escape",
    "vtk_escape_multi",
    # Supporting types
    "DAG",
    "ExecutionResult",
    "InspectResult",
]
