"""Tests for the inspect and screenshot MCP tools."""

import os
import tempfile

import numpy as np
import pyvista as pv
import pytest


# ---------------------------------------------------------------------------
# Helpers (shared with test_create_view.py)
# ---------------------------------------------------------------------------

def _reset_server():
    """Reset server globals between tests."""
    import mcp_server.server as srv
    for vs in srv._views.values():
        if vs.watcher is not None:
            try:
                vs.watcher.stop()
                vs.watcher.join(timeout=2)
            except Exception:
                pass
    srv._working_directory = None
    srv._views = {}


def _make_view(tmpdir=None):
    """Create a temp dir with a test VTK file and pipeline, create the view.

    Returns (tmpdir, view_name).
    """
    if tmpdir is None:
        tmpdir = tempfile.mkdtemp()

    mesh = pv.ImageData(dimensions=(5, 5, 5))
    mesh["T"] = np.linspace(0.0, 1000.0, mesh.n_points)
    vtk_path = os.path.join(tmpdir, "test.vtk")
    mesh.save(vtk_path)

    pipeline_path = os.path.join(tmpdir, "view-main.py")
    with open(pipeline_path, "w") as fh:
        fh.write('mesh = read("test.vtk")\nshow(mesh, colormap="viridis")\n')

    from mcp_server.server import set_working_directory, create_view
    set_working_directory(tmpdir)
    result = create_view("view-main.py")
    assert "Error" not in result, f"create_view failed: {result}"

    return tmpdir, "view-main"


# ---------------------------------------------------------------------------
# TestInspect
# ---------------------------------------------------------------------------

class TestInspect:
    def setup_method(self):
        _reset_server()

    def teardown_method(self):
        _reset_server()

    def test_inspect_basic(self):
        """inspect returns print output from the code."""
        from mcp_server.server import inspect

        tmpdir, view_name = _make_view()
        result = inspect("view-main.py", "print(mesh.n_points)")

        # ImageData(5,5,5) has 5*5*5 = 125 points
        assert "125" in result, f"Expected point count in output, got: {result!r}"

    def test_inspect_no_view(self):
        """inspect returns error if the view doesn't exist."""
        from mcp_server.server import inspect

        result = inspect("nonexistent.py", "print(1)")

        assert result.startswith("Error"), f"Expected error, got: {result!r}"
        assert "nonexistent" in result or "no view" in result.lower()

    def test_inspect_field_stats(self):
        """inspect can query field statistics."""
        from mcp_server.server import inspect

        _make_view()
        result = inspect("view-main.py", "print(round(float(mesh['T'].mean()), 2))")

        # T is linspace(0, 1000, 125), so mean is 500.0
        assert "500" in result, f"Expected mean value in output, got: {result!r}"

    def test_inspect_no_output(self):
        """inspect tells the agent to use print() if no output is produced."""
        from mcp_server.server import inspect

        _make_view()
        result = inspect("view-main.py", "x = 1 + 1")  # No print call

        assert "print()" in result or "no output" in result.lower(), (
            f"Expected hint about print(), got: {result!r}"
        )

    def test_inspect_error(self):
        """inspect returns an error message for bad code."""
        from mcp_server.server import inspect

        _make_view()
        result = inspect("view-main.py", "raise ValueError('test error')")

        assert "Error" in result or "ValueError" in result, (
            f"Expected error in output, got: {result!r}"
        )
        assert "test error" in result


# ---------------------------------------------------------------------------
# TestScreenshot
# ---------------------------------------------------------------------------

class TestScreenshot:
    def setup_method(self):
        _reset_server()

    def teardown_method(self):
        _reset_server()

    def test_screenshot_basic(self):
        """screenshot returns an Image object."""
        from mcp_server.server import screenshot
        from mcp.server.fastmcp import Image

        _make_view()
        result = screenshot("view-main.py")

        assert isinstance(result, Image), (
            f"Expected Image, got {type(result)}: {result!r}"
        )

    def test_screenshot_no_view(self):
        """screenshot raises ValueError if the view doesn't exist."""
        from mcp_server.server import screenshot

        with pytest.raises(ValueError, match="no view|No view"):
            screenshot("nonexistent.py")

    def test_screenshot_has_image_data(self):
        """screenshot Image contains non-empty PNG data."""
        from mcp_server.server import screenshot

        _make_view()
        result = screenshot("view-main.py")

        # The Image object stores data as bytes; check it's a real PNG.
        assert result.data is not None, "Image.data should not be None"
        assert len(result.data) > 0, "Image.data should not be empty"
        # PNG files start with the 8-byte PNG signature.
        assert result.data[:4] == b"\x89PNG", (
            "Expected PNG signature at start of image data"
        )
        assert result._format == "png"
