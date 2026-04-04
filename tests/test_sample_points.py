"""Tests for batch point probing via queries.sample_points().

Uses the synthetic 64x64x64 dataset which has:
  - temperature: Gaussian blob centered at (0.5, 0.5, 0.5), peak ~1000
  - density: linear gradient along X, 0 at x=0, 1.225 at x=1
  - velocity: 3-component vector field (rigid-body rotation about Z)
"""

import os
import sys

import pytest
import vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vislang import queries

SYNTHETIC_DATA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "datasets", "synthetic", "data", "output.vti",
)


@pytest.fixture(scope="module")
def synthetic_data():
    """Load the synthetic VTI dataset."""
    if not os.path.exists(SYNTHETIC_DATA):
        pytest.skip(
            f"Synthetic dataset not found at {SYNTHETIC_DATA}. "
            "Run: bash datasets/synthetic/download.sh"
        )
    reader = vtk.vtkXMLImageDataReader()
    reader.SetFileName(SYNTHETIC_DATA)
    reader.Update()
    return reader.GetOutput()


# ---------------------------------------------------------------------------
# Basic structural tests
# ---------------------------------------------------------------------------

class TestSamplePointsStructure:

    def test_returns_list(self, synthetic_data):
        result = queries.sample_points(synthetic_data, [(0.5, 0.5, 0.5)])
        assert isinstance(result, list)

    def test_one_dict_per_point(self, synthetic_data):
        pts = [(0.1, 0.1, 0.1), (0.5, 0.5, 0.5), (0.9, 0.9, 0.9)]
        result = queries.sample_points(synthetic_data, pts)
        assert len(result) == 3

    def test_dict_has_required_keys(self, synthetic_data):
        result = queries.sample_points(synthetic_data, [(0.5, 0.5, 0.5)])
        entry = result[0]
        assert "query" in entry
        assert "nearest" in entry
        assert "point_id" in entry
        assert "outside_bounds" in entry

    def test_query_coords_preserved(self, synthetic_data):
        result = queries.sample_points(synthetic_data, [(0.123, 0.456, 0.789)])
        assert result[0]["query"] == (0.123, 0.456, 0.789)

    def test_nearest_is_tuple_of_3(self, synthetic_data):
        result = queries.sample_points(synthetic_data, [(0.5, 0.5, 0.5)])
        nearest = result[0]["nearest"]
        assert isinstance(nearest, tuple)
        assert len(nearest) == 3

    def test_point_id_is_int(self, synthetic_data):
        result = queries.sample_points(synthetic_data, [(0.5, 0.5, 0.5)])
        assert isinstance(result[0]["point_id"], int)
        assert result[0]["point_id"] >= 0

    def test_empty_points_list(self, synthetic_data):
        result = queries.sample_points(synthetic_data, [])
        assert result == []

    def test_none_data_returns_empty(self):
        result = queries.sample_points(None, [(0.5, 0.5, 0.5)])
        assert result == []


# ---------------------------------------------------------------------------
# Field selection
# ---------------------------------------------------------------------------

class TestSamplePointsFields:

    def test_all_fields_returned_when_none_specified(self, synthetic_data):
        result = queries.sample_points(synthetic_data, [(0.5, 0.5, 0.5)], fields=None)
        entry = result[0]
        # Synthetic data has temperature, density, velocity
        assert "temperature" in entry
        assert "density" in entry
        assert "velocity" in entry

    def test_only_requested_fields_returned(self, synthetic_data):
        result = queries.sample_points(
            synthetic_data, [(0.5, 0.5, 0.5)], fields=["temperature"]
        )
        entry = result[0]
        assert "temperature" in entry
        assert "density" not in entry
        assert "velocity" not in entry

    def test_missing_field_is_none(self, synthetic_data):
        result = queries.sample_points(
            synthetic_data, [(0.5, 0.5, 0.5)], fields=["nonexistent_field"]
        )
        assert result[0]["nonexistent_field"] is None

    def test_scalar_field_is_float(self, synthetic_data):
        result = queries.sample_points(
            synthetic_data, [(0.5, 0.5, 0.5)], fields=["temperature"]
        )
        val = result[0]["temperature"]
        assert isinstance(val, float)

    def test_vector_field_is_tuple(self, synthetic_data):
        result = queries.sample_points(
            synthetic_data, [(0.5, 0.5, 0.5)], fields=["velocity"]
        )
        val = result[0]["velocity"]
        assert isinstance(val, tuple)
        assert len(val) == 3

    def test_multiple_fields(self, synthetic_data):
        result = queries.sample_points(
            synthetic_data, [(0.5, 0.5, 0.5)], fields=["temperature", "density"]
        )
        entry = result[0]
        assert "temperature" in entry
        assert "density" in entry
        assert "velocity" not in entry


# ---------------------------------------------------------------------------
# Value correctness
# ---------------------------------------------------------------------------

class TestSamplePointsValues:

    def test_center_temperature_near_peak(self, synthetic_data):
        """Temperature at dataset center should be near the Gaussian peak (1000)."""
        result = queries.sample_points(
            synthetic_data, [(0.5, 0.5, 0.5)], fields=["temperature"]
        )
        assert result[0]["temperature"] > 900

    def test_density_low_at_x0(self, synthetic_data):
        """Density at x=0 should be near 0 (linear gradient)."""
        result = queries.sample_points(
            synthetic_data, [(0.0, 0.5, 0.5)], fields=["density"]
        )
        assert result[0]["density"] < 0.1

    def test_density_high_at_x1(self, synthetic_data):
        """Density at x=1 should be near 1.225."""
        result = queries.sample_points(
            synthetic_data, [(1.0, 0.5, 0.5)], fields=["density"]
        )
        assert result[0]["density"] > 1.0

    def test_batch_consistent_with_single(self, synthetic_data):
        """Batch query should return same nearest point as single sample_point."""
        pts = [(0.2, 0.3, 0.4), (0.7, 0.6, 0.5)]
        batch = queries.sample_points(synthetic_data, pts, fields=["temperature"])

        for i, (x, y, z) in enumerate(pts):
            single_text = queries.sample_point(synthetic_data, x, y, z, fields=["temperature"])
            # Extract temperature value from single text output
            for line in single_text.splitlines():
                if "temperature:" in line:
                    single_val = float(line.split(":")[1].strip())
                    break
            batch_val = batch[i]["temperature"]
            assert abs(batch_val - single_val) < 1e-3, (
                f"Point {i}: batch={batch_val}, single={single_val}"
            )


# ---------------------------------------------------------------------------
# Out-of-bounds detection
# ---------------------------------------------------------------------------

class TestSamplePointsOutOfBounds:

    def test_inside_bounds_not_flagged(self, synthetic_data):
        result = queries.sample_points(synthetic_data, [(0.5, 0.5, 0.5)])
        assert result[0]["outside_bounds"] is False

    def test_outside_bounds_flagged(self, synthetic_data):
        result = queries.sample_points(synthetic_data, [(100.0, 100.0, 100.0)])
        assert result[0]["outside_bounds"] is True

    def test_outside_still_returns_nearest(self, synthetic_data):
        """Even for out-of-bounds points we return the nearest grid point."""
        result = queries.sample_points(
            synthetic_data, [(100.0, 100.0, 100.0)], fields=["temperature"]
        )
        entry = result[0]
        assert entry["nearest"] is not None
        assert entry["point_id"] >= 0

    def test_mixed_in_and_out(self, synthetic_data):
        pts = [(0.5, 0.5, 0.5), (999.0, 999.0, 999.0)]
        result = queries.sample_points(synthetic_data, pts)
        assert result[0]["outside_bounds"] is False
        assert result[1]["outside_bounds"] is True


# ---------------------------------------------------------------------------
# Formatted output
# ---------------------------------------------------------------------------

class TestFormatSamplePoints:

    def test_format_nonempty(self, synthetic_data):
        result = queries.sample_points(
            synthetic_data, [(0.5, 0.5, 0.5)], fields=["temperature"]
        )
        text = queries.format_sample_points(result)
        assert "Point 1" in text
        assert "temperature" in text

    def test_format_multiple_points(self, synthetic_data):
        pts = [(0.1, 0.1, 0.1), (0.9, 0.9, 0.9)]
        result = queries.sample_points(synthetic_data, pts, fields=["temperature"])
        text = queries.format_sample_points(result)
        assert "Point 1" in text
        assert "Point 2" in text

    def test_format_outside_bounds_note(self, synthetic_data):
        result = queries.sample_points(synthetic_data, [(999.0, 999.0, 999.0)])
        text = queries.format_sample_points(result)
        assert "outside" in text.lower()

    def test_format_empty_results(self):
        text = queries.format_sample_points([])
        assert "No results" in text

    def test_format_missing_field_noted(self, synthetic_data):
        result = queries.sample_points(
            synthetic_data, [(0.5, 0.5, 0.5)], fields=["bad_field"]
        )
        text = queries.format_sample_points(result)
        assert "not found" in text or "bad_field" in text

    def test_format_shows_nearest_coords(self, synthetic_data):
        result = queries.sample_points(
            synthetic_data, [(0.5, 0.5, 0.5)], fields=["temperature"]
        )
        text = queries.format_sample_points(result)
        assert "Nearest grid point" in text

    def test_format_shows_batch_count(self, synthetic_data):
        pts = [(0.1, 0.2, 0.3), (0.4, 0.5, 0.6), (0.7, 0.8, 0.9)]
        result = queries.sample_points(synthetic_data, pts, fields=["temperature"])
        text = queries.format_sample_points(result)
        assert "3" in text
