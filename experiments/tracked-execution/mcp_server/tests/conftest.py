"""Shared pytest fixtures for mcp_server tests."""

import os
import sys
import tempfile

import numpy as np
import pyvista as pv
import pytest


def _clean_server(srv):
    """Stop watchers, close plotters, and reset server globals."""
    for vs in list(srv._views.values()):
        if vs.watcher is not None:
            try:
                vs.watcher.stop()
                vs.watcher.join(timeout=2)
            except Exception:
                pass
        try:
            vs.plotter.close()
        except Exception:
            pass
    srv._views = {}
    srv._working_directory = None


@pytest.fixture
def reset_server():
    """Reset MCP server module state before and after each test.

    Stops any running watchers, closes plotters, clears views, and resets
    the working directory. Yields the server module so tests can access
    server internals directly.
    """
    te_root = os.path.join(os.path.dirname(__file__), "..", "..")
    if te_root not in sys.path:
        sys.path.insert(0, te_root)

    import mcp_server.server as srv
    _clean_server(srv)
    yield srv
    _clean_server(srv)


@pytest.fixture
def tmp_vtk_dir():
    """Create a temp directory with a small synthetic VTK file.

    Returns the directory path. The VTK file is named 'test.vtk' and
    contains a 5x5x5 ImageData mesh with a scalar field 'T'.
    """
    tmpdir = tempfile.mkdtemp()
    mesh = pv.ImageData(dimensions=(5, 5, 5))
    mesh["T"] = np.linspace(0.0, 1000.0, mesh.n_points)
    mesh.save(os.path.join(tmpdir, "test.vtk"))
    return tmpdir


@pytest.fixture
def view_dir(tmp_vtk_dir, reset_server):
    """Set up a working directory with a test VTK file and a 'view-main.py' pipeline.

    Sets the working directory and creates the view. Yields the tmpdir path.
    """
    pipeline_path = os.path.join(tmp_vtk_dir, "view-main.py")
    with open(pipeline_path, "w") as fh:
        fh.write('mesh = read("test.vtk")\nshow(mesh, colormap="viridis")\n')

    from mcp_server.server import set_working_directory, create_view
    set_working_directory(tmp_vtk_dir)
    result = create_view("view-main.py")
    assert "Error" not in result, f"create_view failed: {result}"

    return tmp_vtk_dir
