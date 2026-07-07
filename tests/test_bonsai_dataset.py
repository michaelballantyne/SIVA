"""Tests for the bonsai CT scan dataset.

These tests verify that the bonsai NRRD file (256^3 uint8 CT scan) loads
correctly and that key SIVA tools work on it. The bonsai dataset is a
vtkImageData (regular grid), structurally different from the wildfire
curvilinear grid dataset.

Tests are skipped when the dataset is not present so that CI can run without
the dataset files.
"""

import os
import sys
import unittest

import vtk
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.filters import load_file, EXT_TO_READER
from siva import queries

# Path to the bonsai NRRD file relative to project root
_BONSAI_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "datasets", "bonsai", "data", "bonsai.nhdr",
)

_skip_no_bonsai = unittest.skipUnless(
    os.path.exists(_BONSAI_FILE),
    f"Bonsai dataset not found at {_BONSAI_FILE}. "
    "Run datasets/bonsai/download.sh to fetch it.",
)


@_skip_no_bonsai
class TestBonsaiLoad(unittest.TestCase):
    """Test that bonsai loads correctly via load_file()."""

    def setUp(self):
        self.data, self.error = load_file(_BONSAI_FILE)

    def test_load_succeeds(self):
        self.assertIsNone(self.error, f"load_file failed: {self.error}")
        self.assertIsNotNone(self.data)

    def test_correct_type(self):
        """Bonsai is a regular grid, must be vtkImageData."""
        self.assertIsNotNone(self.data)
        self.assertEqual(self.data.GetClassName(), "vtkImageData")

    def test_dimensions(self):
        """Bonsai is 256x256x256."""
        self.assertIsNotNone(self.data)
        self.assertEqual(self.data.GetDimensions(), (256, 256, 256))

    def test_point_count(self):
        """256^3 = 16,777,216 points."""
        self.assertIsNotNone(self.data)
        self.assertEqual(self.data.GetNumberOfPoints(), 256 ** 3)

    def test_has_scalar_field(self):
        """The dataset has at least one scalar field."""
        self.assertIsNotNone(self.data)
        pd = self.data.GetPointData()
        self.assertGreater(pd.GetNumberOfArrays(), 0)

    def test_scalar_range(self):
        """Scalar is uint8 so range must be within [0, 255]."""
        self.assertIsNotNone(self.data)
        arr = self.data.GetPointData().GetArray(0)
        self.assertIsNotNone(arr)
        lo, hi = arr.GetRange()
        self.assertGreaterEqual(lo, 0.0)
        self.assertLessEqual(hi, 255.0)
        # The bonsai scan has significant variation
        self.assertGreater(hi - lo, 100.0)

    def test_isotropic_spacing(self):
        """Spacing must be 1x1x1 (isotropic)."""
        self.assertIsNotNone(self.data)
        sx, sy, sz = self.data.GetSpacing()
        self.assertAlmostEqual(sx, 1.0)
        self.assertAlmostEqual(sy, 1.0)
        self.assertAlmostEqual(sz, 1.0)


@_skip_no_bonsai
class TestBonsaiFieldStats(unittest.TestCase):
    """Test get_rich_field_stats() on the bonsai dataset."""

    @classmethod
    def setUpClass(cls):
        cls.data, _ = load_file(_BONSAI_FILE)

    def test_stats_returns_one_field(self):
        """Bonsai has exactly one field."""
        stats = queries.get_rich_field_stats(self.data)
        self.assertEqual(len(stats), 1)

    def test_stats_field_name(self):
        stats = queries.get_rich_field_stats(self.data)
        # NRRD reader names the field "ImageFile"
        self.assertIsNotNone(stats[0]["name"])

    def test_stats_location(self):
        stats = queries.get_rich_field_stats(self.data)
        self.assertEqual(stats[0]["location"], "point")

    def test_stats_scalar(self):
        """Density is a scalar, not a vector."""
        stats = queries.get_rich_field_stats(self.data)
        self.assertEqual(stats[0]["components"], 1)

    def test_stats_percentiles_ordered(self):
        """p1 <= p25 <= p50 <= p75 <= p99."""
        stats = queries.get_rich_field_stats(self.data)
        s = stats[0]
        self.assertLessEqual(s["p1"], s["p25"])
        self.assertLessEqual(s["p25"], s["p50"])
        self.assertLessEqual(s["p50"], s["p75"])
        self.assertLessEqual(s["p75"], s["p99"])

    def test_stats_range_matches_vtk(self):
        """Min/max from rich stats should match VTK's GetRange()."""
        stats = queries.get_rich_field_stats(self.data)
        s = stats[0]
        arr = self.data.GetPointData().GetArray(0)
        vtk_lo, vtk_hi = arr.GetRange()
        self.assertAlmostEqual(s["min"], vtk_lo, places=3)
        self.assertAlmostEqual(s["max"], vtk_hi, places=3)

    def test_stats_format(self):
        """format_rich_field_stats should produce readable output."""
        stats = queries.get_rich_field_stats(self.data)
        text = queries.format_rich_field_stats(stats)
        self.assertIn("p50=", text)
        self.assertIn("mean=", text)


@_skip_no_bonsai
class TestBonsaiIsovalue(unittest.TestCase):
    """Test that isosurface extraction works on bonsai."""

    @classmethod
    def setUpClass(cls):
        cls.data, _ = load_file(_BONSAI_FILE)

    def test_contour_produces_surface(self):
        """Contouring at a mid-range value should produce a non-empty surface."""
        contour = vtk.vtkContourFilter()
        contour.SetInputData(self.data)
        contour.SetValue(0, 80.0)  # Mid-range density isolevel
        contour.Update()
        output = contour.GetOutput()
        self.assertGreater(
            output.GetNumberOfPoints(), 0,
            "Contour at density=80 should produce at least some points",
        )

    def test_contour_has_normals_or_scalars(self):
        """A generated surface should carry scalar data (interpolated from volume)."""
        contour = vtk.vtkContourFilter()
        contour.SetInputData(self.data)
        contour.ComputeScalarsOn()
        contour.SetValue(0, 80.0)
        contour.Update()
        output = contour.GetOutput()
        # Should have point data arrays (interpolated scalars)
        self.assertGreater(output.GetPointData().GetNumberOfArrays(), 0)


@_skip_no_bonsai
class TestBonsaiExtensionRecognition(unittest.TestCase):
    """Test that .nhdr extension is correctly recognized."""

    def test_nhdr_in_ext_to_reader(self):
        self.assertIn("nhdr", EXT_TO_READER)
        self.assertEqual(EXT_TO_READER["nhdr"], "vtkNrrdReader")

    def test_load_file_extension_detection(self):
        """load_file should pick up .nhdr and succeed."""
        data, error = load_file(_BONSAI_FILE)
        self.assertIsNone(error)
        self.assertIsNotNone(data)
        self.assertEqual(data.GetClassName(), "vtkImageData")


@_skip_no_bonsai
class TestBonsaiVolumeRenderPipeline(unittest.TestCase):
    """Test that a minimal volume render DSL pipeline executes without error.

    We don't check the visual output here — just that the pipeline builds and
    the renderer initializes without crashing.
    """

    def test_dsl_interpret_volume_pipeline(self):
        """Run a minimal bonsai volume render pipeline through the DSL."""
        from siva.compute import evaluate

        code = f"""
reader = source("vtkNrrdReader", FileName={_BONSAI_FILE!r})
show(reader, representation="Volume", color_by="ImageFile",
     scalar_range=(0, 255), opacity=0.05)
"""
        try:
            evaluate(code)
        except Exception as e:
            self.fail(f"DSL interpret raised unexpectedly: {e}")


if __name__ == "__main__":
    unittest.main()
