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
  Sets ctx.applied_hash after a successful apply phase.
- MCP callers use wait_for_current() which blocks on _cv (a single Condition
  shared by worker and all waiters).
"""

from __future__ import annotations

import hashlib
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
    report: Optional[str] = None          # terse report (default for wait_for_pipeline)
    verbose_report: Optional[str] = None  # full per-node report (verbose=True)
    node_statuses: Optional[dict] = None  # per-node status dict from interpret_build
    cache_stats: Optional[dict] = None    # {"hits", "misses", "evictions"} for this build
    node_count: int = 0                   # number of nodes interpreted

    def format(self, verbose: bool = False) -> str:
        """Render this record as a human-readable text report.

        Shared by wait_for_pipeline() (which adds a screenshot) and pipeline_status()
        (which does not). Errors and cancellations always return their own
        message regardless of verbose.
        """
        if self.status == "error":
            return self.report or f"Pipeline error: {self.error}"
        if self.status == "cancelled":
            return "Pipeline build cancelled (superseded by a newer save)."
        if self.status == "running":
            return "Pipeline build still running."
        if verbose:
            return self.verbose_report or self.report or "Pipeline built successfully."
        return self.report or "Pipeline built successfully."


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

            # Capture previous build's node_statuses for diff (before updating _latest).
            with self._cv:
                prev_record = self._latest
            prev_node_statuses = prev_record.node_statuses if prev_record is not None else None

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

            # --- Build text reports (terse default + verbose) ---
            t_total = time.monotonic() - t0
            terse_report = _build_report(
                node_statuses, show_statuses, version, t_interpret,
                t_total, cache_stats, renderer,
                verbose=False,
                prev_node_statuses=prev_node_statuses,
            )
            verbose_report = _build_report(
                node_statuses, show_statuses, version, t_interpret,
                t_total, cache_stats, renderer,
                verbose=True,
                prev_node_statuses=prev_node_statuses,
            )

            # Populate record fields before the lock so we can write the
            # status file (I/O) before notifying waiters — they should see
            # the file on disk when they wake up.
            record.status = "ok"
            record.finished_at = time.monotonic()
            record.screenshot_path = taken_path
            record.version = version
            record.report = terse_report
            record.verbose_report = verbose_report
            record.node_statuses = node_statuses
            record.cache_stats = cache_stats
            record.node_count = node_count

            _write_status_files(ctx, version, terse_report)

        except Exception as exc:
            logger.warning("hot_reload: build error for %s: %s", ctx.name, exc)
            log.append(f"Error: {type(exc).__name__}: {exc}")
            record.status = "error"
            record.finished_at = time.monotonic()
            record.error = f"{type(exc).__name__}: {exc}"
            record.report = f"Pipeline error: {type(exc).__name__}: {exc}"
            _write_status_files(ctx, None, record.report)

        # --- Finalize: update shared state and wake waiters ---
        with self._cv:
            self._latest = record
            self._inflight = None  # clear atomically so status peek is consistent
            self._cv.notify_all()


# ---------------------------------------------------------------------------
# PipelineWatcher
# ---------------------------------------------------------------------------

_shared_observer = None
_shared_observer_lock = threading.Lock()


def _get_shared_observer():
    """Return a process-wide watchdog Observer, lazily started.

    All PipelineWatchers share one Observer because watchdog's macOS fsevents
    backend rejects two Observers scheduling the same directory with
    RuntimeError ("already scheduled"), raised in a background thread that
    silently disables the second watcher.
    """
    global _shared_observer
    with _shared_observer_lock:
        if _shared_observer is None:
            from watchdog.observers import Observer
            _shared_observer = Observer()
            _shared_observer.daemon = True
            _shared_observer.start()
        return _shared_observer


class PipelineWatcher:
    """Watchdog wrapper. On file save for the active pipeline file, calls
    coordinator.request_build(). Debounced by ~100ms.

    Schedules a handler on a shared, process-wide Observer that watches the
    pipeline file's parent directory (to catch atomic renames). Filters
    events to the exact resolved file path only.
    """

    def __init__(self, coordinator: BuildCoordinator, file_path: str, debounce_ms: int = 100):
        self._coordinator = coordinator
        self._file_path = Path(file_path).resolve()
        self._debounce_s = debounce_ms / 1000.0
        self._observer = None
        self._watch = None
        self._handler = None
        self._last_event_time: float = 0.0
        self._event_lock = threading.Lock()

    def start(self):
        """Schedule a handler on the shared Observer for our parent directory."""
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

        self._handler = _Handler()
        self._observer = _get_shared_observer()
        self._watch = self._observer.schedule(
            self._handler, str(self._file_path.parent), recursive=False
        )
        logger.info("hot_reload: watching %s", self._file_path)

    def stop(self):
        """Remove this watcher's handler from the shared Observer."""
        if self._observer is not None and self._watch is not None and self._handler is not None:
            try:
                self._observer.remove_handler_for_watch(self._handler, self._watch)
            except Exception as exc:
                logger.warning("hot_reload: error removing handler: %s", exc)
            self._observer = None
            self._watch = None
            self._handler = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write_status_files(ctx, version: Optional[int], report: str) -> None:
    """Write the build report to .vislang/status_<view>.txt and, if a version
    was saved, also to <history_dir>/v{NNNN}/status.txt.
    """
    try:
        status_path = Path(".vislang") / f"status_{ctx.name}.txt"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(report)
    except Exception as exc:
        logger.warning("hot_reload: failed to write status_%s.txt: %s", ctx.name, exc)

    if version is not None:
        try:
            ver_dir = ctx.history_dir / f"v{version:04d}"
            (ver_dir / "status.txt").write_text(report)
        except Exception as exc:
            logger.warning("hot_reload: failed to write history status.txt: %s", exc)


def _diff_node_statuses(
    current: dict,
    prev: Optional[dict],
) -> list[str]:
    """Return a list of human-readable change descriptions between two builds.

    Each entry describes one changed node: added, removed, rebuilt (cache miss),
    or error/status-changed.

    Cache hits are identified by the presence of ``cached: True`` in the node
    status dict — set by the build cache when it returns a cached result.  Any
    node without ``cached: True`` was actually rebuilt (cache miss), and is
    counted as a change.  Nodes with ``cached: True`` that have the same
    diagnostic status as the previous build are counted as unchanged.

    If ``prev`` is None (first build), returns an empty list.
    """
    if prev is None:
        return []

    def _status_key(s: dict) -> tuple:
        """Stable key for semantic status (ignores output geometry)."""
        st = s.get("status", "ok")  # cache-hit records lack 'status'; treat as ok
        cls = s.get("class", "")
        kind = s.get("kind", "")
        message = s.get("message", "")
        upstream = s.get("upstream", "")
        return (st, cls, kind, message, upstream)

    changes = []
    prev_names = {s.get("name", nid): nid for nid, s in prev.items()}
    curr_names = {s.get("name", nid): nid for nid, s in current.items()}

    for name in curr_names:
        curr_s = current[curr_names[name]]
        if name not in prev_names:
            changes.append(f"added '{name}'")
        elif curr_s.get("cached"):
            # Cache hit in this build — only report if diagnostic status changed
            prev_s = prev[prev_names[name]]
            if _status_key(curr_s) != _status_key(prev_s):
                changes.append(f"updated '{name}'")
            # else: unchanged — silent
        else:
            # No 'cached' flag — this node was rebuilt (cache miss)
            changes.append(f"rebuilt '{name}'")

    for name in prev_names:
        if name not in curr_names:
            changes.append(f"removed '{name}'")

    return changes


def _build_report(
    node_statuses: dict,
    show_statuses: dict,
    version: int,
    t_interpret: float,
    t_total: float,
    cache_stats: dict,
    renderer,
    *,
    verbose: bool = True,
    prev_node_statuses: Optional[dict] = None,
) -> str:
    """Build the human-readable pipeline build report.

    Args:
        node_statuses: Per-node status dicts from interpret_build.
        show_statuses: Per-show-directive status dicts from _apply_to_renderer.
        version: Version number saved for this build.
        t_interpret: Time spent in the compute phase (seconds).
        t_total: Total build time including render phase (seconds).
        cache_stats: Dict with keys hits, misses, evictions.
        renderer: Renderer instance (used for camera state in verbose mode).
        verbose: If True, emit the full per-node listing.  If False and there
            are no errors/warnings, emit a short terse summary.
        prev_node_statuses: Node statuses from the previous successful build,
            used to compute the "Changes:" diff line in terse mode.
    """
    has_errors = any(s.get("status") == "error" for s in node_statuses.values())
    has_warnings = any(s.get("status") == "warning" for s in node_statuses.values())
    has_show_errors = any(s.get("status") == "error" for s in show_statuses.values())
    n_nodes = len(node_statuses)
    hits = cache_stats.get("hits", 0)
    misses = cache_stats.get("misses", 0)

    # ------------------------------------------------------------------
    # Terse path: no errors/warnings, caller didn't request verbose
    # ------------------------------------------------------------------
    if not verbose and not has_errors and not has_show_errors and not has_warnings:
        if has_errors or has_show_errors:
            header = f"Pipeline v{version} — ERRORS"
        elif has_warnings:
            header = f"Pipeline v{version} — warnings"
        else:
            header = f"Pipeline v{version} ok. {n_nodes} node{'s' if n_nodes != 1 else ''}."

        changes = _diff_node_statuses(node_statuses, prev_node_statuses)
        if changes:
            header += f" Changes: {', '.join(changes)}."
        else:
            header += " No changes."

        header += f" Cache: {hits} hits, {misses} misses. Took {t_total * 1000:.0f} ms."
        return header

    # ------------------------------------------------------------------
    # Verbose path (first build, errors, warnings, or explicit request)
    # ------------------------------------------------------------------
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
        st = status.get("status")
        if st == "error":
            report_lines.append(f"  {name}: ERROR - {status.get('message', status.get('error', ''))}")
        elif st == "skipped":
            upstream_id = status.get("upstream", "?")
            upstream_name = node_statuses.get(upstream_id, {}).get("name", f"node_{upstream_id}")
            report_lines.append(f"  {name}: skipped (upstream: {upstream_name})")
        else:
            line = f"  {name}: {status.get('class', '?')}"
            num_pts = status.get("num_points")
            num_cells = status.get("num_cells")
            if num_pts is not None or num_cells is not None:
                pts_str = f"{num_pts}" if num_pts is not None else "?"
                cells_str = f"{num_cells}" if num_cells is not None else "?"
                line += f" -> {pts_str} pts, {cells_str} cells"
            if st == "warning":
                line += f" WARNING: {status.get('message', '')}"
            if "point_arrays" in status:
                line += f"\n    arrays: {status['point_arrays']}"
            report_lines.append(line)

    if show_statuses:
        report_lines.append("")
        report_lines.append("Show directives:")
        for name, status in show_statuses.items():
            if status.get("status") == "error":
                report_lines.append(f"  {name}: ERROR - {status.get('message', status.get('error', ''))}")
            else:
                report_lines.append(f"  {name}: ok")

    report_lines.append("")
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
