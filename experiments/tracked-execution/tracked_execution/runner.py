"""runner.py — Complete execution loop for tracked pipeline sessions.

Provides:
    Session        — encapsulates DAG, Plotter, SceneReconciler, and watcher
    run_session    — convenience factory to create and initialize a Session
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .core import DAG
from .executor import execute_pipeline, ExecutionResult
from .executor import inspect_exec, InspectResult
from .reconciler import SceneReconciler, ReconcileResult
from .watcher import watch_and_reload


class Session:
    """A live session combining a DAG, optional Plotter, reconciler, and watcher.

    In **offscreen mode** (default), the plotter renders off-screen and the
    session is controlled programmatically via ``execute()``, ``inspect()``,
    ``screenshot()``, and ``stats()``.

    In **interactive mode**, the plotter opens a window and the caller must
    start the event loop separately (e.g. ``plotter.show()`` after construction).

    When ``plotter`` is ``None``, no rendering is performed.  The session still
    tracks cache hits/misses and reconcile counts, which is useful for testing.

    Args:
        file_path:  Path to the ``.py`` pipeline script.
        plotter:    A ``pyvista.Plotter`` instance, or ``None`` for no rendering.
        dag:        A ``DAG`` instance.  A fresh one is created if not provided.
        reconciler: A ``SceneReconciler``.  Created automatically if not provided.
        auto_watch: If ``True``, start a file watcher immediately.

    Attributes:
        dag:        The content-addressed cache.
        plotter:    The PyVista Plotter (may be ``None``).
        reconciler: The scene reconciler.
        watcher:    The watchdog Observer (``None`` if not started).
        last_result: The ``ExecutionResult`` from the most recent ``execute()`` call.
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

        If *code* is provided it is executed directly.  Otherwise the pipeline
        is read from ``self.file_path``.

        Args:
            code: Optional pipeline code string.  Defaults to reading
                  ``self.file_path``.

        Returns:
            The ``ExecutionResult`` from this execution.

        Raises:
            ValueError: If neither *code* nor a ``file_path`` was set.
        """
        if code is None and self.file_path is None:
            raise ValueError("Session has no file_path and no code was provided")

        source = code if code is not None else self.file_path
        result = execute_pipeline(source, self.dag)
        self.reconciler.reconcile(result.actors)
        self.last_result = result
        return result

    def inspect(self, code: str) -> InspectResult:
        """Run a read-only inspection snippet against the cached DAG state.

        Args:
            code: Python snippet with access to named proxy variables from the
                  last ``execute()`` call.

        Returns:
            ``InspectResult`` with captured print output.
        """
        return inspect_exec(code, self.dag)

    def screenshot(self, path: str | Path) -> None:
        """Save the current rendered scene to an image file.

        Args:
            path: Destination path (e.g. ``"output.png"``).

        Raises:
            RuntimeError: If no plotter is configured.
        """
        if self.plotter is None:
            raise RuntimeError("Session has no plotter — cannot take a screenshot")
        self.plotter.screenshot(str(path))

    def stats(self) -> dict[str, int]:
        """Return cache statistics from the last ``execute()`` call.

        Returns:
            Dict with keys ``hits``, ``misses``, ``evictions``.  Returns empty
            dict if ``execute()`` has not been called yet.
        """
        if self.last_result is None:
            return {}
        return dict(self.last_result.stats)

    # ------------------------------------------------------------------
    # Watcher control
    # ------------------------------------------------------------------

    def start_watcher(self, debounce_ms: int = 100) -> None:
        """Start watching ``self.file_path`` for changes.

        Args:
            debounce_ms: Milliseconds to debounce file-change events.

        Raises:
            ValueError: If ``self.file_path`` is not set.
        """
        if self.file_path is None:
            raise ValueError("Cannot start watcher without a file_path")
        if self.watcher is not None:
            return  # already watching

        self.watcher = watch_and_reload(
            self.file_path,
            self.dag,
            reconciler=self.reconciler,
            debounce_ms=debounce_ms,
        )

    def stop_watcher(self) -> None:
        """Stop the file watcher if it is running."""
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


def run_session(
    file_path: str | Path,
    offscreen: bool = True,
    auto_watch: bool = False,
) -> Session:
    """Create a :class:`Session` for *file_path*, execute the pipeline, and return it.

    This is the primary entry point for programmatic use.  It creates a DAG,
    optionally creates a ``pyvista.Plotter``, does an initial pipeline execution,
    reconciles the scene, and returns the ready-to-use :class:`Session`.

    Args:
        file_path:   Path to the ``.py`` pipeline script.
        offscreen:   If ``True`` (default), create an off-screen plotter.
                     If ``False``, create an interactive plotter (requires a
                     display; caller must start the event loop).
        auto_watch:  If ``True``, start a file watcher immediately.

    Returns:
        An initialized :class:`Session`.

    Example::

        session = run_session("pipeline.py", offscreen=True)
        session.screenshot("output.png")
        print(session.stats())
        session.stop_watcher()
    """
    import pyvista as pv

    file_path = Path(file_path).resolve()
    dag = DAG()
    plotter = pv.Plotter(off_screen=offscreen)
    reconciler = SceneReconciler(plotter=plotter)

    session = Session(
        file_path=file_path,
        plotter=plotter,
        dag=dag,
        reconciler=reconciler,
        auto_watch=False,  # We'll start it after initial execute
    )

    # Do the initial execution
    session.execute()

    # Start watcher after initial run
    if auto_watch:
        session.start_watcher()

    return session
