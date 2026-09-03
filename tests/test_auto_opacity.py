"""Tests for the histogram-guided auto-opacity default of volume rendering.

`create_show(representation="Volume", ...)` without an explicit
`opacity_function` derives one from the field's histogram via
`siva.queries._histogram_opacity_points` (called through
`siva.filters._auto_opacity`). These tests pin the contract of that default:
it must succeed, ramp from transparent at the low end of `scalar_range` to
`max_opacity` at the high end, stay monotone non-decreasing, and de-emphasise
the dominant (background) histogram bin.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest
import vtk
from vtk.util.numpy_support import numpy_to_vtk

from siva.filters import create_show, _auto_opacity
from siva.queries import _histogram_opacity_points


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skewed_volume(field="temperature", dims=(16, 16, 16), lo=0.0, hi=100.0,
                   hot_fraction=0.05):
    """vtkImageData whose field is mostly `lo` (background) with a hot tail.

    That's the shape auto-opacity exists for: a big ambient spike at the low
    end plus a small high-value feature.
    """
    nx, ny, nz = dims
    n = nx * ny * nz
    vals = np.full(n, lo, dtype=np.float64)
    n_hot = max(2, int(n * hot_fraction))
    vals[-n_hot:] = np.linspace(lo + 0.5 * (hi - lo), hi, n_hot)

    img = vtk.vtkImageData()
    img.SetDimensions(nx, ny, nz)
    arr = numpy_to_vtk(vals, deep=True)
    arr.SetName(field)
    img.GetPointData().AddArray(arr)
    img.GetPointData().SetScalars(arr)
    return img


def _otf_nodes(otf):
    """Return the [(value, opacity), ...] nodes of a vtkPiecewiseFunction."""
    nodes = []
    node = [0.0] * 4
    for i in range(otf.GetSize()):
        otf.GetNodeValue(i, node)
        nodes.append((node[0], node[1]))
    return nodes


def _scalar_array(img, field="temperature"):
    return img.GetPointData().GetArray(field)


# ---------------------------------------------------------------------------
# _histogram_opacity_points unit tests
# ---------------------------------------------------------------------------

def test_points_endpoints_and_count():
    img = _skewed_volume()
    pts = _histogram_opacity_points(_scalar_array(img), (0.0, 100.0),
                                    n_bins=32, n_points=8, max_opacity=0.6)
    assert len(pts) == 8
    assert pts[0] == (0.0, 0.0), f"low end should be transparent, got {pts[0]}"
    assert pts[-1] == pytest.approx((100.0, 0.6)), \
        f"high end should reach max_opacity, got {pts[-1]}"


def test_points_monotone_and_spanning_range():
    img = _skewed_volume(lo=-5.0, hi=20.0)
    pts = _histogram_opacity_points(_scalar_array(img), (-5.0, 20.0))
    values = [v for v, _ in pts]
    opacities = [o for _, o in pts]
    assert values == sorted(values), "control points must be ordered by value"
    assert values[0] == pytest.approx(-5.0)
    assert values[-1] == pytest.approx(20.0)
    assert all(b >= a - 1e-12 for a, b in zip(opacities, opacities[1:])), \
        f"opacity should be non-decreasing, got {opacities}"
    assert all(0.0 <= o <= 1.0 for o in opacities)


def test_dominant_bin_is_de_emphasised():
    """The background spike gets less opacity than a plain linear ramp."""
    img = _skewed_volume(lo=0.0, hi=100.0, hot_fraction=0.02)
    max_opacity = 0.6
    pts = _histogram_opacity_points(_scalar_array(img), (0.0, 100.0),
                                    n_points=8, max_opacity=max_opacity)
    # Second point sits just above the ambient spike at lo.
    value, opacity = pts[1]
    linear = (value - 0.0) / 100.0 * max_opacity
    assert opacity < linear, \
        f"dominant bin should be suppressed below the linear ramp ({opacity} vs {linear})"
    assert opacity < 0.1, f"background opacity should stay faint, got {opacity}"


def test_constant_field_degenerate_range():
    img = _skewed_volume(lo=1.0, hi=1.0, hot_fraction=0.5)
    pts = _histogram_opacity_points(_scalar_array(img), (1.0, 1.0))
    assert len(pts) >= 2
    assert pts[0][1] == 0.0
    assert pts[-1][1] == pytest.approx(0.6)


def test_vector_field_uses_magnitude():
    """A 3-component array is reduced to per-tuple magnitude, not flattened."""
    n = 512
    raw = np.zeros((n, 3), dtype=np.float64)
    raw[:, 0] = np.linspace(0.0, 3.0, n)
    arr = numpy_to_vtk(raw.ravel(), deep=True)
    arr.SetNumberOfComponents(3)
    arr.SetNumberOfTuples(n)
    arr.SetName("velocity")

    pts = _histogram_opacity_points(arr, None, n_points=5)
    assert len(pts) == 5
    assert pts[0][0] == pytest.approx(0.0)
    assert pts[-1][0] == pytest.approx(3.0), \
        f"range should be magnitude range, got {pts[-1][0]}"


def test_auto_opacity_wrapper_delegates():
    """filters._auto_opacity forwards its num_* kwargs without a TypeError."""
    img = _skewed_volume()
    pts = _auto_opacity(_scalar_array(img), (0.0, 100.0),
                        num_bins=16, num_points=5, max_opacity=0.4)
    assert len(pts) == 5
    assert pts[-1][1] == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# create_show integration: the default path must not raise
# ---------------------------------------------------------------------------

def test_create_show_volume_without_opacity_function():
    img = _skewed_volume()
    vol, _bar = create_show(img, representation="Volume",
                            color_by="temperature",
                            scalar_range=(0.0, 100.0))
    assert isinstance(vol, vtk.vtkVolume)

    otf = vol.GetProperty().GetScalarOpacity()
    nodes = _otf_nodes(otf)
    assert len(nodes) >= 2, f"expected an auto-generated ramp, got {nodes}"

    values = [v for v, _ in nodes]
    opacities = [o for _, o in nodes]
    assert values[0] == pytest.approx(0.0)
    assert values[-1] == pytest.approx(100.0)
    assert opacities[0] == pytest.approx(0.0), \
        f"low end should be transparent, got {opacities[0]}"
    assert opacities[-1] > 0.3, \
        f"high end should be substantially opaque, got {opacities[-1]}"
    assert all(b >= a - 1e-12 for a, b in zip(opacities, opacities[1:])), \
        f"auto opacity should be monotone non-decreasing, got {opacities}"


def test_create_show_volume_opacity_scales_auto_ramp():
    """`opacity` acts as a global scale on the auto-generated ramp."""
    img = _skewed_volume()
    vol, _bar = create_show(img, representation="Volume",
                            color_by="temperature",
                            scalar_range=(0.0, 100.0),
                            opacity=0.5)
    nodes = _otf_nodes(vol.GetProperty().GetScalarOpacity())
    assert nodes[-1][1] == pytest.approx(0.6 * 0.5, abs=1e-6), \
        f"opacity should multiply the auto ramp, got {nodes[-1]}"
