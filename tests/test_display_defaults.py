"""Tests for Vega-lite-style display-property inference in create_show().

Rules tested:
1. Auto scalar_bar when color_by is set (unless scalar_bar=False).
2. Diverging colormap (cool_to_warm) + symmetric range for signed fields.
3. Non-signed fields keep the default colormap.
4. Asymmetric signed range gets the larger abs value as symmetric bound.
5. Explicit lut overrides diverging inference.
6. Explicit scalar_bar=False suppresses auto bar.
7. Scalar bar title humanized from field name (underscores to spaces).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk

from vislang.filters import create_show, _humanize_field_name, _infer_display_defaults


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scalar_data(field_name="temperature", lo=0.0, hi=100.0, dims=(8, 8, 8)):
    """Create vtkImageData with a single linearly-spaced scalar field."""
    img = vtk.vtkImageData()
    img.SetDimensions(*dims)
    img.SetOrigin(0.0, 0.0, 0.0)
    img.SetSpacing(1.0, 1.0, 1.0)
    n = img.GetNumberOfPoints()
    vals = np.linspace(lo, hi, n).astype(np.float64)
    arr = numpy_to_vtk(vals, deep=True)
    arr.SetName(field_name)
    img.GetPointData().AddArray(arr)
    img.GetPointData().SetActiveScalars(field_name)
    return img


# ---------------------------------------------------------------------------
# _humanize_field_name unit tests
# ---------------------------------------------------------------------------

class TestHumanizeFieldName:
    """Pure-function tests for field name humanization."""

    def test_underscores_replaced(self):
        assert _humanize_field_name("air_temperature") == "air temperature"

    def test_no_underscores_unchanged(self):
        assert _humanize_field_name("pressure") == "pressure"

    def test_multiple_underscores(self):
        assert _humanize_field_name("w_component_z") == "w component z"


# ---------------------------------------------------------------------------
# _infer_display_defaults unit tests (no VTK rendering needed)
# ---------------------------------------------------------------------------

class TestInferDisplayDefaults:
    """Unit tests for the inference function itself."""

    def _data_passthrough(self, field_name, lo, hi):
        """Build a vtkImageData and return it (acts as vtk_algorithm input)."""
        return _make_scalar_data(field_name=field_name, lo=lo, hi=hi)

    def test_scalar_bar_added_when_absent(self):
        data = self._data_passthrough("temperature", 0, 100)
        result = _infer_display_defaults(data, {"color_by": "temperature"})
        assert "scalar_bar" in result
        assert result["scalar_bar"] == "temperature"

    def test_scalar_bar_humanized(self):
        data = self._data_passthrough("air_temperature", 0, 100)
        result = _infer_display_defaults(data, {"color_by": "air_temperature"})
        assert result["scalar_bar"] == "air temperature"

    def test_scalar_bar_false_honored(self):
        data = self._data_passthrough("temperature", 0, 100)
        result = _infer_display_defaults(data, {"color_by": "temperature", "scalar_bar": False})
        assert result["scalar_bar"] is False

    def test_scalar_bar_explicit_string_honored(self):
        data = self._data_passthrough("temperature", 0, 100)
        result = _infer_display_defaults(data, {"color_by": "temperature", "scalar_bar": "My Title"})
        assert result["scalar_bar"] == "My Title"

    def test_diverging_for_signed_field(self):
        """Field range [-50, 50] should trigger cool_to_warm and symmetric range."""
        data = self._data_passthrough("delta_t", -50.0, 50.0)
        result = _infer_display_defaults(data, {"color_by": "delta_t"})
        assert result.get("lut") == "cool_to_warm"
        lo, hi = result.get("scalar_range")
        assert lo == -50.0
        assert hi == 50.0

    def test_asymmetric_signed_range_uses_abs_max(self):
        """Field range [-10, 100] -> symmetric range [-100, 100]."""
        data = self._data_passthrough("w_vel", -10.0, 100.0)
        result = _infer_display_defaults(data, {"color_by": "w_vel"})
        assert result.get("lut") == "cool_to_warm"
        lo, hi = result.get("scalar_range")
        assert lo == -100.0
        assert hi == 100.0

    def test_non_signed_field_no_diverging(self):
        """Field range [0, 100] should NOT get diverging colormap."""
        data = self._data_passthrough("temperature", 0.0, 100.0)
        result = _infer_display_defaults(data, {"color_by": "temperature"})
        assert result.get("lut") is None or "lut" not in result

    def test_explicit_lut_overrides_inference(self):
        """Passing lut='fire' for a signed field should use fire, not cool_to_warm."""
        data = self._data_passthrough("signed_field", -50.0, 50.0)
        result = _infer_display_defaults(data, {"color_by": "signed_field", "lut": "fire"})
        assert result.get("lut") == "fire"

    def test_explicit_scalar_range_suppresses_symmetric(self):
        """Passing explicit scalar_range should not be overridden by signed inference."""
        data = self._data_passthrough("signed_field", -50.0, 50.0)
        result = _infer_display_defaults(data, {
            "color_by": "signed_field",
            "scalar_range": (-20.0, 20.0),
        })
        lo, hi = result.get("scalar_range")
        assert lo == -20.0
        assert hi == 20.0

    def test_no_color_by_no_changes(self):
        """Without color_by, inference should not modify display_props."""
        data = self._data_passthrough("temperature", 0, 100)
        props = {"opacity": 0.5}
        result = _infer_display_defaults(data, props)
        assert result == props


# ---------------------------------------------------------------------------
# create_show integration tests
# ---------------------------------------------------------------------------

class TestCreateShowDefaults:
    """Integration tests: create_show with and without inference."""

    def test_auto_scalar_bar_present(self):
        """color_by without scalar_bar -> scalar bar returned."""
        data = _make_scalar_data("temperature", 0, 100)
        actor, bar = create_show(data, color_by="temperature")
        assert bar is not None, "Expected auto scalar bar"
        assert isinstance(bar, vtk.vtkScalarBarActor)

    def test_auto_scalar_bar_title(self):
        """Scalar bar title is verified at the inference level; bar is returned."""
        data = _make_scalar_data("air_temperature", 0, 100)
        actor, bar = create_show(data, color_by="air_temperature")
        assert bar is not None, "Expected auto scalar bar"
        # The bar actor's built-in title is intentionally suppressed by _style_scalar_bar
        # (the renderer shows a separate vtkTextActor with the humanized title).
        # Verify the humanization rule directly:
        assert _humanize_field_name("air_temperature") == "air temperature"

    def test_explicit_scalar_bar_false_suppresses(self):
        """scalar_bar=False should suppress the auto bar."""
        data = _make_scalar_data("temperature", 0, 100)
        actor, bar = create_show(data, color_by="temperature", scalar_bar=False)
        assert bar is None, "Expected no scalar bar when scalar_bar=False"

    def test_explicit_scalar_bar_string_honored(self):
        """Explicit scalar_bar string still produces a scalar bar."""
        data = _make_scalar_data("temperature", 0, 100)
        actor, bar = create_show(data, color_by="temperature", scalar_bar="My Title")
        assert bar is not None, "Expected a scalar bar when scalar_bar='My Title'"

    def test_diverging_colormap_for_signed_field(self):
        """A signed-range field auto-selects cool_to_warm diverging LUT."""
        data = _make_scalar_data("delta_t", -50.0, 50.0)
        actor, bar = create_show(data, color_by="delta_t")
        mapper = actor.GetMapper()
        sr = mapper.GetScalarRange()
        # Symmetric range: [-50, 50]
        assert abs(sr[0] - (-50.0)) < 1e-6
        assert abs(sr[1] - 50.0) < 1e-6
        # Check LUT has cool (blue) at low end and warm (red) at high end.
        # VTK GetColor API requires an output array.
        lut = mapper.GetLookupTable()
        low_rgb = [0.0, 0.0, 0.0]
        high_rgb = [0.0, 0.0, 0.0]
        lut.GetColor(sr[0], low_rgb)
        lut.GetColor(sr[1], high_rgb)
        # cool_to_warm: low end is blue-ish (B > R)
        assert low_rgb[2] > low_rgb[0], "Low end should be blue (B > R) for cool_to_warm"
        # high end is red-ish (R > B)
        assert high_rgb[0] > high_rgb[2], "High end should be red (R > B) for cool_to_warm"

    def test_asymmetric_signed_range_symmetric_bound(self):
        """[-10, 100] field -> symmetric range [-100, 100]."""
        data = _make_scalar_data("w_vel", -10.0, 100.0)
        actor, bar = create_show(data, color_by="w_vel")
        mapper = actor.GetMapper()
        sr = mapper.GetScalarRange()
        assert abs(sr[0] - (-100.0)) < 1e-6
        assert abs(sr[1] - 100.0) < 1e-6

    def test_non_signed_no_diverging(self):
        """Non-signed field [0, 100] should NOT get cool_to_warm."""
        data = _make_scalar_data("temperature", 0.0, 100.0)
        actor, bar = create_show(data, color_by="temperature")
        mapper = actor.GetMapper()
        sr = mapper.GetScalarRange()
        # Range should not be negative
        assert sr[0] >= 0.0, "Non-signed field range should not be negative"

    def test_explicit_lut_overrides_diverging(self):
        """Passing lut='fire' for signed field uses fire, not cool_to_warm."""
        data = _make_scalar_data("delta_t", -50.0, 50.0)
        actor, bar = create_show(data, color_by="delta_t", lut="fire")
        mapper = actor.GetMapper()
        lut = mapper.GetLookupTable()
        # fire LUT: high end should be white (R=G=B all high), which differs from cool_to_warm.
        # VTK GetColor API requires an output array.
        high_rgb = [0.0, 0.0, 0.0]
        lut.GetColor(50.0, high_rgb)
        # In fire colormap, high end is white: R, G, B all >= 0.8
        assert all(c > 0.8 for c in high_rgb), (
            f"Expected fire LUT (white at high end), got RGB={high_rgb}"
        )

    def test_no_color_by_no_scalar_bar(self):
        """Without color_by, no scalar bar should be auto-added."""
        data = _make_scalar_data("temperature", 0, 100)
        actor, bar = create_show(data, color=(1.0, 0.0, 0.0))
        assert bar is None, "No scalar bar expected without color_by"


# ---------------------------------------------------------------------------
# Humanization title test for DSL path
# ---------------------------------------------------------------------------

class TestTitleHumanization:
    """Verify field name title rule in isolation."""

    def test_air_temperature(self):
        assert _humanize_field_name("air_temperature") == "air temperature"

    def test_no_underscores(self):
        assert _humanize_field_name("pressure") == "pressure"

    def test_leading_trailing_underscores(self):
        # Not a real field name but should still work gracefully
        assert _humanize_field_name("_temp_") == " temp "


# ---------------------------------------------------------------------------
# Run via pytest (also works with unittest runner)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
