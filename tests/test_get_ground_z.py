"""Tests for queries.get_ground_z().

Covers:
  - Structured grids (vtkStructuredGrid) with flat and non-flat bottom layers
  - vtkImageData (also has GetDimensions, should work the same)
  - Non-structured-grid types (vtkPolyData, vtkUnstructuredGrid) → error message
  - None input → error message
"""

import os
import sys

import pytest
import vtk
from vtk.util.numpy_support import numpy_to_vtk
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vislang import queries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_flat_structured_grid(nx=10, ny=8, nz=5, z_offset=0.0):
    """Create a vtkStructuredGrid with flat Z layers.

    Grid spans [0..nx-1] x [0..ny-1] x [z_offset .. z_offset + nz - 1].
    Bottom layer (iz=0) has z = z_offset for all (ix, iy).
    """
    grid = vtk.vtkStructuredGrid()
    grid.SetDimensions(nx, ny, nz)
    pts = vtk.vtkPoints()
    pts.Allocate(nx * ny * nz)
    for iz in range(nz):
        for iy in range(ny):
            for ix in range(nx):
                pts.InsertNextPoint(float(ix), float(iy), z_offset + float(iz))
    grid.SetPoints(pts)
    return grid


def _make_terrain_structured_grid(nx=10, ny=8, nz=5):
    """Create a vtkStructuredGrid where the bottom-layer Z varies with (ix, iy).

    Bottom layer Z = ix + iy  (so it's non-constant, like terrain).
    """
    grid = vtk.vtkStructuredGrid()
    grid.SetDimensions(nx, ny, nz)
    pts = vtk.vtkPoints()
    pts.Allocate(nx * ny * nz)
    for iz in range(nz):
        for iy in range(ny):
            for ix in range(nx):
                z_base = float(ix + iy)
                pts.InsertNextPoint(float(ix), float(iy), z_base + float(iz))
    grid.SetPoints(pts)
    return grid


def _make_image_data(dims=(10, 8, 5)):
    """Create a vtkImageData (uniform rectilinear grid)."""
    img = vtk.vtkImageData()
    img.SetDimensions(*dims)
    img.SetOrigin(0.0, 0.0, 2.5)
    img.SetSpacing(1.0, 1.0, 1.0)
    return img


def _make_poly_data():
    pts = vtk.vtkPoints()
    for i in range(5):
        pts.InsertNextPoint(float(i), 0.0, 0.0)
    poly = vtk.vtkPolyData()
    poly.SetPoints(pts)
    return poly


def _make_unstructured_grid():
    ug = vtk.vtkUnstructuredGrid()
    pts = vtk.vtkPoints()
    pts.InsertNextPoint(0.0, 0.0, 0.0)
    ug.SetPoints(pts)
    return ug


# ---------------------------------------------------------------------------
# None / missing data
# ---------------------------------------------------------------------------

class TestGetGroundZNone:

    def test_none_data_returns_error(self):
        result = queries.get_ground_z(None, 0.0, 0.0)
        assert result.startswith("Error")

    def test_none_mentions_no_data(self):
        result = queries.get_ground_z(None, 0.0, 0.0)
        assert "No data" in result or "no data" in result.lower()


# ---------------------------------------------------------------------------
# Non-structured-grid data types
# ---------------------------------------------------------------------------

class TestGetGroundZUnsupportedTypes:

    def test_polydata_returns_error(self):
        result = queries.get_ground_z(_make_poly_data(), 0.0, 0.0)
        assert result.startswith("Error")

    def test_polydata_error_mentions_data_type(self):
        result = queries.get_ground_z(_make_poly_data(), 0.0, 0.0)
        # VTK Python class names may appear as "PolyData" or "vtkPolyData"
        assert "PolyData" in result

    def test_polydata_error_suggests_alternative(self):
        """Error should hint at an alternative tool."""
        result = queries.get_ground_z(_make_poly_data(), 0.0, 0.0)
        assert "get_spatial_extent" in result

    def test_unstructured_grid_returns_error(self):
        result = queries.get_ground_z(_make_unstructured_grid(), 0.0, 0.0)
        assert result.startswith("Error")

    def test_unstructured_grid_error_mentions_data_type(self):
        result = queries.get_ground_z(_make_unstructured_grid(), 0.0, 0.0)
        # VTK Python class names may appear as "UnstructuredGrid" or "vtkUnstructuredGrid"
        assert "UnstructuredGrid" in result


# ---------------------------------------------------------------------------
# Structured grid (vtkStructuredGrid) — flat bottom layer
# ---------------------------------------------------------------------------

class TestGetGroundZFlatGrid:

    @pytest.fixture(scope="class")
    def flat_grid(self):
        return _make_flat_structured_grid(nx=10, ny=8, nz=5, z_offset=3.0)

    def test_returns_string(self, flat_grid):
        result = queries.get_ground_z(flat_grid, 5.0, 4.0)
        assert isinstance(result, str)

    def test_not_error(self, flat_grid):
        result = queries.get_ground_z(flat_grid, 5.0, 4.0)
        assert not result.startswith("Error"), result

    def test_reports_z_value(self, flat_grid):
        result = queries.get_ground_z(flat_grid, 5.0, 4.0)
        # Bottom layer z_offset=3.0, so iz=0 gives z=3.0
        assert "3.0" in result

    def test_output_contains_multiple_layers(self, flat_grid):
        result = queries.get_ground_z(flat_grid, 5.0, 4.0)
        # Should list z at iz=0, iz=1, iz=2, etc.
        assert "iz=0" in result
        assert "iz=1" in result

    def test_nearest_xy_reasonable(self, flat_grid):
        """Nearest grid point should be close to the queried (x, y)."""
        result = queries.get_ground_z(flat_grid, 3.0, 2.0)
        assert "3.0" in result or "2.0" in result  # xy coords appear in output


# ---------------------------------------------------------------------------
# Structured grid (vtkStructuredGrid) — terrain-following bottom layer
# ---------------------------------------------------------------------------

class TestGetGroundZTerrainGrid:

    @pytest.fixture(scope="class")
    def terrain_grid(self):
        return _make_terrain_structured_grid(nx=10, ny=8, nz=5)

    def test_returns_string(self, terrain_grid):
        result = queries.get_ground_z(terrain_grid, 3.0, 2.0)
        assert isinstance(result, str)

    def test_not_error(self, terrain_grid):
        result = queries.get_ground_z(terrain_grid, 3.0, 2.0)
        assert not result.startswith("Error"), result

    def test_z_varies_with_position(self, terrain_grid):
        """Z at (0,0) should differ from Z at (9,7) for terrain grid."""
        result_corner = queries.get_ground_z(terrain_grid, 0.0, 0.0)
        result_far = queries.get_ground_z(terrain_grid, 9.0, 7.0)
        # Both should succeed
        assert not result_corner.startswith("Error")
        assert not result_far.startswith("Error")
        # Z=0+0=0 at origin, Z=9+7=16 at far corner — values differ
        assert result_corner != result_far

    def test_iz0_z_correct_at_origin(self, terrain_grid):
        """At (0,0), iz=0 z should equal 0+0=0."""
        result = queries.get_ground_z(terrain_grid, 0.0, 0.0)
        # iz=0: z = 0+0 = 0.0
        assert "iz=0: z=0.0" in result

    def test_iz0_z_correct_at_far_corner(self, terrain_grid):
        """At (9,7), iz=0 z should equal 9+7=16."""
        result = queries.get_ground_z(terrain_grid, 9.0, 7.0)
        assert "iz=0: z=16.0" in result


# ---------------------------------------------------------------------------
# vtkImageData (has GetDimensions, uniform spacing)
# ---------------------------------------------------------------------------

class TestGetGroundZImageData:

    @pytest.fixture(scope="class")
    def image_data(self):
        return _make_image_data(dims=(10, 8, 5))

    def test_returns_string(self, image_data):
        result = queries.get_ground_z(image_data, 5.0, 4.0)
        assert isinstance(result, str)

    def test_not_error(self, image_data):
        result = queries.get_ground_z(image_data, 5.0, 4.0)
        assert not result.startswith("Error"), result

    def test_reports_z_origin(self, image_data):
        """Bottom layer z should match the origin z (2.5)."""
        result = queries.get_ground_z(image_data, 5.0, 4.0)
        assert "2.5" in result
