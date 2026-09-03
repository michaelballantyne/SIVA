"""Tests for vector-field (multi-component array) handling in siva.queries.

get_histogram() and suggest_isosurface() used to call arr.GetRange() /
vtk_to_numpy(arr) directly on multi-component arrays: GetRange() silently
reports component 0's range, and a flattened multi-component array mixes
components together in the histogram. Both now use per-tuple magnitude
(_field_values_and_range), matching the convention get_rich_field_stats
(describe_data) already used.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import vtk

from siva.queries import get_histogram, suggest_isosurface, _field_values_and_range


def _make_vector_data(field_name="velocity"):
    """5-point vtkImageData; component 0 is symmetric around zero ([-5, 5])
    but magnitude is asymmetric and non-negative ([0, 13])."""
    img = vtk.vtkImageData()
    img.SetDimensions(5, 1, 1)
    img.SetOrigin(0.0, 0.0, 0.0)
    img.SetSpacing(1.0, 1.0, 1.0)
    tuples = [(-5, 0, 0), (5, 12, 0), (0, 0, 0), (-3, 4, 0), (3, -4, 0)]
    arr = vtk.vtkFloatArray()
    arr.SetName(field_name)
    arr.SetNumberOfComponents(3)
    arr.SetNumberOfTuples(len(tuples))
    for i, t in enumerate(tuples):
        arr.SetTuple3(i, *t)
    img.GetPointData().AddArray(arr)
    return img


def _make_scalar_data(field_name="temperature", lo=0.0, hi=100.0, n=50):
    img = vtk.vtkImageData()
    img.SetDimensions(n, 1, 1)
    img.SetOrigin(0.0, 0.0, 0.0)
    img.SetSpacing(1.0, 1.0, 1.0)
    arr = vtk.vtkFloatArray()
    arr.SetName(field_name)
    arr.SetNumberOfComponents(1)
    vals = np.linspace(lo, hi, n)
    arr.SetNumberOfTuples(n)
    for i, v in enumerate(vals):
        arr.SetValue(i, float(v))
    img.GetPointData().AddArray(arr)
    return img


class TestFieldValuesAndRange:
    def test_scalar_unchanged(self):
        data = _make_scalar_data(lo=0.0, hi=100.0, n=10)
        arr = data.GetPointData().GetArray("temperature")
        vals, rng = _field_values_and_range(arr)
        assert vals.shape == (10,)
        assert rng[0] == 0.0
        assert abs(rng[1] - 100.0) < 1e-6

    def test_vector_uses_magnitude(self):
        data = _make_vector_data()
        arr = data.GetPointData().GetArray("velocity")
        vals, rng = _field_values_and_range(arr)
        assert vals.shape == (5,)
        assert rng[0] == 0.0
        assert abs(rng[1] - 13.0) < 1e-4
        # Never the raw component-0 range.
        assert rng != (-5.0, 5.0)


class TestGetHistogramVectorFields:
    def test_vector_field_histogram_uses_magnitude_range(self):
        data = _make_vector_data()
        text = get_histogram(data, "velocity", bins=5)
        assert "magnitude" in text
        assert "Range: [0" in text
        # Should not report component 0's signed range.
        assert "[-5" not in text

    def test_scalar_field_histogram_unaffected(self):
        data = _make_scalar_data(lo=0.0, hi=100.0, n=50)
        text = get_histogram(data, "temperature", bins=5)
        assert "magnitude" not in text
        assert "Range: [0" in text


class TestSuggestIsosurfaceVectorFields:
    def test_vector_field_uses_magnitude_range_and_hints_calculator(self):
        data = _make_vector_data()
        text = suggest_isosurface(data, "velocity")
        assert "magnitude" in text
        assert "Range: [0" in text
        assert "calculator(" in text
        assert "mag(velocity)" in text

    def test_scalar_field_unaffected(self):
        data = _make_scalar_data(lo=0.0, hi=100.0, n=50)
        text = suggest_isosurface(data, "temperature")
        assert "magnitude" not in text
        assert "ContourBy=\"temperature\"" in text


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
