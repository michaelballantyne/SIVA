"""Tests for the load() MCP tool logic.

Because importing siva.server requires the 'mcp' package (only available
at runtime), these tests verify the underlying infrastructure that load() uses:
  - File existence check (filters.load_file error paths)
  - Extension detection via filters.EXT_TO_READER
  - Reader creation via filters.create_vtk_filter
  - Data is stored as a reader algorithm (has Update/GetOutput), not raw data

These tests mirror what the load() tool does step by step.
"""

import os
import shutil
import sys
import tempfile
import unittest

import vtk
from vtk.util.numpy_support import numpy_to_vtk
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.filters import EXT_TO_READER, create_vtk_filter, load_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_vti(path):
    img = vtk.vtkImageData()
    img.SetDimensions(4, 4, 4)
    img.SetOrigin(0, 0, 0)
    img.SetSpacing(1, 1, 1)
    n = img.GetNumberOfPoints()
    arr = numpy_to_vtk(np.arange(n, dtype=np.float32))
    arr.SetName("temperature")
    img.GetPointData().AddArray(arr)
    w = vtk.vtkXMLImageDataWriter()
    w.SetFileName(path)
    w.SetInputData(img)
    w.Write()


def _write_vtp(path):
    sphere = vtk.vtkSphereSource()
    sphere.Update()
    w = vtk.vtkXMLPolyDataWriter()
    w.SetFileName(path)
    w.SetInputConnection(sphere.GetOutputPort())
    w.Write()


def _write_legacy_vtk_two_point_arrays(path):
    """Write a legacy (.vtk) vtkPolyData file with two point arrays, only
    one of which is marked active. Legacy readers default to emitting only
    the active scalars, so this is the minimal repro for the "hidden
    arrays" bug.
    """
    pts = vtk.vtkPoints()
    for i in range(4):
        pts.InsertNextPoint(float(i), 0.0, 0.0)
    poly = vtk.vtkPolyData()
    poly.SetPoints(pts)

    verts = vtk.vtkCellArray()
    for i in range(4):
        verts.InsertNextCell(1, [i])
    poly.SetVerts(verts)

    active = numpy_to_vtk(np.arange(4, dtype=np.float32))
    active.SetName("active_scalar")
    poly.GetPointData().SetScalars(active)  # marks this one "active"

    other = numpy_to_vtk(np.arange(4, dtype=np.float32) * 10.0)
    other.SetName("other_scalar")
    poly.GetPointData().AddArray(other)  # present but not active

    w = vtk.vtkPolyDataWriter()
    w.SetFileName(path)
    w.SetInputData(poly)
    w.Write()


def _simulate_load(filename):
    """Reproduce the core logic of the load() MCP tool.

    Returns (reader_algorithm, error_string). On success error_string is None.
    The reader is a VTK algorithm with Update() and GetOutput() methods,
    just like what load() stores in _vtk_objects.
    """
    if not os.path.exists(filename):
        return None, f"File not found: {filename}"

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    reader_class = EXT_TO_READER.get(ext)
    if reader_class is None:
        supported = sorted(EXT_TO_READER.keys())
        return None, (
            f"Cannot load '{filename}': unsupported extension '.{ext}'. "
            f"Supported extensions: {supported}"
        )

    try:
        reader, _ = create_vtk_filter(reader_class, FileName=filename)
        reader.Update()
        data = reader.GetOutput()
    except Exception as e:
        return None, f"Error loading '{filename}': {e}"

    if data is None or data.GetNumberOfPoints() == 0:
        return None, f"File '{filename}' loaded but contains no points."

    return reader, None


# ---------------------------------------------------------------------------
# Tests: load() error paths
# ---------------------------------------------------------------------------

class TestLoadToolErrors(unittest.TestCase):
    """Test that the load() logic handles error cases correctly."""

    def test_missing_file_returns_error(self):
        reader, error = _simulate_load("/tmp/does_not_exist_siva_test.vti")
        self.assertIsNone(reader)
        self.assertIn("File not found", error)

    def test_unsupported_extension_returns_error(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            tmppath = f.name
        try:
            reader, error = _simulate_load(tmppath)
            self.assertIsNone(reader)
            self.assertIsNotNone(error)
            self.assertIn(".csv", error)
            self.assertIn("Supported", error)
        finally:
            os.unlink(tmppath)

    def test_no_extension_returns_error(self):
        with tempfile.NamedTemporaryFile(suffix="", delete=False) as f:
            tmppath = f.name
        try:
            reader, error = _simulate_load(tmppath)
            self.assertIsNone(reader)
            self.assertIsNotNone(error)
        finally:
            os.unlink(tmppath)


class _ChdirTmpMixin:
    """create_vtk_filter confines FileName to the working directory, so tests
    that feed it a real path need a relative name inside cwd, not an
    arbitrary /tmp absolute path. This mixin chdirs into a scratch dir for
    the duration of the test and restores cwd + cleans up afterward.
    """

    def _enter_tmp_workdir(self):
        tmpdir = tempfile.mkdtemp()
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        self.addCleanup(os.chdir, old_cwd)
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        return tmpdir


# ---------------------------------------------------------------------------
# Tests: load() stores a VTK algorithm, not raw data
# ---------------------------------------------------------------------------

class TestLoadToolStoresAlgorithm(_ChdirTmpMixin, unittest.TestCase):
    """The load() tool must store reader algorithms in _vtk_objects, not data.

    _get_data() calls obj.Update() and obj.GetOutput(), so the stored object
    must be a VTK algorithm (pipeline source), not a raw vtkDataObject.
    """

    class _NamedFile:
        def __init__(self, name):
            self.name = name

    def setUp(self):
        self._enter_tmp_workdir()
        self.tmpfile = self._NamedFile("data.vti")
        _write_vti(self.tmpfile.name)

    def test_returns_algorithm_not_data(self):
        reader, error = _simulate_load(self.tmpfile.name)
        self.assertIsNone(error)
        self.assertIsNotNone(reader)
        # Must have Update and GetOutput (VTK algorithm interface)
        self.assertTrue(hasattr(reader, "Update"), "Stored object must have Update()")
        self.assertTrue(hasattr(reader, "GetOutput"), "Stored object must have GetOutput()")

    def test_algorithm_update_and_get_output_work(self):
        """Simulate what _get_data() does with the stored algorithm."""
        reader, error = _simulate_load(self.tmpfile.name)
        self.assertIsNone(error)
        reader.Update()
        data = reader.GetOutput()
        self.assertIsNotNone(data)
        self.assertGreater(data.GetNumberOfPoints(), 0)

    def test_loaded_data_has_expected_field(self):
        reader, error = _simulate_load(self.tmpfile.name)
        self.assertIsNone(error)
        reader.Update()
        data = reader.GetOutput()
        pd = data.GetPointData()
        names = [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]
        self.assertIn("temperature", names)


class TestLoadToolVTP(_ChdirTmpMixin, unittest.TestCase):
    """Test load() logic with a .vtp (vtkPolyData) file."""

    def setUp(self):
        self._enter_tmp_workdir()
        self.tmpfile = TestLoadToolStoresAlgorithm._NamedFile("data.vtp")
        _write_vtp(self.tmpfile.name)

    def test_load_vtp_returns_algorithm(self):
        reader, error = _simulate_load(self.tmpfile.name)
        self.assertIsNone(error)
        self.assertIsNotNone(reader)
        reader.Update()
        data = reader.GetOutput()
        self.assertEqual(data.GetClassName(), "vtkPolyData")
        self.assertGreater(data.GetNumberOfPoints(), 0)


class TestLoadToolLegacyVTKReadsAllArrays(_ChdirTmpMixin, unittest.TestCase):
    """Legacy .vtk files must not silently hide non-active arrays.

    vtkGenericDataObjectReader (mapped from the .vtk extension) defaults to
    reading only the active scalars/vectors/etc. create_vtk_filter must turn
    on the ReadAll* flags so describe_data() sees every array in the file.
    """

    def setUp(self):
        self._enter_tmp_workdir()
        self.filename = "legacy.vtk"
        _write_legacy_vtk_two_point_arrays(self.filename)

    def test_both_arrays_present_via_simulated_load(self):
        reader, error = _simulate_load(self.filename)
        self.assertIsNone(error)
        reader.Update()
        data = reader.GetOutput()
        pd = data.GetPointData()
        names = [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]
        self.assertIn("active_scalar", names)
        self.assertIn("other_scalar", names)

    def test_both_arrays_present_via_create_vtk_filter_directly(self):
        reader, _status = create_vtk_filter(
            "vtkGenericDataObjectReader", FileName=self.filename
        )
        data = reader.GetOutput()
        pd = data.GetPointData()
        names = [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]
        self.assertIn("active_scalar", names)
        self.assertIn("other_scalar", names)


# ---------------------------------------------------------------------------
# Tests: EXT_TO_READER coverage for load() supported extensions
# ---------------------------------------------------------------------------

class TestLoadToolExtensionCoverage(unittest.TestCase):
    """Verify all extensions documented in load()'s docstring are supported."""

    LOAD_TOOL_SUPPORTED = ["vts", "vti", "vtp", "vtu", "vtr"]

    def test_all_documented_extensions_in_ext_to_reader(self):
        for ext in self.LOAD_TOOL_SUPPORTED:
            self.assertIn(
                ext, EXT_TO_READER,
                f"Extension '.{ext}' documented in load() but missing from EXT_TO_READER"
            )


if __name__ == "__main__":
    unittest.main()
