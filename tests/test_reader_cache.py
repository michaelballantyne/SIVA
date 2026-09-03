"""Tests for siva.filters._reader_cache invalidation.

_reader_cache avoids re-reading large files on pipeline rebuild, keyed by
(class_name, filename, fingerprint). The fingerprint (mtime+size, via
build_cache._file_fingerprint) must change when the file at a given path is
replaced with different content, so a stale reader/output isn't served
after the file changes (e.g. a broken file gets overwritten with a fixed
one at the same path).
"""

import os
import shutil
import sys
import tempfile
import unittest

import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.filters import create_vtk_filter, clear_reader_cache, _reader_cache


def _write_vti(path, value: float, n=4):
    img = vtk.vtkImageData()
    img.SetDimensions(n, n, n)
    img.SetOrigin(0, 0, 0)
    img.SetSpacing(1, 1, 1)
    npts = img.GetNumberOfPoints()
    arr = numpy_to_vtk(np.full(npts, value, dtype=np.float32))
    arr.SetName("scalar")
    img.GetPointData().AddArray(arr)
    w = vtk.vtkXMLImageDataWriter()
    w.SetFileName(path)
    w.SetInputData(img)
    w.Write()


class _ChdirTmpMixin:
    """create_vtk_filter confines FileName to the working directory, so
    tests need a relative filename inside cwd rather than an arbitrary
    /tmp absolute path.
    """

    def _enter_tmp_workdir(self):
        tmpdir = tempfile.mkdtemp()
        old_cwd = os.getcwd()
        os.chdir(tmpdir)
        self.addCleanup(os.chdir, old_cwd)
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)
        return tmpdir


def _first_scalar_value(data):
    pd = data.GetPointData()
    arr = pd.GetArray("scalar")
    return arr.GetValue(0)


class TestReaderCacheInvalidation(_ChdirTmpMixin, unittest.TestCase):
    def setUp(self):
        self._enter_tmp_workdir()
        clear_reader_cache()
        self.addCleanup(clear_reader_cache)
        self.filename = "data.vti"

    def test_replacing_file_at_same_path_is_seen(self):
        _write_vti(self.filename, value=1.0)
        reader1, status1 = create_vtk_filter(
            "vtkXMLImageDataReader", FileName=self.filename
        )
        self.assertNotIn("cached", status1)
        data1 = reader1.GetOutput()
        self.assertEqual(_first_scalar_value(data1), 1.0)

        # A same-path reload before the file changes should hit the cache.
        reader_same, status_same = create_vtk_filter(
            "vtkXMLImageDataReader", FileName=self.filename
        )
        self.assertIn("cached", status_same)
        self.assertIs(reader_same, reader1)

        mtime1 = os.stat(self.filename).st_mtime
        _write_vti(self.filename, value=2.0)
        _ensure_mtime_after(self.filename, mtime1)

        reader2, status2 = create_vtk_filter(
            "vtkXMLImageDataReader", FileName=self.filename
        )
        data2 = reader2.GetOutput()
        self.assertEqual(
            _first_scalar_value(data2),
            2.0,
            "create_vtk_filter served stale cached data after the file "
            "at the same path was replaced with different content",
        )

    def test_stale_cache_entry_is_evicted(self):
        _write_vti(self.filename, value=1.0)
        create_vtk_filter("vtkXMLImageDataReader", FileName=self.filename)
        keys_before = [
            k for k in _reader_cache
            if k[0] == "vtkXMLImageDataReader" and k[1] == self.filename
        ]
        self.assertEqual(len(keys_before), 1)

        mtime1 = os.stat(self.filename).st_mtime
        _write_vti(self.filename, value=2.0)
        _ensure_mtime_after(self.filename, mtime1)
        create_vtk_filter("vtkXMLImageDataReader", FileName=self.filename)

        keys_after = [
            k for k in _reader_cache
            if k[0] == "vtkXMLImageDataReader" and k[1] == self.filename
        ]
        self.assertEqual(
            len(keys_after), 1,
            "stale cache entry for the old file content was not evicted",
        )


def _ensure_mtime_after(path, prev_mtime, delta_seconds=1.0):
    """Force *path*'s mtime to be strictly after *prev_mtime*.

    A freshly rewritten file's natural mtime may coincide with the
    previous one on filesystems with coarse (e.g. 1s) mtime resolution,
    and its size may also be unchanged (fixed-size binary-appended VTK
    XML data). Without this, a fast rewrite in a test could produce an
    identical fingerprint and mask the very bug being tested. Explicitly
    bumping the mtime makes the test deterministic regardless of clock
    resolution or write timing.
    """
    new_time = prev_mtime + delta_seconds
    os.utime(path, (new_time, new_time))


if __name__ == "__main__":
    unittest.main()
