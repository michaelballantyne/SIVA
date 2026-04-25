"""Smoke tests for server-layer MCP tool functions in vislang/server.py.

These tests import server.py by mocking out the 'mcp' package and
vislang.renderer, following the same pattern as test_auto_screenshot.py.
After mocking, the @mcp.tool() decorated functions become regular callables.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import vtk
from vtk.util.numpy_support import numpy_to_vtk
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Server import helpers
# ---------------------------------------------------------------------------

import vislang.server as srv  # noqa: E402  (after stub)


# ---------------------------------------------------------------------------
# VTI fixture helpers (shared across tests)
# ---------------------------------------------------------------------------

def _make_vti_with_fields(nx=4, ny=4, nz=4):
    """Build a vtkImageData with a scalar 'temperature' and a vector 'velocity'."""
    img = vtk.vtkImageData()
    img.SetDimensions(nx, ny, nz)
    img.SetOrigin(0, 0, 0)
    img.SetSpacing(1, 1, 1)
    n = img.GetNumberOfPoints()

    temp = numpy_to_vtk(np.linspace(273.0, 373.0, n, dtype=np.float32))
    temp.SetName("temperature")
    img.GetPointData().AddArray(temp)

    vel = numpy_to_vtk(
        np.column_stack([
            np.ones(n, dtype=np.float32),
            np.zeros(n, dtype=np.float32),
            np.zeros(n, dtype=np.float32),
        ])
    )
    vel.SetNumberOfComponents(3)
    vel.SetName("velocity")
    img.GetPointData().AddArray(vel)

    return img


def _write_vti(path, nx=4, ny=4, nz=4):
    img = _make_vti_with_fields(nx, ny, nz)
    w = vtk.vtkXMLImageDataWriter()
    w.SetFileName(path)
    w.SetInputData(img)
    w.Write()


def _make_reader_source(data):
    """Write data to a temp file and return a VTK reader algorithm.

    The server's _get_data() calls obj.Update() + obj.GetOutput(), which requires
    an actual VTK algorithm (reader). vtkTrivialProducer lacks GetOutput() in
    this VTK version, so we write to disk and read back.
    """
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".vti", delete=False)
    tmp.close()
    w = vtk.vtkXMLImageDataWriter()
    w.SetFileName(tmp.name)
    w.SetInputData(data)
    w.Write()
    reader = vtk.vtkXMLImageDataReader()
    reader.SetFileName(tmp.name)
    reader.Update()
    # Clean up temp file (reader has already read it into memory)
    os.unlink(tmp.name)
    return reader


# ---------------------------------------------------------------------------
# Helper: reset server global state
# ---------------------------------------------------------------------------

def _reset_server(vtk_objects=None):
    """Reset server pipeline state to a clean slate via _init_for_test().

    Returns the ViewContext so callers can inspect or mutate state through
    ctx.vtk_objects, etc.
    """
    ctx = srv._init_for_test()
    ctx.vtk_objects = vtk_objects if vtk_objects is not None else {}
    ctx.current_code = ""
    return ctx


# ---------------------------------------------------------------------------
# Tests: load()
# ---------------------------------------------------------------------------

class TestLoadTool(unittest.TestCase):

    def setUp(self):
        _reset_server()

    def test_load_valid_vti_returns_describe_data(self):
        """load() with a valid VTI file should return a describe_data overview."""
        with tempfile.NamedTemporaryFile(suffix=".vti", delete=False) as f:
            tmppath = f.name
        try:
            _write_vti(tmppath)
            result = srv.load(tmppath)
            # Should have loaded and returned describe_data output
            self.assertIsInstance(result, str)
            self.assertIn("Points", result)
            # Node 'data' should now be in the current context's vtk_objects
            self.assertIn("data", srv._current_ctx().vtk_objects)
        finally:
            os.unlink(tmppath)

    def test_load_nonexistent_file_returns_error(self):
        """load() with a non-existent file should return a descriptive error string."""
        result = srv.load("/tmp/__vislang_no_such_file_9999.vti")
        self.assertIsInstance(result, str)
        self.assertIn("not found", result.lower())

    def test_load_unsupported_extension_returns_error(self):
        """load() with an unsupported extension should explain what is supported."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            tmppath = f.name
        try:
            result = srv.load(tmppath)
            self.assertIsInstance(result, str)
            self.assertIn("unsupported", result.lower())
            self.assertIn(".csv", result)
        finally:
            os.unlink(tmppath)

    def test_load_stores_reader_algorithm(self):
        """After a successful load, vtk_objects['data'] should be a VTK algorithm."""
        with tempfile.NamedTemporaryFile(suffix=".vti", delete=False) as f:
            tmppath = f.name
        try:
            _write_vti(tmppath)
            srv.load(tmppath)
            reader = srv._current_ctx().vtk_objects.get("data")
            self.assertIsNotNone(reader)
            self.assertTrue(hasattr(reader, "Update"), "Stored object must have Update()")
            self.assertTrue(hasattr(reader, "GetOutput"), "Stored object must have GetOutput()")
        finally:
            os.unlink(tmppath)


# ---------------------------------------------------------------------------
# Tests: describe_data()
# ---------------------------------------------------------------------------

class TestDescribeData(unittest.TestCase):

    def setUp(self):
        data = _make_vti_with_fields()
        reader = _make_reader_source(data)
        _reset_server({"data": reader})

    def test_describe_data_no_args_uses_active_pipeline(self):
        """describe_data() with no args should use the active pipeline."""
        result = srv.describe_data()
        self.assertIsInstance(result, str)
        self.assertIn("Points", result)
        self.assertIn("temperature", result)

    def test_describe_data_with_file_path(self):
        """describe_data(file_path=...) should load and describe without a pipeline."""
        with tempfile.NamedTemporaryFile(suffix=".vti", delete=False) as f:
            tmppath = f.name
        try:
            _write_vti(tmppath)
            _reset_server()  # no pipeline
            result = srv.describe_data(file_path=tmppath)
            self.assertIsInstance(result, str)
            self.assertIn("Points", result)
            self.assertIn("temperature", result)
        finally:
            os.unlink(tmppath)

    def test_describe_data_no_pipeline_returns_helpful_error(self):
        """describe_data() with no pipeline should return a helpful error message."""
        _reset_server()  # clear all state
        result = srv.describe_data()
        self.assertIsInstance(result, str)
        # Should explain there is no pipeline
        self.assertTrue(
            "pipeline" in result.lower() or "no pipeline" in result.lower(),
            f"Expected helpful error, got: {result!r}"
        )


# ---------------------------------------------------------------------------
# Tests: describe_data() with field argument
# ---------------------------------------------------------------------------

class TestDescribeDataWithField(unittest.TestCase):

    def setUp(self):
        data = _make_vti_with_fields()
        reader = _make_reader_source(data)
        _reset_server({"data": reader})

    def test_describe_data_valid_field(self):
        """describe_data() with a field should return min, max and statistical info."""
        result = srv.describe_data(node="data", field="temperature")
        self.assertIsInstance(result, str)
        self.assertTrue(
            any(c.isdigit() for c in result),
            f"Expected numeric output, got: {result!r}"
        )

    def test_describe_data_missing_node_returns_error(self):
        """describe_data() on a missing node should return an error."""
        result = srv.describe_data(node="nonexistent", field="temperature")
        self.assertIsInstance(result, str)
        self.assertIn("nonexistent", result)

    def test_describe_data_missing_field_returns_error(self):
        """describe_data() on a non-existent field should return an error."""
        result = srv.describe_data(node="data", field="no_such_field")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


# ---------------------------------------------------------------------------
# Tests: query_stats()
# ---------------------------------------------------------------------------

class TestQueryStats(unittest.TestCase):

    def setUp(self):
        data = _make_vti_with_fields()
        reader = _make_reader_source(data)
        _reset_server({"data": reader})

    def test_query_stats_valid_condition(self):
        """query_stats() with a valid condition should return statistics."""
        result = srv.query_stats("data", "temperature", "temperature > 290")
        self.assertIsInstance(result, str)
        # Should have some output
        self.assertGreater(len(result), 0)

    def test_query_stats_invalid_condition_returns_error(self):
        """query_stats() with an unparseable condition should explain the problem."""
        result = srv.query_stats("data", "temperature", "this is not valid")
        self.assertIsInstance(result, str)
        self.assertIn("parse", result.lower())

    def test_query_stats_all_operators(self):
        """query_stats() should accept all comparison operators."""
        for op in [">", "<", ">=", "<=", "==", "!="]:
            result = srv.query_stats("data", "temperature", f"temperature {op} 300")
            self.assertIsInstance(result, str)
            # Should not contain a parse error
            self.assertNotIn("Could not parse condition", result)

    def test_query_stats_missing_node_returns_error(self):
        """query_stats() on a missing node should return an error."""
        result = srv.query_stats("ghost", "temperature", "temperature > 300")
        self.assertIsInstance(result, str)
        self.assertIn("ghost", result)


# ---------------------------------------------------------------------------
# Tests: sample_points()
# ---------------------------------------------------------------------------

class TestSamplePoints(unittest.TestCase):

    def setUp(self):
        data = _make_vti_with_fields(nx=8, ny=8, nz=8)
        reader = _make_reader_source(data)
        _reset_server({"data": reader})

    def test_sample_points_single_point(self):
        """sample_points() with a single point inside the grid should return values."""
        result = srv.sample_points("data", [[2.0, 2.0, 2.0]])
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_sample_points_multiple_points(self):
        """sample_points() with multiple points should return a block per point."""
        result = srv.sample_points("data", [[1.0, 1.0, 1.0], [3.0, 3.0, 3.0]])
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_sample_points_with_field_filter(self):
        """sample_points() with fields argument should limit output to those fields."""
        result = srv.sample_points("data", [[2.0, 2.0, 2.0]], fields=["temperature"])
        self.assertIsInstance(result, str)
        self.assertIn("temperature", result)

    def test_sample_points_empty_list_returns_message(self):
        """sample_points() with empty list should return a helpful message."""
        result = srv.sample_points("data", [])
        self.assertIsInstance(result, str)
        self.assertIn("No points", result)

    def test_sample_points_bad_point_shape_returns_error(self):
        """sample_points() with wrong-length point should return error."""
        result = srv.sample_points("data", [[1.0, 2.0]])  # only 2 coords
        self.assertIsInstance(result, str)
        self.assertIn("3", result)  # should mention 3 values expected


# ---------------------------------------------------------------------------
# Tests: get_dsl_reference() — extract_component is a DSL form only
# ---------------------------------------------------------------------------

class TestExtractComponent(unittest.TestCase):
    """extract_component is a DSL-only form (not an MCP tool).
    These tests verify it is documented via get_dsl_reference()."""

    def setUp(self):
        _reset_server()

    def test_extract_x_component_by_index(self):
        """get_dsl_reference('extract_component') should return its reference docs."""
        result = srv.get_dsl_reference("extract_component")
        self.assertIsInstance(result, str)
        self.assertIn("extract_component", result)

    def test_extract_y_component_by_name(self):
        """get_dsl_reference should document component parameter."""
        result = srv.get_dsl_reference("extract_component")
        self.assertIsInstance(result, str)
        self.assertIn("component", result)

    def test_extract_component_custom_result_name(self):
        """get_dsl_reference should document result_name parameter."""
        result = srv.get_dsl_reference("extract_component")
        self.assertIsInstance(result, str)
        self.assertIn("result_name", result)

    def test_extract_component_invalid_component(self):
        """get_dsl_reference with unknown form should list available forms."""
        result = srv.get_dsl_reference("not_a_real_form_xyz")
        self.assertIsInstance(result, str)
        self.assertIn("extract_component", result)  # should appear in the list

    def test_extract_component_missing_node(self):
        """get_dsl_reference should include an example."""
        result = srv.get_dsl_reference("extract_component")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 50)

    def test_extract_component_missing_field(self):
        """get_dsl_reference should document field parameter."""
        result = srv.get_dsl_reference("extract_component")
        self.assertIsInstance(result, str)
        self.assertIn("field", result)


# ---------------------------------------------------------------------------
# Tests: internal helper _get_data_or_error()
# ---------------------------------------------------------------------------

class TestGetDataOrError(unittest.TestCase):

    def setUp(self):
        data = _make_vti_with_fields()
        reader = _make_reader_source(data)
        _reset_server({"data": reader})

    def test_returns_data_for_known_node(self):
        data, err = srv._get_data_or_error("data")
        self.assertIsNone(err)
        self.assertIsNotNone(data)

    def test_returns_error_for_unknown_node(self):
        data, err = srv._get_data_or_error("missing")
        self.assertIsNone(data)
        self.assertIsNotNone(err)
        self.assertIn("missing", err)

    def test_returns_error_when_no_pipeline(self):
        _reset_server()
        data, err = srv._get_data_or_error()
        self.assertIsNone(data)
        self.assertIsNotNone(err)

    def test_returns_first_source_when_empty_name(self):
        data, err = srv._get_data_or_error("")
        self.assertIsNone(err)
        self.assertIsNotNone(data)


# ---------------------------------------------------------------------------
# Tests: get_dsl_overview()
# ---------------------------------------------------------------------------

class TestGetDslOverview(unittest.TestCase):

    def test_overview_returned(self):
        """get_dsl_overview() should return the DSL overview guide."""
        _reset_server()
        result = srv.get_dsl_overview()
        self.assertIsInstance(result, str)
        # DSL overview header
        self.assertIn("VisLang DSL Overview", result)
        # Should mention placeholder fieldname
        self.assertIn("fieldname", result)

    def test_overview_with_pipeline_loaded(self):
        """get_dsl_overview() should return DSL overview even when a pipeline is active."""
        data = _make_vti_with_fields()
        reader = _make_reader_source(data)
        _reset_server({"data": reader})

        result = srv.get_dsl_overview()
        self.assertIsInstance(result, str)
        self.assertIn("VisLang DSL Overview", result)
        # Generic placeholder fieldname should still be present
        self.assertIn("fieldname", result)

    def test_overview_includes_form_index(self):
        """get_dsl_overview() should include DSL form index."""
        _reset_server()
        result = srv.get_dsl_overview()
        self.assertIn("threshold", result)
        self.assertIn("stream_tracer", result)
        self.assertIn("contour", result)

    def test_overview_includes_vtk_classes(self):
        """get_dsl_overview() should include VTK class listings."""
        _reset_server()
        result = srv.get_dsl_overview()
        self.assertIn("vtkContourFilter", result)

    def test_overview_includes_colormaps(self):
        """get_dsl_overview() should include colormap presets."""
        _reset_server()
        result = srv.get_dsl_overview()
        self.assertIn("fire", result)


if __name__ == "__main__":
    unittest.main()
