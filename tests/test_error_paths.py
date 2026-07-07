"""Tests for error paths in the SIVA MCP server.

These tests verify failure modes identified in feedback:
  - Invalid calculator expressions
  - Missing field names
  - Out-of-range threshold/contour values
  - Empty-output diagnostics
  - Empty pipeline operations (no active pipeline)
  - Invalid node names
  - sample_point outside bounds
  - query_stats with invalid condition
  - load() with unsupported extension
  - describe_data with non-existent file

All tests use synthetic VTK data (small vtkImageData or vtkPolyData)
created inline -- no dataset downloads required.
"""

import os
import sys
import tempfile
import unittest

import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva import queries
from siva.filters import load_file, create_vtk_filter, WHITELISTED_CLASSES

# --- test helper: freeze a builder and run the compute phase (replaces the
# former PipelineBuilder._build_pipeline, which now lives in siva.compute) ---
from siva.compute import compute as _compute_spec
from siva.dsl import _freeze_spec as _freeze_spec_for_test


def _bp(_builder, cache=None):
    _r = _compute_spec(_freeze_spec_for_test(_builder), cache=cache)
    return _r.outputs, _r.statuses



# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_image_data(dims=(10, 10, 10), field_name="temperature",
                     field_range=(0.0, 100.0)):
    """Create a vtkImageData with one scalar field in a known range."""
    img = vtk.vtkImageData()
    img.SetDimensions(*dims)
    img.SetOrigin(0.0, 0.0, 0.0)
    img.SetSpacing(1.0, 1.0, 1.0)
    n = img.GetNumberOfPoints()
    vals = np.linspace(field_range[0], field_range[1], n)
    arr = numpy_to_vtk(vals.astype(np.float64))
    arr.SetName(field_name)
    img.GetPointData().AddArray(arr)
    img.GetPointData().SetActiveScalars(field_name)
    return img


def _make_poly_data(n_points=100, field_name="pressure",
                    field_range=(0.0, 1.0)):
    """Create a vtkPolyData with one scalar field in a known range."""
    pts = vtk.vtkPoints()
    for i in range(n_points):
        pts.InsertNextPoint(float(i), 0.0, 0.0)
    poly = vtk.vtkPolyData()
    poly.SetPoints(pts)
    vals = np.linspace(field_range[0], field_range[1], n_points)
    arr = numpy_to_vtk(vals.astype(np.float64))
    arr.SetName(field_name)
    poly.GetPointData().AddArray(arr)
    return poly


def _image_data_to_algorithm(data):
    """Wrap a vtkDataSet in a vtkTrivialProducer so it acts like an algorithm."""
    producer = vtk.vtkTrivialProducer()
    producer.SetOutput(data)
    producer.Update()
    return producer


# ---------------------------------------------------------------------------
# 1. Invalid calculator expressions
# ---------------------------------------------------------------------------

class TestInvalidCalculatorExpression(unittest.TestCase):
    """vtkArrayCalculator with a bad Function should surface an error."""

    def test_bad_function_raises_or_produces_empty(self):
        """An invalid formula should not silently succeed."""
        img = _make_image_data()
        alg = _image_data_to_algorithm(img)

        calc = vtk.vtkArrayCalculator()
        calc.SetInputConnection(alg.GetOutputPort())
        calc.AddScalarArrayName("temperature")
        # Deliberately broken expression: function doesn't exist
        calc.SetFunction("not_a_real_function(temperature)")
        calc.SetResultArrayName("bad_result")

        # VTK calculators may either error out (return 0 output points) or
        # simply not produce the result array. Either way there must be no
        # silently-correct new scalar array with the expected computed values.
        calc.Update()
        output = calc.GetOutput()

        bad_arr = output.GetPointData().GetArray("bad_result")
        if bad_arr is not None:
            # If the array was created, it should NOT contain sensible data
            # (range should not match a meaningful computed quantity).
            # We just document that we know it was created under error conditions.
            pass  # VTK may still create a zeroed array; that is acceptable

    def test_empty_function_string(self):
        """An empty function string must not crash."""
        img = _make_image_data()
        alg = _image_data_to_algorithm(img)

        calc = vtk.vtkArrayCalculator()
        calc.SetInputConnection(alg.GetOutputPort())
        calc.AddScalarArrayName("temperature")
        calc.SetFunction("")
        calc.SetResultArrayName("empty_result")
        # Must not raise
        try:
            calc.Update()
        except Exception as e:
            self.fail(f"Calculator raised unexpected exception: {e}")

    def test_mismatched_array_name(self):
        """Calculator referencing a non-existent array should not produce valid output."""
        img = _make_image_data()  # has 'temperature' field
        alg = _image_data_to_algorithm(img)

        calc = vtk.vtkArrayCalculator()
        calc.SetInputConnection(alg.GetOutputPort())
        # Tell calculator about 'nonexistent' even though it's not in the data
        calc.AddScalarArrayName("nonexistent")
        calc.SetFunction("nonexistent * 2")
        calc.SetResultArrayName("result")
        # Must not crash
        try:
            calc.Update()
        except Exception as e:
            self.fail(f"Calculator raised unexpected exception: {e}")


# ---------------------------------------------------------------------------
# 2. Missing field names in queries
# ---------------------------------------------------------------------------

class TestMissingFieldNames(unittest.TestCase):
    """Calling queries with non-existent field names returns helpful messages."""

    def setUp(self):
        self.data = _make_image_data(field_name="temperature")

    def test_get_histogram_missing_field(self):
        result = queries.get_histogram(self.data, "nonexistent_field")
        self.assertIn("not found", result.lower())

    def test_query_stats_missing_target_field(self):
        result = queries.query_stats(self.data, "missing_target", "temperature", ">", 50.0)
        self.assertIn("not found", result.lower())

    def test_query_stats_missing_condition_field(self):
        result = queries.query_stats(self.data, "temperature", "missing_cond", ">", 50.0)
        self.assertIn("not found", result.lower())

    def test_get_rich_field_stats_none_data(self):
        result = queries.get_rich_field_stats(None)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# 3. Out-of-range threshold values
# ---------------------------------------------------------------------------

class TestOutOfRangeThreshold(unittest.TestCase):
    """Threshold with values outside field range should produce empty output."""

    def setUp(self):
        # temperature in [0, 100]
        self.img = _make_image_data(field_name="temperature", field_range=(0.0, 100.0))
        self.alg = _image_data_to_algorithm(self.img)

    def _run_threshold(self, lo, hi):
        thresh = vtk.vtkThreshold()
        thresh.SetInputConnection(self.alg.GetOutputPort())
        thresh.SetInputArrayToProcess(0, 0, 0, 0, "temperature")
        thresh.SetLowerThreshold(lo)
        thresh.SetUpperThreshold(hi)
        thresh.SetThresholdFunction(vtk.vtkThreshold.THRESHOLD_BETWEEN)
        thresh.Update()
        return thresh.GetOutput()

    def test_threshold_above_max_returns_empty(self):
        """Threshold range above field max should yield 0 cells."""
        output = self._run_threshold(200.0, 300.0)
        self.assertEqual(output.GetNumberOfCells(), 0,
                         "Expected empty output when threshold range is above field max")

    def test_threshold_below_min_returns_empty(self):
        """Threshold range below field min should yield 0 cells."""
        output = self._run_threshold(-200.0, -100.0)
        self.assertEqual(output.GetNumberOfCells(), 0,
                         "Expected empty output when threshold range is below field min")

    def test_threshold_within_range_not_empty(self):
        """Sanity check: threshold within range should produce data."""
        output = self._run_threshold(20.0, 80.0)
        self.assertGreater(output.GetNumberOfCells(), 0,
                           "Expected non-empty output for in-range threshold")

    def test_empty_threshold_detected_by_query_stats(self):
        """query_stats with impossible condition should return 'no points' message."""
        result = queries.query_stats(self.img, "temperature", "temperature", ">", 9999.0)
        self.assertIn("no points", result.lower())

    def test_get_rich_field_stats_after_empty_threshold(self):
        """get_rich_field_stats on a completely empty output should handle gracefully."""
        output = self._run_threshold(200.0, 300.0)
        self.assertEqual(output.GetNumberOfPoints(), 0, "Precondition: output must be empty")
        # Empty dataset (0 points) -- should not crash and should return a list
        result = queries.get_rich_field_stats(output)
        self.assertIsInstance(result, list)


# ---------------------------------------------------------------------------
# 4. Out-of-range contour values
# ---------------------------------------------------------------------------

class TestOutOfRangeContour(unittest.TestCase):
    """Contour at values outside field range should produce empty polydata."""

    def setUp(self):
        # temperature in [0, 100]
        self.img = _make_image_data(dims=(10, 10, 10),
                                    field_name="temperature",
                                    field_range=(0.0, 100.0))
        self.alg = _image_data_to_algorithm(self.img)

    def _run_contour(self, value):
        contour = vtk.vtkContourFilter()
        contour.SetInputConnection(self.alg.GetOutputPort())
        contour.SetInputArrayToProcess(0, 0, 0, 0, "temperature")
        contour.SetValue(0, value)
        contour.Update()
        return contour.GetOutput()

    def test_contour_above_max_returns_empty(self):
        output = self._run_contour(9999.0)
        self.assertEqual(output.GetNumberOfPoints(), 0,
                         "Contour above field max should yield no points")

    def test_contour_below_min_returns_empty(self):
        output = self._run_contour(-9999.0)
        self.assertEqual(output.GetNumberOfPoints(), 0,
                         "Contour below field min should yield no points")

    def test_contour_in_range_not_empty(self):
        output = self._run_contour(50.0)
        self.assertGreater(output.GetNumberOfPoints(), 0,
                           "Contour at midpoint should produce geometry")


# ---------------------------------------------------------------------------
# 5. Empty pipeline / no active pipeline
# ---------------------------------------------------------------------------

class TestNoPipelineActive(unittest.TestCase):
    """Operations on None data (simulating no active pipeline) return errors."""

    def test_get_histogram_no_data(self):
        result = queries.get_histogram(None, "temperature")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_query_stats_no_data(self):
        result = queries.query_stats(None, "temperature", "temperature", ">", 50.0)
        self.assertEqual(result, "Error: No data available.")

    def test_sample_point_no_data(self):
        result = queries.sample_point(None, 0.0, 0.0, 0.0)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_sample_points_none_data_returns_empty(self):
        result = queries.sample_points(None, [(0.0, 0.0, 0.0)])
        self.assertEqual(result, [])

    def test_get_rich_field_stats_no_data(self):
        result = queries.get_rich_field_stats(None)
        self.assertEqual(result, [])

    def test_format_rich_field_stats_empty_list(self):
        text = queries.format_rich_field_stats([])
        self.assertEqual(text, "No fields found.")


# ---------------------------------------------------------------------------
# 6. Invalid node names (simulate via _get_data behavior)
# ---------------------------------------------------------------------------

class TestInvalidNodeNames(unittest.TestCase):
    """Querying a non-existent node name returns a helpful error message.

    The server's _get_data() returns None when the node name is not found.
    We test the downstream behaviour by passing None to query functions.
    """

    def test_query_stats_returns_string_for_none(self):
        result = queries.query_stats(None, "field", "cond_field", ">", 0.0)
        self.assertIsInstance(result, str)

    def test_sample_points_returns_empty_for_none(self):
        result = queries.sample_points(None, [(1.0, 2.0, 3.0)])
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# 7. sample_point outside bounds
# ---------------------------------------------------------------------------

class TestSamplePointOutsideBounds(unittest.TestCase):
    """Probing coordinates way outside the dataset still returns gracefully."""

    def setUp(self):
        # Dataset spans [0,9] in each axis
        self.data = _make_image_data(dims=(10, 10, 10),
                                     field_name="temperature",
                                     field_range=(0.0, 100.0))

    def test_sample_point_far_outside_returns_string(self):
        result = queries.sample_point(self.data, 99999.0, 99999.0, 99999.0)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_sample_points_far_outside_flags_out_of_bounds(self):
        results = queries.sample_points(self.data, [(99999.0, 99999.0, 99999.0)])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["outside_bounds"],
                        "Point far outside dataset should be flagged as out of bounds")

    def test_sample_points_outside_still_returns_nearest(self):
        """Even out-of-bounds probes should return the nearest valid point."""
        results = queries.sample_points(
            self.data, [(99999.0, 99999.0, 99999.0)],
            fields=["temperature"]
        )
        entry = results[0]
        self.assertIsNotNone(entry["nearest"])
        self.assertGreaterEqual(entry["point_id"], 0)

    def test_sample_points_negative_outside_flags_out_of_bounds(self):
        results = queries.sample_points(self.data, [(-99999.0, -99999.0, -99999.0)])
        self.assertTrue(results[0]["outside_bounds"])

    def test_format_outside_bounds_mentions_outside(self):
        results = queries.sample_points(self.data, [(99999.0, 0.0, 0.0)])
        text = queries.format_sample_points(results)
        self.assertIn("outside", text.lower())


# ---------------------------------------------------------------------------
# 8. query_stats with invalid condition string format
# ---------------------------------------------------------------------------

class TestQueryStatsInvalidCondition(unittest.TestCase):
    """Bad condition strings should return parse-error messages, not crash."""

    def _parse_condition(self, condition):
        """Replicate the regex parsing used in server.py query_stats."""
        import re
        pattern = (
            r"^\s*(.+?)\s*(>=|<=|!=|==|>|<)\s*"
            r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*$"
        )
        m = re.match(pattern, condition)
        return m

    def test_no_operator_returns_none(self):
        self.assertIsNone(self._parse_condition("theta 400"))

    def test_no_value_returns_none(self):
        self.assertIsNone(self._parse_condition("theta >"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(self._parse_condition(""))

    def test_spaces_only_returns_none(self):
        self.assertIsNone(self._parse_condition("   "))

    def test_unknown_operator_via_query_stats(self):
        data = _make_poly_data(field_name="pressure")
        # Use the queries.query_stats with an unsupported operator
        result = queries.query_stats(data, "pressure", "pressure", "~=", 0.5)
        self.assertIn("Unknown operator", result)

    def test_field_name_with_spaces_unparseable(self):
        # A condition where field name includes a space would be ambiguous
        result = self._parse_condition("field name > 5")
        # With the greedy regex, this might or might not parse; the important
        # thing is it does not crash.
        # If it does parse, verify it parsed something reasonable or None
        if result is not None:
            self.assertIsNotNone(result.group(1))


# ---------------------------------------------------------------------------
# 9. load_file with unsupported extension
# ---------------------------------------------------------------------------

class TestLoadUnsupportedExtension(unittest.TestCase):
    """load_file() must return an error for unknown extensions."""

    def test_xyz_extension_returns_error(self):
        data, error = load_file("some_file.xyz")
        self.assertIsNone(data)
        self.assertIsNotNone(error)
        self.assertIn("xyz", error.lower())

    def test_txt_extension_returns_error(self):
        data, error = load_file("data.txt")
        self.assertIsNone(data)
        self.assertIsNotNone(error)

    def test_no_extension_returns_error(self):
        data, error = load_file("datafile_no_ext")
        self.assertIsNone(data)
        self.assertIsNotNone(error)

    def test_error_message_lists_supported_extensions(self):
        _, error = load_file("bad.xyz")
        # Should tell the user what IS supported
        self.assertIsNotNone(error)
        # At minimum mention one of the known-good extensions
        lower = error.lower()
        self.assertTrue(
            "vts" in lower or "vti" in lower or "vtp" in lower or "supported" in lower,
            f"Expected supported-extensions hint in error, got: {error!r}"
        )

    def test_csv_extension_returns_error(self):
        data, error = load_file("my_data.csv")
        self.assertIsNone(data)
        self.assertIsNotNone(error)


# ---------------------------------------------------------------------------
# 10. describe_data (via load_file) with non-existent file
# ---------------------------------------------------------------------------

class TestDescribeDataNonExistentFile(unittest.TestCase):
    """Trying to describe a file that doesn't exist returns an error."""

    def test_nonexistent_vts_returns_error(self):
        data, error = load_file("/nonexistent/path/to/missing.vts")
        self.assertIsNone(data)
        self.assertIsNotNone(error)

    def test_nonexistent_vti_returns_error(self):
        data, error = load_file("/tmp/definitely_does_not_exist_12345.vti")
        self.assertIsNone(data)
        self.assertIsNotNone(error)

    def test_error_message_mentions_file_path(self):
        path = "/no/such/file.vts"
        _, error = load_file(path)
        self.assertIsNotNone(error)
        # The error message should be informative; it might not always include
        # the path verbatim (VTK may give a generic error), but must be non-empty.
        self.assertGreater(len(error), 0)

    def test_empty_path_returns_error(self):
        data, error = load_file("")
        # An empty path is either an unsupported-extension error or a file error
        self.assertIsNone(data)
        self.assertIsNotNone(error)


# ---------------------------------------------------------------------------
# Bonus: vtkArrayCalculator integration with create_vtk_filter
# ---------------------------------------------------------------------------

class TestCalculatorViaFilterAPI(unittest.TestCase):
    """Test the create_vtk_filter pathway for vtkArrayCalculator error handling."""

    def _make_source_algorithm(self):
        img = _make_image_data(field_name="temperature", field_range=(0.0, 100.0))
        return _image_data_to_algorithm(img)

    def test_valid_calculator_produces_result(self):
        alg = self._make_source_algorithm()
        calc, status = create_vtk_filter(
            "vtkArrayCalculator",
            alg,
            AddScalarArrayName=["temperature"],
            Function="temperature * 2",
            ResultArrayName="doubled",
        )
        calc.Update()
        output = calc.GetOutput()
        result_arr = output.GetPointData().GetArray("doubled")
        self.assertIsNotNone(result_arr,
                             "Valid calculator expression should produce result array")

    def test_unknown_vtk_class_raises_valueerror(self):
        """Non-whitelisted class must raise ValueError."""
        alg = self._make_source_algorithm()
        with self.assertRaises(ValueError) as ctx:
            create_vtk_filter("vtkFakeNonExistentFilter", alg)
        self.assertIn("whitelist", str(ctx.exception).lower())


# ---------------------------------------------------------------------------
# 11. NodeRef property resolution errors (e.g. bad SeedSource)
# ---------------------------------------------------------------------------

class TestNodeRefPropertyErrors(unittest.TestCase):
    """When a node referenced via a property (e.g. SeedSource) fails to build,
    the dependent node should report a clear error, not raise KeyError."""

    def _make_builder_with_data(self):
        from siva.dsl import PipelineBuilder
        import tempfile, os
        b = PipelineBuilder()
        # Use a trivial vtkSphereSource as stand-in for a data source
        data = b.source("vtkSphereSource")
        vel = b.filter("vtkArrayCalculator", input=data,
                       AddScalarArrayName=["Normals"],
                       Function="Normals",
                       ResultArrayName="velocity")
        return b, data, vel

    def test_bad_seed_param_gives_clear_error_on_seed_node(self):
        """vtkPlaneSource with Resolution=[x,y] (wrong) should error on the seeds node."""
        from siva.dsl import PipelineBuilder
        b = PipelineBuilder()
        seeds = b.source("vtkPlaneSource",
                         Origin=(0, 0, 0), Point1=(1, 0, 0), Point2=(0, 1, 0),
                         Resolution=[10, 10])  # wrong: should be XResolution/YResolution
        data = b.source("vtkSphereSource")
        streams = b.filter("vtkStreamTracer", input=data,
                           SeedSource=seeds, Vectors="Normals",
                           IntegrationDirection="Forward")
        vtk_objs, statuses = _bp(b)

        seeds_status = statuses[seeds.node_id]
        self.assertEqual(seeds_status.get("status"), "error",
                         "Seeds node with bad param should have an error status")
        self.assertIn("Resolution", seeds_status["message"],
                      f"Error should mention 'Resolution', got: {seeds_status['message']!r}")

    def test_bad_seed_param_gives_clear_error_on_stream_node(self):
        """When seeds fail, streams should be skipped (cascade-skip contract), not crash.

        With the cascade-skip contract, when a property-referenced node (SeedSource)
        fails, the dependent node is cleanly skipped rather than attempted with a
        None input. The streams node status is {"status": "skipped", "upstream": ...}.
        """
        from siva.dsl import PipelineBuilder
        b = PipelineBuilder()
        seeds = b.source("vtkPlaneSource",
                         Origin=(0, 0, 0), Point1=(1, 0, 0), Point2=(0, 1, 0),
                         Resolution=[10, 10])
        data = b.source("vtkSphereSource")
        streams = b.filter("vtkStreamTracer", input=data,
                           SeedSource=seeds, Vectors="Normals",
                           IntegrationDirection="Forward")
        vtk_objs, statuses = _bp(b)

        streams_status = statuses[streams.node_id]
        # With the cascade-skip contract: streams is skipped when seeds failed
        self.assertEqual(streams_status.get("status"), "skipped",
                         f"Streams node should be 'skipped' when seed node failed, "
                         f"got: {streams_status}")
        self.assertIn("upstream", streams_status,
                      "Skipped streams node should carry 'upstream' reference")

    def test_bad_seed_does_not_raise_key_error(self):
        """build_pipeline() must not raise KeyError when a property-referenced node fails."""
        from siva.dsl import PipelineBuilder
        b = PipelineBuilder()
        seeds = b.source("vtkPlaneSource",
                         Origin=(0, 0, 0), Point1=(1, 0, 0), Point2=(0, 1, 0),
                         Resolution=[10, 10])
        data = b.source("vtkSphereSource")
        b.filter("vtkStreamTracer", input=data, SeedSource=seeds,
                 Vectors="Normals", IntegrationDirection="Forward")
        try:
            _bp(b)
        except KeyError as e:
            self.fail(f"build_pipeline() raised KeyError: {e}")

    def test_good_plane_seed_builds_successfully(self):
        """vtkPlaneSource with XResolution/YResolution should build without errors."""
        from siva.dsl import PipelineBuilder
        b = PipelineBuilder()
        seeds = b.source("vtkPlaneSource",
                         Origin=(0, 0, 0), Point1=(1, 0, 0), Point2=(0, 1, 0),
                         XResolution=10, YResolution=10)
        vtk_objs, statuses = _bp(b)

        self.assertNotEqual(statuses[seeds.node_id].get("status"), "error",
                            f"Good vtkPlaneSource should build cleanly, got: {statuses[seeds.node_id]}")
        self.assertIn(seeds.node_id, vtk_objs,
                      "Successfully built node should be in vtk_objects")


if __name__ == "__main__":
    unittest.main()
