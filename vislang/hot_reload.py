"""Hot-reload watcher and build coordinator for VisLang pipeline files.

Architecture:
- PipelineWatcher: watchdog wrapper that calls coordinator.request_build()
  on file save events (debounced, filtered to exact file path).
- BuildCoordinator: owns a single build-worker thread. Keyed by source_hash
  (sha256 of file contents). Concurrent requests for the same hash share one
  build. New hash mid-build is queued to start when current build finishes.
- Build worker: runs interpret_build() (compute, no renderer touch), then
  marshals renderer application and screenshot via renderer.run_on_main_thread().
  Writes view-{name}.status.json after every build.
- MCP callers use wait_for_current() which blocks on a per-BuildRecord Event.
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
    code: str
    started_at: float
    finished_at: Optional[float]
    status: str                # "running" | "ok" | "error"
    screenshot_path: Optional[str]
    error: Optional[str]
    log: list                  # human-readable lines
    version: Optional[int]     # version number saved (if any)
    report: Optional[str] = None  # full text report for run_pipeline to return
    _done_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until this record finishes. Returns True if finished, False on timeout."""
        return self._done_event.wait(timeout=timeout)

    def _finish(self):
        self._done_event.set()


# ---------------------------------------------------------------------------
# BuildCoordinator
# ---------------------------------------------------------------------------

class BuildCoordinator:
    """Coordinates background builds for one ViewContext.

    A 'build' is keyed by the SHA-256 of the pipeline file contents.
    Concurrent requests for the same source_hash while a build for that hash
    is already in-flight share the single build. A previously finished build
    is *not* reused — every new request triggers a fresh build unless one is
    already running for the same hash.

    Cancellation is never done mid-build (VTK objects in flight). New requests
    arriving mid-build are queued and start as soon as the current build finishes.
    """

    def __init__(self, ctx, renderer):
        self._ctx = ctx          # ViewContext
        self._renderer = renderer

        self._lock = threading.Lock()

        # The currently in-flight BuildRecord (status=="running"), or None.
        self._inflight: Optional[BuildRecord] = None
        # The most recent finished BuildRecord (for status peek and latest()).
        self._latest: Optional[BuildRecord] = None

        # Pending build request: (source_hash, code) or None.
        # If a build finishes and pending is set, the worker starts it next.
        self._pending_hash: Optional[str] = None
        self._pending_code: Optional[str] = None
        self._pending_record: Optional[BuildRecord] = None
        self._work_event = threading.Event()  # signaled when pending != None
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

        If there is already an in-flight build for the same source_hash, returns
        that existing record (callers share the single build). Otherwise enqueues
        a new build (the worker picks it up when the current one finishes).

        Reads the file if code is None. Returns None if file not found.
        """
        if code is None:
            code = self._read_file()
            if code is None:
                return None

        source_hash = _sha256(code)

        with self._lock:
            # If this hash is already in-flight, share the build.
            if self._inflight is not None and self._inflight.source_hash == source_hash:
                return self._inflight

            # If this hash is already pending (queued but not started), share it.
            pending_record = getattr(self, "_pending_record", None)
            if pending_record is not None and pending_record.source_hash == source_hash:
                return pending_record

            # Otherwise, enqueue this hash (replaces any previous pending request;
            # do NOT interrupt the current in-flight build).
            record = BuildRecord(
                source_hash=source_hash,
                code=code,
                started_at=time.monotonic(),
                finished_at=None,
                status="running",
                screenshot_path=None,
                error=None,
                log=[],
                version=None,
            )
            self._pending_hash = source_hash
            self._pending_code = code
            self._pending_record = record
            self._work_event.set()
            return record

    def wait_for_current(self, timeout: Optional[float] = None) -> Optional[BuildRecord]:
        """Read the file, ensure a build exists for that hash, block until done.

        If the latest finished build matches the current file hash, returns it
        immediately (no redundant rebuild). Otherwise starts a new build and waits.

        This is what run_pipeline MCP tool calls.
        Returns the finished BuildRecord, or None if file not found.
        """
        code = self._read_file()
        if code is None:
            return None
        source_hash = _sha256(code)

        with self._lock:
            # If latest finished build matches current file hash, return it directly.
            if (self._latest is not None
                    and self._latest.source_hash == source_hash
                    and self._latest.status != "running"):
                return self._latest

        record = self.request_build(code)
        if record is None:
            return None
        record.wait(timeout=timeout)
        return record

    def latest(self) -> Optional[BuildRecord]:
        """Most recent finished BuildRecord (for status peek)."""
        with self._lock:
            return self._latest

    def shutdown(self):
        """Stop the worker thread cleanly."""
        self._shutdown = True
        self._work_event.set()
        self._worker.join(timeout=5)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read_file(self) -> Optional[str]:
        """Read the pipeline file. Returns None if not found."""
        file_path = self._ctx.pipeline_file
        try:
            return Path(file_path).read_text()
        except FileNotFoundError:
            return None
        except Exception as exc:
            logger.warning("hot_reload: error reading %s: %s", file_path, exc)
            return None

    def _worker_loop(self):
        """Single-threaded build worker. Drains pending requests one at a time."""
        while not self._shutdown:
            self._work_event.wait()
            self._work_event.clear()
            if self._shutdown:
                break

            with self._lock:
                source_hash = self._pending_hash
                code = self._pending_code
                record = getattr(self, "_pending_record", None)
                self._pending_hash = None
                self._pending_code = None
                self._pending_record = None
                if record is not None:
                    self._inflight = record

            if source_hash is None or record is None:
                continue

            self._run_build(record)

            # Clear inflight; if a new request arrived while building, wake immediately
            with self._lock:
                self._inflight = None
                if self._pending_hash is not None:
                    self._work_event.set()

    def _run_build(self, record: BuildRecord):
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
                record.code, cache=ctx.cache
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
            ctx.current_code = record.code

            # --- Screenshot (must run on main thread) ---
            view_name = ctx.name
            screenshot_path = f".vislang/latest_{view_name}.png"
            Path(".vislang").mkdir(parents=True, exist_ok=True)

            taken_path = renderer.run_on_main_thread(
                lambda: _take_screenshot(renderer, screenshot_path)
            )

            # --- Version save ---
            version = _save_version_for(ctx, record.code, taken_path)
            log.append(f"Saved version v{version}")

            # --- Build text report ---
            t_total = time.monotonic() - t0
            report = _build_report(
                node_statuses, show_statuses, version, t_interpret,
                t_total, renderer
            )

            # --- Finalize record ---
            with self._lock:
                record.status = "ok"
                record.finished_at = time.monotonic()
                record.screenshot_path = taken_path
                record.version = version
                record.report = report
                self._latest = record

        except Exception as exc:
            logger.warning("hot_reload: build error for %s: %s", ctx.name, exc)
            log.append(f"Error: {type(exc).__name__}: {exc}")
            with self._lock:
                record.status = "error"
                record.finished_at = time.monotonic()
                record.error = f"{type(exc).__name__}: {exc}"
                record.report = f"Pipeline error: {type(exc).__name__}: {exc}"
                self._latest = record

        # --- Write status file ---
        try:
            self._write_status_file(record, cache_stats, node_count)
        except Exception as exc:
            logger.warning("hot_reload: failed to write status file: %s", exc)

        record._finish()

    def _write_status_file(self, record: BuildRecord, cache_stats: dict, node_count: int):
        """Write view-{name}.status.json next to the pipeline file."""
        ctx = self._ctx
        # Write the status file next to the pipeline file (absolute path)
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


def _take_screenshot(renderer, path: str) -> str:
    """Take a screenshot (must be called on main thread)."""
    renderer.render()
    return renderer.screenshot(path)


def _build_report(
    node_statuses: dict,
    show_statuses: dict,
    version: int,
    t_interpret: float,
    t_total: float,
    renderer,
) -> str:
    """Build the human-readable pipeline build report (same format as _run_pipeline_impl)."""
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


def _save_version_for(ctx, code: str, screenshot_path: Optional[str]) -> int:
    """Save a version snapshot for ctx. Returns the version number."""
    ctx.version += 1
    ver_dir = ctx.history_dir / f"v{ctx.version:04d}"
    ver_dir.mkdir(parents=True, exist_ok=True)
    (ver_dir / "pipeline.py").write_text(code)
    if screenshot_path:
        import shutil
        png_path = (
            screenshot_path[:-4] + ".png"
            if screenshot_path.endswith(".jpg")
            else screenshot_path
        )
        if os.path.exists(png_path):
            shutil.copy2(png_path, ver_dir / "screenshot.png")
    return ctx.version
