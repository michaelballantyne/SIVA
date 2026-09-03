"""Tests for significant-digit numeric formatting of tool output.

Regression coverage for the backlog item "numeric echoes in tool output are
rounded to one decimal and drop what was asked for": several query/camera
tools used to format coordinates with a fixed ``%.1f``, which silently
rounded millimetre-scale (or smaller) datasets down to 0.0 -- e.g. a Z bound
of 0.0012 printed as "Z=[-0.0, 0.0] (range 0.0)", i.e. "degenerate".

These tests exercise ``queries._fmt`` directly and ``queries.get_spatial_extent``
/ ``queries.get_ground_z`` on a small-scale dataset. See also
tests/test_server_tools.py (describe_data/load bounds), tests/test_get_ground_z.py
(get_ground_z on ordinary-scale grids), and tests/test_mcp_protocol.py
(get_camera's paste-able "To reuse:" line).
"""

import os
import sys
import unittest

import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva import queries


class TestFmtHelper(unittest.TestCase):
    """queries._fmt: significant-digit formatting used across query tools."""

    def test_small_scale_value_not_rounded_to_zero(self):
        self.assertEqual(queries._fmt(0.0012, 4), "0.0012")

    def test_negative_zero_prints_as_zero_not_minus_zero(self):
        self.assertEqual(queries._fmt(-0.0, 4), "0")

    def test_tiny_negative_that_rounds_to_exact_zero(self):
        # -0.0 specifically (not just "very small") is the case that used to
        # print "-0"; confirm the guard covers the float -0.0 literal.
        self.assertNotIn("-0", queries._fmt(-0.0, 4))

    def test_whole_number_has_no_trailing_zero_point(self):
        # %.4g strips a trailing ".0" -- documented behavior, not a bug.
        self.assertEqual(queries._fmt(3.0, 4), "3")

    def test_large_value_keeps_significant_digits(self):
        self.assertEqual(queries._fmt(1234567.891, 4), "1.235e+06")


def _make_small_scale_polydata(n=20, spacing=0.0012):
    """A vtkPolyData spanning a millimetre-scale bounding box."""
    pts = vtk.vtkPoints()
    for i in range(n):
        pts.InsertNextPoint(i * spacing, 0.0, 0.0)
    poly = vtk.vtkPolyData()
    poly.SetPoints(pts)
    field = numpy_to_vtk(np.linspace(0.0, 1.0, n).astype(np.float64))
    field.SetName("value")
    poly.GetPointData().AddArray(field)
    return poly


class TestGetSpatialExtentSmallScale(unittest.TestCase):
    """queries.get_spatial_extent on a millimetre-scale dataset."""

    def setUp(self):
        self.data = _make_small_scale_polydata(n=20, spacing=0.0012)

    def test_x_extent_not_collapsed_to_zero(self):
        result = queries.get_spatial_extent(self.data, "value", 0.0, 1.0)
        x_line = next(line for line in result.splitlines() if line.strip().startswith("X:"))
        # Old %.2f formatting would print "[0.00, 0.02]" -- still nonzero here,
        # but the *last* points (near x = 19*0.0012 = 0.0228) show the loss of
        # precision at %.1f/%.2f more clearly than a full assertion on digits.
        self.assertNotIn("[0.00, 0.00]", x_line)
        self.assertIn("0.0228", x_line)

    def test_no_negative_zero_in_output(self):
        result = queries.get_spatial_extent(self.data, "value", 0.0, 1.0)
        self.assertNotIn("-0,", result)
        self.assertNotIn("-0]", result)


class TestGetGroundZSmallScale(unittest.TestCase):
    """queries.get_ground_z on a millimetre-scale structured grid."""

    def _make_small_scale_grid(self, nx=5, ny=5, nz=3, spacing=0.0012):
        grid = vtk.vtkStructuredGrid()
        grid.SetDimensions(nx, ny, nz)
        pts = vtk.vtkPoints()
        pts.Allocate(nx * ny * nz)
        for iz in range(nz):
            for iy in range(ny):
                for ix in range(nx):
                    pts.InsertNextPoint(ix * spacing, iy * spacing, iz * spacing)
        grid.SetPoints(pts)
        return grid

    def test_ground_z_not_rounded_to_zero(self):
        grid = self._make_small_scale_grid(spacing=0.0012)
        result = queries.get_ground_z(grid, 0.0024, 0.0024, layers=False)
        # Ground layer (iz=0) really is z=0.0 here -- use a nonzero probe
        # instead: check the higher layers report their small z increments.
        full = queries.get_ground_z(grid, 0.0024, 0.0024, layers=True)
        self.assertIn("iz=1: z=0.0012", full)


if __name__ == "__main__":
    unittest.main()
