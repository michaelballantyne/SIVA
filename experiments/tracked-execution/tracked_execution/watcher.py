"""File-watching hot reload: re-execute a pipeline script whenever it is saved."""

from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileMovedEvent, FileCreatedEvent
from watchdog.observers import Observer

from .dispatch import DAG
from .executor import execute_pipeline, ExecutionResult
from .reconciler import SceneReconciler


class ReloadHandler(FileSystemEventHandler):
    """Watchdog handler that re-executes a pipeline file on modification with debounce."""

    def __init__(
        self,
        file_path: str | Path,
        dag: DAG,
        reconciler: SceneReconciler | None = None,
        callback: Callable[[ExecutionResult], None] | None = None,
        error_callback: Callable[[Exception], None] | None = None,
        debounce_ms: int = 100,
        read_fn: Callable | None = None,
    ):
        super().__init__()
        self._file_path = Path(file_path).resolve()
        self._dag = dag
        self._reconciler = reconciler
        self._callback = callback
        self._error_callback = error_callback
        self._debounce_s = debounce_ms / 1000.0
        self._last_event_time: float = 0.0
        self._lock = threading.Lock()
        self._read_fn = read_fn

    def _maybe_reload(self, path: Path) -> None:
        """Debounce and reload if *path* matches the watched file."""
        if path != self._file_path:
            return
        now = time.monotonic()
        with self._lock:
            if now - self._last_event_time < self._debounce_s:
                return
            self._last_event_time = now
            self._reload()

    def on_modified(self, event) -> None:
        """Trigger a reload when the target file is modified in-place."""
        if event.is_directory:
            return
        self._maybe_reload(Path(event.src_path).resolve())

    def on_created(self, event) -> None:
        """Trigger a reload when the target file is recreated.

        Some editors (and Claude Code's Write tool) delete + create rather
        than modifying in-place.
        """
        if event.is_directory:
            return
        self._maybe_reload(Path(event.src_path).resolve())

    def on_moved(self, event) -> None:
        """Trigger a reload when a temp file is renamed to the target file.

        Atomic writes (write to temp, rename to target) are common in editors
        and in Claude Code's Edit tool.  On macOS FSEvents, the rename
        generates a ``moved`` event but may not generate a ``modified`` event
        for the destination, especially on subsequent writes.
        """
        if event.is_directory:
            return
        dest_path = Path(event.dest_path).resolve()
        self._maybe_reload(dest_path)

    def _reload(self) -> None:
        """Re-execute the pipeline; log errors without crashing the watcher thread."""
        try:
            result = execute_pipeline(self._file_path, self._dag, read_fn=self._read_fn)
            if self._reconciler is not None:
                self._reconciler.reconcile(result.actors)
            if self._callback is not None:
                self._callback(result)
        except Exception as exc:
            if self._error_callback is not None:
                try:
                    self._error_callback(exc)
                except Exception:
                    pass  # never let the error callback crash the watcher thread
            else:
                print(f"[watcher] Error reloading {self._file_path.name}:")
                traceback.print_exc()


def watch_and_reload(
    file_path: str | Path,
    dag: DAG,
    reconciler: SceneReconciler | None = None,
    callback: Callable[[ExecutionResult], None] | None = None,
    error_callback: Callable[[Exception], None] | None = None,
    debounce_ms: int = 100,
    read_fn: Callable | None = None,
) -> Observer:
    """Watch file_path and re-execute the pipeline whenever it is saved.

    Returns the started watchdog Observer. Call .stop() and .join() to stop.
    """
    file_path = Path(file_path).resolve()
    watch_dir = str(file_path.parent)

    handler = ReloadHandler(
        file_path=file_path,
        dag=dag,
        reconciler=reconciler,
        callback=callback,
        error_callback=error_callback,
        debounce_ms=debounce_ms,
        read_fn=read_fn,
    )

    observer = Observer()
    observer.schedule(handler, watch_dir, recursive=False)
    observer.start()
    return observer
