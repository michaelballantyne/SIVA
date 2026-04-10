"""watcher.py — File-watching hot reload for pipeline scripts.

Provides:
    watch_and_reload   — watch a pipeline file and re-execute on save
    ReloadHandler      — watchdog FileSystemEventHandler with debounce
"""

from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler, FileModifiedEvent
from watchdog.observers import Observer

from .core import DAG
from .executor import execute_pipeline, ExecutionResult
from .reconciler import SceneReconciler


class ReloadHandler(FileSystemEventHandler):
    """Watchdog event handler that re-executes a pipeline file on modification.

    Applies a debounce window (default 100 ms) to suppress spurious duplicate
    events that many editors emit when saving a file.

    Args:
        file_path:  Absolute path to the pipeline file to watch.
        dag:        The DAG instance for caching between reloads.
        reconciler: Optional SceneReconciler; if provided, reconcile() is called
                    after each successful execution.
        callback:   Optional callable invoked with the ``ExecutionResult`` after
                    each successful reload.  Signature: ``callback(result)``.
        debounce_ms: Milliseconds to wait before re-triggering after an event.
    """

    def __init__(
        self,
        file_path: str | Path,
        dag: DAG,
        reconciler: SceneReconciler | None = None,
        callback: Callable[[ExecutionResult], None] | None = None,
        debounce_ms: int = 100,
    ):
        super().__init__()
        self._file_path = Path(file_path).resolve()
        self._dag = dag
        self._reconciler = reconciler
        self._callback = callback
        self._debounce_s = debounce_ms / 1000.0
        self._last_event_time: float = 0.0
        self._lock = threading.Lock()

    def on_modified(self, event) -> None:
        """Called by watchdog when a file modification event occurs."""
        if event.is_directory:
            return

        # Only care about our target file
        modified_path = Path(event.src_path).resolve()
        if modified_path != self._file_path:
            return

        now = time.monotonic()
        with self._lock:
            elapsed = now - self._last_event_time
            if elapsed < self._debounce_s:
                # Suppress — too close to the previous event
                return
            self._last_event_time = now

        self._reload()

    def _reload(self) -> None:
        """Read the file and re-execute the pipeline, handling errors gracefully."""
        try:
            result = execute_pipeline(self._file_path, self._dag)
            if self._reconciler is not None:
                self._reconciler.reconcile(result.actors)
            if self._callback is not None:
                self._callback(result)
        except Exception:
            # Print the traceback but don't crash the watcher thread
            print(f"[watcher] Error reloading {self._file_path.name}:")
            traceback.print_exc()


def watch_and_reload(
    file_path: str | Path,
    dag: DAG,
    reconciler: SceneReconciler | None = None,
    callback: Callable[[ExecutionResult], None] | None = None,
    debounce_ms: int = 100,
) -> Observer:
    """Watch *file_path* and re-execute the pipeline whenever it changes.

    Uses the ``watchdog`` library to monitor the file's parent directory.
    On modification events that pass the debounce filter, the file is
    re-read and ``execute_pipeline()`` is called.  If a ``reconciler`` is
    provided, ``reconciler.reconcile()`` is called with the new actor list.

    Args:
        file_path:   Path to the ``.py`` pipeline file to watch.
        dag:         The DAG instance to use for content-addressed caching.
        reconciler:  Optional ``SceneReconciler``; reconciles the scene after
                     each successful reload.
        callback:    Optional callable called with each ``ExecutionResult``.
        debounce_ms: Suppress events within this many milliseconds of each other.
                     Defaults to 100 ms.

    Returns:
        The started ``watchdog.Observer`` instance.  Call ``.stop()`` and
        ``.join()`` on it to stop watching.

    Example::

        dag = DAG()
        observer = watch_and_reload("pipeline.py", dag)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()
    """
    file_path = Path(file_path).resolve()
    watch_dir = str(file_path.parent)

    handler = ReloadHandler(
        file_path=file_path,
        dag=dag,
        reconciler=reconciler,
        callback=callback,
        debounce_ms=debounce_ms,
    )

    observer = Observer()
    observer.schedule(handler, watch_dir, recursive=False)
    observer.start()
    return observer
