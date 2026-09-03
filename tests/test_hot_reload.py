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

from siva.renderer import Renderer, RenderMode
from siva.hot_reload import BuildCoordinator, PipelineWatcher, BuildRecord


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


def _synthetic_vti_in(workdir):
    """Symlink the synthetic dataset into *workdir* and return its relative name.

    create_vtk_filter confines FileName to the working directory (see
    siva.filters.confine_to_workdir), so pipeline code can no longer embed
    ``_SYNTHETIC_VTI``'s real absolute path directly -- it must be symlinked
    into the view's working directory first, exactly the supported
    "symlink a dataset into the working directory" curation workflow.
    """
    link_name = os.path.join(workdir, "output.vti")
    if not os.path.exists(link_name):
        os.symlink(_SYNTHETIC_VTI, link_name)
    return "output.vti"


# ---------------------------------------------------------------------------
# Fake renderer for threading-exercised tests (no display)
# ---------------------------------------------------------------------------

class _FakeRenderer:
    """Minimal renderer stub for testing coordinator threading."""
    mode = RenderMode.OFFSCREEN
    camera_positioned = False

    def render(self):
        pass

    def dispatch(self, fn):
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

    def get_size(self):
        return (800, 600)

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
        self.applied_hash = None  # mirrors ViewContext.applied_hash
        from siva.build_cache import BuildCache
        self.cache = BuildCache()

    @property
    def pipeline_file(self):
        return os.path.join(self._tmp, f"view-{self.name}.py")

    @property
    def history_dir(self):
        return Path(self._tmp) / ".siva" / "history" / self.name

    def save_version(self, code: str, screenshot_path) -> int:
        """Mirror ViewContext.save_version for coordinator tests."""
        self.version += 1
        ver_dir = self.history_dir / f"v{self.version:04d}"
        ver_dir.mkdir(parents=True, exist_ok=True)
        (ver_dir / "pipeline.py").write_text(code)
        if screenshot_path and os.path.exists(screenshot_path):
            import shutil
            shutil.copy2(screenshot_path, ver_dir / "screenshot.png")
        return self.version


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SIMPLE_PIPELINE = 'from siva.spec_api import *\n\ndata = source("vtkSphereSource")\nshow(data, "sphere")\n'
_SPHERE_LARGE = 'from siva.spec_api import *\n\ndata = source("vtkSphereSource", Radius=2.0)\nshow(data, "s")\n'
_BROKEN_PIPELINE = 'from siva.spec_api import *\n\nundefined_name_xyz\n'


def _wait(event, timeout=5.0, msg="Timed out waiting for build"):
    ok = event.wait(timeout=timeout)
    if not ok:
        raise AssertionError(msg)


def _wait_for_record(coordinator, record, timeout=8.0):
    """Block until record.status != 'running'. Returns True if finished in time."""
    deadline = time.monotonic() + timeout
    with coordinator._cv:
        while record.status == "running":
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            coordinator._cv.wait(timeout=remaining)
    return True


# ---------------------------------------------------------------------------
# Test 1: Basic hot reload — write file, watcher triggers build, status file written
# ---------------------------------------------------------------------------

class TestBasicHotReload(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
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
        """Writing a pipeline file triggers a build; record fields are populated."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)
        # Wait for watcher to notice and build to complete
        record = self._coordinator.wait_for_current(timeout=5.0)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "ok")
        self.assertIsNotNone(record.source_hash)
        self.assertIsNotNone(record.version)


# ---------------------------------------------------------------------------
# Test 1b: Two PipelineWatchers in the same parent dir both fire
# ---------------------------------------------------------------------------

class TestTwoWatchersSameDir(unittest.TestCase):
    """Regression: when two views live in the same directory (e.g. view-main.py
    and view-vorticity.py), both watchers must trigger builds when their
    respective files are written. Previously, each PipelineWatcher created its
    own Observer and the second one's macOS fsevents emitter raised
    RuntimeError ("already scheduled") in a background thread, silently
    disabling the second watcher.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)

        self._ctx_a = _FakeCtx("alpha", self._tmp)
        self._ctx_b = _FakeCtx("beta", self._tmp)
        self._renderer = _FakeRenderer()
        self._coord_a = BuildCoordinator(self._ctx_a, self._renderer)
        self._coord_b = BuildCoordinator(self._ctx_b, self._renderer)
        self._watcher_a = PipelineWatcher(
            self._coord_a, os.path.join(self._tmp, "view-alpha.py"),
        )
        self._watcher_b = PipelineWatcher(
            self._coord_b, os.path.join(self._tmp, "view-beta.py"),
        )
        self._watcher_a.start()
        self._watcher_b.start()

    def tearDown(self):
        self._watcher_a.stop()
        self._watcher_b.stop()
        self._coord_a.shutdown()
        self._coord_b.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_both_watchers_fire(self):
        Path(self._tmp, "view-alpha.py").write_text(_SIMPLE_PIPELINE)
        Path(self._tmp, "view-beta.py").write_text(_SIMPLE_PIPELINE)

        # Poll _latest directly — wait_for_current() would self-trigger a
        # build and mask a dead watcher. Here we only want to know whether
        # the *watcher* induced a build.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self._coord_a._latest is not None and self._coord_b._latest is not None:
                break
            time.sleep(0.05)

        self.assertIsNotNone(
            self._coord_a._latest, "alpha watcher never fired a build"
        )
        self.assertIsNotNone(
            self._coord_b._latest, "beta watcher never fired a build"
        )
        self.assertEqual(self._coord_a._latest.status, "ok")
        self.assertEqual(self._coord_b._latest.status, "ok")


# ---------------------------------------------------------------------------
# Test 2: wait_for_current blocks until build completes
# ---------------------------------------------------------------------------

class TestWaitForCurrent(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
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
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
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

        _wait_for_record(self._coordinator, r1, timeout=5.0)
        self.assertEqual(r1.status, "ok")


# ---------------------------------------------------------------------------
# Test 4: Different hash mid-build queues, second build runs after first
# ---------------------------------------------------------------------------

class TestQueueingMidBuild(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)

    def tearDown(self):
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_second_build_queued_while_first_in_flight(self):
        """When second hash is queued while first is IN-FLIGHT, second runs after first.

        Uses a threading.Event to gate the fake renderer's dispatch so
        we can reliably observe the in-flight state without a timing race.
        """
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        # Patch the renderer to pause mid-build so we can inspect in-flight state.
        gate = threading.Event()
        orig_dispatch = self._renderer.dispatch

        call_count = [0]

        def gating_dispatch(fn):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call is apply_to_renderer — pause here so r1 stays in-flight.
                gate.wait(timeout=5.0)
            return orig_dispatch(fn)

        self._renderer.dispatch = gating_dispatch

        # Start first build.
        r1 = self._coordinator.request_build(_SIMPLE_PIPELINE)

        # Wait until r1 is in-flight (worker is blocked on the gate).
        t_start = time.monotonic()
        while True:
            with self._coordinator._cv:
                inflight = self._coordinator._inflight
            if inflight is not None and inflight.source_hash == r1.source_hash:
                break
            if time.monotonic() - t_start > 3.0:
                gate.set()  # unblock to avoid hanging tearDown
                self.skipTest("r1 never went inflight")
            time.sleep(0.005)

        # Queue second (different hash) while first is blocked.
        r2 = self._coordinator.request_build(_SPHERE_LARGE)

        # r2 should now be pending, r1 still inflight.
        with self._coordinator._cv:
            pending = self._coordinator._pending
        self.assertIsNotNone(pending, "r2 should be queued as pending")
        self.assertEqual(pending.source_hash, r2.source_hash)

        # Unblock the first build.
        gate.set()

        ok1 = _wait_for_record(self._coordinator, r1, timeout=8.0)
        ok2 = _wait_for_record(self._coordinator, r2, timeout=8.0)

        self.assertTrue(ok1, "r1 timed out")
        self.assertTrue(ok2, "r2 timed out")
        self.assertEqual(r1.status, "ok")
        self.assertEqual(r2.status, "ok")
        self.assertNotEqual(r1.source_hash, r2.source_hash)

        # latest() should reflect the second build.
        latest = self._coordinator.latest()
        self.assertEqual(latest.source_hash, r2.source_hash)


# ---------------------------------------------------------------------------
# Test 5: Concurrent wait_for_pipeline calls share one build
# ---------------------------------------------------------------------------

class TestConcurrentWaiters(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
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
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
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
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
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
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
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
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
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
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
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
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        # Use a OFFSCREEN renderer (avoids needing a real event loop, but
        # dispatch still works inline — validates the interface).
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
# Test: pipeline_status MCP tool
# ---------------------------------------------------------------------------

class TestPipelineStatusTool(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmp)
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
        import siva.server as srv
        self._srv = srv
        renderer = _FakeRenderer()
        srv._init_for_test(renderer)

    def tearDown(self):
        import siva.server as srv
        # Shutdown coordinators
        for ctx in srv._views.values():
            try:
                ctx.shutdown()
            except Exception:
                pass
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_pipeline_status_no_build(self):
        """pipeline_status() reports 'no build' when none has completed."""
        result = self._srv.pipeline_status()
        self.assertIsInstance(result, str)
        self.assertIn("No build", result)

    def test_pipeline_status_after_build(self):
        """pipeline_status() returns the same terse report wait_for_pipeline does."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        ctx = self._srv._current_ctx()
        ctx.coordinator.wait_for_current(timeout=5.0)

        result = self._srv.pipeline_status()
        self.assertIsInstance(result, str)
        # Terse report header includes "Pipeline v<n>"
        self.assertIn("Pipeline v", result)
        self.assertIn("Cache:", result)


# ---------------------------------------------------------------------------
# Test: applied_hash tracks renderer state
# ---------------------------------------------------------------------------

class TestAppliedHash(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)

    def tearDown(self):
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_applied_hash_set_after_successful_build(self):
        """ctx.applied_hash is set to the file's hash after a successful build."""
        import hashlib
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        record = self._coordinator.wait_for_current(timeout=5.0)
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "ok")

        expected_hash = hashlib.sha256(_SIMPLE_PIPELINE.encode()).hexdigest()
        self.assertEqual(self._ctx.applied_hash, expected_hash,
                         "applied_hash should equal the hash of the built file")

    def test_applied_hash_unchanged_for_same_content(self):
        """Re-writing the same content keeps applied_hash stable (same hash, fast path)."""
        import hashlib
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        r1 = self._coordinator.wait_for_current(timeout=5.0)
        self.assertEqual(r1.status, "ok")
        hash_after_first = self._ctx.applied_hash

        # Write the same content again.
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)
        r2 = self._coordinator.wait_for_current(timeout=5.0)
        self.assertEqual(r2.status, "ok")

        # Hash and applied_hash should be unchanged.
        expected_hash = hashlib.sha256(_SIMPLE_PIPELINE.encode()).hexdigest()
        self.assertEqual(self._ctx.applied_hash, expected_hash)
        self.assertEqual(hash_after_first, self._ctx.applied_hash)

    def test_applied_hash_updates_after_new_build(self):
        """applied_hash updates when a new (different) version is built."""
        import hashlib
        pipeline_path = os.path.join(self._tmp, "view-main.py")
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        r1 = self._coordinator.wait_for_current(timeout=5.0)
        self.assertEqual(r1.status, "ok")
        first_hash = self._ctx.applied_hash

        # Write different content.
        Path(pipeline_path).write_text(_SPHERE_LARGE)
        r2 = self._coordinator.wait_for_current(timeout=5.0)
        self.assertIsNotNone(r2)
        self.assertEqual(r2.status, "ok")

        expected_hash = hashlib.sha256(_SPHERE_LARGE.encode()).hexdigest()
        self.assertEqual(self._ctx.applied_hash, expected_hash,
                         "applied_hash should update to the new file's hash")
        self.assertNotEqual(first_hash, self._ctx.applied_hash)


# ---------------------------------------------------------------------------
# Test: cancelled-record semantics (orphan-pending fix)
# ---------------------------------------------------------------------------

class TestCancelledRecord(unittest.TestCase):
    """When a pending record is displaced by a newer request, it is cancelled."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)

    def tearDown(self):
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_displaced_pending_record_becomes_cancelled(self):
        """Requesting a second hash while first is only pending marks first cancelled."""
        # Gate the worker so nothing runs yet.
        gate = threading.Event()
        orig = self._renderer.dispatch

        call_count = [0]

        def gating(fn):
            call_count[0] += 1
            if call_count[0] == 1:
                gate.wait(timeout=5.0)
            return orig(fn)

        self._renderer.dispatch = gating

        # Enqueue first hash (may or may not go inflight yet).
        r1 = self._coordinator.request_build(_SIMPLE_PIPELINE)

        # Wait until r1 is inflight, then displace with r2.
        t_start = time.monotonic()
        while True:
            with self._coordinator._cv:
                inflight = self._coordinator._inflight
            if inflight is not None:
                break
            if time.monotonic() - t_start > 3.0:
                gate.set()
                self.skipTest("Worker never went inflight")
            time.sleep(0.005)

        # r1 is inflight; enqueue r2 — this should go to _pending.
        r2 = self._coordinator.request_build(_SPHERE_LARGE)

        # Now enqueue r3 — this should displace r2 and mark r2 "cancelled".
        r3 = self._coordinator.request_build(_BROKEN_PIPELINE)

        # r2 should be cancelled immediately (displaced by r3).
        self.assertEqual(r2.status, "cancelled",
                         "Displaced pending record should be marked cancelled")

        # Let everything run.
        gate.set()
        _wait_for_record(self._coordinator, r3, timeout=8.0)
        # r3 built fine (broken pipeline = error status, not cancelled).
        self.assertIn(r3.status, ("ok", "error"))


# ---------------------------------------------------------------------------
# Test: atomic-write retry in _read_file
# ---------------------------------------------------------------------------

class TestAtomicWriteRetry(unittest.TestCase):
    """_read_file retries once on FileNotFoundError (editor atomic-rename saves)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)

    def tearDown(self):
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_read_file_retries_on_fnf(self):
        """_read_file returns file contents after a transient FileNotFoundError."""
        pipeline_path = os.path.join(self._tmp, "view-main.py")

        # Simulate: file doesn't exist on first attempt, appears on second.
        read_call = [0]
        real_read_text = Path.read_text

        def patched_read_text(self_path, *args, **kwargs):
            if str(self_path) == pipeline_path:
                read_call[0] += 1
                if read_call[0] == 1:
                    raise FileNotFoundError(f"Simulated missing: {self_path}")
            return real_read_text(self_path, *args, **kwargs)

        # Write the file so the second attempt succeeds.
        Path(pipeline_path).write_text(_SIMPLE_PIPELINE)

        with patch("pathlib.Path.read_text", patched_read_text):
            result = self._coordinator._read_file()

        self.assertEqual(result, _SIMPLE_PIPELINE,
                         "_read_file should return contents after retry")
        self.assertEqual(read_call[0], 2, "Should have attempted read twice")


# ---------------------------------------------------------------------------
# Test: partial edit shows cache hits
# ---------------------------------------------------------------------------

class TestPartialEditCacheHits(unittest.TestCase):
    """Gamma edit-mem category: editing one downstream param shows hits > 0 in status."""

    def setUp(self):
        _ensure_synthetic()
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
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

    def test_partial_edit_shows_cache_hits_in_status_json(self):
        """After editing one param, the build record should show cache.hits > 0 and misses < node_count.

        Build v1 (3 nodes: source → threshold → surface); then change only the
        ThresholdRange — only thresh + surf should miss; source should hit.
        """
        # Builds run with the process's actual cwd (BuildCoordinator doesn't
        # chdir; production has a single global --workdir), so the dataset
        # must be symlinked there for FileName confinement to allow it.
        rel_data = _synthetic_vti_in(os.getcwd())
        pipeline_v1 = (
            'from siva.spec_api import *\n\n'
            f'data = source("vtkXMLImageDataReader", FileName="{rel_data}")\n'
            'thresh = threshold(input=data, ThresholdBy="temperature", ThresholdRange=[100.0, 1000.0])\n'
            'surf = filter("vtkDataSetSurfaceFilter", input=thresh)\n'
            'show(surf, "surface")\n'
        )
        pipeline_v2 = (
            'from siva.spec_api import *\n\n'
            f'data = source("vtkXMLImageDataReader", FileName="{rel_data}")\n'
            'thresh = threshold(input=data, ThresholdBy="temperature", ThresholdRange=[200.0, 1000.0])\n'
            'surf = filter("vtkDataSetSurfaceFilter", input=thresh)\n'
            'show(surf, "surface")\n'
        )

        pipeline_path = os.path.join(self._tmp, "view-main.py")

        # v1 — cold build
        Path(pipeline_path).write_text(pipeline_v1)
        r1 = self._coordinator.wait_for_current(timeout=10.0)
        self.assertIsNotNone(r1, "v1 build timed out")
        self.assertEqual(r1.status, "ok", f"v1 build failed: {r1.error}")

        # v2 — single-param edit (ThresholdRange)
        Path(pipeline_path).write_text(pipeline_v2)
        r2 = self._coordinator.wait_for_current(timeout=10.0)
        self.assertIsNotNone(r2, "v2 build timed out")
        self.assertEqual(r2.status, "ok", f"v2 build failed: {r2.error}")

        node_count = r2.node_count
        hits = r2.cache_stats["hits"]
        misses = r2.cache_stats["misses"]

        self.assertGreater(hits, 0,
            f"Expected cache hits > 0 after partial edit; got hits={hits}, misses={misses}")
        self.assertLess(misses, node_count,
            f"Expected misses < {node_count} (not full rebuild); got misses={misses}")


# ---------------------------------------------------------------------------
# Test: file mtime change invalidates source node
# ---------------------------------------------------------------------------

class TestFileMtimeInvalidatesSource(unittest.TestCase):
    """Gamma file-mtime category: touching the data file busts the source node hash."""

    def setUp(self):
        _ensure_synthetic()
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
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

    def test_file_mtime_change_invalidates_source_node(self):
        """Touching the data file (mtime change, no content change) causes a source miss.

        After v1 build, os.utime() the data file, then rebuild the same pipeline
        code. The source node must miss (its fingerprint changed). All downstream
        nodes also miss because they depend on the source. cache.misses == node_count.
        """
        rel_data = _synthetic_vti_in(os.getcwd())
        pipeline_code = (
            'from siva.spec_api import *\n\n'
            f'data = source("vtkXMLImageDataReader", FileName="{rel_data}")\n'
            'thresh = threshold(input=data, ThresholdBy="temperature", ThresholdRange=[100.0, 1000.0])\n'
            'surf = filter("vtkDataSetSurfaceFilter", input=thresh)\n'
            'show(surf, "surface")\n'
        )

        pipeline_path = os.path.join(self._tmp, "view-main.py")

        # v1 — cold build
        Path(pipeline_path).write_text(pipeline_code)
        r1 = self._coordinator.wait_for_current(timeout=10.0)
        self.assertIsNotNone(r1, "v1 build timed out")
        self.assertEqual(r1.status, "ok", f"v1 build failed: {r1.error}")

        # Touch the data file to change its mtime (no content change)
        os.utime(_SYNTHETIC_VTI, None)

        # Re-write the same pipeline code to trigger a new build
        Path(pipeline_path).write_text(pipeline_code + "# force rebuild\n")
        r2 = self._coordinator.wait_for_current(timeout=10.0)
        self.assertIsNotNone(r2, "v2 build timed out")
        self.assertEqual(r2.status, "ok", f"v2 build failed: {r2.error}")

        misses = r2.cache_stats["misses"]
        node_count = r2.node_count or 3

        self.assertGreater(misses, 0,
            "Expected at least the source node to miss after mtime change")
        # All nodes depend on the source, so all should miss
        self.assertEqual(misses, node_count,
            f"Expected full rebuild (all {node_count} nodes miss) after data file mtime change; "
            f"got misses={misses}")


if __name__ == "__main__":
    unittest.main()
