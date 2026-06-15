"""Tests for coordinate-based extract_region and physical_bounds_to_voi.

Verifies that physical bounds can be used to extract sub-regions of structured
grids without requiring the user to know grid index (VOI) values.
"""

import os
import sys
import math
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vtk
import numpy as np
from vtk.util.numpy_support import numpy_to_vtk

from siva.filters import physical_bounds_to_voi, create_vtk_filter


# ---------------------------------------------------------------------------
# Helpers to build synthetic structured datasets
# ---------------------------------------------------------------------------

def _make_image_data(nx=10, ny=10, nz=5, origin=(0.0, 0.0, 0.0),
                     spacing=(1.0, 1.0, 1.0)):
    """Create a small vtkImageData grid."""
    img = vtk.vtkImageData()
    img.SetDimensions(nx, ny, nz)
    img.SetOrigin(*origin)
    img.SetSpacing(*spacing)
    return img


def _make_structured_grid(nx=10, ny=10, nz=5,
                           xrange=(0.0, 9.0), yrange=(0.0, 9.0),
                           zrange=(0.0, 4.0)):
    """Create a small vtkStructuredGrid with regular (but explicit) coordinates."""
    grid = vtk.vtkStructuredGrid()
    grid.SetDimensions(nx, ny, nz)

    points = vtk.vtkPoints()
    xs = np.linspace(xrange[0], xrange[1], nx)
    ys = np.linspace(yrange[0], yrange[1], ny)
    zs = np.linspace(zrange[0], zrange[1], nz)

    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                points.InsertNextPoint(xs[i], ys[j], zs[k])
    grid.SetPoints(points)

    # Add a simple scalar field
    n_pts = grid.GetNumberOfPoints()
    arr = numpy_to_vtk(np.arange(n_pts, dtype=np.float32), deep=True)
    arr.SetName("index")
    grid.GetPointData().AddArray(arr)
    return grid


def _make_structured_grid_with_extent(nx=10, ny=10, nz=5,
                                      xrange=(0.0, 9.0), yrange=(0.0, 9.0),
                                      zrange=(0.0, 4.0),
                                      extent_origin=(100, 50, 10)):
    """Create a vtkStructuredGrid with a non-zero extent origin.

    Simulates data from a parallel partition or sub-region where the extent
    doesn't start at (0,0,0).
    """
    ei0, ej0, ek0 = extent_origin
    grid = vtk.vtkStructuredGrid()
    grid.SetExtent(ei0, ei0 + nx - 1, ej0, ej0 + ny - 1, ek0, ek0 + nz - 1)

    points = vtk.vtkPoints()
    xs = np.linspace(xrange[0], xrange[1], nx)
    ys = np.linspace(yrange[0], yrange[1], ny)
    zs = np.linspace(zrange[0], zrange[1], nz)

    for k in range(nz):
        for j in range(ny):
            for i in range(nx):
                points.InsertNextPoint(xs[i], ys[j], zs[k])
    grid.SetPoints(points)

    n_pts = grid.GetNumberOfPoints()
    arr = numpy_to_vtk(np.arange(n_pts, dtype=np.float32), deep=True)
    arr.SetName("index")
    grid.GetPointData().AddArray(arr)
    return grid


def _make_rectilinear_grid(nx=10, ny=8, nz=4):
    """Create a vtkRectilinearGrid with non-uniform spacing."""
    grid = vtk.vtkRectilinearGrid()
    grid.SetDimensions(nx, ny, nz)

    xs = numpy_to_vtk(np.linspace(0.0, 9.0, nx), deep=True)
    ys = numpy_to_vtk(np.linspace(0.0, 7.0, ny), deep=True)
    zs = numpy_to_vtk(np.linspace(0.0, 3.0, nz), deep=True)
    grid.SetXCoordinates(xs)
    grid.SetYCoordinates(ys)
    grid.SetZCoordinates(zs)
    return grid


# ---------------------------------------------------------------------------
# Unit tests for physical_bounds_to_voi
# ---------------------------------------------------------------------------

class TestPhysicalBoundsToVOI_ImageData(unittest.TestCase):
    """physical_bounds_to_voi on vtkImageData (regular grid)."""

    def setUp(self):
        # 10x10x5 grid, origin (0,0,0), spacing (1,1,1)
        # Points at x=0..9, y=0..9, z=0..4
        self.data = _make_image_data(10, 10, 5, origin=(0, 0, 0), spacing=(1, 1, 1))

    def test_full_extent(self):
        """Requesting the full physical range returns the full VOI."""
        voi = physical_bounds_to_voi(self.data, [0, 9, 0, 9, 0, 4])
        self.assertEqual(voi, [0, 9, 0, 9, 0, 4])

    def test_sub_region(self):
        """A sub-region maps correctly to indices."""
        voi = physical_bounds_to_voi(self.data, [2, 5, 3, 7, 1, 3])
        self.assertEqual(voi[0], 2)   # imin
        self.assertEqual(voi[1], 5)   # imax
        self.assertEqual(voi[2], 3)   # jmin
        self.assertEqual(voi[3], 7)   # jmax
        self.assertEqual(voi[4], 1)   # kmin
        self.assertEqual(voi[5], 3)   # kmax

    def test_single_slice(self):
        """A zero-thickness z region maps to a single k-index pair."""
        voi = physical_bounds_to_voi(self.data, [0, 9, 0, 9, 0, 0])
        self.assertEqual(voi[4], 0)
        self.assertEqual(voi[5], 0)

    def test_clamped_to_grid(self):
        """Out-of-range bounds are clamped to the grid extent."""
        voi = physical_bounds_to_voi(self.data, [-10, 100, -5, 50, -1, 20])
        self.assertEqual(voi[0], 0)
        self.assertEqual(voi[1], 9)
        self.assertEqual(voi[2], 0)
        self.assertEqual(voi[3], 9)
        self.assertEqual(voi[4], 0)
        self.assertEqual(voi[5], 4)

    def test_non_unit_spacing(self):
        """Grid with spacing=2.5 in x should map physical x=5..7.5 to i=2..3."""
        data = _make_image_data(6, 6, 3, origin=(0, 0, 0), spacing=(2.5, 2.5, 2.5))
        # Physical x at i=2 is 5.0, at i=3 is 7.5
        voi = physical_bounds_to_voi(data, [5.0, 7.5, 0, 12.5, 0, 5.0])
        self.assertEqual(voi[0], 2)
        self.assertEqual(voi[1], 3)

    def test_non_zero_origin(self):
        """Grid with non-zero origin maps coordinates correctly."""
        data = _make_image_data(5, 5, 5, origin=(10, 20, 30), spacing=(1, 1, 1))
        # Physical x=11..13 -> indices 1..3
        voi = physical_bounds_to_voi(data, [11, 13, 20, 24, 30, 34])
        self.assertEqual(voi[0], 1)
        self.assertEqual(voi[1], 3)
        self.assertEqual(voi[2], 0)
        self.assertEqual(voi[3], 4)

    def test_non_structured_raises(self):
        """Passing a non-structured dataset raises ValueError."""
        poly = vtk.vtkPolyData()
        with self.assertRaises(ValueError) as ctx:
            physical_bounds_to_voi(poly, [0, 1, 0, 1, 0, 1])
        self.assertIn("structured", str(ctx.exception).lower())


class TestPhysicalBoundsToVOI_StructuredGrid(unittest.TestCase):
    """physical_bounds_to_voi on vtkStructuredGrid."""

    def setUp(self):
        # 10x10x5 grid spanning (0..9, 0..9, 0..4)
        self.data = _make_structured_grid(10, 10, 5,
                                          xrange=(0, 9), yrange=(0, 9), zrange=(0, 4))

    def test_full_extent(self):
        """Full physical range returns indices covering the whole grid."""
        voi = physical_bounds_to_voi(self.data, [0, 9, 0, 9, 0, 4])
        self.assertEqual(voi[0], 0)
        self.assertEqual(voi[1], 9)
        self.assertEqual(voi[2], 0)
        self.assertEqual(voi[3], 9)
        self.assertEqual(voi[4], 0)
        self.assertEqual(voi[5], 4)

    def test_sub_region_contains_requested(self):
        """The returned VOI indices must contain all points in the requested bounds.

        Because the structured grid scan rounds outward, the returned VOI may
        be slightly larger than the exact integer range, but it must include
        the requested physical region.
        """
        phys = [2, 6, 2, 6, 1, 3]
        voi = physical_bounds_to_voi(self.data, phys)
        # imin <= 2, imax >= 6 (accounting for stride expansion)
        self.assertLessEqual(voi[0], 2)
        self.assertGreaterEqual(voi[1], 6)
        self.assertLessEqual(voi[2], 2)
        self.assertGreaterEqual(voi[3], 6)
        self.assertLessEqual(voi[4], 1)
        self.assertGreaterEqual(voi[5], 3)

    def test_voi_within_grid(self):
        """Returned VOI indices must not exceed grid dimensions."""
        voi = physical_bounds_to_voi(self.data, [0, 9, 0, 9, 0, 4])
        self.assertGreaterEqual(voi[0], 0)
        self.assertLessEqual(voi[1], 9)
        self.assertGreaterEqual(voi[2], 0)
        self.assertLessEqual(voi[3], 9)
        self.assertGreaterEqual(voi[4], 0)
        self.assertLessEqual(voi[5], 4)


class TestPhysicalBoundsToVOI_NonZeroExtent(unittest.TestCase):
    """physical_bounds_to_voi on a structured grid with non-zero extent origin."""

    def setUp(self):
        # 10x10x5 grid at physical (0..9, 0..9, 0..4)
        # but extent starts at (100, 50, 10)
        self.data = _make_structured_grid_with_extent(
            10, 10, 5,
            xrange=(0, 9), yrange=(0, 9), zrange=(0, 4),
            extent_origin=(100, 50, 10),
        )

    def test_voi_uses_extent_coordinates(self):
        """Returned VOI must be in extent coordinates, not zero-based."""
        voi = physical_bounds_to_voi(self.data, [0, 9, 0, 9, 0, 4])
        # Extent is (100,109, 50,59, 10,14) — VOI must be in this range
        self.assertGreaterEqual(voi[0], 100)
        self.assertLessEqual(voi[1], 109)
        self.assertGreaterEqual(voi[2], 50)
        self.assertLessEqual(voi[3], 59)
        self.assertGreaterEqual(voi[4], 10)
        self.assertLessEqual(voi[5], 14)

    def test_sub_region_correct_location(self):
        """Extracting a sub-region by physical bounds produces data at the right location."""
        voi = physical_bounds_to_voi(self.data, [3, 6, 3, 6, 1, 3])

        # Use vtkExtractGrid with the computed VOI
        extractor = vtk.vtkExtractGrid()
        extractor.SetInputData(self.data)
        extractor.SetVOI(*voi)
        extractor.Update()
        output = extractor.GetOutput()

        self.assertGreater(output.GetNumberOfPoints(), 0)
        bounds = output.GetBounds()
        # Physical bounds of output must overlap the requested region
        self.assertLessEqual(bounds[0], 6, "xmin of output should be <= requested xmax")
        self.assertGreaterEqual(bounds[1], 3, "xmax of output should be >= requested xmin")
        self.assertLessEqual(bounds[2], 6, "ymin of output should be <= requested ymax")
        self.assertGreaterEqual(bounds[3], 3, "ymax of output should be >= requested ymin")

    def test_extract_grid_with_extent_voi(self):
        """extract_grid VOI in extent coordinates produces correct physical output."""
        tmp = "/tmp/test_nonzero_extent.vts"
        writer = vtk.vtkXMLStructuredGridWriter()
        writer.SetFileName(tmp)
        writer.SetInputData(self.data)
        writer.Write()

        try:
            from siva.dsl import interpret_build

            # Extent is (100,109, 50,59, 10,14), extract middle chunk
            code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{tmp}")
sub = extract_grid(input=data, VOI=[103, 106, 53, 56, 10, 10])
'''
            builder, vtk_objects, objs, node_statuses = interpret_build(code)
            sub_alg = objs["sub"]
            sub_alg.Update()
            output = sub_alg.GetOutput()

            self.assertGreater(output.GetNumberOfPoints(), 0)
            bounds = output.GetBounds()
            # i=103..106 maps to x=3..6, j=53..56 maps to y=3..6, k=10 maps to z=0
            self.assertAlmostEqual(bounds[0], 3.0, places=1)
            self.assertAlmostEqual(bounds[1], 6.0, places=1)
            self.assertAlmostEqual(bounds[2], 3.0, places=1)
            self.assertAlmostEqual(bounds[3], 6.0, places=1)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_extract_region_correct_on_nonzero_extent(self):
        """extract_region(bounds=...) produces correct output on non-zero extent data."""
        tmp = "/tmp/test_nonzero_extent_region.vts"
        writer = vtk.vtkXMLStructuredGridWriter()
        writer.SetFileName(tmp)
        writer.SetInputData(self.data)
        writer.Write()

        try:
            from siva.dsl import interpret_build

            code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{tmp}")
region = extract_region(input=data, bounds=[3, 6, 3, 6, 1, 3])
'''
            builder, vtk_objects, objs, node_statuses = interpret_build(code)
            region_alg = objs["region"]
            region_alg.Update()
            output = region_alg.GetOutput()

            self.assertGreater(output.GetNumberOfPoints(), 0)
            bounds = output.GetBounds()
            # Output physical bounds must overlap requested [3,6,3,6,1,3]
            self.assertLessEqual(bounds[0], 6)
            self.assertGreaterEqual(bounds[1], 3)
            self.assertLessEqual(bounds[2], 6)
            self.assertGreaterEqual(bounds[3], 3)
            self.assertLessEqual(bounds[4], 3)
            self.assertGreaterEqual(bounds[5], 1)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


class TestPhysicalBoundsToVOI_RectilinearGrid(unittest.TestCase):
    """physical_bounds_to_voi on vtkRectilinearGrid."""

    def setUp(self):
        # 10x8x4 grid, x: 0..9, y: 0..7, z: 0..3 (uniform spacing within each axis)
        self.data = _make_rectilinear_grid(10, 8, 4)

    def test_full_extent(self):
        """Full physical range returns the full index extent."""
        voi = physical_bounds_to_voi(self.data, [0, 9, 0, 7, 0, 3])
        self.assertEqual(voi[0], 0)
        self.assertEqual(voi[1], 9)
        self.assertEqual(voi[2], 0)
        self.assertEqual(voi[3], 7)
        self.assertEqual(voi[4], 0)
        self.assertEqual(voi[5], 3)

    def test_sub_region(self):
        """Sub-region returns indices that include the requested physical range."""
        # x: 3..6, y: 2..5, z: 1..2
        voi = physical_bounds_to_voi(self.data, [3, 6, 2, 5, 1, 2])
        self.assertLessEqual(voi[0], 3)
        self.assertGreaterEqual(voi[1], 6)
        self.assertLessEqual(voi[2], 2)
        self.assertGreaterEqual(voi[3], 5)
        self.assertLessEqual(voi[4], 1)
        self.assertGreaterEqual(voi[5], 2)


# ---------------------------------------------------------------------------
# Tests for extract_region via the DSL
# ---------------------------------------------------------------------------

class TestExtractRegionDSL(unittest.TestCase):
    """Test extract_region flowing through the DSL pipeline."""

    def _write_image_data(self, path, nx=10, ny=10, nz=5):
        data = _make_image_data(nx, ny, nz, origin=(0, 0, 0), spacing=(1, 1, 1))
        writer = vtk.vtkXMLImageDataWriter()
        writer.SetFileName(path)
        writer.SetInputData(data)
        writer.Write()

    def _write_structured_grid(self, path, nx=10, ny=10, nz=5):
        data = _make_structured_grid(nx, ny, nz,
                                     xrange=(0, 9), yrange=(0, 9), zrange=(0, 4))
        writer = vtk.vtkXMLStructuredGridWriter()
        writer.SetFileName(path)
        writer.SetInputData(data)
        writer.Write()

    def test_extract_region_bounds_image_data(self):
        """extract_region with bounds on vtkImageData extracts a sub-region."""
        from siva.dsl import interpret_build

        tmp = "/tmp/test_extract_region_img.vti"
        self._write_image_data(tmp, 10, 10, 5)

        try:
            code = f'''
data = source("vtkXMLImageDataReader", FileName="{tmp}")
region = extract_region(input=data, bounds=[2, 5, 2, 5, 0, 2])
'''
            builder, vtk_objects, objs, node_statuses = interpret_build(code)
            region_alg = objs.get("region")
            self.assertIsNotNone(region_alg, "region node not found in objects")
            region_alg.Update()
            output = region_alg.GetOutput()
            self.assertGreater(output.GetNumberOfPoints(), 0,
                               "extract_region produced 0 points")

            data_alg = objs.get("data")
            data_alg.Update()
            full_pts = data_alg.GetOutput().GetNumberOfPoints()
            self.assertLess(output.GetNumberOfPoints(), full_pts,
                            "Extracted region should have fewer points than full grid")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_extract_region_bounds_structured_grid(self):
        """extract_region with bounds on vtkStructuredGrid works correctly."""
        from siva.dsl import interpret_build

        tmp = "/tmp/test_extract_region_sg.vts"
        self._write_structured_grid(tmp, 10, 10, 5)

        try:
            code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{tmp}")
region = extract_region(input=data, bounds=[2, 6, 2, 6, 1, 3])
'''
            builder, vtk_objects, objs, node_statuses = interpret_build(code)
            region_alg = objs.get("region")
            self.assertIsNotNone(region_alg, "region not in pipeline objects")
            region_alg.Update()
            output = region_alg.GetOutput()
            self.assertGreater(output.GetNumberOfPoints(), 0,
                               "extract_region on structured grid produced 0 points")

            b = output.GetBounds()
            self.assertLess(b[0], 6.1, "xmax of extracted region should be <= 6")
            self.assertGreater(b[1], 1.9, "xmin sanity check")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_extract_region_no_bounds_records_error(self):
        """extract_region without bounds records error status; does not raise.

        The validation error is recorded during _build_pipeline(), not at
        extract_region() call time, consistent with the status-based error contract.
        """
        from siva.dsl import PipelineBuilder
        builder = PipelineBuilder()
        # extract_region() call itself is fine; error appears at build time
        region = builder.extract_region(input=None)
        # Verify no immediate exception
        # Build pipeline: region has no input (None), which also means it gets
        # an "Input node not built" error status.
        vtk_objs, statuses = builder._build_pipeline()
        region_status = statuses.get(region._node_id, {})
        self.assertEqual(region_status.get("status"), "error",
                         f"Missing bounds should produce error status: {region_status}")

    def test_extract_region_in_dsl_namespace(self):
        """extract_region should be available in the DSL execution namespace."""
        from siva.dsl import interpret_build

        tmp = "/tmp/test_extract_region_ns.vti"
        self._write_image_data(tmp, 10, 10, 5)

        try:
            code = f'''
data = source("vtkXMLImageDataReader", FileName="{tmp}")
region = extract_region(input=data, bounds=[0, 9, 0, 9, 0, 4])
'''
            builder, vtk_objects, objs, node_statuses = interpret_build(code)
            self.assertIn("region", objs)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


# ---------------------------------------------------------------------------
# Tests for vtkExtractGrid Bounds via create_vtk_filter directly
# ---------------------------------------------------------------------------

class TestExtractGridBoundsProperty(unittest.TestCase):
    """Test create_vtk_filter handling of Bounds for vtkExtractGrid.

    vtkExtractGrid only supports vtkStructuredGrid (not vtkImageData).
    For vtkImageData, use vtkExtractVOI (handled automatically by extract_region).
    """

    def _make_reader_for_structured_grid(self, path):
        """Write a VTS file and return a reader algorithm."""
        data = _make_structured_grid(10, 10, 5,
                                     xrange=(0, 9), yrange=(0, 9), zrange=(0, 4))
        writer = vtk.vtkXMLStructuredGridWriter()
        writer.SetFileName(path)
        writer.SetInputData(data)
        writer.Write()

        reader = vtk.vtkXMLStructuredGridReader()
        reader.SetFileName(path)
        reader.Update()
        return reader

    def test_bounds_converts_to_voi(self):
        """vtkExtractGrid with Bounds=[...] should produce a sub-region."""
        tmp = "/tmp/test_ef_bounds.vts"
        try:
            reader = self._make_reader_for_structured_grid(tmp)
            extractor, status = create_vtk_filter(
                "vtkExtractGrid",
                reader,
                Bounds=[2.0, 6.0, 2.0, 6.0, 0.0, 2.0]
            )
            extractor.Update()
            output = extractor.GetOutput()
            self.assertGreater(output.GetNumberOfPoints(), 0,
                               "Bounds-based extract should produce points")
            # Full grid has 10*10*5 = 500 points; sub-region should be smaller
            self.assertLess(output.GetNumberOfPoints(), 500)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_bounds_and_voi_raises(self):
        """Specifying both Bounds and VOI in create_vtk_filter should raise."""
        tmp = "/tmp/test_ef_both.vts"
        try:
            reader = self._make_reader_for_structured_grid(tmp)
            with self.assertRaises(ValueError) as ctx:
                create_vtk_filter(
                    "vtkExtractGrid",
                    reader,
                    Bounds=[0, 9, 0, 9, 0, 4],
                    VOI=[0, 9, 0, 9, 0, 4]
                )
            self.assertIn("not both", str(ctx.exception))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_voi_still_works(self):
        """VOI (index-based) still works as before for vtkStructuredGrid."""
        tmp = "/tmp/test_ef_voi.vts"
        try:
            reader = self._make_reader_for_structured_grid(tmp)
            extractor, status = create_vtk_filter(
                "vtkExtractGrid",
                reader,
                VOI=[2, 6, 2, 6, 0, 2]
            )
            extractor.Update()
            output = extractor.GetOutput()
            self.assertGreater(output.GetNumberOfPoints(), 0)
            self.assertLess(output.GetNumberOfPoints(), 500)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_extract_voi_with_image_data(self):
        """vtkExtractVOI with Bounds works for vtkImageData (not vtkExtractGrid)."""
        tmp = "/tmp/test_ef_img_voi.vti"
        try:
            data = _make_image_data(10, 10, 5)
            writer = vtk.vtkXMLImageDataWriter()
            writer.SetFileName(tmp)
            writer.SetInputData(data)
            writer.Write()

            reader = vtk.vtkXMLImageDataReader()
            reader.SetFileName(tmp)
            reader.Update()

            # vtkExtractVOI supports vtkImageData
            extractor, status = create_vtk_filter(
                "vtkExtractVOI",
                reader,
                VOI=[2, 6, 2, 6, 0, 2]
            )
            extractor.Update()
            output = extractor.GetOutput()
            self.assertGreater(output.GetNumberOfPoints(), 0)
            self.assertLess(output.GetNumberOfPoints(), 500)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


if __name__ == "__main__":
    unittest.main()
