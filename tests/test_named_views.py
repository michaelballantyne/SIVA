"""Tests for the named-view MCP tools: new_view, focus, close_view, list_views."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Server import helpers (same pattern as test_auto_screenshot.py)
# ---------------------------------------------------------------------------

def _stub_mcp_and_renderer():
    """Stub out mcp and vislang.renderer so server.py can be imported headless."""
    if "mcp" not in sys.modules:
        mcp_mock = MagicMock()
        fake_fastmcp = MagicMock()
        fake_fastmcp.tool.return_value = lambda f: f
        mcp_mock.server.fastmcp.FastMCP.return_value = fake_fastmcp
        mcp_mock.server.fastmcp.Image = MagicMock
        sys.modules["mcp"] = mcp_mock
        sys.modules["mcp.server"] = mcp_mock.server
        sys.modules["mcp.server.fastmcp"] = mcp_mock.server.fastmcp

    if "vislang.renderer" not in sys.modules:
        renderer_mock = MagicMock()
        sys.modules["vislang.renderer"] = renderer_mock

    if "vislang.server" not in sys.modules:
        import vislang.server  # noqa: F401


_stub_mcp_and_renderer()
import vislang.server as srv  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeRenderer:
    """Minimal renderer stub for testing views."""

    def __init__(self, name="fake"):
        self._actors = {}
        self._render_calls = 0
        self.name = name

    def clear(self):
        self._actors.clear()

    def render(self):
        self._render_calls += 1

    def run_on_main_thread(self, fn):
        return fn()

    def screenshot(self, path):
        return path

    def get_camera_state(self):
        return {"position": [0, 0, 0], "focal_point": [0, 0, 0], "up": [0, 0, 1]}


def _reset_views():
    """Reset view state to a clean slate with one main view backed by a fake renderer."""
    srv._views = {}
    srv._current_view = "main"
    main_renderer = _FakeRenderer("main")
    ctx = srv.ViewContext("main", main_renderer)
    # Don't create history dirs in tests
    srv._views["main"] = ctx
    srv._renderer = main_renderer


# ---------------------------------------------------------------------------
# ViewContext unit tests
# ---------------------------------------------------------------------------

class TestViewContext(unittest.TestCase):
    """Test the ViewContext class directly."""

    def test_init_sets_name(self):
        r = _FakeRenderer()
        ctx = srv.ViewContext("alpha", r)
        self.assertEqual(ctx.name, "alpha")

    def test_init_empty_vtk_objects(self):
        r = _FakeRenderer()
        ctx = srv.ViewContext("beta", r)
        self.assertEqual(ctx.vtk_objects, {})

    def test_init_empty_annotations(self):
        r = _FakeRenderer()
        ctx = srv.ViewContext("gamma", r)
        self.assertEqual(ctx.annotations, {})

    def test_init_zero_version(self):
        r = _FakeRenderer()
        ctx = srv.ViewContext("delta", r)
        self.assertEqual(ctx.version, 0)

    def test_history_dir_uses_view_name(self):
        from pathlib import Path
        r = _FakeRenderer()
        ctx = srv.ViewContext("myview", r)
        self.assertIn("myview", str(ctx.history_dir))


# ---------------------------------------------------------------------------
# new_view() tests
# ---------------------------------------------------------------------------

class TestNewView(unittest.TestCase):

    def setUp(self):
        _reset_views()

    def test_creates_new_view(self):
        result = srv.new_view("secondary")
        self.assertIn("secondary", srv._views)

    def test_returns_success_message(self):
        result = srv.new_view("detail")
        self.assertIsInstance(result, str)
        self.assertIn("detail", result)

    def test_new_view_becomes_current(self):
        srv.new_view("secondary")
        self.assertEqual(srv._current_view, "secondary")

    def test_duplicate_name_returns_error(self):
        result = srv.new_view("main")
        self.assertIsInstance(result, str)
        self.assertIn("already exists", result)

    def test_current_view_unchanged_on_duplicate(self):
        srv.new_view("secondary")
        srv.new_view("secondary")  # duplicate
        self.assertEqual(srv._current_view, "secondary")

    def test_new_view_has_empty_pipeline(self):
        srv.new_view("fresh")
        ctx = srv._views["fresh"]
        self.assertEqual(ctx.vtk_objects, {})
        self.assertEqual(ctx.current_code, "")
        self.assertEqual(ctx.version, 0)

    def test_new_view_has_independent_renderer(self):
        srv.new_view("secondary")
        ctx_main = srv._views["main"]
        ctx_sec = srv._views["secondary"]
        self.assertIsNot(ctx_main.renderer, ctx_sec.renderer)

    def test_two_new_views_created(self):
        srv.new_view("view_a")
        srv.new_view("view_b")
        self.assertIn("view_a", srv._views)
        self.assertIn("view_b", srv._views)
        self.assertEqual(len(srv._views), 3)  # main + view_a + view_b


# ---------------------------------------------------------------------------
# focus() tests
# ---------------------------------------------------------------------------

class TestFocus(unittest.TestCase):

    def setUp(self):
        _reset_views()
        srv.new_view("secondary")
        srv._current_view = "main"  # reset back to main

    def test_focus_existing_view(self):
        result = srv.focus("secondary")
        self.assertEqual(srv._current_view, "secondary")

    def test_focus_returns_message_with_view_name(self):
        with patch.object(srv, "_auto_screenshot", return_value=None):
            result = srv.focus("secondary")
        self.assertIsInstance(result, str)
        self.assertIn("secondary", result)

    def test_focus_returns_screenshot_when_available(self):
        fake_img = MagicMock()
        with patch.object(srv, "_auto_screenshot", return_value=fake_img):
            result = srv.focus("secondary")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertIs(result[1], fake_img)

    def test_focus_unknown_view_returns_error(self):
        result = srv.focus("nonexistent")
        self.assertIsInstance(result, str)
        self.assertIn("not found", result)

    def test_focus_unknown_view_does_not_change_current(self):
        srv.focus("nonexistent")
        self.assertEqual(srv._current_view, "main")

    def test_focus_lists_available_views_in_error(self):
        result = srv.focus("nonexistent")
        self.assertIn("main", result)
        self.assertIn("secondary", result)

    def test_focus_main_from_secondary(self):
        srv._current_view = "secondary"
        with patch.object(srv, "_auto_screenshot", return_value=None):
            srv.focus("main")
        self.assertEqual(srv._current_view, "main")


# ---------------------------------------------------------------------------
# close_view() tests
# ---------------------------------------------------------------------------

class TestCloseView(unittest.TestCase):

    def setUp(self):
        _reset_views()
        srv.new_view("secondary")
        srv._current_view = "main"

    def test_close_secondary_view(self):
        result = srv.close_view("secondary")
        self.assertNotIn("secondary", srv._views)

    def test_close_returns_success_message(self):
        result = srv.close_view("secondary")
        self.assertIsInstance(result, str)
        self.assertIn("secondary", result)

    def test_cannot_close_last_view(self):
        # Close secondary first
        srv.close_view("secondary")
        # Now only main remains
        result = srv.close_view("main")
        self.assertIn("only remaining", result)
        self.assertIn("main", srv._views)

    def test_close_nonexistent_view_returns_error(self):
        result = srv.close_view("nonexistent")
        self.assertIsInstance(result, str)
        self.assertIn("not found", result)

    def test_close_current_view_switches_focus(self):
        srv._current_view = "secondary"
        srv.close_view("secondary")
        # Should have switched to "main"
        self.assertEqual(srv._current_view, "main")

    def test_close_non_current_view_preserves_current(self):
        srv._current_view = "main"
        srv.close_view("secondary")
        self.assertEqual(srv._current_view, "main")

    def test_close_reduces_view_count(self):
        srv.new_view("third")
        self.assertEqual(len(srv._views), 3)
        srv.close_view("third")
        self.assertEqual(len(srv._views), 2)


# ---------------------------------------------------------------------------
# list_views() tests
# ---------------------------------------------------------------------------

class TestListViews(unittest.TestCase):

    def setUp(self):
        _reset_views()

    def test_list_with_single_view(self):
        result = srv.list_views()
        self.assertIsInstance(result, str)
        self.assertIn("main", result)

    def test_list_marks_current_view(self):
        result = srv.list_views()
        # Current view should be marked with *
        self.assertIn("*", result)

    def test_list_with_multiple_views(self):
        srv.new_view("second")
        srv.new_view("third")
        result = srv.list_views()
        self.assertIn("main", result)
        self.assertIn("second", result)
        self.assertIn("third", result)

    def test_list_shows_view_count(self):
        srv.new_view("second")
        result = srv.list_views()
        self.assertIn("2", result)

    def test_list_shows_current_name(self):
        srv.new_view("secondary")
        result = srv.list_views()
        # Both views listed, secondary is current
        self.assertIn("secondary", result)

    def test_list_shows_no_pipeline_for_empty_view(self):
        result = srv.list_views()
        self.assertIn("no pipeline", result)

    def test_list_shows_pipeline_info_when_active(self):
        # Set up a fake pipeline on the main view
        fake_alg = MagicMock()
        srv._views["main"].vtk_objects = {"data": fake_alg}
        srv._views["main"].version = 2
        result = srv.list_views()
        self.assertIn("v2", result)
        self.assertIn("1 nodes", result)

    def test_list_empty_views_returns_message(self):
        saved = srv._views
        srv._views = {}
        result = srv.list_views()
        self.assertIn("No views", result)
        srv._views = saved


# ---------------------------------------------------------------------------
# _current_ctx() isolation tests
# ---------------------------------------------------------------------------

class TestCurrentCtxIsolation(unittest.TestCase):
    """Verify that each view has truly independent state."""

    def setUp(self):
        _reset_views()
        srv.new_view("secondary")

    def test_views_have_separate_vtk_objects(self):
        srv._current_view = "main"
        srv._current_ctx().vtk_objects["nodeA"] = "val_main"

        srv._current_view = "secondary"
        self.assertNotIn("nodeA", srv._current_ctx().vtk_objects)

    def test_views_have_separate_current_code(self):
        srv._current_view = "main"
        srv._views["main"].current_code = "main code"

        srv._current_view = "secondary"
        self.assertEqual(srv._current_ctx().current_code, "")

    def test_views_have_separate_version(self):
        srv._views["main"].version = 5
        srv._views["secondary"].version = 0

        srv._current_view = "main"
        self.assertEqual(srv._current_ctx().version, 5)

        srv._current_view = "secondary"
        self.assertEqual(srv._current_ctx().version, 0)

    def test_views_have_separate_annotations(self):
        srv._current_view = "main"
        srv._current_ctx().annotations["label1"] = "actor1"

        srv._current_view = "secondary"
        self.assertNotIn("label1", srv._current_ctx().annotations)


# ---------------------------------------------------------------------------
# Legacy shim tests — test backward-compat when _views is empty
# ---------------------------------------------------------------------------

class TestLegacyShim(unittest.TestCase):
    """When _views is empty, _current_ctx() shim should proxy module-level globals."""

    def setUp(self):
        # Temporarily empty _views to trigger shim
        self._saved_views = srv._views
        self._saved_vtk = srv._vtk_objects
        self._saved_code = srv._current_code
        self._saved_annotations = srv._annotations
        srv._views = {}
        srv._vtk_objects = {}
        srv._current_code = ""
        srv._annotations = {}

    def tearDown(self):
        srv._views = self._saved_views
        srv._vtk_objects = self._saved_vtk
        srv._current_code = self._saved_code
        srv._annotations = self._saved_annotations

    def test_shim_vtk_objects_reads_global(self):
        srv._vtk_objects = {"data": "x"}
        ctx = srv._current_ctx()
        self.assertEqual(ctx.vtk_objects, {"data": "x"})

    def test_shim_annotations_reads_global(self):
        srv._annotations = {"lbl": "actor"}
        ctx = srv._current_ctx()
        self.assertEqual(ctx.annotations, {"lbl": "actor"})

    def test_shim_current_code_reads_global(self):
        srv._current_code = "test code"
        ctx = srv._current_ctx()
        self.assertEqual(ctx.current_code, "test code")


if __name__ == "__main__":
    unittest.main()
