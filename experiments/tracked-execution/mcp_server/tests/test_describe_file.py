"""Tests for the describe_file MCP tool."""

import os
import tempfile

import numpy as np
import pyvista as pv
import pytest


class TestDescribeFile:
    """Tests for the describe_file MCP tool."""

    def test_describe_file_no_working_dir(self, reset_server):
        """describe_file returns an error if no working directory is set."""
        from mcp_server.server import describe_file

        result = describe_file("any.vtk")
        assert "Error" in result
        assert "set_working_directory" in result

    def test_describe_file_not_found(self, reset_server):
        """describe_file returns an error if the file does not exist."""
        from mcp_server.server import set_working_directory, describe_file

        with tempfile.TemporaryDirectory() as tmpdir:
            set_working_directory(tmpdir)
            result = describe_file("nonexistent.vtk")

        assert "Error" in result
        assert "not found" in result.lower() or "nonexistent" in result

    def test_describe_file_basic_fields(self, tmp_vtk_dir, reset_server):
        """describe_file returns file type, point count, cell count, and bounds."""
        from mcp_server.server import set_working_directory, describe_file

        set_working_directory(tmp_vtk_dir)
        result = describe_file("test.vtk")

        assert "Error" not in result, f"Unexpected error: {result}"
        assert "File: test.vtk" in result
        assert "Type:" in result
        assert "Points:" in result
        assert "Cells:" in result
        assert "Bounds:" in result

    def test_describe_file_shows_field_names(self, tmp_vtk_dir, reset_server):
        """describe_file lists field names and their ranges."""
        from mcp_server.server import set_working_directory, describe_file

        set_working_directory(tmp_vtk_dir)
        result = describe_file("test.vtk")

        # The tmp_vtk_dir fixture creates a mesh with a 'T' field.
        assert "Fields" in result
        assert "T" in result
        # Should show dtype and range.
        assert "range=" in result
        assert "shape=" in result

    def test_describe_file_shows_dimensions_for_image_data(self, reset_server):
        """describe_file shows Dimensions for ImageData (structured grids)."""
        tmpdir = tempfile.mkdtemp()
        mesh = pv.ImageData(dimensions=(4, 5, 6))
        mesh["Value"] = np.arange(mesh.n_points, dtype=float)
        mesh.save(os.path.join(tmpdir, "grid.vtk"))

        from mcp_server.server import set_working_directory, describe_file

        set_working_directory(tmpdir)
        result = describe_file("grid.vtk")

        assert "Dimensions:" in result
        # Should report the actual dimensions.
        assert "4" in result
        assert "5" in result
        assert "6" in result

    def test_describe_file_no_fields(self, reset_server):
        """describe_file handles meshes with no data fields."""
        tmpdir = tempfile.mkdtemp()
        # Create a mesh with no field data.
        mesh = pv.ImageData(dimensions=(3, 3, 3))
        mesh.save(os.path.join(tmpdir, "empty.vtk"))

        from mcp_server.server import set_working_directory, describe_file

        set_working_directory(tmpdir)
        result = describe_file("empty.vtk")

        assert "No fields" in result or "Fields" in result

    def test_describe_file_no_view_needed(self, reset_server):
        """describe_file works without any view being created."""
        tmpdir = tempfile.mkdtemp()
        mesh = pv.ImageData(dimensions=(3, 3, 3))
        mesh["T"] = np.ones(mesh.n_points)
        mesh.save(os.path.join(tmpdir, "data.vtk"))

        from mcp_server.server import set_working_directory, describe_file

        set_working_directory(tmpdir)
        # No create_view() call — describe_file should work on its own.
        result = describe_file("data.vtk")
        assert "Error" not in result
        assert "T" in result

    def test_describe_file_point_count_matches(self, reset_server):
        """describe_file reports point count that matches the actual mesh."""
        tmpdir = tempfile.mkdtemp()
        dims = (5, 6, 7)
        mesh = pv.ImageData(dimensions=dims)
        mesh["V"] = np.zeros(mesh.n_points)
        mesh.save(os.path.join(tmpdir, "sized.vtk"))

        from mcp_server.server import set_working_directory, describe_file

        set_working_directory(tmpdir)
        result = describe_file("sized.vtk")

        expected_points = dims[0] * dims[1] * dims[2]
        assert str(expected_points) in result.replace(",", "")
