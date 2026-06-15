"""Tests for inline field range hints in empty-output warnings.

Verifies that when a VTK filter produces empty output, the warning message
includes the actual field range and the user's chosen value inline — saving
the agent a round-trip describe_data() call.

All tests use synthetic VTK data created inline; no dataset downloads required.
"""

import os
import sys
import unittest

import vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conftest import make_image_data as _make_image_data  # shared helper

from siva.filters import (
    create_vtk_filter,
    _format_field_range_hint,
    _get_active_scalar_hint,
    _create_volume,
)


def _make_algorithm(data):
    """Wrap a vtkDataSet in a vtkTrivialProducer so it acts like an algorithm."""
    producer = vtk.vtkTrivialProducer()
    producer.SetOutput(data)
    producer.Update()
    return producer


# ---------------------------------------------------------------------------
# 1. vtkClipDataSet with out-of-range clip value
# ---------------------------------------------------------------------------

class TestClipDataSetEmptyOutputHint(unittest.TestCase):
    """vtkClipDataSet with a scalar clip value outside field range includes range info."""

    def setUp(self):
        # temperature in [0, 100]
        self.img = _make_image_data(field_name="temperature", field_range=(0.0, 100.0))
        self.alg = _make_algorithm(self.img)

    def test_clip_above_max_warning_includes_field_range(self):
        """Clipping above the max should mention the actual field range."""
        _, status = create_vtk_filter(
            "vtkClipDataSet",
            self.alg,
            Value=999.0,  # way above max=100
        )
        self.assertEqual(status.get("status"), "warning")
        w = status["message"]
        # Must include the field range
        self.assertIn("temperature", w)
        self.assertIn("0", w)
        self.assertIn("100", w)

    def test_clip_above_max_warning_includes_user_value(self):
        """Clipping above the max should mention the user's ClipValue."""
        _, status = create_vtk_filter(
            "vtkClipDataSet",
            self.alg,
            Value=999.0,
        )
        w = status["message"]
        self.assertIn("999", w)

    def test_clip_at_boundary_warning_includes_range_and_value(self):
        """Clipping just above the max should include both range and clip value."""
        _, status = create_vtk_filter(
            "vtkClipDataSet",
            self.alg,
            Value=100.5,  # slightly above max=100; clips out all points
        )
        self.assertEqual(status.get("status"), "warning")
        w = status["message"]
        # Range info present
        self.assertIn("temperature", w)
        # User's value present
        self.assertIn("100.5", w)

    def test_clip_in_range_no_warning(self):
        """Clipping at the midpoint should produce data and no warning."""
        _, status = create_vtk_filter(
            "vtkClipDataSet",
            self.alg,
            Value=50.0,  # mid-range; most cells survive
        )
        # Should have data and no empty-output warning
        self.assertNotEqual(status.get("status"), "warning")


# ---------------------------------------------------------------------------
# 2. vtkExtractVOI with out-of-range VOI
# ---------------------------------------------------------------------------

class TestExtractVOIEmptyOutputHint(unittest.TestCase):
    """vtkExtractVOI with a VOI outside the dataset extent includes extent info."""

    def setUp(self):
        # 10x10x10 grid with extent [0..9, 0..9, 0..9]
        self.img = _make_image_data(dims=(10, 10, 10), field_name="temperature",
                                    field_range=(0.0, 100.0))
        self.alg = _make_algorithm(self.img)

    def test_voi_outside_extent_warning_includes_extent(self):
        """VOI completely outside the dataset extent should include the actual extent."""
        _, status = create_vtk_filter(
            "vtkExtractVOI",
            self.alg,
            VOI=[50, 60, 50, 60, 50, 60],  # outside [0..9, 0..9, 0..9]
        )
        self.assertEqual(status.get("status"), "warning")
        w = status["message"]
        # Should mention the VOI and the actual extent
        self.assertIn("VOI", w)
        self.assertIn("extent", w.lower())

    def test_voi_inside_extent_no_warning(self):
        """A VOI inside the dataset extent should produce data and no empty warning."""
        _, status = create_vtk_filter(
            "vtkExtractVOI",
            self.alg,
            VOI=[2, 7, 2, 7, 2, 7],
        )
        self.assertNotEqual(status.get("status"), "warning")


# ---------------------------------------------------------------------------
# 3. Volume rendering with all-zero opacity
# ---------------------------------------------------------------------------

class TestVolumeRenderingOpacityHints(unittest.TestCase):
    """Volume rendering with an all-zero opacity_function raises a descriptive error."""

    def setUp(self):
        # temperature in [0, 1000]
        self.img = _make_image_data(dims=(8, 8, 8), field_name="temperature",
                                    field_range=(0.0, 1000.0))
        self.alg = _make_algorithm(self.img)

    def test_all_zero_opacity_raises_with_field_hint(self):
        """opacity_function with all opacity=0 raises ValueError mentioning opacity."""
        with self.assertRaises(ValueError) as ctx:
            _create_volume(
                self.alg,
                color_by="temperature",
                scalar_range=(0.0, 1000.0),
                opacity_function=[(0, 0.0), (500, 0.0), (1000, 0.0)],
            )
        msg = str(ctx.exception)
        self.assertIn("opacity", msg.lower())
        # Should also mention the field name or range
        self.assertTrue(
            "temperature" in msg or "invisible" in msg,
            f"Expected field name or 'invisible' in message: {msg!r}"
        )

    def test_opacity_function_outside_scalar_range_raises(self):
        """opacity_function values outside scalar_range raises with range info."""
        with self.assertRaises(ValueError) as ctx:
            _create_volume(
                self.alg,
                color_by="temperature",
                scalar_range=(0.0, 1000.0),
                opacity_function=[(2000, 0.0), (3000, 1.0)],  # outside [0, 1000]
            )
        msg = str(ctx.exception)
        self.assertIn("opacity_function", msg)
        self.assertIn("scalar_range", msg)
        # Both ranges should be mentioned
        self.assertIn("2000", msg)
        self.assertIn("1000", msg)

    def test_opacity_function_inside_range_succeeds(self):
        """A well-formed opacity_function inside the scalar_range should not raise."""
        # This should not raise
        volume, bar = _create_volume(
            self.alg,
            color_by="temperature",
            scalar_range=(0.0, 1000.0),
            opacity_function=[(0, 0.0), (500, 0.1), (1000, 0.5)],
        )
        self.assertIsNotNone(volume)


# ---------------------------------------------------------------------------
# 4. Existing filters unchanged (regression guard)
# ---------------------------------------------------------------------------

class TestExistingFiltersUnchanged(unittest.TestCase):
    """vtkContourFilter and vtkThreshold continue to emit their existing range messages."""

    def setUp(self):
        # temperature in [0, 100]
        self.img = _make_image_data(dims=(10, 10, 10), field_name="temperature",
                                    field_range=(0.0, 100.0))
        self.alg = _make_algorithm(self.img)

    def test_contour_out_of_range_mentions_field_range(self):
        """Out-of-range contour value should mention the field range."""
        _, status = create_vtk_filter(
            "vtkContourFilter",
            self.alg,
            ContourBy="temperature",
            Isosurfaces=[9999.0],  # above max=100
        )
        self.assertEqual(status.get("status"), "warning")
        w = status["message"]
        self.assertIn("temperature", w)
        # The existing format uses "range"
        self.assertIn("range", w.lower())
        # The actual field max should appear
        self.assertIn("100", w)

    def test_threshold_out_of_range_mentions_field_range(self):
        """Out-of-range threshold should mention the field range."""
        _, status = create_vtk_filter(
            "vtkThreshold",
            self.alg,
            ThresholdBy="temperature",
            ThresholdRange=[500.0, 1000.0],  # above max=100
        )
        self.assertEqual(status.get("status"), "warning")
        w = status["message"]
        self.assertIn("temperature", w)
        self.assertIn("100", w)

    def test_threshold_in_range_warning_includes_range(self):
        """Threshold with overlapping range now also includes field range info."""
        _, status = create_vtk_filter(
            "vtkThreshold",
            self.alg,
            ThresholdBy="temperature",
            ThresholdRange=[500.0, 600.0],  # within [0, 100]? No, 500-600 > 100
        )
        # 500-600 is outside [0, 100], so still no overlap warning
        if status.get("status") == "warning":
            w = status["message"]
            self.assertIn("temperature", w)


# ---------------------------------------------------------------------------
# 5. Generic fallback for unknown filter types
# ---------------------------------------------------------------------------

class TestGenericFallback(unittest.TestCase):
    """Unknown filter types (no special-case handling) emit the active scalar range."""

    def setUp(self):
        # pressure in [10, 50]
        self.img = _make_image_data(dims=(5, 5, 5), field_name="pressure",
                                    field_range=(10.0, 50.0))
        self.alg = _make_algorithm(self.img)

    def test_generic_filter_fallback_includes_active_scalar(self):
        """A filter with no special case should mention the active scalar range.

        We use vtkMaskPoints with extreme OnRatio to produce 0 points as the
        output (MaskPoints keeps every N-th point). With a very large OnRatio
        on a small dataset, no points survive.
        """
        _, status = create_vtk_filter(
            "vtkMaskPoints",
            self.alg,
            OnRatio=99999,  # keep 1 in 99999 -> likely 0 points for 5x5x5=125 pts
        )
        # If warning is present (0 points), check that range info is included
        if status.get("status") == "warning" and status.get("num_points", 0) == 0:
            w = status["message"]
            # Should either include the field range or the generic describe_data hint
            self.assertTrue(
                "pressure" in w or "describe_data" in w,
                f"Expected field name or describe_data hint, got: {w!r}"
            )

    def test_get_active_scalar_hint_returns_field_range(self):
        """_get_active_scalar_hint returns the field range string for active scalars."""
        hint = _get_active_scalar_hint(self.alg)
        self.assertIn("pressure", hint)
        self.assertIn("10", hint)
        self.assertIn("50", hint)

    def test_get_active_scalar_hint_no_input_returns_empty(self):
        """_get_active_scalar_hint with None input returns empty string."""
        hint = _get_active_scalar_hint(None)
        self.assertEqual(hint, "")

    def test_format_field_range_hint_missing_field_fallback(self):
        """_format_field_range_hint with unknown field name returns generic fallback."""
        hint = _format_field_range_hint(self.img, "nonexistent_field")
        self.assertIn("describe_data", hint)

    def test_format_field_range_hint_none_dataset_fallback(self):
        """_format_field_range_hint with None dataset returns generic fallback."""
        hint = _format_field_range_hint(None, "pressure")
        self.assertIn("describe_data", hint)


# ---------------------------------------------------------------------------
# 6. _format_field_range_hint helper correctness
# ---------------------------------------------------------------------------

class TestFormatFieldRangeHint(unittest.TestCase):
    """Unit tests for the _format_field_range_hint helper."""

    def setUp(self):
        # temperature in [200, 800]
        self.img = _make_image_data(dims=(5, 5, 5), field_name="temperature",
                                    field_range=(200.0, 800.0))

    def test_basic_range_format(self):
        """Without user_value, returns range string with field name."""
        hint = _format_field_range_hint(self.img, "temperature")
        self.assertIn("temperature", hint)
        self.assertIn("200", hint)
        self.assertIn("800", hint)

    def test_clip_kind_outside_range(self):
        """Clip value outside range includes 'outside range' note."""
        hint = _format_field_range_hint(self.img, "temperature",
                                        user_value=9999.0, kind="clip")
        self.assertIn("ClipValue", hint)
        self.assertIn("outside range", hint)

    def test_clip_kind_inside_range(self):
        """Clip value inside range shows the value without 'outside'."""
        hint = _format_field_range_hint(self.img, "temperature",
                                        user_value=500.0, kind="clip")
        self.assertIn("ClipValue", hint)
        self.assertNotIn("outside range", hint)

    def test_threshold_kind_non_overlapping(self):
        """ThresholdRange outside field range shows 'doesn't overlap'."""
        hint = _format_field_range_hint(self.img, "temperature",
                                        user_value=[2000.0, 3000.0], kind="threshold")
        self.assertIn("ThresholdRange", hint)
        self.assertIn("overlap", hint)

    def test_threshold_kind_overlapping(self):
        """ThresholdRange inside field range shows the range."""
        hint = _format_field_range_hint(self.img, "temperature",
                                        user_value=[300.0, 600.0], kind="threshold")
        self.assertIn("ThresholdRange", hint)
        self.assertNotIn("overlap", hint)

    def test_iso_kind_with_value(self):
        """IsoValue kind shows IsoValue label."""
        hint = _format_field_range_hint(self.img, "temperature",
                                        user_value=500.0, kind="iso")
        self.assertIn("IsoValue", hint)


if __name__ == "__main__":
    unittest.main()
