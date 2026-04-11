"""Tests for the file watcher — verifies modify / create / rename trigger reloads.

These exercise the ReloadHandler directly (dispatching synthetic watchdog events)
rather than going through the filesystem, so they are deterministic and do not
depend on OS-level FSEvents / inotify timing.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from watchdog.events import (
    DirModifiedEvent,
    FileCreatedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

_LIB_DIR = Path(__file__).resolve().parent.parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from tracked_execution.dispatch import DAG
from tracked_execution.watcher import ReloadHandler, watch_and_reload


PIPELINE_SRC = 'x = np.arange(5)\n'


@pytest.fixture
def pipeline_file():
    """Yield a tmp .py file containing a trivial pipeline."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "pipeline.py"
        path.write_text(PIPELINE_SRC)
        yield path.resolve()


def _make_handler(pipeline_file, debounce_ms=0):
    """Build a ReloadHandler with a reload counter + latest-result recorder."""
    dag = DAG()
    reloads = []
    done = threading.Event()

    def on_reload(result):
        reloads.append(result)
        done.set()

    handler = ReloadHandler(
        file_path=pipeline_file,
        dag=dag,
        reconciler=None,
        callback=on_reload,
        debounce_ms=debounce_ms,
    )
    return handler, reloads, done


class TestReloadHandlerEvents:
    """Dispatch synthetic watchdog events and verify the handler reloads."""

    def test_on_modified_triggers_reload(self, pipeline_file):
        handler, reloads, done = _make_handler(pipeline_file)
        handler.on_modified(FileModifiedEvent(str(pipeline_file)))
        assert done.wait(timeout=2)
        assert len(reloads) == 1

    def test_on_created_triggers_reload(self, pipeline_file):
        """Write-then-recreate (Claude Code's Write tool) uses create events."""
        handler, reloads, done = _make_handler(pipeline_file)
        handler.on_created(FileCreatedEvent(str(pipeline_file)))
        assert done.wait(timeout=2)
        assert len(reloads) == 1

    def test_on_moved_triggers_reload(self, pipeline_file):
        """Atomic writes (temp file → rename) generate a moved event on the dest."""
        handler, reloads, done = _make_handler(pipeline_file)
        temp_src = str(pipeline_file.parent / "pipeline.py.tmp")
        handler.on_moved(FileMovedEvent(temp_src, str(pipeline_file)))
        assert done.wait(timeout=2)
        assert len(reloads) == 1

    def test_ignores_unrelated_file(self, pipeline_file):
        handler, reloads, _ = _make_handler(pipeline_file)
        other = str(pipeline_file.parent / "other.py")
        handler.on_modified(FileModifiedEvent(other))
        handler.on_created(FileCreatedEvent(other))
        handler.on_moved(FileMovedEvent("whatever", other))
        time.sleep(0.1)
        assert reloads == []

    def test_ignores_directory_events(self, pipeline_file):
        handler, reloads, _ = _make_handler(pipeline_file)
        handler.on_modified(DirModifiedEvent(str(pipeline_file.parent)))
        time.sleep(0.1)
        assert reloads == []

    def test_debounce_coalesces_rapid_events(self, pipeline_file):
        """Two events within the debounce window produce exactly one reload."""
        handler, reloads, done = _make_handler(pipeline_file, debounce_ms=200)
        handler.on_modified(FileModifiedEvent(str(pipeline_file)))
        assert done.wait(timeout=2)
        handler.on_modified(FileModifiedEvent(str(pipeline_file)))  # within 200ms
        time.sleep(0.05)
        assert len(reloads) == 1

    def test_modified_then_moved_both_fire_after_debounce(self, pipeline_file):
        """Different event kinds still go through the same debounce gate."""
        handler, reloads, done = _make_handler(pipeline_file, debounce_ms=50)
        handler.on_modified(FileModifiedEvent(str(pipeline_file)))
        assert done.wait(timeout=2)
        time.sleep(0.1)  # past debounce window
        done.clear()
        handler.on_moved(FileMovedEvent("x", str(pipeline_file)))
        assert done.wait(timeout=2)
        assert len(reloads) == 2


class TestWatchAndReload:
    """End-to-end test via watch_and_reload (real watchdog Observer on disk)."""

    def test_real_write_triggers_reload(self, pipeline_file):
        dag = DAG()
        done = threading.Event()
        reloads = []

        def on_reload(result):
            reloads.append(result)
            done.set()

        observer = watch_and_reload(
            file_path=pipeline_file,
            dag=dag,
            callback=on_reload,
            debounce_ms=50,
        )
        try:
            time.sleep(0.1)  # let the observer start
            pipeline_file.write_text(PIPELINE_SRC + "# edit\n")
            assert done.wait(timeout=3), "watcher did not fire on real write"
            assert len(reloads) >= 1
        finally:
            observer.stop()
            observer.join(timeout=2)
