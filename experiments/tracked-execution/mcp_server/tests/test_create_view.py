"""Tests for the create_view MCP tool."""

import os
import tempfile
import time

import numpy as np
import pyvista as pv
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_server():
    """Reset server globals between tests."""
    import mcp_server.server as srv
    # Stop any running watchers so we don't leak threads.
    for vs in srv._views.values():
        if vs.watcher is not None:
            try:
                vs.watcher.stop()
                vs.watcher.join(timeout=2)
            except Exception:
                pass
    srv._working_directory = None
    srv._views = {}


def _make_tmpdir_with_data():
    """Create a temp directory with a small test VTK file and a pipeline file.

    Returns (tmpdir, vtk_path, pipeline_path).
    """
    tmpdir = tempfile.mkdtemp()

    # Create a small synthetic mesh and save it.
    mesh = pv.ImageData(dimensions=(5, 5, 5))
    mesh["T"] = np.random.rand(mesh.n_points) * 1000.0
    vtk_path = os.path.join(tmpdir, "test.vtk")
    mesh.save(vtk_path)

    # Write a minimal pipeline file.
    pipeline_path = os.path.join(tmpdir, "view-main.py")
    with open(pipeline_path, "w") as fh:
        fh.write('mesh = read("test.vtk")\nshow(mesh, colormap="viridis")\n')

    return tmpdir, vtk_path, pipeline_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCreateView:
    def setup_method(self):
        _reset_server()

    def teardown_method(self):
        _reset_server()

    # --- test_create_view_basic ---

    def test_create_view_basic(self):
        """create_view should succeed and return a useful description."""
        import mcp_server.server as srv
        from mcp_server.server import set_working_directory, create_view

        tmpdir, _, _ = _make_tmpdir_with_data()

        set_working_directory(tmpdir)
        result = create_view("view-main.py")

        # Should not be an error message.
        assert "Error" not in result, f"Unexpected error: {result}"
        assert "view-main" in result
        # Cache stats should be present.
        assert "hits=" in result or "misses=" in result
        # The view should be registered.
        assert "view-main" in srv._views

    def test_create_view_view_state_populated(self):
        """After create_view, ViewState.last_result should hold an ExecutionResult."""
        import mcp_server.server as srv
        from mcp_server.server import set_working_directory, create_view

        tmpdir, _, _ = _make_tmpdir_with_data()
        set_working_directory(tmpdir)
        create_view("view-main.py")

        vs = srv._views["view-main"]
        assert vs.last_result is not None
        assert vs.last_error is None
        # Pipeline should have captured at least one actor (show() call).
        assert len(vs.last_result.actors) >= 1

    # --- test_create_view_no_working_dir ---

    def test_create_view_no_working_dir(self):
        """create_view must return an error if no working directory is set."""
        from mcp_server.server import create_view

        result = create_view("view-main.py")
        assert result.startswith("Error")
        assert "set_working_directory" in result

    # --- test_create_view_file_not_found ---

    def test_create_view_file_not_found(self):
        """create_view must return an error if the pipeline file doesn't exist."""
        from mcp_server.server import set_working_directory, create_view

        with tempfile.TemporaryDirectory() as tmpdir:
            set_working_directory(tmpdir)
            result = create_view("nonexistent.py")

        assert result.startswith("Error")
        assert "not found" in result.lower() or "nonexistent" in result

    # --- test_create_view_duplicate ---

    def test_create_view_duplicate(self):
        """Creating the same view name twice must return an error on the second call."""
        import mcp_server.server as srv
        from mcp_server.server import set_working_directory, create_view

        tmpdir, _, _ = _make_tmpdir_with_data()
        set_working_directory(tmpdir)
        r1 = create_view("view-main.py")
        assert "Error" not in r1, f"First create_view failed: {r1}"

        r2 = create_view("view-main.py")
        assert r2.startswith("Error")
        assert "already exists" in r2

        # Only one view registered.
        assert list(srv._views.keys()) == ["view-main"]

    # --- test_create_view_with_syntax_error ---

    def test_create_view_with_syntax_error(self):
        """Syntax errors in the pipeline file should return an error; no view created."""
        import mcp_server.server as srv
        from mcp_server.server import set_working_directory, create_view

        tmpdir = tempfile.mkdtemp()
        bad_pipeline = os.path.join(tmpdir, "bad-view.py")
        with open(bad_pipeline, "w") as fh:
            fh.write("def f(\n")  # Unclosed function — SyntaxError

        set_working_directory(tmpdir)
        result = create_view("bad-view.py")

        assert "Error" in result
        assert "syntax" in result.lower() or "SyntaxError" in result
        # No view should be registered.
        assert "bad-view" not in srv._views

    # --- test_create_view_with_runtime_error ---

    def test_create_view_with_runtime_error(self):
        """Runtime errors in the pipeline should still create the view (for the watcher)."""
        import mcp_server.server as srv
        from mcp_server.server import set_working_directory, create_view

        tmpdir = tempfile.mkdtemp()
        bad_pipeline = os.path.join(tmpdir, "runtime-err.py")
        with open(bad_pipeline, "w") as fh:
            # NameError: 'nonexistent_variable' is not defined in the namespace.
            fh.write("result = nonexistent_variable + 1\n")

        set_working_directory(tmpdir)
        result = create_view("runtime-err.py")

        # The response should mention the error but not be a top-level Error.
        assert "runtime-err" in result
        assert "error" in result.lower() or "Error" in result
        # The view IS still created.
        assert "runtime-err" in srv._views
        vs = srv._views["runtime-err"]
        assert vs.last_error is not None

    # --- test_create_view_view_name_derivation ---

    def test_create_view_view_name_derivation(self):
        """View name is the basename without extension."""
        import mcp_server.server as srv
        from mcp_server.server import set_working_directory, create_view

        tmpdir = tempfile.mkdtemp()
        pipeline = os.path.join(tmpdir, "my-pipeline.py")
        with open(pipeline, "w") as fh:
            fh.write("# empty pipeline\n")

        set_working_directory(tmpdir)
        create_view("my-pipeline.py")

        assert "my-pipeline" in srv._views

    # --- test_create_view_watcher_started ---

    def test_create_view_watcher_started(self):
        """After create_view, a file watcher observer should be running."""
        import mcp_server.server as srv
        from mcp_server.server import set_working_directory, create_view

        tmpdir, _, _ = _make_tmpdir_with_data()
        set_working_directory(tmpdir)
        create_view("view-main.py")

        vs = srv._views["view-main"]
        assert vs.watcher is not None
        assert vs.watcher.is_alive()
