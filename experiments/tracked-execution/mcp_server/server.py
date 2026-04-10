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

def _get_view(name: str) -> ViewState | None:
    """Return the ViewState for *name*, or None if it doesn't exist."""
    return _views.get(name)


def _resolve_view_name(pipeline_file: str) -> str:
    """Derive the view name from a pipeline file path (basename without extension).

    Examples:
        "view-main.py"     -> "view-main"
        "path/to/fire.py"  -> "fire"
    """
    return os.path.splitext(os.path.basename(pipeline_file))[0]


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
    global _views

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
        tmp = tempfile.mktemp(suffix=".png")
        try:
            vs.plotter.screenshot(tmp)
            with open(tmp, "rb") as f:
                img_data = f.read()
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

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
