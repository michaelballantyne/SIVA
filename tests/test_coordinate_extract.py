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

from vislang.filters import physical_bounds_to_voi, create_vtk_filter


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
        from vislang.renderer import Renderer
        from vislang.dsl import interpret

        tmp = "/tmp/test_extract_region_img.vti"
        self._write_image_data(tmp, 10, 10, 5)

        try:
            r = Renderer(400, 300, offscreen=True)
            r.render = lambda: None
            code = f'''
data = source("vtkXMLImageDataReader", FileName="{tmp}")
region = extract_region(input=data, bounds=[2, 5, 2, 5, 0, 2])
'''
            objs, node_statuses, show_statuses, builder = interpret(code, r)

            region_alg = objs.get("region")
            self.assertIsNotNone(region_alg, "region node not found in objects")
            region_alg.Update()
            output = region_alg.GetOutput()
            self.assertIsNotNone(output)
            self.assertGreater(output.GetNumberOfPoints(), 0,
                               "extract_region produced 0 points")

            # The extracted region should be smaller than the original
            data_alg = objs.get("data")
            data_alg.Update()
            full_pts = data_alg.GetOutput().GetNumberOfPoints()
            region_pts = output.GetNumberOfPoints()
            self.assertLess(region_pts, full_pts,
                            "Extracted region should have fewer points than full grid")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_extract_region_voi_image_data(self):
        """extract_region with voi on vtkImageData uses direct index extraction."""
        from vislang.renderer import Renderer
        from vislang.dsl import interpret

        tmp = "/tmp/test_extract_region_voi.vti"
        self._write_image_data(tmp, 10, 10, 5)

        try:
            r = Renderer(400, 300, offscreen=True)
            r.render = lambda: None
            code = f'''
data = source("vtkXMLImageDataReader", FileName="{tmp}")
region = extract_region(input=data, voi=[2, 5, 2, 5, 0, 2])
'''
            objs, node_statuses, show_statuses, builder = interpret(code, r)
            region_alg = objs.get("region")
            self.assertIsNotNone(region_alg)
            region_alg.Update()
            output = region_alg.GetOutput()
            self.assertGreater(output.GetNumberOfPoints(), 0)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_extract_region_bounds_structured_grid(self):
        """extract_region with bounds on vtkStructuredGrid works correctly."""
        from vislang.renderer import Renderer
        from vislang.dsl import interpret

        tmp = "/tmp/test_extract_region_sg.vts"
        self._write_structured_grid(tmp, 10, 10, 5)

        try:
            r = Renderer(400, 300, offscreen=True)
            r.render = lambda: None
            code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{tmp}")
region = extract_region(input=data, bounds=[2, 6, 2, 6, 1, 3])
'''
            objs, node_statuses, show_statuses, builder = interpret(code, r)
            region_alg = objs.get("region")
            self.assertIsNotNone(region_alg, "region not in pipeline objects")
            region_alg.Update()
            output = region_alg.GetOutput()
            self.assertGreater(output.GetNumberOfPoints(), 0,
                               "extract_region on structured grid produced 0 points")

            # Bounds of extracted region should not exceed requested bounds
            b = output.GetBounds()
            # Allow a small margin for grid cell boundaries
            self.assertLess(b[0], 6.1, "xmax of extracted region should be <= 6")
            self.assertGreater(b[1], 1.9, "xmin sanity check")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_extract_region_both_raises(self):
        """Specifying both bounds and voi should raise an error."""
        from vislang.dsl import PipelineBuilder
        builder = PipelineBuilder()
        with self.assertRaises(ValueError) as ctx:
            builder.extract_region(bounds=[0, 1, 0, 1, 0, 1], voi=[0, 1, 0, 1, 0, 1])
        self.assertIn("not both", str(ctx.exception))

    def test_extract_region_neither_raises(self):
        """Calling extract_region with no bounds or voi should raise an error."""
        from vislang.dsl import PipelineBuilder
        builder = PipelineBuilder()
        with self.assertRaises(ValueError) as ctx:
            builder.extract_region()
        self.assertIn("requires", str(ctx.exception))

    def test_extract_region_in_dsl_namespace(self):
        """extract_region should be available in the DSL execution namespace."""
        from vislang.renderer import Renderer
        from vislang.dsl import interpret

        # Just check that the symbol resolves without error (use voi to avoid
        # needing a real file for coordinate conversion)
        tmp = "/tmp/test_extract_region_ns.vti"
        self._write_image_data(tmp, 10, 10, 5)

        try:
            r = Renderer(400, 300, offscreen=True)
            r.render = lambda: None
            code = f'''
data = source("vtkXMLImageDataReader", FileName="{tmp}")
region = extract_region(input=data, voi=[0, 9, 0, 9, 0, 4])
'''
            objs, node_statuses, show_statuses, builder = interpret(code, r)
            self.assertIn("region", objs)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


# ---------------------------------------------------------------------------
# Tests for vtkExtractGrid Bounds via create_vtk_filter directly
# ---------------------------------------------------------------------------

class TestExtractGridBoundsProperty(unittest.TestCase):
    """Test create_vtk_filter handling of Bounds for vtkExtractGrid."""

    def _make_reader_for_image(self, path):
        """Write a VTI file and return a reader algorithm."""
        data = _make_image_data(10, 10, 5)
        writer = vtk.vtkXMLImageDataWriter()
        writer.SetFileName(path)
        writer.SetInputData(data)
        writer.Write()

        reader = vtk.vtkXMLImageDataReader()
        reader.SetFileName(path)
        reader.Update()
        return reader

    def test_bounds_converts_to_voi(self):
        """vtkExtractGrid with Bounds=[...] should produce a sub-region."""
        tmp = "/tmp/test_ef_bounds.vti"
        try:
            reader = self._make_reader_for_image(tmp)
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
        tmp = "/tmp/test_ef_both.vti"
        try:
            reader = self._make_reader_for_image(tmp)
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
        """VOI (index-based) still works as before."""
        tmp = "/tmp/test_ef_voi.vti"
        try:
            reader = self._make_reader_for_image(tmp)
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


if __name__ == "__main__":
    unittest.main()
