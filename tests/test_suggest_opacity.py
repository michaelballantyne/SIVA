"""Tests for the rarity-based CDF opacity suggestion algorithm."""

import numpy as np
import pytest
import vtk
from vtkmodules.util.numpy_support import numpy_to_vtk

from vislang.queries import _histogram_opacity_points


def _make_vtk_array(values):
    """Convert a numpy array to a vtkFloatArray."""
    vtk_arr = vtk.vtkFloatArray()
    vtk_arr.SetNumberOfTuples(len(values))
    for i, v in enumerate(values):
        vtk_arr.SetValue(i, float(v))
    return vtk_arr


def test_skewed_distribution_rarity_ramp():
    """High-end (rare) values should get higher opacity than low-end (common) values."""
    rng = np.random.default_rng(42)
    # Skewed: 80% of values in [0, 0.3], 20% in [0.7, 1.0]
    low = rng.uniform(0.0, 0.3, 8000)
    high = rng.uniform(0.7, 1.0, 2000)
    values = np.concatenate([low, high])

    arr = _make_vtk_array(values)
    points = _histogram_opacity_points(arr, scalar_range=(0.0, 1.0), num_points=8)

    assert points is not None

    def opacity_at(target):
        # Nearest control point
        nearest = min(points, key=lambda pt: abs(pt[0] - target))
        return nearest[1]

    # 0.15 is deep in the dense (common) region — CDF ~0.4, should be low opacity
    # 0.85 is deep in the sparse (rare) region — CDF ~0.9, should be higher opacity
    op_dense = opacity_at(0.15)
    op_rare = opacity_at(0.85)
    assert op_dense < op_rare, (
        f"Expected opacity at dense value (0.15) < opacity at rare value (0.85), "
        f"got {op_dense} vs {op_rare}"
    )


def test_uniform_distribution_rarity_ramp():
    """For a uniform distribution opacity should increase from lo to hi (CDF ramp)."""
    rng = np.random.default_rng(0)
    values = rng.uniform(0.0, 1.0, 10000)
    arr = _make_vtk_array(values)
    points = _histogram_opacity_points(arr, scalar_range=(0.0, 1.0), num_points=8)

    assert points is not None
    # Opacities increase left to right (CDF increases)
    opacities = [op for _, op in points]
    # First opacity should be near 0, last near max_opacity
    assert opacities[0] <= opacities[-1], (
        f"Expected first opacity <= last opacity for uniform dist, "
        f"got {opacities[0]} vs {opacities[-1]}"
    )

    p25 = float(np.percentile(values, 25))
    p75 = float(np.percentile(values, 75))

    def opacity_at(target):
        nearest = min(points, key=lambda pt: abs(pt[0] - target))
        return nearest[1]

    assert opacity_at(p25) <= opacity_at(p75), (
        "For uniform distribution, lower values should have <= opacity than higher values"
    )


def test_max_opacity_not_exceeded():
    """No opacity value in the output should exceed max_opacity."""
    rng = np.random.default_rng(7)
    values = rng.exponential(scale=1.0, size=5000)
    arr = _make_vtk_array(values)

    for max_op in [0.5, 0.8, 1.0]:
        points = _histogram_opacity_points(
            arr, scalar_range=(0.0, float(np.max(values))), num_points=8, max_opacity=max_op
        )
        assert points is not None
        for val, op in points:
            assert op <= max_op + 1e-9, (
                f"Opacity {op} exceeds max_opacity {max_op} at value {val}"
            )
