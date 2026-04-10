"""Tracked Execution MCP Server.

A visualization server that watches PyVista pipeline files and provides
content-addressed caching for fast iterative refinement.
"""
from mcp.server.fastmcp import FastMCP, Image
import os
import sys
import tempfile
import threading

import pyvista as pv

# Add tracked_execution to sys.path so imports work when running this file directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from tracked_execution import DAG, execute_pipeline, SceneReconciler
from tracked_execution.executor import tracked_read
from tracked_execution.dispatch import stable_hash
from tracked_execution.proxy import TrackedProxy
from tracked_execution.watcher import watch_and_reload

INSTRUCTIONS = """
Tracked Execution Visualization Server

This server lets you build scientific visualizations by writing PyVista
pipeline files. The server watches your files and automatically re-executes
them when they change, with content-addressed caching so only the parts
that changed re-run.

WORKFLOW:
1. Call set_working_directory(path) to set where data and pipeline files live
2. Call create_view(pipeline_file) to create a visualization view
   - This starts watching the file for changes
   - Write PyVista code to the file; the server executes it automatically
3. Use inspect(pipeline_file, code) to query data without modifying the pipeline
4. Use screenshot(pipeline_file) to capture the current render
5. Use pipeline_status(pipeline_file) to check whether your latest file edits
   were picked up and executed successfully by the watcher
6. Use list_views() to see all active views and their cache stats
7. Use close_view(pipeline_file) to free resources when done with a view

WRITING PIPELINE CODE:
Pipeline files are Python scripts with these available names:
- read(path) — load a data file (VTK, VTS, VTI, etc.)
- show(mesh, ...) / add_mesh(mesh, ...) — display a mesh
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
- Changing colormap/opacity: essentially free (mesh is cached)
- Reading a file: cached by filename + modification time

EXAMPLE PIPELINE FILE:
```python
mesh = read("output.30000.vts")
fire = mesh.threshold(value=400, scalars="theta")
surface = fire.extract_surface()
show(surface, colormap="inferno", scalar_bar_args={"title": "Temperature"})
```
"""

mcp = FastMCP("tracked-execution", instructions=INSTRUCTIONS)

# --- Server state ---
_working_directory: str | None = None
_views: dict = {}  # view_name -> ViewState

# Shared read cache: abs_path:mtime → real mesh object.
# Avoids re-loading large files when multiple views read the same path.
# Protected by _shared_read_cache_lock for thread safety.
_shared_read_cache: dict[str, object] = {}
_shared_read_cache_lock = threading.Lock()


class ViewState:
    """State for a single pipeline view."""

    def __init__(self, pipeline_file: str, dag, plotter, reconciler):
        self.pipeline_file = pipeline_file
        self.dag = dag
        self.plotter = plotter
        self.reconciler = reconciler
        self.watcher = None
        self.last_result = None
        self.last_error = None
        self.lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_view(name: str) -> "ViewState | None":
    """Return the ViewState for *name*, or None if it doesn't exist."""
    return _views.get(name)


def _resolve_view_name(pipeline_file: str) -> str:
    """Derive the view name from a pipeline file path (basename without extension).

    Examples:
        "view-main.py"     -> "view-main"
        "path/to/fire.py"  -> "fire"
    """
    return os.path.splitext(os.path.basename(pipeline_file))[0]


def _shared_tracked_read(path: str, dag: DAG) -> TrackedProxy:
    """Like tracked_read but checks a shared cross-view cache first.

    Large files (e.g. 1.1 GB .vts) are expensive to load.  When two views
    both read the same file at the same mtime, the shared cache returns the
    already-loaded mesh without hitting disk again.

    The mesh is also recorded in the view's own DAG cache so that per-view
    GC (dag.end_run) works correctly — the DAG may evict its reference, but
    the mesh stays alive in _shared_read_cache.

    Thread safety: _shared_read_cache_lock protects the shared dict.  Each
    view's DAG cache is only accessed while holding vs.lock (the caller's
    responsibility).
    """
    abs_path = os.path.abspath(path)
    mtime = os.path.getmtime(abs_path)
    cache_key = f"{abs_path}:{mtime}"
    read_hash = stable_hash(("tracked_read", abs_path, mtime))

    with _shared_read_cache_lock:
        if cache_key in _shared_read_cache:
            mesh = _shared_read_cache[cache_key]
            # Register in the view's DAG so GC tracking and proxy wrapping work.
            dag.cache[read_hash] = mesh
            dag.current_run.add(read_hash)
            dag.hits += 1
            return TrackedProxy(mesh, read_hash, dag)

    # Not in shared cache — do the real read, then store in both caches.
    result = tracked_read(path, dag)
    real_mesh = object.__getattribute__(result, "_real")
    with _shared_read_cache_lock:
        _shared_read_cache[cache_key] = real_mesh
    return result


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

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

    # Resolve path — support absolute paths as well as relative ones.
    if os.path.isabs(pipeline_file):
        full_path = pipeline_file
    else:
        full_path = os.path.join(_working_directory, pipeline_file)

    if not os.path.exists(full_path):
        return f"Error: file not found: {full_path}"

    # View name from filename (basename without extension).
    view_name = _resolve_view_name(pipeline_file)

    if view_name in _views:
        return (
            f"Error: view '{view_name}' already exists. "
            "Close it first or use a different filename."
        )

    # Create components.
    plotter = pv.Plotter(off_screen=True)
    dag = DAG()
    reconciler = SceneReconciler(plotter=plotter)

    # Execute initial pipeline.
    result = None
    last_error = None
    try:
        result = execute_pipeline(full_path, dag)
        reconciler.reconcile(result.actors)
        plotter.render()
    except SyntaxError as exc:
        # Syntax errors mean the file is unparseable — don't create the view.
        return (
            f"Error: syntax error in pipeline file — view not created.\n"
            f"{type(exc).__name__}: {exc}"
        )
    except Exception as exc:
        # Runtime errors — still create the view so the watcher can re-execute
        # when the file is fixed.
        last_error = f"{type(exc).__name__}: {exc}"

    # Create view state and start file watcher.
    vs = ViewState(
        pipeline_file=full_path,
        dag=dag,
        plotter=plotter,
        reconciler=reconciler,
    )
    vs.last_result = result
    vs.last_error = last_error
    vs.watcher = _start_watcher(full_path, dag, reconciler, vs)

    _views[view_name] = vs

    # Build response.
    lines = [f"View '{view_name}' created watching {pipeline_file}"]
    if result is not None:
        stats = result.stats
        lines.append(
            f"Cache stats: hits={stats.get('hits', 0)}, misses={stats.get('misses', 0)}"
        )
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
    vs = _get_view(view_name)
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
    """List all active visualization views.

    Returns the name, pipeline file, cache stats, and any errors
    for each view.
    """
    if not _views:
        return "No views. Call create_view(pipeline_file) to create one."

    lines = ["Active views:"]
    for name, vs in _views.items():
        with vs.lock:
            pipeline_basename = os.path.basename(vs.pipeline_file)
            stats = vs.last_result.stats if vs.last_result is not None else {}
            hits = stats.get("hits", 0)
            misses = stats.get("misses", 0)
            miss_word = "miss" if misses == 1 else "misses"
            hit_word = "hit" if hits == 1 else "hits"
            if vs.last_error:
                error_info = f"error: {vs.last_error}"
            else:
                error_info = "no errors"
        lines.append(
            f"  {name} ({pipeline_basename}) \u2014 {hits} {hit_word}, {misses} {miss_word}, {error_info}"
        )

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
    vs = _get_view(view_name)
    if vs is None:
        return (
            f"Error: no view '{view_name}'. "
            f"Use list_views() to see active views."
        )

    # Stop the file watcher.
    if vs.watcher is not None:
        try:
            vs.watcher.stop()
            vs.watcher.join(timeout=2)
        except Exception:
            pass

    # Close the plotter.
    try:
        vs.plotter.close()
    except Exception:
        pass

    del _views[view_name]
    return f"View '{view_name}' closed."


@mcp.tool()
def pipeline_status(pipeline_file: str) -> str:
    """Get the current status of a view's pipeline.

    Returns cache stats, last execution result, any errors from the
    most recent file-watch reload, and the list of pipeline variables.

    Use this to check whether your latest file edits were picked up by
    the watcher and executed successfully.

    Args:
        pipeline_file: The pipeline file name identifying the view.
    """
    view_name = _resolve_view_name(pipeline_file)
    vs = _get_view(view_name)
    if vs is None:
        return (
            f"Error: no view '{view_name}'. "
            f"Call create_view('{pipeline_file}') first."
        )

    with vs.lock:
        lines = [f"Pipeline status for '{view_name}':"]

        # Watcher state.
        watcher_alive = vs.watcher is not None and vs.watcher.is_alive()
        lines.append(f"  Watcher running: {watcher_alive}")

        # Cache stats.
        if vs.last_result is not None:
            stats = vs.last_result.stats
            hits = stats.get("hits", 0)
            misses = stats.get("misses", 0)
            evictions = stats.get("evictions", 0)
            lines.append(
                f"  Cache stats: hits={hits}, misses={misses}, evictions={evictions}"
            )

            # Pipeline variables.
            if vs.last_result.names:
                lines.append(
                    f"  Pipeline variables: {', '.join(vs.last_result.names)}"
                )
            else:
                lines.append("  Pipeline variables: (none)")

            # Captured print output.
            if vs.last_result.output:
                lines.append(f"  Pipeline output:\n{vs.last_result.output}")
        else:
            lines.append("  No successful execution yet.")

        # Errors.
        if vs.last_error:
            lines.append(f"  Last error:\n{vs.last_error}")
        else:
            lines.append("  No errors.")

    return "\n".join(lines)


@mcp.tool()
def screenshot(pipeline_file: str) -> Image:
    """Capture a screenshot of a view's current render.

    Args:
        pipeline_file: The pipeline file name (identifies the view).

    Returns:
        PNG image of the current render.
    """
    view_name = _resolve_view_name(pipeline_file)
    vs = _get_view(view_name)
    if vs is None:
        raise ValueError(
            f"No view '{view_name}'. "
            f"Call create_view('{pipeline_file}') first."
        )

    with vs.lock:
        vs.plotter.render()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            vs.plotter.screenshot(tmp_path)
            with open(tmp_path, "rb") as f:
                img_data = f.read()
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        return Image(data=img_data, format="png")


# ---------------------------------------------------------------------------
# Internal: watcher helpers
# ---------------------------------------------------------------------------

def _start_watcher(full_path, dag, reconciler, vs):
    """Start a file watcher for *full_path* that reconciles on reload.

    Uses the tracked_execution watcher with a callback that handles
    reconciliation and stores results/errors on *vs*.

    THREADING NOTE: VTK's OpenGL context is not thread-safe. The watcher
    callback runs on a background thread. Do NOT call plotter.render() here.
    The reconciler updates actor state; actual rendering happens only when
    screenshot() is called from the main thread.

    Returns the started Observer.
    """
    def on_reload(reload_result):
        with vs.lock:
            try:
                reconciler.reconcile(reload_result.actors)
                # Do NOT call plotter.render() here — VTK OpenGL is not
                # thread-safe. render() is called in screenshot() instead.
                vs.last_result = reload_result
                vs.last_error = None
            except Exception as exc:
                vs.last_error = f"{type(exc).__name__}: {exc}"

    def on_error(exc):
        with vs.lock:
            vs.last_error = f"{type(exc).__name__}: {exc}"

    return watch_and_reload(
        file_path=full_path,
        dag=dag,
        reconciler=None,   # We handle reconcile ourselves in the callback.
        callback=on_reload,
        error_callback=on_error,
    )
