"""Session: encapsulates DAG, Plotter, SceneReconciler, and optional file watcher."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .dispatch import DAG
from .executor import execute_pipeline, ExecutionResult
from .executor import inspect_pipeline, InspectResult
from .reconciler import SceneReconciler, ReconcileResult
from .watcher import watch_and_reload


class Session:
    """A pipeline session combining a DAG, optional Plotter, reconciler, and file watcher.

    When plotter is None, no rendering is performed (useful for testing).
    Call execute() to run the pipeline, inspect() for read-only queries,
    and screenshot() to capture the render.
    """

    def __init__(
        self,
        file_path: str | Path | None = None,
        plotter=None,
        dag: DAG | None = None,
        reconciler: SceneReconciler | None = None,
        auto_watch: bool = False,
    ):
        self.file_path: Path | None = Path(file_path).resolve() if file_path else None
        self.plotter = plotter
        self.dag: DAG = dag if dag is not None else DAG()
        self.reconciler: SceneReconciler = (
            reconciler if reconciler is not None else SceneReconciler(plotter=plotter)
        )
        self.watcher = None
        self.last_result: ExecutionResult | None = None

        if auto_watch and self.file_path is not None:
            self.start_watcher()

    # ------------------------------------------------------------------
    # Primary API
    # ------------------------------------------------------------------

    def execute(self, code: str | None = None) -> ExecutionResult:
        """Execute the pipeline and reconcile the scene.

        Args:
            code: Pipeline code string. Defaults to reading self.file_path.

        Raises:
            ValueError: If neither code nor file_path is set.
        """
        if code is None and self.file_path is None:
            raise ValueError("Session has no file_path and no code was provided")

        source = code if code is not None else self.file_path
        result = execute_pipeline(source, self.dag)
        self.reconciler.reconcile(result.actors)
        self.last_result = result
        return result

    def inspect(self, code: str) -> InspectResult:
        """Run a read-only snippet against the cached DAG state."""
        return inspect_pipeline(code, self.dag)

    def screenshot(self, path: str | Path) -> None:
        """Save the current rendered scene to an image file.

        Raises:
            RuntimeError: If no plotter is configured.
        """
        if self.plotter is None:
            raise RuntimeError("Session has no plotter — cannot take a screenshot")
        self.plotter.screenshot(str(path))

    def stats(self) -> dict[str, int]:
        """Return cache stats from the last execute() call, or {} if not yet called."""
        if self.last_result is None:
            return {}
        return dict(self.last_result.stats)

    # ------------------------------------------------------------------
    # Watcher control
    # ------------------------------------------------------------------

    def start_watcher(self, debounce_ms: int = 100) -> None:
        """Start watching self.file_path for changes and re-executing on save.

        Raises:
            ValueError: If self.file_path is not set.
        """
        if self.file_path is None:
            raise ValueError("Cannot start watcher without a file_path")
        if self.watcher is not None:
            return

        self.watcher = watch_and_reload(
            self.file_path,
            self.dag,
            reconciler=self.reconciler,
            debounce_ms=debounce_ms,
        )

    def stop_watcher(self) -> None:
        """Stop the file watcher if running."""
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher.join()
            self.watcher = None

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *args) -> None:
        self.stop_watcher()
        if self.plotter is not None:
            try:
                self.plotter.close()
            except Exception:
                pass
