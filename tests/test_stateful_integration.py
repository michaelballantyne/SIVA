"""Stateful integration tests for VisLang.

Tests sequences of operations that exercise server state directly through
Python function calls (no MCP protocol needed).

Covers:
- Multi-view workflow: create views, switch focus, verify independent pipeline state
- Version history: set pipeline multiple times, list_versions, restore_version
- Load + query + pipeline + filtered query: load synthetic data, describe_data,
  set_pipeline with threshold, verify describe_data on filtered node shows
  fewer points than the original
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vislang.server as srv

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SYNTHETIC_DATA = os.path.join(_HERE, "datasets", "synthetic", "data", "output.vti")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeRenderer:
    """Minimal renderer stub that avoids any display or VTK rendering.

    Supports the full interface expected by server.py and dsl.py:
    - run_on_main_thread (executes inline, same thread)
    - screenshot (creates an empty file and returns the path)
    - render, clear, reset_camera, get_camera_state, set_camera, destroy
    - add_actor, add_volume, add_overlay, add_overlay_actor, set_background
    """

    def __init__(self, name="fake"):
        self.name = name
        self._mode = srv.RenderMode.OFFSCREEN
        self._actors = {}     # name -> actor (mirrors real Renderer._actors)
        self._overlays = {}   # name -> actor2d (mirrors real Renderer._overlays)

    def render(self):
        pass

    def run_on_main_thread(self, fn):
        return fn()

    def screenshot(self, path):
        # Create an empty file so os.path.exists checks pass
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"")
        return path

    def clear(self):
        pass

    def reset_camera(self):
        pass

    def get_camera_state(self):
        return {"position": [0, 0, 1], "focal_point": [0, 0, 0], "up": [0, 1, 0]}

    def set_camera(self, **kwargs):
        pass

    def set_background(self, *args, **kwargs):
        pass

    def add_actor(self, *args, **kwargs):
        pass

    def add_volume(self, *args, **kwargs):
        pass

    def add_overlay(self, *args, **kwargs):
        pass

    def add_overlay_actor(self, *args, **kwargs):
        pass

    def destroy(self):
        pass


def _reset(tmp_dir: str):
    """Reset server state with a fake renderer, working from tmp_dir."""
    os.chdir(tmp_dir)
    renderer = _FakeRenderer("main")
    srv._init_for_test(renderer)


def _write_pipeline(path: str, code: str):
    Path(path).write_text(code)


def _run_pipeline(code: str) -> list:
    """Write code to the current view's pipeline file and execute it."""
    ctx = srv._current_ctx()
    pipeline_file = ctx.pipeline_file
    _write_pipeline(pipeline_file, code)
    with patch.object(srv, "_auto_screenshot", return_value=None):
        result = srv.set_pipeline(pipeline_file)
    return result


# ---------------------------------------------------------------------------
# Multi-view workflow
# ---------------------------------------------------------------------------

class TestMultiViewWorkflow(unittest.TestCase):
    """Create multiple views, set pipelines independently, verify isolation."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        _reset(self._tmp)

    def test_new_view_starts_empty(self):
        """A newly created view has no pipeline code or VTK objects."""
        srv.new_view("secondary")
        ctx = srv._views["secondary"]
        self.assertEqual(ctx.current_code, "")
        self.assertEqual(ctx.vtk_objects, {})
        self.assertEqual(ctx.version, 0)

    def test_new_view_becomes_current_view(self):
        """new_view() switches focus to the new view."""
        srv.new_view("detail")
        self.assertEqual(srv._current_view, "detail")

    def test_views_have_independent_pipeline_state(self):
        """Setting a pipeline on one view does not affect the other view."""
        # Set up a pipeline on main view
        main_code = 'data = source("vtkSphereSource")\nshow(data, "sphere")'
        _run_pipeline(main_code)
        self.assertIn("data", srv._views["main"].vtk_objects)
        main_version = srv._views["main"].version

        # Create secondary view and set a different pipeline
        srv.new_view("secondary")
        secondary_code = 'data = source("vtkConeSource")\nshow(data, "cone")'
        _run_pipeline(secondary_code)

        # Secondary has its own state
        self.assertIn("data", srv._views["secondary"].vtk_objects)
        self.assertEqual(srv._views["secondary"].version, 1)

        # Main view state is unchanged
        self.assertEqual(srv._views["main"].version, main_version)
        self.assertEqual(srv._views["main"].current_code, main_code)

    def test_focus_switches_current_view(self):
        """focus() changes _current_view and returns a message containing the view name."""
        srv.new_view("secondary")
        srv._current_view = "main"  # reset back to main

        with patch.object(srv, "_auto_screenshot", return_value=None):
            result = srv.focus("secondary")
        self.assertEqual(srv._current_view, "secondary")
        # Result should mention the view name in some form
        result_text = result if isinstance(result, str) else result[0]
        self.assertIn("secondary", result_text)

    def test_pipeline_operations_affect_current_view_only(self):
        """A set_pipeline call only modifies the currently focused view."""
        # Create two views
        srv.new_view("alpha")
        srv.new_view("beta")

        # Operate on beta (currently focused)
        beta_code = 'data = source("vtkCylinderSource")\nshow(data, "cyl")'
        _run_pipeline(beta_code)
        self.assertEqual(srv._views["beta"].version, 1)

        # Alpha remains untouched
        self.assertEqual(srv._views["alpha"].version, 0)
        self.assertEqual(srv._views["alpha"].current_code, "")

    def test_list_views_shows_all_view_names(self):
        """list_views() includes all created view names."""
        srv.new_view("view_a")
        srv.new_view("view_b")
        result = srv.list_views()
        self.assertIn("main", result)
        self.assertIn("view_a", result)
        self.assertIn("view_b", result)

    def test_list_views_marks_current_view(self):
        """list_views() marks the currently focused view with an asterisk."""
        srv.new_view("secondary")
        result = srv.list_views()
        # The current view (secondary) should be marked
        self.assertIn("*", result)
        self.assertIn("secondary", result)

    def test_pipeline_code_is_preserved_per_view_after_focus_switch(self):
        """Switching focus and back does not alter a view's pipeline code."""
        main_code = 'data = source("vtkSphereSource")\nshow(data, "sphere")'
        _run_pipeline(main_code)

        srv.new_view("secondary")
        secondary_code = 'data = source("vtkConeSource")\nshow(data, "cone")'
        _run_pipeline(secondary_code)

        # Switch back to main
        with patch.object(srv, "_auto_screenshot", return_value=None):
            srv.focus("main")

        self.assertEqual(srv._views["main"].current_code, main_code)
        self.assertEqual(srv._views["secondary"].current_code, secondary_code)

    def test_version_histories_are_independent_per_view(self):
        """Each view maintains its own version counter independently."""
        # Run two pipelines on main
        for i in range(2):
            code = f'data = source("vtkSphereSource", radius={i + 1})\nshow(data, "s")'
            _run_pipeline(code)
        self.assertEqual(srv._views["main"].version, 2)

        # New secondary view starts at version 0
        srv.new_view("secondary")
        self.assertEqual(srv._views["secondary"].version, 0)

        # Run one pipeline on secondary
        _run_pipeline('data = source("vtkConeSource")\nshow(data, "c")')
        self.assertEqual(srv._views["secondary"].version, 1)

        # Main version is still 2
        self.assertEqual(srv._views["main"].version, 2)


# ---------------------------------------------------------------------------
# Version history workflow
# ---------------------------------------------------------------------------

class TestVersionHistoryWorkflow(unittest.TestCase):
    """Set pipeline multiple times, list versions, restore an earlier version."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        _reset(self._tmp)

    def _run(self, code: str):
        """Helper: write and execute a pipeline."""
        return _run_pipeline(code)

    def test_version_increments_with_each_set_pipeline(self):
        """Each set_pipeline() call increments the version counter."""
        for i in range(3):
            self._run(f'data = source("vtkSphereSource", radius={i + 1})\nshow(data, "s")')
        self.assertEqual(srv._current_ctx().version, 3)

    def test_list_versions_shows_no_versions_initially(self):
        """list_versions() reports no versions before any pipeline is set."""
        result = srv.list_versions()
        self.assertIn("No versions", result)

    def test_list_versions_shows_all_versions(self):
        """After setting the pipeline three times, list_versions shows three entries."""
        self._run('data = source("vtkSphereSource", radius=1.0)\nshow(data, "v1")')
        self._run('data = source("vtkSphereSource", radius=2.0)\nshow(data, "v2")')
        self._run('data = source("vtkSphereSource", radius=3.0)\nshow(data, "v3")')
        result = srv.list_versions()
        self.assertIn("v1", result)
        self.assertIn("v2", result)
        self.assertIn("v3", result)

    def test_list_versions_shows_current_version(self):
        """list_versions() shows which version is current."""
        self._run('data = source("vtkSphereSource")\nshow(data, "s")')
        self._run('data = source("vtkConeSource")\nshow(data, "c")')
        result = srv.list_versions()
        self.assertIn("Current: v2", result)

    def test_restore_version_returns_previous_code(self):
        """restore_version(1) sets current_code back to the v1 pipeline code."""
        code_v1 = 'data = source("vtkSphereSource", radius=1.0)\nshow(data, "sphere1")'
        code_v2 = 'data = source("vtkSphereSource", radius=99.0)\nshow(data, "sphere99")'

        self._run(code_v1)
        self._run(code_v2)

        # Confirm we are at v2
        self.assertEqual(srv._current_ctx().version, 2)
        self.assertIn("radius=99.0", srv._current_ctx().current_code)

        # Restore v1
        with patch.object(srv, "_auto_screenshot", return_value=None):
            srv.restore_version(1)

        # Current code should be the v1 code
        self.assertIn("radius=1.0", srv._current_ctx().current_code)

    def test_restore_version_increments_version_counter(self):
        """Restoring a version creates a new version entry (does not overwrite)."""
        self._run('data = source("vtkSphereSource")\nshow(data, "v1")')
        self._run('data = source("vtkConeSource")\nshow(data, "v2")')

        version_before = srv._current_ctx().version  # should be 2
        with patch.object(srv, "_auto_screenshot", return_value=None):
            srv.restore_version(1)

        # Restoring executes set_pipeline, which increments version
        self.assertEqual(srv._current_ctx().version, version_before + 1)

    def test_restore_nonexistent_version_returns_error(self):
        """restore_version with an invalid number returns an error message."""
        self._run('data = source("vtkSphereSource")\nshow(data, "s")')
        result = srv.restore_version(999)
        msg = result if isinstance(result, str) else result[0]
        self.assertIn("999", msg)
        self.assertIn("not found", msg)

    def test_list_versions_shows_first_line_of_code(self):
        """list_versions() shows the first line of each pipeline as a preview."""
        code = '# My special pipeline\ndata = source("vtkSphereSource")\nshow(data, "s")'
        self._run(code)
        result = srv.list_versions()
        self.assertIn("# My special pipeline", result)

    def test_version_history_is_per_view(self):
        """Version history is stored per-view and does not bleed across views."""
        # Set pipelines on main
        self._run('data = source("vtkSphereSource")\nshow(data, "main1")')
        self._run('data = source("vtkConeSource")\nshow(data, "main2")')

        # Create secondary view and set one pipeline
        srv.new_view("secondary")
        self._run('data = source("vtkCylinderSource")\nshow(data, "sec1")')

        # Main has 2 versions, secondary has 1
        with patch.object(srv, "_auto_screenshot", return_value=None):
            srv.focus("main")
        main_versions = srv.list_versions()
        self.assertIn("v2", main_versions)

        with patch.object(srv, "_auto_screenshot", return_value=None):
            srv.focus("secondary")
        sec_versions = srv.list_versions()
        self.assertIn("Current: v1", sec_versions)
        # Secondary history should not mention main's pipelines
        self.assertNotIn("main1", sec_versions)
        self.assertNotIn("main2", sec_versions)


# ---------------------------------------------------------------------------
# Load + query + pipeline + filtered query workflow
# ---------------------------------------------------------------------------

class TestLoadQueryPipelineWorkflow(unittest.TestCase):
    """End-to-end workflow: load data, query, set threshold pipeline, query filtered."""

    @classmethod
    def setUpClass(cls):
        """Check that synthetic data is available."""
        if not os.path.exists(_SYNTHETIC_DATA):
            raise unittest.SkipTest(
                f"Synthetic dataset not found at {_SYNTHETIC_DATA}. "
                "Run datasets/synthetic/generate.py to create it."
            )

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        _reset(self._tmp)

    def test_load_returns_describe_data_output(self):
        """load() returns a describe_data() overview string with point count and fields."""
        result = srv.load(_SYNTHETIC_DATA)
        self.assertIsInstance(result, str)
        self.assertIn("Points:", result)
        self.assertIn("Fields", result)

    def test_load_creates_data_node_in_pipeline(self):
        """After load(), the 'data' node is present in vtk_objects."""
        srv.load(_SYNTHETIC_DATA)
        self.assertIn("data", srv._current_ctx().vtk_objects)

    def test_describe_data_after_load_shows_point_count(self):
        """describe_data(node='data') reports a non-zero point count."""
        srv.load(_SYNTHETIC_DATA)
        result = srv.describe_data(node="data")
        self.assertIn("Points:", result)
        # Extract point count — must be > 0
        for line in result.splitlines():
            if "Points:" in line:
                count_str = line.split("Points:")[1].strip().replace(",", "")
                count = int(count_str)
                self.assertGreater(count, 0, "Expected at least one point in loaded data")
                break
        else:
            self.fail("'Points:' line not found in describe_data output")

    def test_threshold_pipeline_reduces_point_count(self):
        """set_pipeline with a threshold filter produces fewer points than the raw data."""
        # Load data and record original point count
        srv.load(_SYNTHETIC_DATA)
        raw_result = srv.describe_data(node="data")
        raw_points = _extract_point_count(raw_result)
        self.assertGreater(raw_points, 0, "Raw data should have points")

        # Get the field names available so we can pick a real field
        self.assertIn("temperature", raw_result.lower() + " " + raw_result,
                      "Expected 'temperature' field in synthetic data")

        # Build a threshold pipeline that keeps only the lower half of temperature values.
        # The synthetic dataset has temperature in [0, 100] (see generate.py).
        threshold_code = (
            f'data = source("vtkXMLImageDataReader", FileName="{_SYNTHETIC_DATA}")\n'
            f'hot = threshold(input=data, ThresholdBy="temperature", '
            f'ThresholdRange=[50.0, 100.0])\n'
            f'show(hot, "hot", color_by="temperature")\n'
        )
        pipeline_file = srv._current_ctx().pipeline_file
        Path(pipeline_file).write_text(threshold_code)
        with patch.object(srv, "_auto_screenshot", return_value=None):
            srv.set_pipeline(pipeline_file)

        # Query the filtered node
        filtered_result = srv.describe_data(node="hot")
        filtered_points = _extract_point_count(filtered_result)

        # The threshold should have removed roughly half the points
        self.assertGreater(filtered_points, 0,
                           "Threshold with range [50, 100] should include some points")
        self.assertLess(filtered_points, raw_points,
                        "Threshold should reduce point count vs raw data")

    def test_pipeline_replaces_load_state(self):
        """After load(), setting an explicit pipeline replaces the loaded data."""
        srv.load(_SYNTHETIC_DATA)
        self.assertIn("data", srv._current_ctx().vtk_objects)

        # Set a pipeline that creates a sphere (no data file needed)
        new_code = 'sphere = source("vtkSphereSource")\nshow(sphere, "s")'
        pipeline_file = srv._current_ctx().pipeline_file
        Path(pipeline_file).write_text(new_code)
        with patch.object(srv, "_auto_screenshot", return_value=None):
            srv.set_pipeline(pipeline_file)

        # 'data' from load() should no longer be present; 'sphere' should be
        ctx = srv._current_ctx()
        self.assertIn("sphere", ctx.vtk_objects)
        self.assertNotIn("data", ctx.vtk_objects)

    def test_combined_load_describe_threshold_describe(self):
        """Full workflow: load -> describe -> threshold pipeline -> describe filtered node."""
        # Step 1: load
        load_result = srv.load(_SYNTHETIC_DATA)
        self.assertIn("Points:", load_result)

        # Step 2: describe raw data
        raw_desc = srv.describe_data(node="data")
        raw_points = _extract_point_count(raw_desc)
        self.assertGreater(raw_points, 0)

        # Step 3: set pipeline with threshold
        threshold_code = (
            f'data = source("vtkXMLImageDataReader", FileName="{_SYNTHETIC_DATA}")\n'
            f'low_temp = threshold(input=data, ThresholdBy="temperature", '
            f'ThresholdRange=[0.0, 40.0])\n'
            f'show(low_temp, "low_temp", color_by="temperature")\n'
        )
        pipeline_file = srv._current_ctx().pipeline_file
        Path(pipeline_file).write_text(threshold_code)
        with patch.object(srv, "_auto_screenshot", return_value=None):
            pipeline_result = srv.set_pipeline(pipeline_file)

        pipeline_text = pipeline_result if isinstance(pipeline_result, str) else pipeline_result[0]
        self.assertIn("built", pipeline_text.lower(),
                      "Pipeline should report a build status")

        # Step 4: describe filtered node
        filtered_desc = srv.describe_data(node="low_temp")
        filtered_points = _extract_point_count(filtered_desc)
        self.assertGreater(filtered_points, 0,
                           "Lower-temperature threshold should include some points")
        self.assertLess(filtered_points, raw_points,
                        "Filtered data should have fewer points than raw")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_point_count(describe_output: str) -> int:
    """Parse the point count from describe_data() output.

    Looks for a line like:  '  Points: 262,144'
    Returns the integer value, or raises AssertionError if not found.
    """
    for line in describe_output.splitlines():
        if "Points:" in line:
            count_str = line.split("Points:")[1].strip().replace(",", "")
            return int(count_str)
    raise AssertionError(f"'Points:' line not found in describe_data output:\n{describe_output}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
