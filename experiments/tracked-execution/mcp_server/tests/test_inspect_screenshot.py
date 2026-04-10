"""Tests for the inspect and screenshot MCP tools."""

import pytest


class TestInspect:
    """Tests for the inspect MCP tool."""

    def test_inspect_basic(self, view_dir):
        """inspect returns print output from the code."""
        from mcp_server.server import inspect

        # ImageData(5,5,5) has 5*5*5 = 125 points
        result = inspect("view-main.py", "print(mesh.n_points)")
        assert "125" in result, f"Expected point count in output, got: {result!r}"

    def test_inspect_no_view(self, reset_server):
        """inspect returns error if the view doesn't exist."""
        from mcp_server.server import inspect

        result = inspect("nonexistent.py", "print(1)")
        assert result.startswith("Error"), f"Expected error, got: {result!r}"
        assert "nonexistent" in result or "no view" in result.lower()

    def test_inspect_field_stats(self, view_dir):
        """inspect can query field statistics."""
        from mcp_server.server import inspect

        # T is linspace(0, 1000, 125), so mean is 500.0
        result = inspect("view-main.py", "print(round(float(mesh['T'].mean()), 2))")
        assert "500" in result, f"Expected mean value in output, got: {result!r}"

    def test_inspect_no_output(self, view_dir):
        """inspect tells the agent to use print() if no output is produced."""
        from mcp_server.server import inspect

        result = inspect("view-main.py", "x = 1 + 1")  # No print call
        assert "print()" in result or "no output" in result.lower(), (
            f"Expected hint about print(), got: {result!r}"
        )

    def test_inspect_error(self, view_dir):
        """inspect returns an error message for bad code."""
        from mcp_server.server import inspect

        result = inspect("view-main.py", "raise ValueError('test error')")
        assert "Error" in result or "ValueError" in result, (
            f"Expected error in output, got: {result!r}"
        )
        assert "test error" in result


class TestScreenshot:
    """Tests for the screenshot MCP tool."""

    def test_screenshot_basic(self, view_dir):
        """screenshot returns an Image object."""
        from mcp_server.server import screenshot
        from mcp.server.fastmcp import Image

        result = screenshot("view-main.py")
        assert isinstance(result, Image), (
            f"Expected Image, got {type(result)}: {result!r}"
        )

    def test_screenshot_no_view(self, reset_server):
        """screenshot raises ValueError if the view doesn't exist."""
        from mcp_server.server import screenshot

        with pytest.raises(ValueError, match="no view|No view"):
            screenshot("nonexistent.py")

    def test_screenshot_has_image_data(self, view_dir):
        """screenshot Image contains non-empty PNG data."""
        from mcp_server.server import screenshot

        result = screenshot("view-main.py")

        assert result.data is not None, "Image.data should not be None"
        assert len(result.data) > 0, "Image.data should not be empty"
        # PNG files start with the 8-byte PNG signature.
        assert result.data[:4] == b"\x89PNG", (
            "Expected PNG signature at start of image data"
        )
        assert result._format == "png"
