"""Tests for rich describe_data with percentiles and distribution classification."""

import os
import sys
import unittest

import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vislang import queries


def _make_image_data(dims=(10, 10, 10), spacing=(1, 1, 1)):
    """Create a basic vtkImageData."""
    img = vtk.vtkImageData()
    img.SetDimensions(*dims)
    img.SetOrigin(0, 0, 0)
    img.SetSpacing(*spacing)
    return img


def _add_scalar(data, name, values):
    """Add a scalar point data array."""
    arr = numpy_to_vtk(values.astype(np.float64))
    arr.SetName(name)
    data.GetPointData().AddArray(arr)


def _add_vector(data, name, values):
    """Add a vector (multi-component) point data array."""
    arr = numpy_to_vtk(values.astype(np.float64))
    arr.SetName(name)
    data.GetPointData().AddArray(arr)


class TestClassifyDistribution(unittest.TestCase):
    """Test _classify_distribution helper."""

    def test_uniform(self):
        vals = np.random.uniform(0, 1, 10000)
        shape = queries._classify_distribution(vals)
        self.assertEqual(shape, "uniform")

    def test_skewed(self):
        vals = np.random.exponential(scale=1.0, size=10000)
        shape = queries._classify_distribution(vals)
        self.assertEqual(shape, "skewed")

    def test_sparse(self):
        vals = np.zeros(10000)
        vals[:50] = np.random.uniform(1, 10, 50)
        shape = queries._classify_distribution(vals)
        self.assertEqual(shape, "sparse")

    def test_bimodal(self):
        vals = np.concatenate([
            np.random.normal(-5, 0.5, 5000),
            np.random.normal(5, 0.5, 5000),
        ])
        shape = queries._classify_distribution(vals)
        self.assertEqual(shape, "bimodal")

    def test_constant(self):
        vals = np.full(1000, 42.0)
        shape = queries._classify_distribution(vals)
        self.assertEqual(shape, "uniform")

    def test_empty(self):
        vals = np.array([])
        shape = queries._classify_distribution(vals)
        self.assertEqual(shape, "sparse")


class TestGetRichFieldStats(unittest.TestCase):
    """Test get_rich_field_stats with various data types."""

    def test_scalar_field(self):
        data = _make_image_data()
        n = data.GetNumberOfPoints()
        _add_scalar(data, "pressure", np.random.randn(n))
        stats = queries.get_rich_field_stats(data)
        self.assertEqual(len(stats), 1)
        s = stats[0]
        self.assertEqual(s["name"], "pressure")
        self.assertEqual(s["location"], "point")
        self.assertEqual(s["components"], 1)
        # Check all expected keys
        for key in ("min", "max", "p1", "p25", "p50", "p75", "p99", "mean", "std", "shape"):
            self.assertIn(key, s, f"Missing key: {key}")
        # Percentiles should be ordered
        self.assertLessEqual(s["p1"], s["p25"])
        self.assertLessEqual(s["p25"], s["p50"])
        self.assertLessEqual(s["p50"], s["p75"])
        self.assertLessEqual(s["p75"], s["p99"])

    def test_vector_field(self):
        data = _make_image_data()
        n = data.GetNumberOfPoints()
        _add_vector(data, "velocity", np.random.randn(n, 3))
        stats = queries.get_rich_field_stats(data)
        self.assertEqual(len(stats), 1)
        s = stats[0]
        self.assertEqual(s["name"], "velocity")
        self.assertEqual(s["components"], 3)
        self.assertIn("magnitude", s)
        mag = s["magnitude"]
        for key in ("min", "max", "p1", "p25", "p50", "p75", "p99", "mean", "std", "shape"):
            self.assertIn(key, mag, f"Missing magnitude key: {key}")
        self.assertIn("components_stats", s)
        self.assertEqual(len(s["components_stats"]), 3)

    def test_multiple_fields(self):
        data = _make_image_data()
        n = data.GetNumberOfPoints()
        _add_scalar(data, "temp", np.random.randn(n))
        _add_scalar(data, "density", np.random.uniform(0, 1, n))
        _add_vector(data, "vel", np.random.randn(n, 3))
        stats = queries.get_rich_field_stats(data)
        self.assertEqual(len(stats), 3)
        names = [s["name"] for s in stats]
        self.assertIn("temp", names)
        self.assertIn("density", names)
        self.assertIn("vel", names)

    def test_cell_data(self):
        data = _make_image_data()
        n = data.GetNumberOfCells()
        vals = np.random.randn(n).astype(np.float64)
        arr = numpy_to_vtk(vals)
        arr.SetName("cell_field")
        data.GetCellData().AddArray(arr)
        stats = queries.get_rich_field_stats(data)
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["location"], "cell")

    def test_none_data(self):
        stats = queries.get_rich_field_stats(None)
        self.assertEqual(stats, [])


class TestFormatRichFieldStats(unittest.TestCase):
    """Test format_rich_field_stats output."""

    def test_scalar_formatting(self):
        data = _make_image_data()
        n = data.GetNumberOfPoints()
        _add_scalar(data, "temperature", np.random.randn(n))
        stats = queries.get_rich_field_stats(data)
        text = queries.format_rich_field_stats(stats)
        self.assertIn("temperature", text)
        self.assertIn("p1=", text)
        self.assertIn("p50=", text)
        self.assertIn("p99=", text)
        self.assertIn("shape=", text)
        self.assertIn("mean=", text)

    def test_vector_formatting(self):
        data = _make_image_data()
        n = data.GetNumberOfPoints()
        _add_vector(data, "velocity", np.random.randn(n, 3))
        stats = queries.get_rich_field_stats(data)
        text = queries.format_rich_field_stats(stats)
        self.assertIn("velocity", text)
        self.assertIn("|magnitude|", text)
        self.assertIn("component 0", text)
        self.assertIn("component 1", text)
        self.assertIn("component 2", text)

    def test_empty_stats(self):
        text = queries.format_rich_field_stats([])
        self.assertEqual(text, "No fields found.")


class TestDescribeDataIntegration(unittest.TestCase):
    """Test that describe_data in server.py uses rich stats.

    These tests create VTK objects directly and call the queries module,
    since calling the server tool requires MCP infrastructure.
    """

    def test_structured_grid(self):
        """Test with vtkStructuredGrid (like wildfire data)."""
        sg = vtk.vtkStructuredGrid()
        sg.SetDimensions(5, 5, 5)
        pts = vtk.vtkPoints()
        for k in range(5):
            for j in range(5):
                for i in range(5):
                    pts.InsertNextPoint(i * 10.0, j * 10.0, k * 10.0)
        sg.SetPoints(pts)
        n = sg.GetNumberOfPoints()
        _add_scalar(sg, "theta", np.random.uniform(300, 1200, n))
        stats = queries.get_rich_field_stats(sg)
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["name"], "theta")
        self.assertIn("shape", stats[0])

    def test_image_data(self):
        """Test with vtkImageData (synthetic data)."""
        data = _make_image_data(dims=(20, 20, 20))
        n = data.GetNumberOfPoints()
        _add_scalar(data, "field1", np.random.randn(n))
        _add_scalar(data, "field2", np.random.exponential(1, n))
        stats = queries.get_rich_field_stats(data)
        self.assertEqual(len(stats), 2)
        formatted = queries.format_rich_field_stats(stats)
        self.assertIn("field1", formatted)
        self.assertIn("field2", formatted)


if __name__ == "__main__":
    unittest.main()
