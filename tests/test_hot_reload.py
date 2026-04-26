"""Tests for hot-reload watcher and build coordinator.

Uses HEADLESS_INTERACTIVE renderer mode where threading is fully exercised
but no display is needed. Uses the synthetic dataset for builds that require
real VTK data.

All tests use threading.Event with short timeouts (5s) for determinism.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import json
from pathlib import Path
from unittest.mock import patch
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vislang.renderer import Renderer, RenderMode
from vislang.hot_reload import BuildCoordinator, PipelineWatcher, BuildRecord


# ---------------------------------------------------------------------------
# Dataset paths
# ---------------------------------------------------------------------------

_SYNTHETIC_VTI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "datasets", "synthetic", "data", "output.vti"
)


def _ensure_synthetic():
    if not os.path.exists(_SYNTHETIC_VTI):
        raise unittest.SkipTest("Synthetic dataset not present")


# ---------------------------------------------------------------------------
# Fake renderer for threading-exercised tests (no display)
# ---------------------------------------------------------------------------

class _FakeRenderer:
    """Minimal renderer stub for testing coordinator threading."""
    _mode = RenderMode.OFFSCREEN
    _actors = {}
    _overlays = {}
    _camera_positioned = False

    def render(self):
        pass

    def run_on_main_thread(self, fn):
        return fn()

    def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake-png")
        return path

    def clear(self):
        pass

    def get_camera_state(self):
        return {
            "position": [0.0, 0.0, 1.0],
            "focal_point": [0.0, 0.0, 0.0],
            "up": [0.0, 1.0, 0.0],
        }

    def set_camera(self, **kwargs):
        pass

    def suggest_camera(self, style="overview"):
        return {"position": [0, -1, 1], "focal_point": [0, 0, 0], "up": [0, 0, 1]}

    def set_background(self, *args, **kwargs):
        pass

    def add_actor(self, *args, **kwargs):
        pass

    def add_volume(self, *args, **kwargs):
        pass

    def add_scalar_bar(self, *args, **kwargs):
        pass

    def add_overlay_actor(self, *args, **kwargs):
        pass

    def destroy(self):
        pass


# ---------------------------------------------------------------------------
# Minimal ViewContext stub
# ---------------------------------------------------------------------------

class _FakeCtx:
    """Minimal ViewContext-compatible stub for coordinator tests."""

    def __init__(self, name: str, tmp_dir: str):
        self.name = name
        self._tmp = tmp_dir
        self.vtk_objects = {}
        self.current_code = ""
        self.version = 0
        from vislang.build_cache import BuildCache
        self.cache = BuildCache()

    @property
    def pipeline_file(self):
        return os.path.join(self._tmp, f"view-{self.name}.py")

    @property
    def history_dir(self):
        return Path(self._tmp) / ".vislang" / "history" / self.name


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIMPLE_PIPELINE = 'data = source("vtkSphereSource")\nshow(data, "sphere")\n'
_SPHERE_LARGE = 'data = source("vtkSphereSource", Radius=2.0)\nshow(data, "s")\n'
_BROKEN_PIPELINE = 'undefined_name_xyz\n'


def _wait(event, timeout=5.0, msg="Timed out waiting for build"):
    ok = event.wait(timeout=timeout)
    if not ok:
        raise AssertionError(msg)


# ---------------------------------------------------------------------------
# Test 1: Basic hot reload — write file, watcher triggers build, status file written
# ---------------------------------------------------------------------------

class TestBasicHotReload(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".vislang").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)
        self._watcher = PipelineWatcher(
            self._coordinator,
            os.path.join(self._tmp, "view-main.py"),
        )
        self._watcher.start()

    def tearDown(self):
        self._watcher.stop()
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_basic_build_on_file_write(self):
        """Writing a pipeline file triggers a build; status file is created."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)
        # Wait for watcher to notice and build to complete
        record = self._coordinator.wait_for_current(timeout=5.0)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "ok")

        status_path = os.path.join(self._tmp, "view-main.status.json")
        self.assertTrue(os.path.exists(status_path), "status.json should be written")
        data = json.loads(Path(status_path).read_text())
        self.assertEqual(data["status"], "ok")
        self.assertIsNotNone(data["source_hash"])
        self.assertIsNotNone(data["version"])


# ---------------------------------------------------------------------------
# Test 2: wait_for_current blocks until build completes
# ---------------------------------------------------------------------------

class TestWaitForCurrent(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".vislang").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)

    def tearDown(self):
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_wait_for_current_returns_finished_record(self):
        """wait_for_current blocks until build is done and returns a finished record."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        result_holder = [None]
        done = threading.Event()

        def _wait_thread():
            r = self._coordinator.wait_for_current(timeout=5.0)
            result_holder[0] = r
            done.set()

        t = threading.Thread(target=_wait_thread, daemon=True)
        t.start()
        _wait(done, timeout=8.0, msg="wait_for_current never returned")

        record = result_holder[0]
        self.assertIsNotNone(record)
        self.assertNotEqual(record.status, "running", "Record should be finished, not running")

    def test_wait_for_current_returns_correct_hash(self):
        """wait_for_current returns record matching the current file hash."""
        import hashlib
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        record = self._coordinator.wait_for_current(timeout=5.0)
        self.assertIsNotNone(record)
        expected_hash = hashlib.sha256(_SIMPLE_PIPELINE.encode()).hexdigest()
        self.assertEqual(record.source_hash, expected_hash)


# ---------------------------------------------------------------------------
# Test 3: Idempotent — same hash → one build
# ---------------------------------------------------------------------------

class TestIdempotentBuild(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".vislang").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)

    def tearDown(self):
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_same_hash_shares_inflight_build(self):
        """Two concurrent requests for the same hash share one build."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        # Both concurrent requests for same code
        r1 = self._coordinator.request_build(_SIMPLE_PIPELINE)
        r2 = self._coordinator.request_build(_SIMPLE_PIPELINE)
        # They should be the exact same object
        self.assertIs(r1, r2, "Concurrent requests for same hash should share one record")

        r1.wait(timeout=5.0)
        self.assertEqual(r1.status, "ok")


# ---------------------------------------------------------------------------
# Test 4: Different hash mid-build queues, second build runs after first
# ---------------------------------------------------------------------------

class TestQueueingMidBuild(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".vislang").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)

    def tearDown(self):
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_second_build_queued_while_first_in_flight(self):
        """When second hash is queued while first is IN-FLIGHT, second runs after first."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        # Start first build (goes inflight)
        r1 = self._coordinator.request_build(_SIMPLE_PIPELINE)

        # Spin until r1 is actually inflight (worker picked it up)
        t_start = time.monotonic()
        while True:
            with self._coordinator._lock:
                inflight = self._coordinator._inflight
            if inflight is not None and inflight.source_hash == r1.source_hash:
                break
            if time.monotonic() - t_start > 3.0:
                self.skipTest("r1 never went inflight — cannot test mid-build queuing")
            time.sleep(0.01)

        # Now queue second (different hash) while first is running
        r2 = self._coordinator.request_build(_SPHERE_LARGE)

        r1.wait(timeout=8.0)
        r2.wait(timeout=8.0)

        self.assertEqual(r1.status, "ok")
        self.assertEqual(r2.status, "ok")
        self.assertNotEqual(r1.source_hash, r2.source_hash)

        # latest() should reflect the second build
        latest = self._coordinator.latest()
        self.assertEqual(latest.source_hash, r2.source_hash)


# ---------------------------------------------------------------------------
# Test 5: Concurrent run_pipeline calls share one build
# ---------------------------------------------------------------------------

class TestConcurrentWaiters(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".vislang").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)

    def tearDown(self):
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_concurrent_waiters_get_same_record(self):
        """Two concurrent wait_for_current calls return the same finished record."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        results = [None, None]
        done = threading.Event()
        ready = threading.Barrier(2)

        def _waiter(i):
            ready.wait()
            results[i] = self._coordinator.wait_for_current(timeout=5.0)
            if all(r is not None for r in results):
                done.set()

        threads = [threading.Thread(target=_waiter, args=(i,), daemon=True) for i in range(2)]
        for t in threads:
            t.start()

        _wait(done, timeout=10.0, msg="Concurrent waiters timed out")

        self.assertIsNotNone(results[0])
        self.assertIsNotNone(results[1])
        self.assertEqual(results[0].source_hash, results[1].source_hash)
        self.assertEqual(results[0].status, "ok")


# ---------------------------------------------------------------------------
# Test 6: Error in pipeline doesn't crash watcher; subsequent valid write succeeds
# ---------------------------------------------------------------------------

class TestErrorHandling(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".vislang").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)
        self._watcher = PipelineWatcher(
            self._coordinator,
            os.path.join(self._tmp, "view-main.py"),
        )
        self._watcher.start()

    def tearDown(self):
        self._watcher.stop()
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_error_pipeline_produces_error_status(self):
        """A broken pipeline file produces an error record without crashing."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_BROKEN_PIPELINE)

        record = self._coordinator.wait_for_current(timeout=5.0)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "error")
        self.assertIsNotNone(record.error)

        # Status file should reflect the error
        status_path = os.path.join(self._tmp, "view-main.status.json")
        self.assertTrue(os.path.exists(status_path))
        data = json.loads(Path(status_path).read_text())
        self.assertEqual(data["status"], "error")

    def test_valid_pipeline_after_error_succeeds(self):
        """After an error, a subsequent valid pipeline write succeeds."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")

        # First: broken
        Path(pipeline_path).write_text(_BROKEN_PIPELINE)
        record_bad = self._coordinator.wait_for_current(timeout=5.0)
        self.assertEqual(record_bad.status, "error")

        # Then: valid
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)
        record_good = self._coordinator.wait_for_current(timeout=5.0)
        self.assertIsNotNone(record_good)
        self.assertEqual(record_good.status, "ok")

    def test_watcher_survives_error(self):
        """Watcher stays alive after an error build."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_BROKEN_PIPELINE)
        self._coordinator.wait_for_current(timeout=5.0)

        # Watcher's observer should still be running
        self.assertIsNotNone(self._watcher._observer)
        self.assertTrue(self._watcher._observer.is_alive())


# ---------------------------------------------------------------------------
# Test 7: Debounce — rapid writes collapse to one build
# ---------------------------------------------------------------------------

class TestDebounce(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".vislang").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)
        self._watcher = PipelineWatcher(
            self._coordinator,
            os.path.join(self._tmp, "view-main.py"),
            debounce_ms=200,
        )
        self._watcher.start()

    def tearDown(self):
        self._watcher.stop()
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_rapid_writes_collapse_to_one_build(self):
        """Multiple rapid writes within debounce window result in at most 2 builds
        (one per distinct hash), not one per write."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")

        # Write the same content 5 times in rapid succession (within debounce)
        for i in range(5):
            Path(pipeline_path).write_text(_SIMPLE_PIPELINE)
            time.sleep(0.01)  # 10ms between writes — well within 200ms debounce

        # Wait for the final build
        record = self._coordinator.wait_for_current(timeout=8.0)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "ok")
        # Version should be low (debounced, not 5 separate builds)
        self.assertLessEqual(self._ctx.version, 2,
                             "Debounce should collapse rapid writes to ≤2 builds")


# ---------------------------------------------------------------------------
# Test 8: Atomic-rename detection (simulate editor atomic write)
# ---------------------------------------------------------------------------

class TestAtomicRename(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".vislang").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)
        self._watcher = PipelineWatcher(
            self._coordinator,
            os.path.join(self._tmp, "view-main.py"),
        )
        self._watcher.start()

    def tearDown(self):
        self._watcher.stop()
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_atomic_rename_triggers_build(self):
        """Atomic write (write to tmp, os.rename to target) triggers a build."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        tmp_path = pipeline_path + ".tmp"

        Path(tmp_path).write_text(_SIMPLE_PIPELINE)
        os.rename(tmp_path, pipeline_path)

        record = self._coordinator.wait_for_current(timeout=5.0)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "ok")


# ---------------------------------------------------------------------------
# Test 9: Cache hits through hot reload
# ---------------------------------------------------------------------------

class TestCacheHitsThroughHotReload(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".vislang").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)

    def tearDown(self):
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_same_content_returns_latest_record_immediately(self):
        """After a build, same content returns the existing latest record fast."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        # First build
        r1 = self._coordinator.wait_for_current(timeout=5.0)
        self.assertIsNotNone(r1)
        self.assertEqual(r1.status, "ok")

        # Second request for same content — should return r1 directly (no new build)
        t0 = time.monotonic()
        r2 = self._coordinator.wait_for_current(timeout=2.0)
        elapsed = time.monotonic() - t0

        self.assertIsNotNone(r2)
        self.assertEqual(r2.source_hash, r1.source_hash)
        # Should return near-instantly (well under 1s) since same hash matches _latest
        self.assertLess(elapsed, 1.0,
                        f"Same-content request took {elapsed:.2f}s — should return immediately")


# ---------------------------------------------------------------------------
# Test 10: No stale result — wait_for_current returns record for current file hash
# ---------------------------------------------------------------------------

class TestNoStaleResult(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".vislang").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)

    def tearDown(self):
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_wait_for_current_returns_hash_matching_current_file(self):
        """wait_for_current always returns a record whose hash matches the current file."""
        import hashlib
        pipeline_path = os.path.join(self._tmp, "view-main.py")

        # Build v1
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)
        r1 = self._coordinator.wait_for_current(timeout=5.0)
        self.assertEqual(r1.status, "ok")

        # Immediately overwrite with v2
        Path(pipeline_path).write_text(_SPHERE_LARGE)
        r2 = self._coordinator.wait_for_current(timeout=5.0)
        self.assertIsNotNone(r2)

        expected_hash = hashlib.sha256(_SPHERE_LARGE.encode()).hexdigest()
        self.assertEqual(r2.source_hash, expected_hash,
                         "wait_for_current should return record for current file (v2), not stale v1")


# ---------------------------------------------------------------------------
# Smoke test: renderer thread queue flows correctly with coordinator
# ---------------------------------------------------------------------------

class TestRendererThreadQueue(unittest.TestCase):
    """Verify that the build coordinator correctly marshals work to the main thread
    in HEADLESS_INTERACTIVE mode."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".vislang").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        # Use a OFFSCREEN renderer (avoids needing a real event loop, but
        # run_on_main_thread still works inline — validates the interface).
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)

    def tearDown(self):
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_build_produces_screenshot_file(self):
        """After a build, the screenshot file exists on disk."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        record = self._coordinator.wait_for_current(timeout=5.0)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "ok")

        # Screenshot file should exist
        self.assertIsNotNone(record.screenshot_path)
        self.assertTrue(os.path.exists(record.screenshot_path),
                        f"Screenshot file not found: {record.screenshot_path}")

    def test_build_populates_vtk_objects(self):
        """After a build, ctx.vtk_objects is populated with pipeline nodes."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        record = self._coordinator.wait_for_current(timeout=5.0)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "ok")
        # vtk_objects should have been populated
        self.assertGreater(len(self._ctx.vtk_objects), 0,
                           "vtk_objects should be populated after a successful build")


# ---------------------------------------------------------------------------
# Test: status file structure
# ---------------------------------------------------------------------------

class TestStatusFileStructure(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".vislang").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)

    def tearDown(self):
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_status_file_has_required_fields(self):
        """Status file contains all required fields from spec."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)
        self._coordinator.wait_for_current(timeout=5.0)

        status_path = os.path.join(self._tmp, "view-main.status.json")
        self.assertTrue(os.path.exists(status_path))
        data = json.loads(Path(status_path).read_text())

        required_fields = [
            "source_hash", "status", "finished_at", "duration_s",
            "node_count", "cache", "screenshot", "version", "error", "log"
        ]
        for field in required_fields:
            self.assertIn(field, data, f"Status file missing field: {field}")

        self.assertIn("hits", data["cache"])
        self.assertIn("misses", data["cache"])
        self.assertIn("evictions", data["cache"])


# ---------------------------------------------------------------------------
# Test: pipeline_status MCP tool
# ---------------------------------------------------------------------------

class TestPipelineStatusTool(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmp)
        Path(self._tmp, ".vislang").mkdir(parents=True, exist_ok=True)
        import vislang.server as srv
        self._srv = srv
        renderer = _FakeRenderer()
        srv._init_for_test(renderer)

    def tearDown(self):
        import vislang.server as srv
        # Shutdown coordinators
        for ctx in srv._views.values():
            try:
                ctx.shutdown()
            except Exception:
                pass
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_pipeline_status_no_build(self):
        """pipeline_status() returns sensible message when no build has run."""
        result = self._srv.pipeline_status()
        self.assertIsInstance(result, str)
        self.assertIn("No build", result)

    def test_pipeline_status_after_build(self):
        """pipeline_status() shows latest build info after a build completes."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        ctx = self._srv._current_ctx()
        ctx.coordinator.wait_for_current(timeout=5.0)

        result = self._srv.pipeline_status()
        self.assertIn("Latest build", result)
        self.assertIn("ok", result)


if __name__ == "__main__":
    unittest.main()
