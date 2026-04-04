"""Unit tests for auto-screenshot behavior on state-changing tools.

These tests verify that _with_screenshot correctly combines text results
with images, and that the right set of tools use auto-screenshot.
"""

import ast
import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

# Ensure imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ensure_server_importable():
    """Stub out mcp and vislang.renderer so vislang.server can be imported without
    a live MCP installation or VTK display."""
    if "mcp" not in sys.modules:
        mcp_mock = MagicMock()
        # FastMCP needs to return a usable object with a .tool() decorator
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


class TestWithScreenshotLogic(unittest.TestCase):
    """Test _with_screenshot combining logic without VTK."""

    @classmethod
    def setUpClass(cls):
        _ensure_server_importable()

    def test_with_screenshot_returns_list_when_image_available(self):
        """When _auto_screenshot succeeds, _with_screenshot returns [text, image]."""
        import vislang.server as srv
        fake_image = MagicMock()
        with patch.object(srv, "_auto_screenshot", return_value=fake_image):
            result = srv._with_screenshot("Pipeline built successfully.")
            self.assertIsInstance(result, list)
            self.assertEqual(len(result), 2)
            self.assertEqual(result[0], "Pipeline built successfully.")
            self.assertIs(result[1], fake_image)

    def test_with_screenshot_returns_text_when_image_fails(self):
        """When _auto_screenshot fails, _with_screenshot returns just text."""
        import vislang.server as srv
        with patch.object(srv, "_auto_screenshot", return_value=None):
            result = srv._with_screenshot("Pipeline built successfully.")
            self.assertIsInstance(result, str)
            self.assertEqual(result, "Pipeline built successfully.")


class TestStateChangingToolsUseAutoScreenshot(unittest.TestCase):
    """Verify that the correct set of tools use _with_screenshot via AST analysis."""

    @classmethod
    def setUpClass(cls):
        server_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "vislang", "server.py"
        )
        with open(server_path) as f:
            cls.source = f.read()
        cls.tree = ast.parse(cls.source)

        # Find all @mcp.tool() decorated functions
        cls.tool_funcs = set()
        for node in ast.walk(cls.tree):
            if isinstance(node, ast.FunctionDef):
                for deco in node.decorator_list:
                    if isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute):
                        if deco.func.attr == "tool":
                            cls.tool_funcs.add(node.name)

        # Find which tool functions contain _with_screenshot
        cls.tools_with_screenshot = set()
        for node in ast.walk(cls.tree):
            if isinstance(node, ast.FunctionDef) and node.name in cls.tool_funcs:
                src = ast.get_source_segment(cls.source, node)
                if src and "_with_screenshot" in src:
                    cls.tools_with_screenshot.add(node.name)

    def test_state_changing_tools_have_auto_screenshot(self):
        """All state-changing tools should use _with_screenshot."""
        expected = {
            "set_pipeline",
            "reset_pipeline",
            "set_camera",
            "set_opacity",
            "set_colormap",
            "toggle_visibility",
            "set_window_size",
            "set_background",
            "annotate",
            "clear_annotations",
        }
        missing = expected - self.tools_with_screenshot
        self.assertEqual(
            missing, set(),
            f"These state-changing tools should use _with_screenshot: {missing}"
        )

    def test_query_tools_do_not_have_auto_screenshot(self):
        """Query/read-only tools should NOT use _with_screenshot."""
        query_tools = {
            "describe_data",
            "get_array_info",
            "get_field_summary",
            "get_node_info",
            "get_bounds",
            "get_statistics",
            "get_histogram",
            "get_spatial_extent",
            "get_ground_z",
            "suggest_scalar_range",
            "suggest_opacity",
            "suggest_isosurface",
            "suggest_camera",
            "get_actor_info",
            "list_actors",
            "list_versions",
            "get_pipeline",
            "list_data_files",
            "list_capabilities",
            "get_examples",
        }
        unexpected = query_tools & self.tools_with_screenshot
        self.assertEqual(
            unexpected, set(),
            f"These query tools should NOT use _with_screenshot: {unexpected}"
        )

    def test_screenshot_tool_not_doubled(self):
        """The screenshot tool itself should not use _with_screenshot (it returns Image directly)."""
        self.assertNotIn("screenshot", self.tools_with_screenshot)


if __name__ == "__main__":
    unittest.main()
