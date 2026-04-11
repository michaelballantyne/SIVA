"""Tests for the run_pipeline MCP tool.

run_pipeline blocks until the file watcher has executed a pipeline version
at least as new as the file's current mtime, then returns status text plus
a screenshot.  These tests cover the happy path, immediate return when the
watcher is already up to date, and error propagation from a broken pipeline.
"""

import os
import time

import pytest

from mcp_server.server import Image


class TestRunPipeline:
    def test_returns_status_and_image_after_edit(self, view_dir, reset_server):
        """After editing the pipeline, run_pipeline should block until the
        watcher reruns and then return status + a PNG screenshot."""
        from mcp_server.server import run_pipeline

        pipeline_path = os.path.join(view_dir, "view-main.py")
        # Sleep past the watcher's 100ms debounce window so this edit is
        # not collapsed with any spurious initial file-create event the
        # watcher may have picked up during fixture setup.
        time.sleep(0.15)
        with open(pipeline_path, "w") as fh:
            fh.write(
                'mesh = read("test.vtk")\n'
                'print("edited")\n'
                'show(mesh, colormap="plasma")\n'
            )

        result = run_pipeline("view-main.py")

        assert isinstance(result, list), f"Expected list, got: {type(result).__name__} ({result!r})"
        assert len(result) == 2
        status, image = result
        assert "successfully" in status
        assert "edited" in status  # captured print output is included
        assert isinstance(image, Image)
        assert image.data[:4] == b"\x89PNG"

    def test_returns_immediately_when_up_to_date(self, view_dir, reset_server):
        """If the watcher has already processed the current file, run_pipeline
        returns without waiting for another reload."""
        from mcp_server.server import run_pipeline

        # view_dir fixture already created and ran the view.  last_run_mtime
        # was set by create_view to match the file on disk.  A direct call
        # should return immediately.
        start = time.monotonic()
        result = run_pipeline("view-main.py")
        elapsed = time.monotonic() - start

        assert isinstance(result, list)
        assert elapsed < 1.0, f"run_pipeline took {elapsed:.2f}s — should be immediate"
        assert "successfully" in result[0]

    def test_reports_pipeline_error(self, view_dir, reset_server):
        """A broken pipeline should surface through run_pipeline as an error
        in the status text (not crash, not hang)."""
        from mcp_server.server import run_pipeline

        pipeline_path = os.path.join(view_dir, "view-main.py")
        time.sleep(0.15)  # past the watcher's 100ms debounce window
        with open(pipeline_path, "w") as fh:
            fh.write('mesh = read("test.vtk")\nraise ValueError("boom")\n')

        result = run_pipeline("view-main.py")

        # Error path may return list [status, image] or a bare error string
        # if the screenshot fails.  Either way, status must mention the error.
        if isinstance(result, list):
            status = result[0]
        else:
            status = result
        assert "error" in status.lower() or "boom" in status.lower(), status

    def test_unknown_view_returns_error(self, reset_server):
        """run_pipeline on a view that was never created returns an error
        string rather than raising or hanging."""
        from mcp_server.server import run_pipeline

        result = run_pipeline("never-created.py")
        assert isinstance(result, str)
        assert result.startswith("Error")
        assert "create_view" in result


class TestViewStateMtimeTracking:
    """Verify create_view and the watcher keep last_run_mtime in sync."""

    def test_create_view_sets_last_run_mtime(self, view_dir, reset_server):
        """After create_view, last_run_mtime should match the file's mtime."""
        vs = reset_server._views["view-main"]
        pipeline_path = os.path.join(view_dir, "view-main.py")
        assert vs.last_run_mtime == pytest.approx(os.path.getmtime(pipeline_path))

    def test_watcher_advances_last_run_mtime(self, view_dir, reset_server):
        """After an edit + watcher reload, last_run_mtime should advance."""
        from mcp_server.server import run_pipeline

        vs = reset_server._views["view-main"]
        initial_mtime = vs.last_run_mtime

        pipeline_path = os.path.join(view_dir, "view-main.py")
        time.sleep(0.15)  # past the watcher's 100ms debounce window
        with open(pipeline_path, "w") as fh:
            fh.write('mesh = read("test.vtk")\nshow(mesh)\n')
        file_mtime_after_edit = os.path.getmtime(pipeline_path)
        assert file_mtime_after_edit > initial_mtime, (
            "test setup: file mtime did not advance after edit"
        )

        # run_pipeline blocks until the watcher has processed a version
        # at least as new as the file on disk.
        run_pipeline("view-main.py")

        assert vs.last_run_mtime >= file_mtime_after_edit
