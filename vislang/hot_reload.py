"""Hot-reload watcher and build coordinator for VisLang pipeline files.

Architecture:
- PipelineWatcher: watchdog wrapper that calls coordinator.request_build()
  on file save events (debounced, filtered to exact file path).
- BuildCoordinator: owns a single build-worker thread. Keyed by source_hash
  (sha256 of file contents). Concurrent requests for the same hash share one
  build. New hash mid-build is queued and starts when current build finishes.
  Displaced pending records are marked "cancelled" so waiters unblock cleanly.
- Build worker: runs interpret_build() (compute, no renderer touch), then
  marshals renderer application and screenshot via renderer.run_on_main_thread().
  Sets ctx.applied_hash after a successful apply phase. Writes
  view-{name}.status.json after every build.
- MCP callers use wait_for_current() which blocks on _cv (a single Condition
  shared by worker and all waiters).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("vislang.hot_reload")


# ---------------------------------------------------------------------------
# BuildRecord
# ---------------------------------------------------------------------------

@dataclass
class BuildRecord:
    source_hash: str           # sha256 of pipeline file contents
    started_at: float
    finished_at: Optional[float]
    status: str                # "running" | "ok" | "error" | "cancelled"
    screenshot_path: Optional[str]
    error: Optional[str]
    log: list                  # human-readable lines
    version: Optional[int]     # version number saved (if any)
    report: Optional[str] = None  # full text report for run_pipeline to return


# ---------------------------------------------------------------------------
# BuildCoordinator
# ---------------------------------------------------------------------------

class BuildCoordinator:
    """Coordinates background builds for one ViewContext.

    A 'build' is keyed by the SHA-256 of the pipeline file contents.
    Concurrent requests for the same source_hash while a build for that hash
    is already in-flight or pending share the single record. A new hash
    mid-build is queued; when the current build finishes the worker picks it
    up. The displaced pending record (if any) is marked "cancelled".

    Synchronisation: a single threading.Condition (_cv) replaces the old
    _lock + _work_event + per-record _done_event trio. The worker waits on _cv
    for "pending or shutdown". MCP threads wait on _cv for "record finished
    (status != running)". All state transitions notify_all under _cv.

    Shutdown note: shutdown() must NOT be called from the renderer's main
    thread while a build is mid-render-phase. The worker holds
    run_on_main_thread(), which queues work back to that same thread — calling
    join() there would deadlock. In practice server teardown calls ctx.shutdown()
    from a signal handler / MCP thread, not the VTK event loop thread. If the
    main thread must call shutdown(), it should ensure the renderer's work
    queue is drained first. The 5s join timeout prevents a true hang.
    """

    def __init__(self, ctx, renderer):
        self._ctx = ctx          # ViewContext
        self._renderer = renderer

        # Single condition variable — guards all mutable state below.
        self._cv = threading.Condition()

        # The currently in-flight BuildRecord (status=="running"), or None.
        self._inflight: Optional[BuildRecord] = None
        # The most recent finished BuildRecord (for status peek and latest()).
        self._latest: Optional[BuildRecord] = None

        # At most one pending record waiting to be picked up by the worker.
        # When a newer request arrives, this record is marked "cancelled" and
        # notify_all() wakes any waiter.
        self._pending: Optional[BuildRecord] = None
        # Code for the pending record (worker reads from this, not from record,
        # so file contents are captured at request time even if the file changes
        # again before the worker picks it up).
        self._pending_code: Optional[str] = None

        self._shutdown = False

        self._worker = threading.Thread(
            target=self._worker_loop, name=f"build-worker-{ctx.name}", daemon=True
        )
        self._worker.start()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def request_build(self, code: Optional[str] = None) -> Optional[BuildRecord]:
        """Ensure a build for the given code (or current file) is in flight.

        If there is already an in-flight or pending build for the same
        source_hash, returns that existing record. Otherwise enqueues a new
        build, displacing any current pending record (which is cancelled).

        Reads the file if code is None. Returns None if file not found.
        """
        if code is None:
            code = self._read_file()
            if code is None:
                return None

        source_hash = _sha256(code)

        with self._cv:
            # Share an in-flight build for the same hash.
            if self._inflight is not None and self._inflight.source_hash == source_hash:
                return self._inflight

            # Share a pending build for the same hash.
            if self._pending is not None and self._pending.source_hash == source_hash:
                return self._pending

            # Cancel any existing pending record so its waiter unblocks.
            if self._pending is not None:
                self._pending.status = "cancelled"
                self._cv.notify_all()

            record = BuildRecord(
                source_hash=source_hash,
                started_at=time.monotonic(),
                finished_at=None,
                status="running",
                screenshot_path=None,
                error=None,
                log=[],
                version=None,
            )
            self._pending = record
            self._pending_code = code
            self._cv.notify_all()  # wake worker
            return record

    def wait_for_current(self, timeout: Optional[float] = None) -> Optional[BuildRecord]:
        """Read the file, ensure a build exists for that hash, block until done.

        Hash is re-computed inside the lock to avoid a race where the file
        changes between read and lock acquisition. Returns immediately if the
        latest finished build matches the current file hash (no rebuild needed).

        Returns the finished BuildRecord, or None if file not found.
        """
        with self._cv:
            # Re-read and hash inside the lock to avoid the read→lock race.
            code = self._read_file()
            if code is None:
                return None
            source_hash = _sha256(code)

            # Fast path: an in-flight or pending build for this hash exists.
            if self._inflight is not None and self._inflight.source_hash == source_hash:
                record = self._inflight
            elif self._pending is not None and self._pending.source_hash == source_hash:
                record = self._pending
            elif (self._latest is not None
                  and self._latest.source_hash == source_hash
                  and self._latest.status != "running"):
                # Latest finished build matches — return it immediately.
                return self._latest
            else:
                # Need a new build. Cancel any existing pending first.
                if self._pending is not None:
                    self._pending.status = "cancelled"
                    self._cv.notify_all()
                record = BuildRecord(
                    source_hash=source_hash,
                    started_at=time.monotonic(),
                    finished_at=None,
                    status="running",
                    screenshot_path=None,
                    error=None,
                    log=[],
                    version=None,
                )
                self._pending = record
                self._pending_code = code
                self._cv.notify_all()  # wake worker
        # Lock released — wait for the record WITHOUT holding _cv.
        # Waiting inside the lock would deadlock in headless-interactive mode:
        # run_on_main_thread (called by the worker) needs to execute on the
        # main thread, which may be the same thread that called wait_for_current.
        self._wait_for_record(record, timeout)
        return record

    def latest(self) -> Optional[BuildRecord]:
        """Most recent finished BuildRecord (for status peek)."""
        with self._cv:
            return self._latest

    def shutdown(self):
        """Stop the worker thread cleanly.

        Must not be called from the renderer's main thread while a build is
        mid-render-phase — see class docstring.
        """
        with self._cv:
            self._shutdown = True
            self._cv.notify_all()
        self._worker.join(timeout=5)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _wait_for_record(self, record: BuildRecord, timeout: Optional[float]) -> None:
        """Block until record.status != 'running', WITHOUT holding _cv.

        Called after releasing the lock so that the calling thread (which may be
        the renderer's main thread in headless-interactive mode) can still service
        run_on_main_thread callbacks from the build worker.
        """
        deadline = (time.monotonic() + timeout) if timeout is not None else None
        with self._cv:
            while record.status == "running":
                remaining = (deadline - time.monotonic()) if deadline is not None else None
                if remaining is not None and remaining <= 0:
                    break
                self._cv.wait(timeout=remaining)

    def _read_file(self) -> Optional[str]:
        """Read the pipeline file, retrying once on FileNotFoundError.

        The retry handles editor atomic-write (rename-over) saves where the
        inode briefly disappears between the delete and the rename.
        """
        file_path = self._ctx.pipeline_file
        for attempt in range(2):
            try:
                return Path(file_path).read_text()
            except FileNotFoundError:
                if attempt == 0:
                    time.sleep(0.02)  # 20ms retry for atomic-rename saves
                else:
                    return None
            except Exception as exc:
                logger.warning("hot_reload: error reading %s: %s", file_path, exc)
                return None
        return None

    def _worker_loop(self):
        """Single-threaded build worker. Drains pending requests one at a time."""
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._pending is not None or self._shutdown)
                if self._shutdown:
                    break
                record = self._pending
                code = self._pending_code
                self._pending = None
                self._pending_code = None
                if record is not None:
                    self._inflight = record

            if record is None:
                continue

            self._run_build(record, code)

            # _inflight was already cleared inside _run_build under _cv.
            # Notify again in case the exception path left a waiter sleeping.
            with self._cv:
                self._inflight = None  # idempotent in success path, needed for crashes
                self._cv.notify_all()

    def _run_build(self, record: BuildRecord, code: str):
        """Execute one build: compute phase (this thread) + render phase (main thread)."""
        ctx = self._ctx
        renderer = self._renderer
        t0 = time.monotonic()
        log = record.log
        cache_stats = {"hits": 0, "misses": 0, "evictions": 0}
        node_count = 0

        try:
            from vislang.dsl import interpret_build

            # --- Compute phase (no renderer touch) ---
            builder, vtk_objs_raw, vtk_objs, node_statuses = interpret_build(
                code, cache=ctx.cache
            )
            t_interpret = time.monotonic() - t0
            log.append(
                f"Build computed in {t_interpret:.2f}s ({len(vtk_objs)} nodes)"
            )
            log.append(
                f"Cache: {ctx.cache.hits} hits, {ctx.cache.misses} misses, "
                f"{ctx.cache.evictions} evictions"
            )
            cache_stats = {
                "hits": ctx.cache.hits,
                "misses": ctx.cache.misses,
                "evictions": ctx.cache.evictions,
            }
            node_count = len(vtk_objs)

            # --- Render phase (must run on main thread) ---
            show_statuses = renderer.run_on_main_thread(
                lambda: builder._apply_to_renderer(vtk_objs_raw, renderer)
            )
            ctx.vtk_objects = vtk_objs
            ctx.current_code = code

            # --- Mark renderer state as reflecting this hash ---
            ctx.applied_hash = record.source_hash

            # --- Screenshot (must run on main thread; render() also requires it) ---
            view_name = ctx.name
            screenshot_path = f".vislang/latest_{view_name}.png"
            Path(".vislang").mkdir(parents=True, exist_ok=True)

            taken_path = renderer.run_on_main_thread(
                lambda: (renderer.render(), renderer.screenshot(screenshot_path))[1]
            )

            # --- Version save ---
            version = ctx.save_version(code, taken_path)
            log.append(f"Saved version v{version}")

            # --- Build text report ---
            t_total = time.monotonic() - t0
            report = _build_report(
                node_statuses, show_statuses, version, t_interpret,
                t_total, cache_stats, renderer
            )

            # Populate record fields before the lock so we can write the
            # status file (I/O) before notifying waiters — they should see
            # the file on disk when they wake up.
            record.status = "ok"
            record.finished_at = time.monotonic()
            record.screenshot_path = taken_path
            record.version = version
            record.report = report

        except Exception as exc:
            logger.warning("hot_reload: build error for %s: %s", ctx.name, exc)
            log.append(f"Error: {type(exc).__name__}: {exc}")
            record.status = "error"
            record.finished_at = time.monotonic()
            record.error = f"{type(exc).__name__}: {exc}"
            record.report = f"Pipeline error: {type(exc).__name__}: {exc}"

        # --- Write status file before notifying waiters ---
        try:
            self._write_status_file(record, cache_stats, node_count)
        except Exception as exc:
            logger.warning("hot_reload: failed to write status file: %s", exc)

        # --- Finalize: update shared state and wake waiters ---
        with self._cv:
            self._latest = record
            self._inflight = None  # clear atomically so status peek is consistent
            self._cv.notify_all()

    def _write_status_file(self, record: BuildRecord, cache_stats: dict, node_count: int):
        """Write view-{name}.status.json next to the pipeline file."""
        ctx = self._ctx
        pipeline_path = Path(ctx.pipeline_file)
        status_path = str(pipeline_path.parent / f"view-{ctx.name}.status.json")
        duration_s = (record.finished_at - record.started_at) if record.finished_at else None

        payload = {
            "source_hash": record.source_hash,
            "status": record.status,
            "finished_at": record.finished_at,
            "duration_s": round(duration_s, 4) if duration_s is not None else None,
            "node_count": node_count,
            "cache": cache_stats,
            "screenshot": record.screenshot_path,
            "version": record.version,
            "error": record.error,
            "log": record.log,
        }
        Path(status_path).write_text(json.dumps(payload, indent=2))
        logger.debug("hot_reload: wrote status file %s (status=%s)", status_path, record.status)


# ---------------------------------------------------------------------------
# PipelineWatcher
# ---------------------------------------------------------------------------

class PipelineWatcher:
    """Watchdog wrapper. On file save for the active pipeline file, calls
    coordinator.request_build(). Debounced by ~100ms.

    Watches the parent directory (to catch atomic renames). Filters events
    to the exact resolved file path only.
    """

    def __init__(self, coordinator: BuildCoordinator, file_path: str, debounce_ms: int = 100):
        self._coordinator = coordinator
        self._file_path = Path(file_path).resolve()
        self._debounce_s = debounce_ms / 1000.0
        self._observer = None
        self._last_event_time: float = 0.0
        self._event_lock = threading.Lock()

    def start(self):
        """Start watching the pipeline file's parent directory."""
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler

        coordinator = self._coordinator
        file_path = self._file_path
        debounce_s = self._debounce_s
        event_lock = self._event_lock
        last_event_ref = [self._last_event_time]

        class _Handler(FileSystemEventHandler):
            def _handle(self, path: Path):
                if path != file_path:
                    return
                now = time.monotonic()
                with event_lock:
                    if now - last_event_ref[0] < debounce_s:
                        return
                    last_event_ref[0] = now
                # Enqueue build request — do NOT do compute here (watchdog thread)
                logger.debug("hot_reload: file event for %s, requesting build", path.name)
                try:
                    coordinator.request_build()
                except Exception as exc:
                    logger.warning("hot_reload: request_build failed: %s", exc)

            def on_modified(self, event):
                if not event.is_directory:
                    self._handle(Path(event.src_path).resolve())

            def on_created(self, event):
                if not event.is_directory:
                    self._handle(Path(event.src_path).resolve())

            def on_moved(self, event):
                if not event.is_directory:
                    self._handle(Path(event.dest_path).resolve())

        watch_dir = str(self._file_path.parent)
        self._observer = Observer()
        self._observer.schedule(_Handler(), watch_dir, recursive=False)
        self._observer.start()
        logger.info("hot_reload: watching %s", self._file_path)

    def stop(self):
        """Stop the watchdog observer."""
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=3)
            except Exception as exc:
                logger.warning("hot_reload: error stopping watcher: %s", exc)
            self._observer = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _build_report(
    node_statuses: dict,
    show_statuses: dict,
    version: int,
    t_interpret: float,
    t_total: float,
    cache_stats: dict,
    renderer,
) -> str:
    """Build the human-readable pipeline build report."""
    has_errors = any("error" in s for s in node_statuses.values())
    has_warnings = any("warning" in s for s in node_statuses.values())
    has_show_errors = any("error" in s for s in show_statuses.values())

    if has_errors or has_show_errors:
        report_lines = [f"Pipeline v{version} built with ERRORS."]
    elif has_warnings:
        report_lines = [f"Pipeline v{version} built with warnings."]
    else:
        report_lines = [f"Pipeline v{version} built successfully."]
    report_lines.append("")

    report_lines.append("Nodes:")
    for node_id, status in sorted(node_statuses.items()):
        name = status.get("name", f"node_{node_id}")
        if "error" in status:
            report_lines.append(f"  {name}: ERROR - {status['error']}")
        else:
            line = f"  {name}: {status['class']}"
            num_pts = status.get("num_points")
            num_cells = status.get("num_cells")
            if num_pts is not None or num_cells is not None:
                pts_str = f"{num_pts}" if num_pts is not None else "?"
                cells_str = f"{num_cells}" if num_cells is not None else "?"
                line += f" -> {pts_str} pts, {cells_str} cells"
            if "warning" in status:
                line += f" WARNING: {status['warning']}"
            if "point_arrays" in status:
                line += f"\n    arrays: {status['point_arrays']}"
            report_lines.append(line)

    if show_statuses:
        report_lines.append("")
        report_lines.append("Show directives:")
        for name, status in show_statuses.items():
            if "error" in status:
                report_lines.append(f"  {name}: ERROR - {status['error']}")
            else:
                report_lines.append(f"  {name}: ok")

    report_lines.append("")
    hits = cache_stats.get("hits", 0)
    misses = cache_stats.get("misses", 0)
    evictions = cache_stats.get("evictions", 0)
    rebuilt = misses  # each miss = one node rebuilt
    report_lines.append(
        f"Cache: {hits} hits, {misses} misses ({rebuilt} node{'s' if rebuilt != 1 else ''} rebuilt)"
    )
    report_lines.append(
        f"Timing: pipeline {t_interpret:.2f}s, total {t_total:.2f}s"
    )
    report_lines.append("")
    try:
        cam = renderer.run_on_main_thread(renderer.get_camera_state)
        report_lines.append(
            f"Camera: position={[round(x, 1) for x in cam['position']]}, "
            f"focal_point={[round(x, 1) for x in cam['focal_point']]}"
        )
    except Exception:
        pass

    return "\n".join(report_lines)
