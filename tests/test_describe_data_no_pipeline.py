"""Tests for describe_data() working without an active pipeline via file_path parameter."""

import os
import sys
import tempfile
import unittest

import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _write_vti(path, dims=(8, 8, 8)):
    """Write a synthetic vtkImageData file (.vti) to the given path."""
    img = vtk.vtkImageData()
    img.SetDimensions(*dims)
    img.SetOrigin(0, 0, 0)
    img.SetSpacing(1, 1, 1)
    n = img.GetNumberOfPoints()
    rng = np.random.RandomState(0)
    vals = rng.uniform(0, 100, n).astype(np.float64)
    arr = numpy_to_vtk(vals)
    arr.SetName("pressure")
    img.GetPointData().AddArray(arr)

    writer = vtk.vtkXMLImageDataWriter()
    writer.SetFileName(path)
    writer.SetInputData(img)
    writer.Write()


def _write_vtp(path, n_pts=100):
    """Write a synthetic vtkPolyData file (.vtp) to the given path."""
    pts = vtk.vtkPoints()
    rng = np.random.RandomState(1)
    for _ in range(n_pts):
        pts.InsertNextPoint(*rng.uniform(0, 10, 3).tolist())
    pd = vtk.vtkPolyData()
    pd.SetPoints(pts)

    vals = rng.uniform(0, 1, n_pts).astype(np.float64)
    arr = numpy_to_vtk(vals)
    arr.SetName("density")
    pd.GetPointData().AddArray(arr)

    writer = vtk.vtkXMLPolyDataWriter()
    writer.SetFileName(path)
    writer.SetInputData(pd)
    writer.Write()


class TestLoadFileDirectly(unittest.TestCase):
    """Unit tests for the _load_file_directly helper function."""

    def _load(self, path):
        # Import via the server module
        from vislang import server
        return server._load_file_directly(path)

    def test_load_vti(self):
        with tempfile.NamedTemporaryFile(suffix=".vti", delete=False) as f:
            path = f.name
        try:
            _write_vti(path)
            data, error = self._load(path)
            self.assertIsNone(error, f"Unexpected error: {error}")
            self.assertIsNotNone(data)
            self.assertGreater(data.GetNumberOfPoints(), 0)
        finally:
            os.unlink(path)

    def test_load_vtp(self):
        with tempfile.NamedTemporaryFile(suffix=".vtp", delete=False) as f:
            path = f.name
        try:
            _write_vtp(path)
            data, error = self._load(path)
            self.assertIsNone(error, f"Unexpected error: {error}")
            self.assertIsNotNone(data)
            self.assertGreater(data.GetNumberOfPoints(), 0)
        finally:
            os.unlink(path)

    def test_unknown_extension(self):
        data, error = self._load("some_file.xyz")
        self.assertIsNone(data)
        self.assertIsNotNone(error)
        self.assertIn(".xyz", error)
        self.assertIn("Supported", error)

    def test_missing_file(self):
        data, error = self._load("/tmp/does_not_exist_vislang_test.vti")
        self.assertIsNone(data)
        self.assertIsNotNone(error)


class TestDescribeDataFilePathParam(unittest.TestCase):
    """Integration tests for describe_data() using the file_path parameter."""

    def _call_describe_data(self, **kwargs):
        # We call the underlying function directly (bypassing MCP infrastructure).
        # The server module registers MCP tools via @mcp.tool() decorator, but
        # the decorated function is still callable as a regular Python function.
        from vislang import server
        return server.describe_data(**kwargs)

    def test_describe_vti_no_pipeline(self):
        """describe_data(file_path=...) works with a .vti file and no active pipeline."""
        with tempfile.NamedTemporaryFile(suffix=".vti", delete=False) as f:
            path = f.name
        try:
            _write_vti(path)
            result = self._call_describe_data(file_path=path)
            self.assertIn("Dataset Overview", result)
            self.assertIn("Points:", result)
            self.assertIn("Fields", result)
            self.assertIn("pressure", result)
            self.assertIn("p1=", result)
            self.assertIn("p50=", result)
            self.assertIn("p99=", result)
            self.assertIn("shape=", result)
            # Quick Start hint should reference the file path
            self.assertIn(path, result)
        finally:
            os.unlink(path)

    def test_describe_vtp_no_pipeline(self):
        """describe_data(file_path=...) works with a .vtp file."""
        with tempfile.NamedTemporaryFile(suffix=".vtp", delete=False) as f:
            path = f.name
        try:
            _write_vtp(path)
            result = self._call_describe_data(file_path=path)
            self.assertIn("Dataset Overview", result)
            self.assertIn("density", result)
        finally:
            os.unlink(path)

    def test_file_path_overrides_node(self):
        """When both file_path and node are given, file_path takes precedence."""
        with tempfile.NamedTemporaryFile(suffix=".vti", delete=False) as f:
            path = f.name
        try:
            _write_vti(path)
            # Pass a non-existent node name; result should still work because file_path wins
            result = self._call_describe_data(file_path=path, node="nonexistent_node_xyz")
            self.assertIn("Dataset Overview", result)
            self.assertIn("pressure", result)
        finally:
            os.unlink(path)

    def test_unknown_extension_error_message(self):
        """Meaningful error for unsupported file extension."""
        result = self._call_describe_data(file_path="data.csv")
        self.assertIn("csv", result)
        self.assertIn("Supported", result)

    def test_no_pipeline_no_file_returns_hint(self):
        """Without a file_path and with no active pipeline, returns helpful hint."""
        from vislang import server
        # Clear any pipeline state
        server._vtk_objects.clear()
        result = self._call_describe_data()
        self.assertIn("No pipeline is active", result)

    def test_dimensions_included_for_image_data(self):
        """vtkImageData should show Dimensions line."""
        with tempfile.NamedTemporaryFile(suffix=".vti", delete=False) as f:
            path = f.name
        try:
            _write_vti(path, dims=(5, 6, 7))
            result = self._call_describe_data(file_path=path)
            self.assertIn("Dimensions:", result)
            self.assertIn("5 x 6 x 7", result)
        finally:
            os.unlink(path)

    def test_volume_rendering_hint_for_image_data(self):
        """vtkImageData should include Volume Rendering section."""
        with tempfile.NamedTemporaryFile(suffix=".vti", delete=False) as f:
            path = f.name
        try:
            _write_vti(path)
            result = self._call_describe_data(file_path=path)
            self.assertIn("Volume Rendering", result)
            self.assertIn("vtkImageData", result)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
