"""Shared utilities for tracked-execution examples.

Provides create_test_dataset() for making a synthetic PyVista mesh
large enough that caching benefits are measurable.
"""

import os
import tempfile

import numpy as np
import pyvista as pv


def create_test_dataset(dims=(100, 100, 100), seed=42):
    """Create a synthetic structured grid with Temperature and Pressure fields.

    The mesh is saved to a temporary VTK file whose path is returned.
    Call cleanup(path) when done.

    Args:
        dims: Tuple of (nx, ny, nz) for the ImageData grid.
        seed: Random seed for reproducibility.

    Returns:
        Absolute path to the saved .vtk file.
    """
    mesh = pv.ImageData(dimensions=dims)
    rng = np.random.RandomState(seed)
    mesh["Temperature"] = rng.rand(mesh.n_points) * 1000
    mesh["Pressure"] = rng.rand(mesh.n_points) * 100
    # Add a gradient-based field for more interesting thresholding
    coords = mesh.points
    mesh["Gradient"] = np.sqrt(
        coords[:, 0] ** 2 + coords[:, 1] ** 2 + coords[:, 2] ** 2
    )
    path = tempfile.mktemp(suffix=".vtk")
    mesh.save(path)
    return path


def cleanup(path):
    """Remove a temporary dataset file."""
    try:
        os.unlink(path)
    except OSError:
        pass
