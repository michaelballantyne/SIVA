"""Tests for conditional/subregion statistics (query_stats)."""

import os
import sys
import unittest

import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vislang import queries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dataset(n_points=1000):
    """Create a simple vtkPolyData with two scalar fields.

    - 'theta': linearly spaced from 300 to 600 K
    - 'w':     alternates positive and negative, proportional to index

    This gives predictable subsets for condition tests.
    """
    pts = vtk.vtkPoints()
    for i in range(n_points):
        pts.InsertNextPoint(float(i), 0.0, 0.0)

    poly = vtk.vtkPolyData()
    poly.SetPoints(pts)

    # theta: 300..600 linearly
    theta_vals = np.linspace(300.0, 600.0, n_points)
    theta_arr = numpy_to_vtk(theta_vals.astype(np.float64))
    theta_arr.SetName("theta")
    poly.GetPointData().AddArray(theta_arr)

    # w: vertical velocity, varies between -10 and +10
    w_vals = np.sin(np.linspace(0, 4 * np.pi, n_points)) * 10.0
    w_arr = numpy_to_vtk(w_vals.astype(np.float64))
    w_arr.SetName("w")
    poly.GetPointData().AddArray(w_arr)

    return poly, theta_vals, w_vals


# ---------------------------------------------------------------------------
# Unit tests for queries.query_stats
# ---------------------------------------------------------------------------

class TestQueryStatsBasic(unittest.TestCase):
    """Test the queries.query_stats function directly."""

    def setUp(self):
        self.data, self.theta, self.w = _make_dataset(1000)

    def test_greater_than_condition(self):
        """Points where theta > 450 should match roughly the upper half."""
        result = queries.query_stats(self.data, "w", "theta", ">", 450.0)
        self.assertIn("Conditional statistics", result)
        self.assertIn("Matching points:", result)
        # theta > 450 means the upper half ~500 points
        expected_count = int((self.theta > 450.0).sum())
        self.assertIn(str(expected_count), result)

    def test_less_than_condition(self):
        """Points where theta < 450."""
        result = queries.query_stats(self.data, "w", "theta", "<", 450.0)
        expected_count = int((self.theta < 450.0).sum())
        self.assertIn(str(expected_count), result)

    def test_gte_condition(self):
        """Points where theta >= 450."""
        result = queries.query_stats(self.data, "w", "theta", ">=", 450.0)
        expected_count = int((self.theta >= 450.0).sum())
        self.assertIn(str(expected_count), result)

    def test_lte_condition(self):
        """Points where theta <= 300 (just the boundary)."""
        result = queries.query_stats(self.data, "theta", "theta", "<=", 300.0)
        # Only the first point has theta == 300
        expected_count = int((self.theta <= 300.0).sum())
        self.assertIn(str(expected_count), result)

    def test_eq_condition(self):
        """Equality condition (exact match — may be 0 for floats, that's OK)."""
        result = queries.query_stats(self.data, "w", "theta", "==", 300.0)
        # 300.0 is exactly the first point's value
        expected_count = int((self.theta == 300.0).sum())
        self.assertIn(str(expected_count), result)

    def test_ne_condition(self):
        """Not-equal condition."""
        result = queries.query_stats(self.data, "w", "theta", "!=", 300.0)
        expected_count = int((self.theta != 300.0).sum())
        self.assertIn(str(expected_count), result)

    def test_statistics_values_correct(self):
        """Verify that returned stats match direct numpy computations."""
        mask = self.theta > 450.0
        expected_mean = float(np.mean(self.w[mask]))
        expected_min = float(np.min(self.w[mask]))
        expected_max = float(np.max(self.w[mask]))

        result = queries.query_stats(self.data, "w", "theta", ">", 450.0)

        # Parse mean from result (format: "  mean: <value>")
        for line in result.splitlines():
            if "mean:" in line:
                mean_str = line.split("mean:")[1].strip()
                parsed_mean = float(mean_str)
                self.assertAlmostEqual(parsed_mean, expected_mean, places=4)
            if "min:" in line:
                min_str = line.split("min:")[1].strip()
                parsed_min = float(min_str)
                self.assertAlmostEqual(parsed_min, expected_min, places=4)
            if "max:" in line:
                max_str = line.split("max:")[1].strip()
                parsed_max = float(max_str)
                self.assertAlmostEqual(parsed_max, expected_max, places=4)

    def test_no_match_returns_message(self):
        """When no points satisfy the condition, return a clear message."""
        result = queries.query_stats(self.data, "w", "theta", ">", 99999.0)
        self.assertIn("No points satisfy the condition", result)

    def test_none_data(self):
        """None data should return a helpful message."""
        result = queries.query_stats(None, "w", "theta", ">", 400.0)
        self.assertEqual(result, "Error: No data available.")

    def test_field_not_found(self):
        """Missing target field should return an error message."""
        result = queries.query_stats(self.data, "nonexistent_field", "theta", ">", 400.0)
        self.assertIn("not found", result.lower())

    def test_condition_field_not_found(self):
        """Missing condition field should return an error message."""
        result = queries.query_stats(self.data, "w", "nonexistent_cond", ">", 400.0)
        self.assertIn("not found", result.lower())

    def test_invalid_operator(self):
        """Unsupported operator should return an error."""
        result = queries.query_stats(self.data, "w", "theta", "~=", 400.0)
        self.assertIn("Unknown operator", result)

    def test_percentiles_present(self):
        """Output should contain p1, p25, p50, p75, p99."""
        result = queries.query_stats(self.data, "w", "theta", ">", 400.0)
        for label in ("p1:", "p25:", "p50:", "p75:", "p99:"):
            self.assertIn(label, result)

    def test_same_field_as_target_and_condition(self):
        """It's valid to filter a field by itself."""
        mask = self.theta > 450.0
        expected_mean = float(np.mean(self.theta[mask]))
        result = queries.query_stats(self.data, "theta", "theta", ">", 450.0)
        self.assertIn("Conditional statistics", result)
        for line in result.splitlines():
            if "mean:" in line:
                parsed = float(line.split("mean:")[1].strip())
                # _fmt formats to 6 significant figures, so compare to 1 decimal
                self.assertAlmostEqual(parsed, expected_mean, places=1)


class TestQueryStatsVectorFieldRejection(unittest.TestCase):
    """query_stats should reject vector (multi-component) fields gracefully."""

    def setUp(self):
        pts = vtk.vtkPoints()
        for i in range(10):
            pts.InsertNextPoint(float(i), 0.0, 0.0)
        self.data = vtk.vtkPolyData()
        self.data.SetPoints(pts)

        # Add a vector field (3 components)
        vecs = np.ones((10, 3), dtype=np.float64)
        arr = numpy_to_vtk(vecs)
        arr.SetName("velocity")
        arr.SetNumberOfComponents(3)
        # numpy_to_vtk collapses shape; rebuild properly
        vtk_arr = vtk.vtkFloatArray()
        vtk_arr.SetName("velocity")
        vtk_arr.SetNumberOfComponents(3)
        vtk_arr.SetNumberOfTuples(10)
        for i in range(10):
            vtk_arr.SetTuple3(i, 1.0, 0.5, 0.0)
        self.data.GetPointData().AddArray(vtk_arr)

        # Add a scalar condition field
        scalar = np.linspace(0.0, 1.0, 10)
        s_arr = numpy_to_vtk(scalar.astype(np.float64))
        s_arr.SetName("scalar")
        self.data.GetPointData().AddArray(s_arr)

    def test_vector_target_rejected(self):
        result = queries.query_stats(self.data, "velocity", "scalar", ">", 0.5)
        self.assertIn("components", result)

    def test_vector_condition_rejected(self):
        result = queries.query_stats(self.data, "scalar", "velocity", ">", 0.5)
        self.assertIn("components", result)


# ---------------------------------------------------------------------------
# Tests for the condition string parser (server-side)
# ---------------------------------------------------------------------------

class TestConditionStringParsing(unittest.TestCase):
    """Test that the condition string parser in server.py works correctly.

    We test the parsing logic directly by replicating it here, to keep tests
    independent of the MCP server startup.
    """

    def _parse(self, condition):
        """Replicate the regex parsing from server.py query_stats."""
        import re
        pattern = r"^\s*(.+?)\s*(>=|<=|!=|==|>|<)\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*$"
        m = re.match(pattern, condition)
        if not m:
            return None
        return m.group(1).strip(), m.group(2), float(m.group(3))

    def test_simple_gt(self):
        result = self._parse("theta > 400")
        self.assertEqual(result, ("theta", ">", 400.0))

    def test_simple_lt(self):
        result = self._parse("O2 < 0.2")
        self.assertEqual(result, ("O2", "<", 0.2))

    def test_gte(self):
        result = self._parse("fuel_density >= 0.1")
        self.assertEqual(result, ("fuel_density", ">=", 0.1))

    def test_lte(self):
        result = self._parse("temperature <= 500")
        self.assertEqual(result, ("temperature", "<=", 500.0))

    def test_eq(self):
        result = self._parse("phase == 1")
        self.assertEqual(result, ("phase", "==", 1.0))

    def test_ne(self):
        result = self._parse("mask != 0")
        self.assertEqual(result, ("mask", "!=", 0.0))

    def test_scientific_notation(self):
        result = self._parse("density > 1.5e-3")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "density")
        self.assertEqual(result[1], ">")
        self.assertAlmostEqual(result[2], 1.5e-3)

    def test_field_with_underscores(self):
        result = self._parse("fuel_temp_K > 400")
        self.assertEqual(result, ("fuel_temp_K", ">", 400.0))

    def test_negative_value(self):
        result = self._parse("w > -5")
        self.assertEqual(result, ("w", ">", -5.0))

    def test_invalid_no_operator(self):
        result = self._parse("theta 400")
        self.assertIsNone(result)

    def test_invalid_no_value(self):
        result = self._parse("theta >")
        self.assertIsNone(result)

    def test_invalid_empty(self):
        result = self._parse("")
        self.assertIsNone(result)

    def test_operator_priority_gte_over_gt(self):
        """'>=' should be matched, not '>' with '='."""
        result = self._parse("theta >= 400")
        self.assertIsNotNone(result)
        self.assertEqual(result[1], ">=")


if __name__ == "__main__":
    unittest.main()
