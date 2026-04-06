"""Tests for the render_chart MCP tool.

Tests chart generation returns valid PNG data without requiring a running
MCP server or a loaded VTK dataset, plus integration tests that exercise
the pipeline lookup path when a synthetic dataset is available.
"""

import io
import json
import os
import sys

import numpy as np
import pytest
import vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vislang.server as srv
from vislang.renderer import Renderer, RenderMode

SYNTHETIC_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "datasets", "synthetic", "data", "output.vti",
)

_PNG_MAGIC = b"\x89PNG"


def _is_valid_png(data: bytes) -> bool:
    return data[:4] == _PNG_MAGIC


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_chart_direct(**kwargs):
    """Call render_chart and return the result."""
    return srv.render_chart(**kwargs)


# ---------------------------------------------------------------------------
# Line chart from raw JSON data (no VTK pipeline needed)
# ---------------------------------------------------------------------------

class TestLineChartFromJSON:

    def test_returns_two_items(self):
        xy = json.dumps({"x": [1, 2, 3], "y": [4, 5, 6]})
        result = _render_chart_direct(chart_type="line", data=xy)
        assert isinstance(result, list), f"Expected list, got: {result}"
        assert len(result) == 2

    def test_description_is_string(self):
        xy = json.dumps({"x": [1, 2, 3], "y": [4, 5, 6]})
        result = _render_chart_direct(chart_type="line", data=xy)
        assert isinstance(result[0], str)
        assert "Line plot" in result[0]

    def test_image_is_valid_png(self):
        xy = json.dumps({"x": [0, 1, 2, 3, 4], "y": [0, 1, 4, 9, 16]})
        result = _render_chart_direct(chart_type="line", data=xy)
        from mcp.server.fastmcp import Image
        assert isinstance(result[1], Image)
        assert _is_valid_png(result[1].data)

    def test_description_contains_point_count(self):
        n = 7
        xy = json.dumps({"x": list(range(n)), "y": list(range(n))})
        result = _render_chart_direct(chart_type="line", data=xy)
        assert str(n) in result[0]

    def test_custom_title_and_labels(self):
        xy = json.dumps({"x": [0, 1], "y": [0, 1]})
        result = _render_chart_direct(
            chart_type="line", data=xy,
            title="My Title", x_label="Time", y_label="Value"
        )
        assert isinstance(result, list)
        assert isinstance(result[1].data, bytes)

    def test_single_point_line(self):
        """A single-point line chart should succeed."""
        xy = json.dumps({"x": [5.0], "y": [10.0]})
        result = _render_chart_direct(chart_type="line", data=xy)
        assert isinstance(result, list)
        assert _is_valid_png(result[1].data)

    def test_large_dataset(self):
        """1000 points should render without error."""
        n = 1000
        xy = json.dumps({"x": list(range(n)), "y": [float(i ** 2) for i in range(n)]})
        result = _render_chart_direct(chart_type="line", data=xy)
        assert isinstance(result, list)
        assert _is_valid_png(result[1].data)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestChartErrors:

    def test_invalid_chart_type_returns_error_string(self):
        result = _render_chart_direct(chart_type="pie")
        assert isinstance(result, str)
        assert "pie" in result or "Unknown" in result

    def test_line_missing_both_data_and_field(self):
        result = _render_chart_direct(chart_type="line")
        assert isinstance(result, str)
        assert "field" in result.lower() or "data" in result.lower()

    def test_invalid_json_data(self):
        result = _render_chart_direct(chart_type="line", data="not valid json {{{")
        assert isinstance(result, str)
        assert "JSON" in result or "parse" in result.lower()

    def test_json_missing_y_key(self):
        result = _render_chart_direct(
            chart_type="line", data=json.dumps({"x": [1, 2, 3]})
        )
        assert isinstance(result, str)
        assert "y" in result

    def test_json_mismatched_lengths(self):
        result = _render_chart_direct(
            chart_type="line",
            data=json.dumps({"x": [1, 2, 3], "y": [4, 5]})
        )
        assert isinstance(result, str)
        assert "equal" in result.lower() or "length" in result.lower()

    def test_histogram_missing_field(self):
        result = _render_chart_direct(chart_type="histogram")
        assert isinstance(result, str)
        assert "field" in result.lower()

    def test_histogram_no_pipeline(self):
        """histogram with no pipeline should return an informative error."""
        # Reset pipeline state
        original_vtk_objects = srv._vtk_objects
        srv._vtk_objects = {}
        try:
            result = _render_chart_direct(chart_type="histogram", field="temperature")
            assert isinstance(result, str)
        finally:
            srv._vtk_objects = original_vtk_objects

    def test_case_insensitive_chart_type(self):
        """chart_type should be case-insensitive."""
        xy = json.dumps({"x": [1, 2], "y": [3, 4]})
        result = _render_chart_direct(chart_type="LINE", data=xy)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Integration tests that need the synthetic dataset
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synthetic_pipeline():
    """Set up a minimal pipeline using the synthetic dataset."""
    if not os.path.exists(SYNTHETIC_DATA):
        pytest.skip(
            f"Synthetic dataset not found at {SYNTHETIC_DATA}. "
            "Run: bash datasets/synthetic/download.sh"
        )

    renderer = Renderer(mode=RenderMode.OFFSCREEN)
    srv._renderer = renderer
    srv._vtk_objects = {}

    reader = vtk.vtkXMLImageDataReader()
    reader.SetFileName(SYNTHETIC_DATA)
    reader.Update()
    srv._vtk_objects = {"data": reader}

    yield

    srv._vtk_objects = {}
    srv._renderer = None


class TestHistogramFromPipeline:

    def test_histogram_returns_list(self, synthetic_pipeline):
        result = _render_chart_direct(chart_type="histogram", field="temperature")
        assert isinstance(result, list), f"Expected list, got: {result}"
        assert len(result) == 2

    def test_histogram_image_is_png(self, synthetic_pipeline):
        result = _render_chart_direct(chart_type="histogram", field="temperature")
        from mcp.server.fastmcp import Image
        assert isinstance(result[1], Image)
        assert _is_valid_png(result[1].data)

    def test_histogram_description_contains_field(self, synthetic_pipeline):
        result = _render_chart_direct(chart_type="histogram", field="density")
        assert "density" in result[0]

    def test_histogram_description_contains_stats(self, synthetic_pipeline):
        result = _render_chart_direct(chart_type="histogram", field="temperature")
        desc = result[0]
        assert "min=" in desc
        assert "max=" in desc
        assert "mean=" in desc

    def test_histogram_nonexistent_field(self, synthetic_pipeline):
        result = _render_chart_direct(chart_type="histogram", field="no_such_field")
        assert isinstance(result, str)
        assert "no_such_field" in result or "not found" in result.lower()

    def test_histogram_with_custom_labels(self, synthetic_pipeline):
        result = _render_chart_direct(
            chart_type="histogram", field="temperature",
            title="Temp Distribution", x_label="K", y_label="Frequency"
        )
        assert isinstance(result, list)
        assert _is_valid_png(result[1].data)


class TestLinePlotFromPipeline:

    def test_line_from_field_returns_list(self, synthetic_pipeline):
        result = _render_chart_direct(chart_type="line", field="density")
        assert isinstance(result, list), f"Expected list, got: {result}"
        assert len(result) == 2

    def test_line_from_field_png(self, synthetic_pipeline):
        result = _render_chart_direct(chart_type="line", field="density")
        assert _is_valid_png(result[1].data)

    def test_line_from_field_description(self, synthetic_pipeline):
        result = _render_chart_direct(chart_type="line", field="temperature")
        assert "temperature" in result[0]
        assert "min=" in result[0]


class TestPNGOutputProperties:
    """Verify that PNG output bytes look structurally valid."""

    def test_png_has_nonzero_size(self):
        xy = json.dumps({"x": list(range(20)), "y": list(range(20))})
        result = _render_chart_direct(chart_type="line", data=xy)
        assert len(result[1].data) > 1000  # PNG should be at least 1 KB

    def test_png_bytes_not_truncated(self):
        """PNG ends with IEND chunk — bytes should contain IEND marker."""
        xy = json.dumps({"x": list(range(10)), "y": list(range(10))})
        result = _render_chart_direct(chart_type="line", data=xy)
        # IEND chunk marker appears near the end of every valid PNG
        # (chunk type 4 bytes + 4-byte CRC follow it, so last 8 bytes
        # are b"IEND" + 4-byte CRC).
        assert b"IEND" in result[1].data[-16:]
