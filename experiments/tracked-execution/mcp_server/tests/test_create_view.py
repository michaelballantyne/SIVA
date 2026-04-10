"""Tests for the create_view MCP tool."""

import os
import tempfile

import pytest


class TestCreateView:
    """Tests for the create_view MCP tool."""

    def test_create_view_basic(self, tmp_vtk_dir, reset_server):
        """create_view should succeed and return a useful description."""
        from mcp_server.server import set_working_directory, create_view

        pipeline_path = os.path.join(tmp_vtk_dir, "view-main.py")
        with open(pipeline_path, "w") as fh:
            fh.write('mesh = read("test.vtk")\nshow(mesh, colormap="viridis")\n')

        set_working_directory(tmp_vtk_dir)
        result = create_view("view-main.py")

        assert "Error" not in result, f"Unexpected error: {result}"
        assert "view-main" in result
        assert "hits=" in result or "misses=" in result
        assert "view-main" in reset_server._views

    def test_create_view_view_state_populated(self, tmp_vtk_dir, reset_server):
        """After create_view, ViewState.last_result should hold an ExecutionResult."""
        from mcp_server.server import set_working_directory, create_view

        pipeline_path = os.path.join(tmp_vtk_dir, "view-main.py")
        with open(pipeline_path, "w") as fh:
            fh.write('mesh = read("test.vtk")\nshow(mesh, colormap="viridis")\n')

        set_working_directory(tmp_vtk_dir)
        create_view("view-main.py")

        vs = reset_server._views["view-main"]
        assert vs.last_result is not None
        assert vs.last_error is None
        assert len(vs.last_result.actors) >= 1

    def test_create_view_no_working_dir(self, reset_server):
        """create_view must return an error if no working directory is set."""
        from mcp_server.server import create_view

        result = create_view("view-main.py")
        assert result.startswith("Error")
        assert "set_working_directory" in result

    def test_create_view_file_not_found(self, reset_server):
        """create_view must return an error if the pipeline file doesn't exist."""
        from mcp_server.server import set_working_directory, create_view

        with tempfile.TemporaryDirectory() as tmpdir:
            set_working_directory(tmpdir)
            result = create_view("nonexistent.py")

        assert result.startswith("Error")
        assert "not found" in result.lower() or "nonexistent" in result

    def test_create_view_duplicate(self, tmp_vtk_dir, reset_server):
        """Creating the same view name twice must return an error on the second call."""
        from mcp_server.server import set_working_directory, create_view

        pipeline_path = os.path.join(tmp_vtk_dir, "view-main.py")
        with open(pipeline_path, "w") as fh:
            fh.write('mesh = read("test.vtk")\nshow(mesh, colormap="viridis")\n')

        set_working_directory(tmp_vtk_dir)
        r1 = create_view("view-main.py")
        assert "Error" not in r1, f"First create_view failed: {r1}"

        r2 = create_view("view-main.py")
        assert r2.startswith("Error")
        assert "already exists" in r2
        assert list(reset_server._views.keys()) == ["view-main"]

    def test_create_view_with_syntax_error(self, reset_server):
        """Syntax errors in the pipeline file should return an error; no view created."""
        from mcp_server.server import set_working_directory, create_view

        tmpdir = tempfile.mkdtemp()
        bad_pipeline = os.path.join(tmpdir, "bad-view.py")
        with open(bad_pipeline, "w") as fh:
            fh.write("def f(\n")  # Unclosed function — SyntaxError

        set_working_directory(tmpdir)
        result = create_view("bad-view.py")

        assert "Error" in result
        assert "syntax" in result.lower() or "SyntaxError" in result
        assert "bad-view" not in reset_server._views

    def test_create_view_with_runtime_error(self, reset_server):
        """Runtime errors in the pipeline should still create the view (for the watcher)."""
        from mcp_server.server import set_working_directory, create_view

        tmpdir = tempfile.mkdtemp()
        bad_pipeline = os.path.join(tmpdir, "runtime-err.py")
        with open(bad_pipeline, "w") as fh:
            fh.write("result = nonexistent_variable + 1\n")

        set_working_directory(tmpdir)
        result = create_view("runtime-err.py")

        assert "runtime-err" in result
        assert "error" in result.lower() or "Error" in result
        assert "runtime-err" in reset_server._views
        vs = reset_server._views["runtime-err"]
        assert vs.last_error is not None

    def test_create_view_view_name_derivation(self, reset_server):
        """View name is the basename without extension."""
        from mcp_server.server import set_working_directory, create_view

        tmpdir = tempfile.mkdtemp()
        pipeline = os.path.join(tmpdir, "my-pipeline.py")
        with open(pipeline, "w") as fh:
            fh.write("# empty pipeline\n")

        set_working_directory(tmpdir)
        create_view("my-pipeline.py")

        assert "my-pipeline" in reset_server._views

    def test_create_view_watcher_started(self, tmp_vtk_dir, reset_server):
        """After create_view, a file watcher observer should be running."""
        from mcp_server.server import set_working_directory, create_view

        pipeline_path = os.path.join(tmp_vtk_dir, "view-main.py")
        with open(pipeline_path, "w") as fh:
            fh.write('mesh = read("test.vtk")\nshow(mesh, colormap="viridis")\n')

        set_working_directory(tmp_vtk_dir)
        create_view("view-main.py")

        vs = reset_server._views["view-main"]
        assert vs.watcher is not None
        assert vs.watcher.is_alive()

    def test_create_view_includes_data_description(self, tmp_vtk_dir, reset_server):
        """create_view output includes field names, point count, and type info."""
        from mcp_server.server import set_working_directory, create_view

        pipeline_path = os.path.join(tmp_vtk_dir, "view-desc.py")
        with open(pipeline_path, "w") as fh:
            fh.write('mesh = read("test.vtk")\nshow(mesh)\n')

        set_working_directory(tmp_vtk_dir)
        result = create_view("view-desc.py")

        assert "Points:" in result
        assert "Fields" in result
        assert "T:" in result  # field name from tmp_vtk_dir fixture
