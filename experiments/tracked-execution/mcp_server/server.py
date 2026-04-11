"""MCP server for PyVista pipeline visualization with content-addressed caching."""

import collections
import os
import queue
import sys
import tempfile
import threading
import time
from typing import TYPE_CHECKING

import pyvista as pv
from mcp.server.fastmcp import FastMCP, Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))  # allow running directly
from tracked_execution import DAG, execute_pipeline, SceneReconciler
from tracked_execution.dispatch import _dag_call, stable_hash
from tracked_execution.executor import tracked_read
from tracked_execution.proxy import TrackedProxy
from tracked_execution.watcher import watch_and_reload

if TYPE_CHECKING:
    from mcp_server.trame_viewer import TrameViewer

INSTRUCTIONS = """
Tracked Execution Visualization Server

This server lets you build scientific visualizations by writing PyVista
pipeline files. The server watches your files and automatically re-executes
them when they change, with content-addressed caching so only the parts
that changed re-run.

WORKFLOW:
1. Call set_working_directory(path) to set where data and pipeline files live
2. Write a pipeline file, then call create_view(pipeline_file) to start it
   - Returns data description (fields, dimensions, bounds) + pipeline output
   - The view name is the basename without extension (e.g., "fire.py" -> "fire")
   - The server watches the file; edits are re-executed automatically
3. Use inspect(pipeline_file, code) to query data without modifying the pipeline
4. Use screenshot(pipeline_file) to capture the current render
5. Edit the pipeline file to refine; the watcher re-executes with caching
6. Use list_views() / close_view(pipeline_file) to manage views
   - list_views() shows watcher status, pipeline variables, and last error per view

WRITING PIPELINE CODE:
Pipeline files are Python scripts with these available names:
- read(path) — load a data file (VTK, VTS, VTI, etc.)
- show(mesh, ...) — display a mesh
- np — numpy (tracked for caching)
- vtk_escape(proxy, func) — escape to raw VTK for custom filters
- pv — pyvista module (for use inside vtk_escape functions)
- print() — output captured and returned

IMPORTANT RULES:
- Always specify scalars= in threshold(), contour(), etc.
  Omitting it uses hidden state that breaks caching.
- Objects are cached — don't try to mutate them in place.
- Use vtk_escape() for operations not in the whitelist.

CACHING:
- Same code = instant (fully cached)
- Changing a threshold value: only re-runs from that point down
- Changing colormap or opacity: essentially free (mesh stays cached)
- Reading a file: cached by filename + modification time

COMMON COLORMAPS:
viridis, plasma, inferno, magma, coolwarm, bone, copper, jet

EXAMPLE PIPELINE FILE:
```python
mesh = read("output.30000.vts")
fire = mesh.threshold(value=400, scalars="theta")
surface = fire.extract_surface()
show(surface, colormap="inferno", scalar_bar_args={"title": "Temperature"})
```
"""

mcp = FastMCP("tracked-execution", instructions=INSTRUCTIONS)

_working_directory: str | None = None
_views: dict = {}  # view_name → ViewState
_trame_viewer: "TrameViewer | None" = None  # set by run.py in --trame mode

_SHARED_CACHE_MAX_ENTRIES = 10


class _LRUCache:
    """Thread-safe LRU cache with a fixed max entry count."""

    def __init__(self, maxsize=10):
        self._data = collections.OrderedDict()
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def get(self, key):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            return None

    def put(self, key, value):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = value
            else:
                self._data[key] = value
                while len(self._data) > self._maxsize:
                    self._data.popitem(last=False)

    def clear(self):
        with self._lock:
            self._data.clear()

    def __len__(self):
        with self._lock:
            return len(self._data)

    def __iter__(self):
        with self._lock:
            return iter(list(self._data))

    def keys(self):
        with self._lock:
            return list(self._data.keys())


# Avoids re-loading large files when multiple views read the same path.
_shared_read_cache = _LRUCache(maxsize=_SHARED_CACHE_MAX_ENTRIES)

# VTK's OpenGL context is not thread-safe. In interactive mode, tool handlers run on a
# background thread, so VTK calls must be marshaled to the main thread via run_on_main_thread().
_offscreen: bool = True  # set by run.py
_work_queue: queue.Queue = queue.Queue()
_main_thread_id: int | None = None


def run_on_main_thread(fn):
    """Run fn on the main thread, blocking until done. In offscreen mode, run directly."""
    if _offscreen or _main_thread_id is None or threading.get_ident() == _main_thread_id:
        return fn()
    result_q: queue.Queue = queue.Queue()
    _work_queue.put((fn, result_q))
    ok, result = result_q.get()
    if ok:
        return result
    raise result


def run_event_loop():
    """Drain the work queue and pump VTK events at ~60 fps. Blocks forever."""
    global _main_thread_id
    _main_thread_id = threading.get_ident()
    while True:
        while not _work_queue.empty():
            try:
                fn, result_q = _work_queue.get_nowait()
                try:
                    result = fn()
                    result_q.put((True, result))
                except Exception as e:
                    result_q.put((False, e))
            except queue.Empty:
                break
        for vs in list(_views.values()):
            try:
                iren = vs.plotter.iren
                if iren is not None:
                    iren.process_events()
            except Exception:
                pass
        time.sleep(0.016)


def _show_or_render(plotter):
    """Initialize the render window and render.

    In interactive mode, uses show(interactive_update=True) to create
    the window and clear the _first_time flag so screenshots work.
    In offscreen mode, just calls render().
    """
    if _offscreen:
        plotter.render()
    else:
        plotter.show(interactive_update=True)


class ViewState:
    """Mutable state for one pipeline view (DAG, plotter, reconciler, watcher, last result)."""

    def __init__(self, pipeline_file: str, dag, plotter, reconciler):
        self.pipeline_file = pipeline_file
        self.dag = dag
        self.plotter = plotter
        self.reconciler = reconciler
        self.watcher = None
        self.last_result = None
        self.last_error = None
        self.last_change_summary = None
        self.reload_count = 0
        self.last_run_mtime: float = 0.0
        self.lock = threading.Lock()
        self.run_complete = threading.Condition(self.lock)


def _resolve_view_name(pipeline_file: str) -> str:
    """Derive the view name from a pipeline file path (basename without extension)."""
    return os.path.splitext(os.path.basename(pipeline_file))[0]


def _shared_tracked_read(path: str, dag: DAG) -> TrackedProxy:
    """Like tracked_read but checks a shared cross-view cache first.

    Large files are expensive to load. When two views read the same file at the
    same mtime, the shared cache returns the loaded mesh without hitting disk again.
    The mesh is also recorded in the view's own DAG for correct per-view eviction.
    """
    abs_path = os.path.abspath(path)
    mtime = os.path.getmtime(abs_path)
    cache_key = f"{abs_path}:{mtime}"
    read_hash = stable_hash(("tracked_read", abs_path, mtime))

    def _load():
        cached = _shared_read_cache.get(cache_key)
        if cached is not None:
            return cached
        mesh = pv.read(abs_path)
        _shared_read_cache.put(cache_key, mesh)
        return mesh

    return _dag_call(dag, read_hash, _load)


def _describe_mesh(mesh, filename: str = "") -> str:
    """Format a human-readable mesh description: type, points, cells, bounds, fields."""
    lines = []
    if filename:
        lines.append(f"Data: {filename}")
    lines.append(f"Type: {type(mesh).__name__}")
    lines.append(f"Points: {mesh.n_points:,}")
    lines.append(f"Cells: {mesh.n_cells:,}")
    if hasattr(mesh, 'dimensions'):
        lines.append(f"Dimensions: {mesh.dimensions}")
    lines.append(f"Bounds: {mesh.bounds}")
    if mesh.array_names:
        lines.append(f"Fields ({len(mesh.array_names)}):")
        for name in mesh.array_names:
            arr = mesh[name]
            lines.append(f"  {name}: {arr.dtype}, range=[{arr.min():.4g}, {arr.max():.4g}]")
    return "\n".join(lines)


@mcp.tool()
def set_working_directory(path: str) -> str:
    """Set the working directory for all file operations.

    Must be called before creating any views. Cannot be changed after
    the first view is created.

    Args:
        path: Absolute path to the working directory.
    """
    global _working_directory

    if _views:
        return "Error: cannot change working directory after views have been created."

    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return f"Error: directory does not exist: {path}"

    _working_directory = path
    os.chdir(path)

    # List data files
    files = []
    for f in sorted(os.listdir(path)):
        if any(f.endswith(ext) for ext in ('.vts', '.vti', '.vtk', '.vtp', '.vtu', '.nhdr', '.nrrd')):
            size = os.path.getsize(os.path.join(path, f))
            files.append(f"{f} ({size / 1024 / 1024:.1f} MB)")

    result = f"Working directory set to: {path}\n"
    if files:
        result += "Data files found:\n" + "\n".join(f"  - {f}" for f in files)
    else:
        result += "No data files found in this directory."

    return result


@mcp.tool()
def create_view(pipeline_file: str) -> str:
    """Create a visualization view watching a pipeline file.

    The pipeline file should contain PyVista code using the tracked execution
    namespace (read, show, np, vtk_escape, etc.).

    The view name is derived from the filename (e.g., "view-main.py" -> "view-main").

    Args:
        pipeline_file: Path to the pipeline Python file (relative to working dir,
                       or absolute).
    """
    if _working_directory is None:
        return "Error: call set_working_directory first."

    if os.path.isabs(pipeline_file):
        full_path = pipeline_file
    else:
        full_path = os.path.join(_working_directory, pipeline_file)

    if not os.path.exists(full_path):
        return f"Error: file not found: {full_path}"

    view_name = _resolve_view_name(pipeline_file)

    if view_name in _views:
        return (
            f"Error: view '{view_name}' already exists. "
            "Close it first or use a different filename."
        )

    plotter = run_on_main_thread(lambda: pv.Plotter(off_screen=_offscreen))
    dag = DAG()
    reconciler = SceneReconciler(plotter=plotter)

    result = None
    last_error = None
    try:
        result = execute_pipeline(full_path, dag, read_fn=_shared_tracked_read)
        def _reconcile_and_render():
            reconciler.reconcile(result.actors)
            _show_or_render(plotter)
        run_on_main_thread(_reconcile_and_render)
    except SyntaxError as exc:
        # Don't create the view for unparseable files.
        return (
            f"Error: syntax error in pipeline file — view not created.\n"
            f"{type(exc).__name__}: {exc}"
        )
    except Exception as exc:
        # Runtime errors — still create the view; watcher will re-execute when the file is fixed.
        last_error = f"{type(exc).__name__}: {exc}"

    vs = ViewState(
        pipeline_file=full_path,
        dag=dag,
        plotter=plotter,
        reconciler=reconciler,
    )
    vs.last_result = result
    vs.last_error = last_error
    vs.last_run_mtime = os.path.getmtime(full_path)
    vs.watcher = _start_watcher(full_path, dag, reconciler, vs, view_name=view_name, read_fn=_shared_tracked_read)

    _views[view_name] = vs

    if _trame_viewer is not None:
        _trame_viewer.add_view(view_name, plotter)

    lines = [f"View '{view_name}' created watching {pipeline_file}"]

    # Describe the first mesh so the agent knows fields/dims/bounds immediately.
    for var_name, content_hash in dag.names.items():
        if content_hash in dag.cache:
            obj = dag.cache[content_hash]
            if hasattr(obj, 'n_points') and hasattr(obj, 'array_names'):
                lines.append("")
                lines.append(_describe_mesh(obj, var_name))
                break

    if result is not None:
        stats = result.stats
        total_time = sum(dag.timings.get(h, 0.0) for h in dag.current_run)
        lines.append(
            f"\nCache stats: hits={stats.get('hits', 0)}, misses={stats.get('misses', 0)}"
        )
        lines.append(f"Execution time: {total_time:.3f}s")
        if result.names:
            lines.append(f"Pipeline variables: {', '.join(result.names)}")
        if result.output:
            lines.append(f"Pipeline output:\n{result.output}")
    if last_error is not None:
        lines.append(
            f"Pipeline error (view created — fix the file and it will re-execute):\n"
            f"{last_error}"
        )

    return "\n".join(lines)


@mcp.tool()
def inspect(pipeline_file: str, code: str) -> str:
    """Run a read-only inspection snippet against a view's cached data.

    The code has access to all pipeline variables from the last execution
    (meshes, arrays, etc.) and numpy as ``np``. It cannot modify the
    pipeline or trigger rendering.

    Use this for data exploration: checking field ranges, computing
    statistics, counting points in filtered regions, etc.

    Args:
        pipeline_file: The pipeline file name (identifies the view).
        code: Python code to execute. Use print() for output.

    Returns:
        The captured print output from the code, plus any errors.
    """
    view_name = _resolve_view_name(pipeline_file)
    vs = _views.get(view_name)
    if vs is None:
        return (
            f"Error: no view '{view_name}'. "
            f"Call create_view('{pipeline_file}') first."
        )

    with vs.lock:
        try:
            from tracked_execution import inspect_pipeline
            result = inspect_pipeline(code, vs.dag)
            response = result.output
            if not response.strip():
                response = "(no output — use print() to see results)"
            return response
        except Exception as e:
            return f"Error in inspection code:\n{type(e).__name__}: {e}"


@mcp.tool()
def list_views() -> str:
    """List all active visualization views with full status for each.

    For each view, returns: name, pipeline file, cache stats, watcher alive
    status, pipeline variable names, and last error (if any).
    """
    if not _views:
        return "No views. Call create_view(pipeline_file) to create one."

    lines = ["Active views:"]
    for name, vs in _views.items():
        with vs.lock:
            pipeline_basename = os.path.basename(vs.pipeline_file)
            watcher_alive = vs.watcher is not None and vs.watcher.is_alive()
            stats = vs.last_result.stats if vs.last_result is not None else {}
            hits = stats.get("hits", 0)
            misses = stats.get("misses", 0)
            evictions = stats.get("evictions", 0)
            total_time = sum(
                vs.dag.timings.get(h, 0.0) for h in vs.dag.current_run
            )
            miss_word = "miss" if misses == 1 else "misses"
            hit_word = "hit" if hits == 1 else "hits"
            if vs.last_error:
                error_info = f"Last error: {vs.last_error}"
            else:
                error_info = "No errors."
            var_names = vs.last_result.names if vs.last_result is not None else []
            change_summary = vs.last_change_summary
        lines.append(
            f"  {name} ({pipeline_basename})"
        )
        lines.append(
            f"    Cache: {hits} {hit_word}, {misses} {miss_word}, {evictions} evictions"
        )
        lines.append(f"    Compute time: {total_time:.3f}s")
        lines.append(f"    Watcher running: {watcher_alive}")
        if var_names:
            lines.append(f"    Pipeline variables: {', '.join(var_names)}")
        lines.append(f"    {error_info}")
        if change_summary:
            lines.append(f"    Last change: {change_summary}")

    return "\n".join(lines)


@mcp.tool()
def close_view(pipeline_file: str) -> str:
    """Close a visualization view and free its resources.

    Stops the file watcher, closes the plotter, and removes the view
    from the active views list.

    Args:
        pipeline_file: The pipeline file name identifying the view.
    """
    view_name = _resolve_view_name(pipeline_file)
    vs = _views.get(view_name)
    if vs is None:
        return (
            f"Error: no view '{view_name}'. "
            f"Use list_views() to see active views."
        )

    if vs.watcher is not None:
        try:
            vs.watcher.stop()
            vs.watcher.join(timeout=2)
        except Exception:
            pass

    if _trame_viewer is not None:
        try:
            _trame_viewer.remove_view(view_name)
        except Exception:
            pass

    try:
        run_on_main_thread(vs.plotter.close)
    except Exception:
        pass

    del _views[view_name]
    return f"View '{view_name}' closed."


def _take_screenshot(vs) -> bytes:
    """Capture a PNG screenshot from *vs*'s plotter.  Caller must hold vs.lock."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        def _render_and_screenshot():
            vs.plotter.render()
            vs.plotter.screenshot(tmp_path)
        run_on_main_thread(_render_and_screenshot)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@mcp.tool()
def screenshot(pipeline_file: str) -> Image:
    """Capture a screenshot of a view's current render.

    Args:
        pipeline_file: The pipeline file name (identifies the view).

    Returns:
        PNG image of the current render, or an error string if the view
        does not exist.
    """
    view_name = _resolve_view_name(pipeline_file)
    vs = _views.get(view_name)
    if vs is None:
        return (
            f"Error: no view '{view_name}'. "
            f"Call create_view('{pipeline_file}') first."
        )

    with vs.lock:
        return Image(data=_take_screenshot(vs), format="png")


_RUN_PIPELINE_TIMEOUT = 60  # seconds


@mcp.tool()
def run_pipeline(pipeline_file: str) -> list:
    """Wait for the pipeline to execute the current file contents, then return
    status and a screenshot.

    Call this after writing or editing the pipeline file.  It blocks until
    the file watcher picks up the change and finishes executing (success or
    error), then returns the execution status text followed by a screenshot.

    If the watcher has already executed the current version of the file
    (same mtime), it returns immediately.

    Args:
        pipeline_file: The pipeline file name (identifies the view).

    Returns:
        A list containing a text status message and a PNG screenshot image.
    """
    view_name = _resolve_view_name(pipeline_file)
    vs = _views.get(view_name)
    if vs is None:
        return (
            f"Error: no view '{view_name}'. "
            f"Call create_view('{pipeline_file}') first."
        )

    current_mtime = os.path.getmtime(vs.pipeline_file)

    with vs.run_complete:
        # Wait until the watcher has processed a version at least as new as
        # what's on disk right now.
        while vs.last_run_mtime < current_mtime:
            if not vs.run_complete.wait(timeout=_RUN_PIPELINE_TIMEOUT):
                return "Error: timed out waiting for pipeline execution."

        # Build status text.
        lines = []
        if vs.last_error:
            lines.append(f"Pipeline error:\n{vs.last_error}")
        else:
            lines.append("Pipeline executed successfully.")

        if vs.last_result is not None:
            stats = vs.last_result.stats
            lines.append(
                f"Cache: hits={stats.get('hits', 0)}, "
                f"misses={stats.get('misses', 0)}"
            )
            if vs.last_result.names:
                lines.append(f"Variables: {', '.join(vs.last_result.names)}")
            if vs.last_result.output:
                lines.append(f"Output:\n{vs.last_result.output}")

        status_text = "\n".join(lines)

        # Take a screenshot while we still hold the lock.
        try:
            img_data = _take_screenshot(vs)
            return [status_text, Image(data=img_data, format="png")]
        except Exception as exc:
            return f"{status_text}\n\nScreenshot failed: {exc}"


def _start_watcher(full_path, dag, reconciler, vs, view_name=None, read_fn=None):
    """Start a file watcher that reconciles the scene on every successful reload.

    The callback runs on a background thread — do NOT call plotter.render() here.
    Rendering happens only when screenshot() is called from the main thread.
    """
    def on_reload(reload_result):
        with vs.run_complete:
            try:
                # reconcile() and render touch the plotter (VTK OpenGL) —
                # must run on the main thread.
                def _reconcile_and_render():
                    reconciler.reconcile(reload_result.actors)
                    _show_or_render(vs.plotter)
                run_on_main_thread(_reconcile_and_render)

                # Build change summary comparing previous and new results.
                prev_result = vs.last_result
                if prev_result is not None:
                    prev_names = set(prev_result.names)
                    new_names = set(reload_result.names)
                    added = new_names - prev_names
                    removed = prev_names - new_names

                    stats = reload_result.stats
                    hits = stats.get("hits", 0)
                    misses = stats.get("misses", 0)
                    parts = [f"{hits} cached, {misses} recomputed"]
                    if added:
                        parts.append(f"new variables: {', '.join(sorted(added))}")
                    if removed:
                        parts.append(f"removed variables: {', '.join(sorted(removed))}")
                    if reload_result.output:
                        parts.append(f"output: {reload_result.output.strip()}")
                    vs.last_change_summary = " | ".join(parts)
                else:
                    vs.last_change_summary = "Initial execution"

                vs.last_result = reload_result
                vs.last_error = None
                vs.reload_count += 1
                if _trame_viewer is not None and view_name is not None:
                    _trame_viewer.update_view(view_name)
            except Exception as exc:
                vs.last_error = f"{type(exc).__name__}: {exc}"
                vs.reload_count += 1
            vs.last_run_mtime = os.path.getmtime(vs.pipeline_file)
            vs.run_complete.notify_all()

    def on_error(exc):
        with vs.run_complete:
            vs.last_error = f"{type(exc).__name__}: {exc}"
            vs.last_change_summary = f"Pipeline error: {type(exc).__name__}: {exc}"
            vs.reload_count += 1
            vs.last_run_mtime = os.path.getmtime(vs.pipeline_file)
            vs.run_complete.notify_all()

    return watch_and_reload(
        file_path=full_path,
        dag=dag,
        reconciler=None,  # reconcile handled in on_reload above
        callback=on_reload,
        error_callback=on_error,
        read_fn=read_fn,
    )
