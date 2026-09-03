"""Tests for describe_data() working without an active pipeline via file_path parameter.

These tests cover:
  - filters.load_file()  -- reads a VTK file by extension
  - The describe_data(file_path=...) code path, exercised by calling the real
    srv.describe_data() tool function directly (see TestDescribeDataViaFileLoad),
    following the srv._init_for_test() pattern used in test_server_tools.py.
"""

import contextlib
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva import queries
from siva.filters import load_file, EXT_TO_READER
import siva.server as srv


# ---------------------------------------------------------------------------
# Helpers for writing synthetic VTK files
# ---------------------------------------------------------------------------

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


def _write_vtu(path, n_pts=50):
    """Write a synthetic vtkUnstructuredGrid file (.vtu) to the given path."""
    pts = vtk.vtkPoints()
    rng = np.random.RandomState(2)
    for _ in range(n_pts):
        pts.InsertNextPoint(*rng.uniform(0, 5, 3).tolist())
    ug = vtk.vtkUnstructuredGrid()
    ug.SetPoints(pts)
    vals = rng.randn(n_pts).astype(np.float64)
    arr = numpy_to_vtk(vals)
    arr.SetName("temperature")
    ug.GetPointData().AddArray(arr)
    writer = vtk.vtkXMLUnstructuredGridWriter()
    writer.SetFileName(path)
    writer.SetInputData(ug)
    writer.Write()


@contextlib.contextmanager
def _tmp_relpath(suffix):
    """Yield a path relative to a scratch working directory.

    create_vtk_filter (via load_file) confines FileName to the working
    directory (siva.filters.confine_to_workdir), so tests need a real
    relative path inside cwd rather than an arbitrary /tmp absolute path.
    Chdirs into a scratch dir for the duration of the ``with`` block.
    """
    tmpdir = tempfile.mkdtemp()
    old_cwd = os.getcwd()
    os.chdir(tmpdir)
    try:
        yield f"data{suffix}"
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tests for filters.load_file()
# ---------------------------------------------------------------------------

class TestLoadFile(unittest.TestCase):
    """Unit tests for filters.load_file()."""

    def test_load_vti(self):
        with _tmp_relpath(".vti") as path:
            _write_vti(path)
            data, error = load_file(path)
            self.assertIsNone(error, f"Unexpected error: {error}")
            self.assertIsNotNone(data)
            self.assertGreater(data.GetNumberOfPoints(), 0)
            self.assertEqual(data.GetClassName(), "vtkImageData")

    def test_load_vtp(self):
        with _tmp_relpath(".vtp") as path:
            _write_vtp(path)
            data, error = load_file(path)
            self.assertIsNone(error, f"Unexpected error: {error}")
            self.assertIsNotNone(data)
            self.assertGreater(data.GetNumberOfPoints(), 0)
            self.assertEqual(data.GetClassName(), "vtkPolyData")

    def test_load_vtu(self):
        with _tmp_relpath(".vtu") as path:
            _write_vtu(path)
            data, error = load_file(path)
            self.assertIsNone(error, f"Unexpected error: {error}")
            self.assertIsNotNone(data)
            self.assertGreater(data.GetNumberOfPoints(), 0)

    def test_unknown_extension_returns_error(self):
        data, error = load_file("some_file.xyz")
        self.assertIsNone(data)
        self.assertIsNotNone(error)
        self.assertIn(".xyz", error)
        self.assertIn("Supported", error)

    def test_no_extension_returns_error(self):
        data, error = load_file("nodotinname")
        self.assertIsNone(data)
        self.assertIsNotNone(error)

    def test_missing_file_returns_error(self):
        data, error = load_file("/tmp/does_not_exist_siva_test.vti")
        self.assertIsNone(data)
        self.assertIsNotNone(error)

    def test_ext_to_reader_mapping(self):
        """EXT_TO_READER covers all expected extensions."""
        for ext in ("vts", "vti", "vtp", "vtu", "vtr"):
            self.assertIn(ext, EXT_TO_READER)


# ---------------------------------------------------------------------------
# Tests for describe_data logic applied to file-loaded data
# ---------------------------------------------------------------------------

class TestDescribeDataViaFileLoad(unittest.TestCase):
    """Verify that srv.describe_data(file_path=...) works with no active pipeline.

    Calls the real describe_data() tool function directly (no pipeline
    required, following srv._init_for_test() as in test_server_tools.py),
    rather than reimplementing its formatting locally.
    """

    def _run_describe(self, path):
        """Call the real describe_data(file_path=path) and also return the
        loaded data / field stats for assertions that need the raw values."""
        srv._init_for_test()
        result = srv.describe_data(file_path=path)

        data, error = load_file(path)
        self.assertIsNone(error, f"load_file failed: {error}")
        self.assertIsNotNone(data)
        field_stats = queries.get_rich_field_stats(data)

        return result, data, field_stats

    def test_vti_describe_output(self):
        with _tmp_relpath(".vti") as path:
            _write_vti(path, dims=(5, 6, 7))
            result, data, field_stats = self._run_describe(path)
            # Basic structure checks
            self.assertIn("Dataset Overview", result)
            self.assertIn("Points:", result)
            self.assertIn("Fields", result)
            # Field name present
            self.assertIn("pressure", result)
            # Percentiles and shape in output
            self.assertIn("p1=", result)
            self.assertIn("p50=", result)
            self.assertIn("p99=", result)
            self.assertIn("shape=", result)
            # Dimensions
            self.assertEqual(data.GetClassName(), "vtkImageData")
            self.assertEqual(len(field_stats), 1)
            self.assertEqual(field_stats[0]["name"], "pressure")

    def test_vtp_describe_output(self):
        with _tmp_relpath(".vtp") as path:
            _write_vtp(path, n_pts=200)
            result, data, field_stats = self._run_describe(path)
            self.assertIn("Dataset Overview", result)
            self.assertIn("density", result)
            self.assertIn("p1=", result)
            self.assertEqual(len(field_stats), 1)
            self.assertEqual(field_stats[0]["name"], "density")

    def test_stats_have_expected_keys(self):
        """Each field stat dict has all required percentile/shape keys."""
        with _tmp_relpath(".vti") as path:
            _write_vti(path)
            data, error = load_file(path)
            self.assertIsNone(error)
            field_stats = queries.get_rich_field_stats(data)
            self.assertGreater(len(field_stats), 0)
            s = field_stats[0]
            for key in ("min", "max", "p1", "p25", "p50", "p75", "p99",
                        "mean", "std", "shape"):
                self.assertIn(key, s, f"Missing key: {key}")
            # Percentiles ordered
            self.assertLessEqual(s["p1"], s["p25"])
            self.assertLessEqual(s["p25"], s["p50"])
            self.assertLessEqual(s["p50"], s["p75"])
            self.assertLessEqual(s["p75"], s["p99"])


if __name__ == "__main__":
    unittest.main()
