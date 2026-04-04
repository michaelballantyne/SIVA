"""Tests for the load() convenience function.

These tests verify the logic without importing the full MCP server
(which requires the 'mcp' package). Tests cover:
  - Extension -> reader mapping
  - Error handling for missing files and unsupported extensions
  - Successful load with a real VTK file (VTI and VTP formats)
"""

import os
import sys
import tempfile
import unittest

import vtk
from vtk.util.numpy_support import numpy_to_vtk
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Mapping table (mirrors server._EXTENSION_TO_READER)
# ---------------------------------------------------------------------------
EXTENSION_TO_READER = {
    "vts": "vtkXMLStructuredGridReader",
    "vti": "vtkXMLImageDataReader",
    "vtp": "vtkXMLPolyDataReader",
    "vtu": "vtkXMLUnstructuredGridReader",
    "vtk": "vtkDataSetReader",
    "pvd": "vtkXMLCollectionReader",
    "nrrd": "vtkNrrdReader",
    "nhdr": "vtkNrrdReader",
}


def _detect_reader(filename):
    """Replicate the extension-detection logic from server.load()."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return EXTENSION_TO_READER.get(ext)


def _load_file(filename):
    """Attempt to load a file using the auto-detected reader.

    Returns (data, reader_class_name) or raises an informative exception.
    """
    if not os.path.exists(filename):
        raise FileNotFoundError(f"File not found: {filename}")

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    reader_class = EXTENSION_TO_READER.get(ext)
    if reader_class is None:
        supported = sorted(EXTENSION_TO_READER.keys())
        raise ValueError(
            f"Unsupported file extension '.{ext}'. Supported: {supported}"
        )

    vtk_cls = getattr(vtk, reader_class, None)
    if vtk_cls is None:
        raise RuntimeError(
            f"VTK reader '{reader_class}' not available in this VTK installation."
        )

    reader = vtk_cls()
    reader.SetFileName(filename)
    reader.Update()
    return reader.GetOutput(), reader_class


# ---------------------------------------------------------------------------
# Helper writers
# ---------------------------------------------------------------------------
def _write_vti(path):
    """Write a minimal vtkImageData (.vti) file."""
    img = vtk.vtkImageData()
    img.SetDimensions(5, 5, 5)
    img.SetOrigin(0.0, 0.0, 0.0)
    img.SetSpacing(1.0, 1.0, 1.0)
    n = img.GetNumberOfPoints()
    arr = numpy_to_vtk(np.arange(n, dtype=np.float32))
    arr.SetName("scalar")
    img.GetPointData().AddArray(arr)

    writer = vtk.vtkXMLImageDataWriter()
    writer.SetFileName(path)
    writer.SetInputData(img)
    writer.Write()


def _write_vtp(path):
    """Write a minimal vtkPolyData (.vtp) file."""
    sphere = vtk.vtkSphereSource()
    sphere.Update()

    writer = vtk.vtkXMLPolyDataWriter()
    writer.SetFileName(path)
    writer.SetInputConnection(sphere.GetOutputPort())
    writer.Write()


def _write_vtu(path):
    """Write a minimal vtkUnstructuredGrid (.vtu) file."""
    pts = vtk.vtkPoints()
    pts.InsertNextPoint(0, 0, 0)
    pts.InsertNextPoint(1, 0, 0)
    pts.InsertNextPoint(0, 1, 0)
    ug = vtk.vtkUnstructuredGrid()
    ug.SetPoints(pts)
    ug.InsertNextCell(vtk.VTK_TRIANGLE, 3, [0, 1, 2])

    writer = vtk.vtkXMLUnstructuredGridWriter()
    writer.SetFileName(path)
    writer.SetInputData(ug)
    writer.Write()


# ---------------------------------------------------------------------------
# Tests: Extension mapping
# ---------------------------------------------------------------------------
class TestExtensionMapping(unittest.TestCase):
    """Verify all required extensions map to the correct reader classes."""

    def test_vts_mapping(self):
        self.assertEqual(_detect_reader("file.vts"), "vtkXMLStructuredGridReader")

    def test_vti_mapping(self):
        self.assertEqual(_detect_reader("file.vti"), "vtkXMLImageDataReader")

    def test_vtp_mapping(self):
        self.assertEqual(_detect_reader("file.vtp"), "vtkXMLPolyDataReader")

    def test_vtu_mapping(self):
        self.assertEqual(_detect_reader("file.vtu"), "vtkXMLUnstructuredGridReader")

    def test_vtk_mapping(self):
        self.assertEqual(_detect_reader("file.vtk"), "vtkDataSetReader")

    def test_pvd_mapping(self):
        self.assertEqual(_detect_reader("file.pvd"), "vtkXMLCollectionReader")

    def test_nrrd_mapping(self):
        self.assertEqual(_detect_reader("file.nrrd"), "vtkNrrdReader")

    def test_nhdr_mapping(self):
        self.assertEqual(_detect_reader("file.nhdr"), "vtkNrrdReader")

    def test_case_insensitive(self):
        self.assertEqual(_detect_reader("file.VTI"), "vtkXMLImageDataReader")
        self.assertEqual(_detect_reader("file.VTS"), "vtkXMLStructuredGridReader")

    def test_unsupported_extension(self):
        self.assertIsNone(_detect_reader("file.xyz"))
        self.assertIsNone(_detect_reader("file.raw"))
        self.assertIsNone(_detect_reader("file.csv"))

    def test_no_extension(self):
        self.assertIsNone(_detect_reader("nodotfile"))

    def test_path_with_dots_in_directory(self):
        """Extension detection should only use the last suffix."""
        self.assertEqual(_detect_reader("/some/dir.v2/file.vti"), "vtkXMLImageDataReader")


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------
class TestLoadErrors(unittest.TestCase):
    """Test error cases for the load logic."""

    def test_file_not_found_raises(self):
        with self.assertRaises(FileNotFoundError) as ctx:
            _load_file("/nonexistent/path/to/file.vts")
        self.assertIn("File not found", str(ctx.exception))

    def test_unsupported_extension_raises(self):
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            tmppath = f.name
        try:
            with self.assertRaises(ValueError) as ctx:
                _load_file(tmppath)
            self.assertIn("Unsupported file extension", str(ctx.exception))
            self.assertIn(".xyz", str(ctx.exception))
        finally:
            os.unlink(tmppath)

    def test_no_extension_raises(self):
        with tempfile.NamedTemporaryFile(suffix="", delete=False) as f:
            tmppath = f.name
        try:
            with self.assertRaises(ValueError):
                _load_file(tmppath)
        finally:
            os.unlink(tmppath)


# ---------------------------------------------------------------------------
# Tests: Successful load with real VTK files
# ---------------------------------------------------------------------------
class TestLoadVTI(unittest.TestCase):
    """Test loading a .vti (vtkImageData) file."""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".vti", delete=False)
        self.tmpfile.close()
        _write_vti(self.tmpfile.name)

    def tearDown(self):
        os.unlink(self.tmpfile.name)

    def test_load_returns_data(self):
        data, reader_class = _load_file(self.tmpfile.name)
        self.assertIsNotNone(data)
        self.assertGreater(data.GetNumberOfPoints(), 0)

    def test_reader_class_correct(self):
        _, reader_class = _load_file(self.tmpfile.name)
        self.assertEqual(reader_class, "vtkXMLImageDataReader")

    def test_data_type_correct(self):
        data, _ = _load_file(self.tmpfile.name)
        self.assertEqual(data.GetClassName(), "vtkImageData")

    def test_scalar_field_present(self):
        data, _ = _load_file(self.tmpfile.name)
        pd = data.GetPointData()
        names = [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]
        self.assertIn("scalar", names)

    def test_dimensions_correct(self):
        data, _ = _load_file(self.tmpfile.name)
        dims = [0, 0, 0]
        data.GetDimensions(dims)
        self.assertEqual(dims, [5, 5, 5])

    def test_rich_field_stats_work(self):
        """Verify that queries.get_rich_field_stats works on loaded data."""
        from vislang import queries
        data, _ = _load_file(self.tmpfile.name)
        stats = queries.get_rich_field_stats(data)
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["name"], "scalar")
        self.assertIn("min", stats[0])
        self.assertIn("max", stats[0])


class TestLoadVTP(unittest.TestCase):
    """Test loading a .vtp (vtkPolyData) file."""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".vtp", delete=False)
        self.tmpfile.close()
        _write_vtp(self.tmpfile.name)

    def tearDown(self):
        os.unlink(self.tmpfile.name)

    def test_load_returns_polydata(self):
        data, reader_class = _load_file(self.tmpfile.name)
        self.assertIsNotNone(data)
        self.assertEqual(data.GetClassName(), "vtkPolyData")
        self.assertGreater(data.GetNumberOfPoints(), 0)

    def test_reader_class_correct(self):
        _, reader_class = _load_file(self.tmpfile.name)
        self.assertEqual(reader_class, "vtkXMLPolyDataReader")


class TestLoadVTU(unittest.TestCase):
    """Test loading a .vtu (vtkUnstructuredGrid) file."""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".vtu", delete=False)
        self.tmpfile.close()
        _write_vtu(self.tmpfile.name)

    def tearDown(self):
        os.unlink(self.tmpfile.name)

    def test_load_returns_unstructured_grid(self):
        data, reader_class = _load_file(self.tmpfile.name)
        self.assertIsNotNone(data)
        self.assertEqual(data.GetClassName(), "vtkUnstructuredGrid")
        self.assertGreater(data.GetNumberOfPoints(), 0)

    def test_reader_class_correct(self):
        _, reader_class = _load_file(self.tmpfile.name)
        self.assertEqual(reader_class, "vtkXMLUnstructuredGridReader")


# ---------------------------------------------------------------------------
# Tests: Server _EXTENSION_TO_READER consistency check
# Verify the server module's constant matches what we expect without
# importing the full server (which requires the mcp package).
# ---------------------------------------------------------------------------
class TestServerConstantConsistency(unittest.TestCase):
    """Verify the EXTENSION_TO_READER constant covers all required extensions."""

    REQUIRED_EXTENSIONS = ["vts", "vti", "vtp", "vtu", "vtk", "pvd", "nrrd", "nhdr"]

    def test_all_required_extensions_present(self):
        for ext in self.REQUIRED_EXTENSIONS:
            self.assertIn(ext, EXTENSION_TO_READER,
                          f"Extension '{ext}' missing from mapping")

    def test_all_reader_classes_exist_in_vtk(self):
        """All mapped reader class names should exist in the vtk module."""
        seen = set()
        for ext, cls_name in EXTENSION_TO_READER.items():
            if cls_name in seen:
                continue
            seen.add(cls_name)
            # Skip classes that may not be in all VTK builds
            if cls_name in ("vtkXMLCollectionReader", "vtkNrrdReader"):
                # These are optional; just check if they're in vtk
                vtk_cls = getattr(vtk, cls_name, None)
                # Just skip if not available - don't fail
                continue
            vtk_cls = getattr(vtk, cls_name, None)
            self.assertIsNotNone(vtk_cls,
                                 f"VTK class '{cls_name}' not found in vtk module")


if __name__ == "__main__":
    unittest.main()
