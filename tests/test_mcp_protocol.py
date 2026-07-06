"""MCP protocol-level smoke tests for all @mcp.tool() decorated functions.

Every tool defined in siva/server.py is called with minimal valid inputs.
The goal is coverage: every tool can be invoked without raising an exception,
and its return value matches the declared return type (str, list, or Image).

Tests are grouped into three classes matching the server's own QUERY_TOOLS /
MUTATION_TOOLS / META_TOOLS lists.

No actual rendering window is created — the _NoOpRenderer stub from
_init_for_test() is used throughout.  Rendering-dependent tools (screenshot,
camera_orbit, etc.) call through to the stub which does nothing.
"""

import os
import sys
import tempfile
import unittest

import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk
from mcp.server.fastmcp import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import siva.server as srv
from siva.renderer import RenderMode


# ---------------------------------------------------------------------------
# Enhanced no-op renderer stub
# ---------------------------------------------------------------------------

class _FullNoOpRenderer:
    """A richer no-op renderer stub that satisfies all tool calls.

    Extends the minimal _NoOpRenderer in _init_for_test() with the attributes
    accessed by tools that manipulate actors, background, and camera suggestions.
    """

    mode = RenderMode.OFFSCREEN
    camera_positioned = False

    def render(self):
        pass

    def dispatch(self, fn):
        return fn()

    def screenshot(self, path):
        return path

    def clear(self):
        pass

    def set_size(self, width, height):
        pass

    def get_size(self):
        return (1920, 1080)

    def get_camera_state(self):
        return {"position": [0, 0, 1], "focal_point": [0, 0, 0], "up": [0, 1, 0]}

    def set_camera(self, **kwargs):
        pass

    def set_background(self, r, g, b):
        pass

    def suggest_camera(self, style="overview"):
        # Return None to simulate empty scene (no actors)
        return None

    def destroy(self):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vti(nx=8, ny=8, nz=8):
    """Return a vtkImageData with temperature (scalar) and velocity (vector)."""
    img = vtk.vtkImageData()
    img.SetDimensions(nx, ny, nz)
    img.SetOrigin(0, 0, 0)
    img.SetSpacing(1, 1, 1)
    n = img.GetNumberOfPoints()

    temp = numpy_to_vtk(np.linspace(273.0, 1500.0, n, dtype=np.float32))
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


def _write_vti(path):
    """Write a minimal VTI file to *path*."""
    w = vtk.vtkXMLImageDataWriter()
    w.SetFileName(path)
    w.SetInputData(_make_vti())
    w.Write()


def _make_reader_source(data):
    """Wrap vtkImageData as an algorithm for the server pipeline."""
    tmp = tempfile.NamedTemporaryFile(suffix=".vti", delete=False)
    tmp.close()
    w = vtk.vtkXMLImageDataWriter()
    w.SetFileName(tmp.name)
    w.SetInputData(data)
    w.Write()
    reader = vtk.vtkXMLImageDataReader()
    reader.SetFileName(tmp.name)
    reader.Update()
    os.unlink(tmp.name)
    return reader


def _reset_with_data():
    """Reset server state and load a minimal VTI dataset. Returns ViewContext."""
    ctx = srv._init_for_test(renderer=_FullNoOpRenderer())
    reader = _make_reader_source(_make_vti())
    ctx.vtk_objects = {"data": reader}
    ctx.current_code = ""
    return ctx


def _reset_empty():
    """Reset server to an empty state with no pipeline."""
    return srv._init_for_test(renderer=_FullNoOpRenderer())


def _is_str_or_list(val):
    """Return True if val is a str or a non-empty list."""
    return isinstance(val, (str, list))


def _first_str(val):
    """Return the first string element from a list, or val itself if already str."""
    if isinstance(val, list):
        strs = [x for x in val if isinstance(x, str)]
        return strs[0] if strs else ""
    return val if isinstance(val, str) else ""


# ---------------------------------------------------------------------------
# QUERY TOOLS
# ---------------------------------------------------------------------------

class TestQueryToolsMCP(unittest.TestCase):
    """Call every QUERY_TOOLS function with minimal valid arguments.

    For tools that need data, a small vtkImageData is loaded first.
    For tools that don't, the empty-pipeline error path is also exercised.
    """

    def setUp(self):
        _reset_with_data()

    # describe_data --------------------------------------------------------

    def test_describe_data_no_args(self):
        result = srv.describe_data()
        self.assertIsInstance(result, str)
        self.assertIn("Points", result)

    def test_describe_data_with_node(self):
        result = srv.describe_data(node="data")
        self.assertIsInstance(result, str)
        self.assertIn("temperature", result)

    def test_describe_data_no_pipeline(self):
        _reset_empty()
        result = srv.describe_data()
        self.assertIsInstance(result, str)

    # describe_data(field=) ------------------------------------------------

    def test_describe_data_single_field(self):
        result = srv.describe_data(node="data", field="temperature")
        self.assertIsInstance(result, str)
        self.assertTrue(any(c.isdigit() for c in result))

    def test_describe_data_missing_field(self):
        result = srv.describe_data(node="data", field="no_such_field")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_describe_data_missing_node(self):
        result = srv.describe_data(node="ghost", field="temperature")
        self.assertIsInstance(result, str)
        self.assertIn("ghost", result)

    # query_stats ----------------------------------------------------------

    def test_query_stats_valid(self):
        result = srv.query_stats("data", "temperature", "temperature > 500")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_query_stats_invalid_condition(self):
        result = srv.query_stats("data", "temperature", "not valid syntax")
        self.assertIsInstance(result, str)
        self.assertIn("parse", result.lower())

    def test_query_stats_missing_node(self):
        result = srv.query_stats("ghost", "temperature", "temperature > 500")
        self.assertIsInstance(result, str)

    # get_histogram --------------------------------------------------------

    def test_get_histogram(self):
        result = srv.get_histogram("data", "temperature")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_get_histogram_missing_field(self):
        result = srv.get_histogram("data", "nosuchfield")
        self.assertIsInstance(result, str)

    # get_spatial_extent ---------------------------------------------------

    def test_get_spatial_extent(self):
        result = srv.get_spatial_extent("data", "temperature", 273.0, 1500.0)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_get_spatial_extent_includes_grid_indices(self):
        result = srv.get_spatial_extent("data", "temperature", 273.0, 1500.0)
        self.assertIsInstance(result, str)
        self.assertIn("Grid indices", result)

    def test_get_spatial_extent_impossible_range(self):
        result = srv.get_spatial_extent("data", "temperature", 9999.0, 99999.0)
        self.assertIsInstance(result, str)

    # sample_points --------------------------------------------------------

    def test_sample_points_single(self):
        result = srv.sample_points("data", [[2.0, 2.0, 2.0]])
        self.assertIsInstance(result, str)

    def test_sample_points_multiple(self):
        result = srv.sample_points("data", [[1.0, 1.0, 1.0], [3.0, 3.0, 3.0]])
        self.assertIsInstance(result, str)

    def test_sample_points_empty_list(self):
        result = srv.sample_points("data", [])
        self.assertIsInstance(result, str)
        self.assertIn("No points", result)

    def test_sample_points_bad_shape(self):
        result = srv.sample_points("data", [[1.0, 2.0]])  # 2 coords not 3
        self.assertIsInstance(result, str)
        self.assertIn("3", result)

    def test_sample_points_with_field_filter(self):
        result = srv.sample_points("data", [[2.0, 2.0, 2.0]], fields=["temperature"])
        self.assertIsInstance(result, str)

    # profile --------------------------------------------------------------

    def test_profile(self):
        result = srv.profile("data", [0.0, 0.0, 0.0], [7.0, 7.0, 7.0],
                             fields=["temperature"], resolution=20)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_profile_bad_point_length(self):
        result = srv.profile("data", [0.0, 0.0], [7.0, 7.0, 7.0],
                             fields=["temperature"])
        self.assertIsInstance(result, str)
        self.assertIn("3", result)

    # get_ground_z ---------------------------------------------------------

    def test_get_ground_z_image_data_returns_error(self):
        # get_ground_z is only valid for vtkStructuredGrid
        result = srv.get_ground_z("data", 2.0, 2.0)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    # suggest_isosurface ---------------------------------------------------

    def test_suggest_isosurface(self):
        result = srv.suggest_isosurface("data", "temperature")
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_suggest_isosurface_more_values(self):
        result = srv.suggest_isosurface("data", "temperature", num_values=5)
        self.assertIsInstance(result, str)

    # set_suggested_camera -------------------------------------------------

    def test_suggest_camera_overview(self):
        result = srv.set_suggested_camera("overview")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_suggest_camera_default(self):
        result = srv.set_suggested_camera()
        self.assertIsInstance(result, list)

    # get_camera -----------------------------------------------------------

    def test_get_camera(self):
        result = srv.get_camera()
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


# ---------------------------------------------------------------------------
# MUTATION TOOLS
# ---------------------------------------------------------------------------

class TestMutationToolsMCP(unittest.TestCase):
    """Call every MUTATION_TOOLS function with minimal valid arguments."""

    def setUp(self):
        _reset_with_data()

    # load -----------------------------------------------------------------

    def test_load_valid_file(self):
        with tempfile.NamedTemporaryFile(suffix=".vti", delete=False) as f:
            tmp = f.name
        pipeline_file = srv._current_ctx().pipeline_file
        existed = os.path.exists(pipeline_file)
        if existed:
            os.rename(pipeline_file, pipeline_file + ".bak")
        try:
            _write_vti(tmp)
            result = srv.load(tmp)
            self.assertIsInstance(result, str)
            self.assertIn("Points", result)
            if os.path.exists(pipeline_file):
                os.unlink(pipeline_file)
        finally:
            os.unlink(tmp)
            if existed:
                os.rename(pipeline_file + ".bak", pipeline_file)

    def test_load_existing_pipeline_file(self):
        pipeline_file = srv._current_ctx().pipeline_file
        with tempfile.NamedTemporaryFile(suffix=".vti", delete=False) as f:
            tmp = f.name
        try:
            _write_vti(tmp)
            with open(pipeline_file, "w") as pf:
                pf.write("# existing pipeline\n")
            result = srv.load(tmp)
            self.assertIsInstance(result, str)
            self.assertIn("already exists", result)
        finally:
            os.unlink(tmp)
            if os.path.exists(pipeline_file):
                os.unlink(pipeline_file)

    def test_load_nonexistent_file(self):
        result = srv.load("/tmp/__siva_no_such_99999.vti")
        self.assertIsInstance(result, str)
        self.assertIn("not found", result.lower())

    def test_load_unsupported_extension(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            tmp = f.name
        try:
            result = srv.load(tmp)
            self.assertIsInstance(result, str)
            self.assertIn("unsupported", result.lower())
        finally:
            os.unlink(tmp)

    # wait_for_pipeline ---------------------------------------------------------

    def test_wait_for_pipeline_missing_file(self):
        _reset_empty()
        import os
        pipeline_file = srv._current_ctx().pipeline_file
        existed = os.path.exists(pipeline_file)
        if existed:
            os.rename(pipeline_file, pipeline_file + ".bak")
        try:
            result = srv.wait_for_pipeline()
            self.assertTrue(_is_str_or_list(result))
            self.assertIn("not found", _first_str(result).lower())
        finally:
            if existed:
                os.rename(pipeline_file + ".bak", pipeline_file)

    # set_camera -----------------------------------------------------------

    def test_set_camera_with_position(self):
        result = srv.set_camera(position=[100.0, -500.0, 200.0])
        self.assertIsInstance(result, list)
        self.assertIsInstance(result[0], str)
        self.assertIn("Camera", result[0])

    def test_set_camera_no_args(self):
        result = srv.set_camera()
        self.assertIsInstance(result, list)
        # Should say to specify at least one param
        self.assertIn("at least", result[0].lower())

    def test_set_camera_with_zoom(self):
        result = srv.set_camera(zoom=1.5)
        self.assertIsInstance(result, list)

    # set_window_size ------------------------------------------------------

    def test_set_window_size(self):
        # The fake renderer implements set_size/get_size, so this should
        # return normally. We just verify the return type.
        result = srv.set_window_size(1920, 1080)
        self.assertTrue(_is_str_or_list(result))


# ---------------------------------------------------------------------------
# META TOOLS
# ---------------------------------------------------------------------------

class TestMetaToolsMCP(unittest.TestCase):
    """Call every META_TOOLS function with minimal valid arguments."""

    def setUp(self):
        _reset_with_data()

    # screenshot -----------------------------------------------------------

    def test_screenshot(self):
        result = srv.screenshot()
        # Should return an Image (path stub returns path string, Image wraps it)
        self.assertIsInstance(result, Image)

    # camera_orbit ---------------------------------------------------------

    def test_camera_orbit_minimal(self):
        result = srv.camera_orbit(n_frames=2)
        self.assertIsInstance(result, list)
        # [description, Image, description, Image]
        self.assertEqual(len(result), 4)

    def test_camera_orbit_clamped(self):
        result = srv.camera_orbit(n_frames=100, elevation=200.0)
        self.assertIsInstance(result, list)
        # Clamped to 16 frames
        self.assertEqual(len(result), 32)

    # list_versions --------------------------------------------------------

    def test_list_versions_none(self):
        result = srv.list_versions()
        self.assertIsInstance(result, str)
        # Should say "No versions" or similar
        self.assertGreater(len(result), 0)

    # restore_version ------------------------------------------------------

    def test_restore_version_nonexistent(self):
        result = srv.restore_version(999)
        self.assertTrue(_is_str_or_list(result))
        first = _first_str(result)
        self.assertGreater(len(first), 0)

    # export_standalone ----------------------------------------------------

    # get_dsl_overview -----------------------------------------------------

    def test_get_dsl_overview(self):
        result = srv.get_dsl_overview()
        self.assertIsInstance(result, str)
        self.assertIn("SIVA DSL Overview", result)
        self.assertIn("fieldname", result)

    def test_get_dsl_overview_includes_key_forms(self):
        result = srv.get_dsl_overview()
        for form in ["threshold", "contour", "stream_tracer", "show"]:
            self.assertIn(form, result, f"Expected '{form}' in DSL overview")

    # list_data_files ------------------------------------------------------

    def test_list_data_files_returns_string(self):
        result = srv.list_data_files()
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    # get_dsl_reference ----------------------------------------------------

    def test_get_dsl_reference_known_form(self):
        result = srv.get_dsl_reference("show")
        self.assertIsInstance(result, str)
        self.assertIn("show", result)

    def test_get_dsl_reference_unknown_form(self):
        result = srv.get_dsl_reference("not_a_real_form_xyz")
        self.assertIsInstance(result, str)
        # Should list available forms
        self.assertGreater(len(result), 50)

    def test_get_dsl_reference_case_insensitive(self):
        result = srv.get_dsl_reference("SHOW")
        self.assertIsInstance(result, str)
        self.assertIn("show", result.lower())

    def test_get_dsl_reference_threshold(self):
        result = srv.get_dsl_reference("threshold")
        self.assertIsInstance(result, str)
        self.assertIn("threshold", result.lower())

    def test_get_dsl_reference_extract_component(self):
        result = srv.get_dsl_reference("extract_component")
        self.assertIsInstance(result, str)
        self.assertIn("extract_component", result)

    # new_view / focus / close_view / list_views ---------------------------

    def test_new_view_creates_and_switches(self):
        result = srv.new_view("test_view")
        self.assertIsInstance(result, list)
        self.assertIn("test_view", result[0])

    def test_new_view_duplicate_name(self):
        srv.new_view("dupe_view")
        result = srv.new_view("dupe_view")
        self.assertIsInstance(result, list)
        self.assertIn("already exists", result[0])

    def test_list_views(self):
        result = srv.list_views()
        self.assertIsInstance(result, str)
        self.assertIn("main", result)

    def test_focus_valid_view(self):
        result = srv.focus("main")
        # Returns str or [str, Image]
        self.assertTrue(_is_str_or_list(result))

    def test_focus_invalid_view(self):
        result = srv.focus("does_not_exist")
        self.assertIsInstance(result, str)
        self.assertIn("not found", result.lower())

    def test_close_view_last_view(self):
        result = srv.close_view("main")
        self.assertIsInstance(result, str)
        self.assertIn("only remaining", result)

    def test_close_view_nonexistent(self):
        result = srv.close_view("ghost_view")
        self.assertIsInstance(result, str)
        self.assertIn("not found", result.lower())

    def test_close_view_second_view(self):
        srv.new_view("temp_view")
        srv.focus("main")  # switch back to main
        result = srv.close_view("temp_view")
        self.assertIsInstance(result, str)
        self.assertIn("Closed", result)

# ---------------------------------------------------------------------------
# Return-type invariants — verify declared return types are honoured
# ---------------------------------------------------------------------------

class TestReturnTypeInvariants(unittest.TestCase):
    """Verify that each tool's return type matches its declared annotation.

    QUERY_TOOLS declare -> str.
    MUTATION_TOOLS that use _with_screenshot declare -> list[str | Image].
    META_TOOLS vary; we check the critical ones.
    """

    def setUp(self):
        _reset_with_data()

    def _assert_str(self, result, tool_name):
        self.assertIsInstance(result, str, f"{tool_name} should return str, got {type(result)}")

    def _assert_list(self, result, tool_name):
        self.assertIsInstance(result, list, f"{tool_name} should return list, got {type(result)}")
        self.assertGreater(len(result), 0, f"{tool_name} returned empty list")
        self.assertIsInstance(result[0], str, f"{tool_name} list[0] should be str")

    def test_describe_data_returns_str(self):
        self._assert_str(srv.describe_data(), "describe_data")

    def test_describe_data_field_returns_str(self):
        self._assert_str(srv.describe_data(node="data", field="temperature"), "describe_data(field=)")

    def test_get_histogram_returns_str(self):
        self._assert_str(srv.get_histogram("data", "temperature"), "get_histogram")

    def test_get_dsl_overview_returns_str(self):
        self._assert_str(srv.get_dsl_overview(), "get_dsl_overview")

    def test_get_dsl_reference_returns_str(self):
        self._assert_str(srv.get_dsl_reference("show"), "get_dsl_reference")

    def test_list_versions_returns_str(self):
        self._assert_str(srv.list_versions(), "list_versions")

    def test_list_views_returns_str(self):
        self._assert_str(srv.list_views(), "list_views")

    def test_screenshot_returns_image(self):
        result = srv.screenshot()
        self.assertIsInstance(result, Image, "screenshot should return Image")


class TestToolListConsistency(unittest.TestCase):
    """Verify QUERY_TOOLS / MUTATION_TOOLS / META_TOOLS match actual @mcp.tool() definitions."""

    def test_tool_lists_match_registered_tools(self):
        declared = set(srv.QUERY_TOOLS + srv.MUTATION_TOOLS + srv.META_TOOLS)
        registered = set(srv.mcp._tool_manager._tools.keys())
        missing_from_registered = declared - registered
        missing_from_lists = registered - declared
        self.assertEqual(
            missing_from_registered, set(),
            f"Tools in lists but not registered as @mcp.tool(): {missing_from_registered}"
        )
        self.assertEqual(
            missing_from_lists, set(),
            f"Tools registered as @mcp.tool() but missing from lists: {missing_from_lists}"
        )


if __name__ == "__main__":
    unittest.main()
