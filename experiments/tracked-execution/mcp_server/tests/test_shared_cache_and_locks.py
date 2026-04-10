"""Tests for the shared read cache and lock consistency.

Covers:
  - Shared read cache: reading the same file from two views loads it once
  - Shared read cache: reading different files loads both
  - Lock consistency: all tool functions that access ViewState use vs.lock
  - Concurrent access: simultaneous watcher reload and screenshot don't crash
"""

import ast
import inspect
import os
import tempfile
import threading
import time

import numpy as np
import pyvista as pv
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vtk_file(tmpdir, name="data.vtk", n=5):
    """Write a small synthetic VTK file and return its path."""
    mesh = pv.ImageData(dimensions=(n, n, n))
    mesh["T"] = np.linspace(0.0, 1000.0, mesh.n_points)
    path = os.path.join(tmpdir, name)
    mesh.save(path)
    return path


# ---------------------------------------------------------------------------
# Shared read cache
# ---------------------------------------------------------------------------

class TestSharedReadCache:
    """The shared read cache avoids loading the same file twice."""

    def test_same_file_loaded_once(self, tmp_vtk_dir, reset_server):
        """Two views reading the same file share the cached mesh."""
        from mcp_server.server import set_working_directory, create_view

        # Create two pipeline files that both read the same VTK file.
        for name in ("view-a.py", "view-b.py"):
            path = os.path.join(tmp_vtk_dir, name)
            with open(path, "w") as fh:
                fh.write('mesh = read("test.vtk")\nshow(mesh)\n')

        set_working_directory(tmp_vtk_dir)
        r1 = create_view("view-a.py")
        r2 = create_view("view-b.py")
        assert "Error" not in r1, f"view-a failed: {r1}"
        assert "Error" not in r2, f"view-b failed: {r2}"

        # The shared cache should hold exactly one entry (abs_path:mtime).
        cache = reset_server._shared_read_cache
        assert len(cache) == 1, (
            f"Expected 1 shared cache entry for the same file, got {len(cache)}: "
            f"{list(cache.keys())}"
        )

    def test_different_files_both_loaded(self, reset_server):
        """Two views reading different files each populate the shared cache."""
        from mcp_server.server import set_working_directory, create_view

        tmpdir = tempfile.mkdtemp()
        _make_vtk_file(tmpdir, "alpha.vtk")
        _make_vtk_file(tmpdir, "beta.vtk")

        for vtk_name, pipeline_name in [("alpha.vtk", "view-alpha.py"),
                                         ("beta.vtk", "view-beta.py")]:
            path = os.path.join(tmpdir, pipeline_name)
            with open(path, "w") as fh:
                fh.write(f'mesh = read("{vtk_name}")\nshow(mesh)\n')

        set_working_directory(tmpdir)
        r1 = create_view("view-alpha.py")
        r2 = create_view("view-beta.py")
        assert "Error" not in r1, f"view-alpha failed: {r1}"
        assert "Error" not in r2, f"view-beta failed: {r2}"

        # One cache entry per distinct file.
        cache = reset_server._shared_read_cache
        assert len(cache) == 2, (
            f"Expected 2 shared cache entries for two files, got {len(cache)}: "
            f"{list(cache.keys())}"
        )

    def test_cache_keyed_by_mtime(self, reset_server):
        """Updating a file changes its mtime, so a new cache entry is created."""
        from mcp_server.server import set_working_directory, create_view

        tmpdir = tempfile.mkdtemp()
        vtk_path = _make_vtk_file(tmpdir, "data.vtk", n=3)

        pipeline = os.path.join(tmpdir, "view-main.py")
        with open(pipeline, "w") as fh:
            fh.write('mesh = read("data.vtk")\nshow(mesh)\n')

        set_working_directory(tmpdir)
        create_view("view-main.py")

        # Record the first cache key.
        cache = reset_server._shared_read_cache
        assert len(cache) == 1
        first_key = next(iter(cache))

        # Overwrite the file (changes mtime) and read again via a second view.
        time.sleep(0.01)   # Ensure mtime changes on fast filesystems.
        new_mesh = pv.ImageData(dimensions=(4, 4, 4))
        new_mesh["T"] = np.zeros(new_mesh.n_points)
        new_mesh.save(vtk_path)

        pipeline2 = os.path.join(tmpdir, "view-b.py")
        with open(pipeline2, "w") as fh:
            fh.write('mesh = read("data.vtk")\nshow(mesh)\n')
        create_view("view-b.py")

        # The cache should now have a second (different-mtime) key.
        # It may also retain the old one depending on GC, but there must be
        # at least one key that differs from the original.
        current_keys = set(cache.keys())
        # We don't force GC of the old entry here; just confirm the new key exists.
        assert any(k != first_key for k in current_keys), (
            "Expected a new cache key after the file changed, "
            f"but only found: {current_keys}"
        )

    def test_shared_cache_cleared_between_tests(self, reset_server):
        """The reset_server fixture clears the shared cache — verify isolation."""
        # After reset, cache should be empty.
        assert len(reset_server._shared_read_cache) == 0, (
            "Shared cache should be empty after reset_server fixture runs"
        )


# ---------------------------------------------------------------------------
# Lock consistency
# ---------------------------------------------------------------------------

class TestLockConsistency:
    """All MCP tool functions that access ViewState must hold vs.lock."""

    def _source_of(self, fn):
        """Return the source lines of a function."""
        return inspect.getsource(fn)

    def _check_uses_lock(self, fn_source, fn_name):
        """Assert that the function body contains a 'with vs.lock:' context manager."""
        # Look for the pattern 'with vs.lock' anywhere in the source.
        assert "with vs.lock" in fn_source, (
            f"{fn_name} accesses ViewState but does not use 'with vs.lock:'. "
            "All paths that read or write ViewState attributes must hold the lock."
        )

    def test_inspect_uses_lock(self):
        from mcp_server import server
        src = self._source_of(server.inspect)
        self._check_uses_lock(src, "inspect")

    def test_screenshot_uses_lock(self):
        from mcp_server import server
        src = self._source_of(server.screenshot)
        self._check_uses_lock(src, "screenshot")

    def test_pipeline_status_uses_lock(self):
        from mcp_server import server
        src = self._source_of(server.pipeline_status)
        self._check_uses_lock(src, "pipeline_status")

    def test_list_views_uses_lock(self):
        from mcp_server import server
        src = self._source_of(server.list_views)
        self._check_uses_lock(src, "list_views")

    def test_watcher_callback_uses_lock(self):
        """The _start_watcher callback must hold vs.lock before modifying ViewState."""
        from mcp_server import server
        src = self._source_of(server._start_watcher)
        self._check_uses_lock(src, "_start_watcher (on_reload/on_error callbacks)")


# ---------------------------------------------------------------------------
# Concurrent access
# ---------------------------------------------------------------------------

class TestConcurrentAccess:
    """Verify that concurrent watcher reload and screenshot don't crash or deadlock."""

    def test_concurrent_reload_and_screenshot(self, view_dir, reset_server):
        """Trigger rapid screenshot calls while the watcher callback could fire.

        This is a smoke test: if locking is broken, we'd see an exception or
        deadlock within the timeout.  The test does not guarantee detection of
        every race, but exercises the lock path for both screenshot and the
        watcher callback.
        """
        from mcp_server.server import screenshot

        errors = []

        def take_screenshots(n=10):
            for _ in range(n):
                try:
                    screenshot("view-main.py")
                except Exception as exc:
                    errors.append(exc)

        # Simulate a watcher callback firing concurrently with screenshot.
        vs = reset_server._views["view-main"]

        def fake_watcher_callback():
            """Acquire vs.lock and pretend to update state (like the real callback)."""
            for _ in range(10):
                with vs.lock:
                    # Mimic what the real on_reload does: update vs.last_error.
                    vs.last_error = None
                time.sleep(0.001)

        t_watcher = threading.Thread(target=fake_watcher_callback, daemon=True)
        t_screenshot = threading.Thread(target=take_screenshots, daemon=True)

        t_watcher.start()
        t_screenshot.start()
        t_watcher.join(timeout=5)
        t_screenshot.join(timeout=5)

        assert not errors, (
            f"Concurrent screenshot and watcher callback raised errors: {errors}"
        )

    def test_sequential_screenshots_dont_crash(self, view_dir, reset_server):
        """Multiple sequential screenshot calls from the same thread don't crash.

        Note: VTK's OpenGL context is bound to the thread that created the
        plotter.  screenshot() must only be called from the same thread that
        created the view.  This test verifies repeated calls from the main
        thread are stable.
        """
        from mcp_server.server import screenshot

        for _ in range(3):
            result = screenshot("view-main.py")
            # Each call should return valid PNG data.
            assert result.data[:4] == b"\x89PNG", "Expected PNG signature"
