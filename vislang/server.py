"""MCP server for VisLang - declarative VTK visualization via conversation."""

import argparse
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from mcp.server.fastmcp import FastMCP, Image

from .renderer import Renderer
from .dsl import interpret
from . import queries

# Module-level logger (no file handler until main() runs — avoids side effects on import)
logger = logging.getLogger("vislang")


def _parse_args():
    parser = argparse.ArgumentParser(description="VisLang MCP server")
    parser.add_argument(
        "--offscreen",
        action="store_true",
        help="Use off-screen rendering (no interactive window)",
    )
    args, remaining = parser.parse_known_args()
    # Put unconsumed args back so FastMCP can use them
    sys.argv = [sys.argv[0]] + remaining
    return args


# _args and _renderer are None until main() initialises them.
# All tool functions access these as module globals and are only called
# after main() has run (either via the MCP server or tests that set them
# directly), so lazy initialisation is safe.
_args = None

# Whether the server is running in offscreen mode. Set in main() so that
# new_view() knows how to create additional Renderer instances.
_offscreen = False

# Initialize
mcp = FastMCP(
    "VisLang",
    instructions="""VisLang: Declarative VTK scientific visualization via conversation.

WORKFLOW:
1. Call list_data_files() to see what's available, then load("file.vts") to load it
2. load() auto-detects the reader and returns describe_data() output immediately
3. Write pipeline code to pipeline.py, then call set_pipeline()
3. State-changing tools (set_pipeline, set_camera, set_colormap, etc.)
   automatically return a screenshot — no separate screenshot() call needed
4. Edit the pipeline file to add layers incrementally
5. Use get_pipeline() to see current code if needed

Do NOT try to build a complex multi-layer pipeline in one shot. It will
likely fail due to wrong value ranges, bad seed positions, or field name
typos, and debugging is harder.

CRITICAL RULES:
- Always query field ranges with get_statistics() BEFORE choosing isosurface
  values, threshold ranges, or scalar_range for coloring
- Use get_ground_z() to find valid z-coordinates for seed placement in
  structured grids (terrain-following or curvilinear)
- Call get_examples() to see working pipeline patterns you can copy

VOLUME RENDERING:
- Use representation="Volume" in show() for volumetric rendering
- Call suggest_opacity() to get histogram-guided opacity transfer functions
- Use gradient_opacity=True for edge-enhanced volume rendering
- Threshold data first to focus on regions of interest

TROUBLESHOOTING:
- Empty output (0 points): check field ranges with get_statistics(), use suggest_isosurface()
- Wrong colors: check scalar_range, or just use color_by="fieldname" for auto defaults
- To color by one component of a vector: use component=0/1/2 or "x"/"y"/"z" in show()
- Volume looks empty: opacity too low, use suggest_opacity() or a preset like "fire"
- Volume too opaque: lower opacity parameter or adjust opacity_function control points
- Streamlines empty: seeds outside data, use seeds_near() or check get_ground_z()
- Slow pipeline: reduce volume_resolution, threshold before volume render
- Camera too far/close: use suggest_camera("overview") or set_camera(position=[x,y,z])

Call list_data_files() to see available datasets.

Available tools: load, set_pipeline, screenshot, camera_orbit, describe_data, get_array_info,
get_field_summary, get_node_info, get_bounds, get_statistics, query_stats, get_histogram,
get_spatial_extent, sample_points, sample_line, get_ground_z,
suggest_scalar_range, suggest_opacity, suggest_isosurface, suggest_camera, quick_start,
set_camera, set_opacity, set_colormap, set_background, set_window_size,
toggle_visibility, list_actors, get_actor_info, extract_component,
annotate, clear_annotations,
list_data_files, list_capabilities, list_versions, get_examples,
get_pipeline, restore_version, reset_pipeline, export_standalone""",
)

# ---------------------------------------------------------------------------
# Per-view state
# ---------------------------------------------------------------------------

class ViewContext:
    """Bundles all per-view state: pipeline objects, version history, renderer."""

    def __init__(self, name: str, renderer):
        self.name = name
        self.renderer = renderer
        self.vtk_objects: dict = {}
        self.current_code: str = ""
        self.version: int = 0
        self.versions: list = []
        self.annotations: dict = {}  # label -> vtkBillboardTextActor3D

    @property
    def history_dir(self) -> Path:
        """Per-view history directory under .vislang/history/<view_name>/."""
        return Path(f".vislang/history/{self.name}")

    @property
    def pipeline_file(self) -> str:
        """Per-view pipeline file name, e.g. 'view-main.py', 'view-closeup.py'."""
        return f"view-{self.name}.py"


# Global view registry — _renderer is None (and _views empty) until main() initialises them.
_renderer = None  # kept for backward compat with tests that set srv._renderer directly
_vtk_objects = {}  # kept for backward compat with tests that set srv._vtk_objects directly
_current_code = ""  # kept for backward compat
_annotations = {}  # kept for backward compat with tests that set srv._annotations directly

_views: dict = {}       # name -> ViewContext
_current_view: str = "main"

_history_dir = Path(".vislang/history")


def _current_ctx() -> "ViewContext":
    """Return the ViewContext for the currently active view.

    Falls back to a lightweight shim backed by the legacy module-level globals
    so that tests that poke srv._renderer / srv._vtk_objects / srv._annotations
    directly still work without a full main() initialisation.
    """
    if _views:
        return _views[_current_view]
    # Shim for legacy test setup — proxies module-level globals
    class _LegacyCtx:
        name = "main"
        @property
        def renderer(self_inner):
            return _renderer
        @renderer.setter
        def renderer(self_inner, v):
            pass
        @property
        def vtk_objects(self_inner):
            return _vtk_objects
        @vtk_objects.setter
        def vtk_objects(self_inner, v):
            global _vtk_objects
            _vtk_objects = v
        @property
        def current_code(self_inner):
            return _current_code
        @current_code.setter
        def current_code(self_inner, v):
            global _current_code
            _current_code = v
        @property
        def version(self_inner):
            return 0
        @version.setter
        def version(self_inner, v):
            pass
        @property
        def versions(self_inner):
            return []
        @property
        def annotations(self_inner):
            return _annotations
        @property
        def history_dir(self_inner):
            return _history_dir
        @property
        def pipeline_file(self_inner):
            return "view-main.py"
    return _LegacyCtx()


def _get_data(node_name=""):
    """Get VTK data output for a named node, or root source."""
    vtk_objects = _current_ctx().vtk_objects
    if node_name and node_name in vtk_objects:
        obj = vtk_objects[node_name]
        obj.Update()
        return obj.GetOutput()
    if node_name and node_name not in vtk_objects:
        return None  # caller handles this
    # Return the first source's output if no name given
    for name, obj in vtk_objects.items():
        obj.Update()
        return obj.GetOutput()
    return None


def _available_nodes_hint():
    vtk_objects = _current_ctx().vtk_objects
    if vtk_objects:
        return f"Available nodes: {sorted(vtk_objects.keys())}"
    return "No pipeline is active. Call set_pipeline() first to load data."


def _get_data_or_error(node: str = ""):
    """Look up VTK data for *node*, returning (data, None) or (None, error_str).

    Centralises the repeated lookup pattern used by query tools::

        data, err = _get_data_or_error(node)
        if err:
            return err
        # ... use data ...
    """
    data = _get_data(node)
    if data is None:
        if node:
            return None, f"Node '{node}' not found. {_available_nodes_hint()}"
        return None, _available_nodes_hint()
    return data, None


def _load_file_directly(file_path: str):
    """Load a VTK file directly, returning (data, error_message).

    Delegates to filters.load_file which detects the reader from the extension.
    Returns (vtk_data_object, None) on success, (None, error_str) on failure.
    """
    from .filters import load_file
    return load_file(file_path)


def _save_version(code, screenshot_path):
    """Save pipeline spec and screenshot to version history (current view)."""
    ctx = _current_ctx()
    ctx.version += 1
    ver_dir = ctx.history_dir / f"v{ctx.version:04d}"
    ver_dir.mkdir(parents=True, exist_ok=True)
    (ver_dir / "pipeline.py").write_text(code)
    if screenshot_path and os.path.exists(screenshot_path):
        import shutil
        shutil.copy2(screenshot_path, ver_dir / "screenshot.png")
    return ctx.version


def _auto_screenshot():
    """Capture and return an Image of the current scene for auto-screenshot.

    Uses run_on_main_thread to ensure correctness in both offscreen and
    interactive modes.
    """
    try:
        renderer = _current_ctx().renderer
        view_name = _current_ctx().name
        screenshot_path = f".vislang/latest_{view_name}.png"
        def _take():
            renderer.render()
            return renderer.screenshot(screenshot_path)
        path = renderer.run_on_main_thread(_take)
        return Image(path=path)
    except Exception:
        logger.debug("Auto-screenshot failed", exc_info=True)
        return None


def _with_screenshot(text_result):
    """Combine a text result with an auto-screenshot image.

    Returns a list of [text, image] if screenshot succeeds, or just the text string.
    State-changing tools use this to automatically return a screenshot.
    """
    img = _auto_screenshot()
    if img is not None:
        return [text_result, img]
    return text_result


@mcp.tool()
def load(filename: str) -> str:
    """Load a VTK data file and make it available for visualization.

    Auto-detects the appropriate reader from the file extension.
    Stores the data in the pipeline under the node name "data" so
    other tools can access it immediately. Returns a describe_data()
    overview of the loaded dataset.

    Supported extensions: .vts, .vti, .vtp, .vtu, .vtr

    Args:
        filename: Path to the VTK file to load (relative to the session directory).
    """
    if not os.path.exists(filename):
        return f"File not found: {filename}\n\nUse list_data_files() to see available files."

    from .filters import EXT_TO_READER, create_vtk_filter

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    reader_class = EXT_TO_READER.get(ext)
    if reader_class is None:
        supported = sorted(EXT_TO_READER.keys())
        return (
            f"Cannot load '{filename}': unsupported extension '.{ext}'. "
            f"Supported extensions: {supported}"
        )

    try:
        reader, _ = create_vtk_filter(reader_class, FileName=filename)
        reader.Update()
        data = reader.GetOutput()
    except Exception as e:
        logger.exception("load() failed for %s", filename)
        return f"Error loading '{filename}': {e}"

    if data is None or data.GetNumberOfPoints() == 0:
        return f"File '{filename}' loaded but contains no points. The file may be empty or corrupt."

    ctx = _current_ctx()
    ctx.vtk_objects = {"data": reader}
    # Keep legacy global in sync for tests that read srv._vtk_objects directly
    global _vtk_objects
    _vtk_objects = ctx.vtk_objects
    logger.info("load(): stored reader for '%s' as node 'data' (%d pts)", filename, data.GetNumberOfPoints())

    return describe_data(node="data")


@mcp.tool()
def set_pipeline(file: str = "") -> str:
    """Execute a VisLang DSL pipeline from a file. Clears the scene and rebuilds.

    Write your pipeline code to the file first, then call this tool.
    The code uses builder functions: source(), filter(), show(), camera(), background().
    Returns a status report with per-node output info.

    Args:
        file: Path to the pipeline .py file. Defaults to the current view's
              pipeline file (e.g. view-main.py, view-closeup.py).
    """
    if not file:
        file = _current_ctx().pipeline_file
    try:
        code = Path(file).read_text()
    except FileNotFoundError:
        return f"File not found: {file}\n\nWrite your pipeline code to this file first, then call set_pipeline()."
    except Exception as e:
        return f"Error reading {file}: {e}"
    renderer = _current_ctx().renderer
    result = renderer.run_on_main_thread(lambda: _set_pipeline_impl(code))
    return _with_screenshot(result)


def _set_pipeline_impl(code: str) -> str:
    ctx = _current_ctx()
    renderer = ctx.renderer

    logger.info("set_pipeline called (%d chars)", len(code))
    t0 = time.monotonic()
    try:
        vtk_objs, node_statuses, show_statuses, builder = interpret(code, renderer)
        t_interpret = time.monotonic() - t0
        logger.info("Pipeline interpreted in %.2fs: %d nodes, %d show directives",
                     t_interpret, len(vtk_objs), len(show_statuses))
        ctx.vtk_objects = vtk_objs
        ctx.current_code = code
        # Keep legacy globals in sync for tests that read srv._vtk_objects / srv._current_code
        global _vtk_objects, _current_code
        _vtk_objects = ctx.vtk_objects
        _current_code = ctx.current_code

        # Take screenshot (per-view path)
        t_ss = time.monotonic()
        screenshot_path = renderer.screenshot(f".vislang/latest_{ctx.name}.png")
        t_screenshot = time.monotonic() - t_ss
        version = _save_version(code, screenshot_path)

        # Build report
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
                line += f" -> {status['num_points']} pts, {status['num_cells']} cells"
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

        t_total = time.monotonic() - t0
        report_lines.append("")
        report_lines.append(f"Timing: pipeline {t_interpret:.2f}s, screenshot {t_screenshot:.2f}s, total {t_total:.2f}s")
        report_lines.append("")
        cam = renderer.get_camera_state()
        report_lines.append(
            f"Camera: position={[round(x,1) for x in cam['position']]}, "
            f"focal_point={[round(x,1) for x in cam['focal_point']]}"
        )

        # Add hints for common issues
        if has_warnings:
            empty_nodes = [
                s.get("name", f"node_{nid}")
                for nid, s in node_statuses.items()
                if s.get("warning") == "Filter produced empty output"
            ]
            if empty_nodes:
                report_lines.append("")
                report_lines.append(
                    f"Hint: Nodes {empty_nodes} produced empty output. "
                    "For streamlines, ensure seed points are inside the grid "
                    "(use get_ground_z to find valid z-coordinates). "
                    "For thresholds/contours, check the field's value range "
                    "with get_statistics."
                )

        # Suggest next steps
        hints = []
        if "camera(" not in code:
            hints.append("Use suggest_camera() for a good camera angle")
        if show_statuses and not any(n.endswith("_bar") for n in renderer._actors):
            hints.append("Add scalar_bar='label' to show() for a color legend")
        if hints:
            report_lines.append("")
            report_lines.append("Suggestions: " + ". ".join(hints) + ".")

        return "\n".join(report_lines)

    except SyntaxError as e:
        logger.warning("DSL syntax error: %s", e)
        return f"DSL syntax error: {e}\n\nCheck your pipeline code for syntax issues."
    except NameError as e:
        logger.warning("DSL name error: %s", e)
        msg = str(e)
        hint = ""
        if "is not defined" in msg:
            name = msg.split("'")[1] if "'" in msg else ""
            if name:
                hint = (f"\n\nHint: '{name}' is not a recognized DSL function or variable. "
                        "Did you forget to define it earlier in the pipeline? "
                        "Use list_capabilities() to see available functions.")
        return f"Pipeline error: {e}{hint}"
    except Exception as e:
        logger.exception("Pipeline error")
        tb = traceback.format_exc()
        tb_lines = tb.strip().split("\n")
        short_tb = "\n".join(tb_lines[-3:])
        return f"Pipeline error: {type(e).__name__}: {e}\n\n{short_tb}"


@mcp.tool()
def quick_start(filename: str) -> str:
    """Generate a starting pipeline for a data file.

    Returns DSL code you can paste into set_pipeline() to get a basic
    visualization quickly, which you can then modify.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    from .filters import EXT_TO_READER, create_vtk_filter
    reader_class = EXT_TO_READER.get(ext)
    if reader_class is None:
        return (f"Unknown file extension '.{ext}'. "
                f"Supported: {sorted(EXT_TO_READER.keys())}. "
                "For .raw files, use raw_source() in set_pipeline().")

    # Load the data to inspect it
    try:
        reader, status = create_vtk_filter(reader_class, FileName=filename)
        reader.Update()
        data = reader.GetOutput()
    except Exception as e:
        return f"Error loading '{filename}': {e}"

    pd = data.GetPointData()
    scalar_fields = []
    for i in range(pd.GetNumberOfArrays()):
        arr = pd.GetArray(i)
        if arr and arr.GetNumberOfComponents() == 1:
            scalar_fields.append(pd.GetArrayName(i))

    first_field = scalar_fields[0] if scalar_fields else None
    is_image = data.GetClassName() in ("vtkImageData", "vtkUniformGrid")

    lines = [f'data = source("{reader_class}", FileName="{filename}")']

    if is_image and first_field:
        # CT/image data: suggest volume rendering
        lines.append(f'# Volume rendering')
        lines.append(f'show(data, "volume", representation="Volume",')
        lines.append(f'    color_by="{first_field}", lut="grayscale")')
        lines.append(f'# Isosurface (adjust value based on data range)')
        arr = pd.GetArray(first_field)
        mid_val = (arr.GetRange()[0] + arr.GetRange()[1]) / 2
        lines.append(f'iso = contour(input=data, ContourBy="{first_field}",')
        lines.append(f'    Isosurfaces={round(mid_val, 2)})')
        lines.append(f'show(iso, "surface", color=(0.8, 0.8, 0.8), opacity=0.3)')
    elif first_field:
        arr = pd.GetArray(first_field)
        rng = arr.GetRange()
        lines.append(f'# Color by first scalar field (range: {rng[0]:.4g} to {rng[1]:.4g})')
        lines.append(f'show(data, "data", color_by="{first_field}", scalar_range=({rng[0]:.4g}, {rng[1]:.4g}))')

    lines.append(f'scene_preset("dark")')

    code = "\n".join(lines)
    return f"Suggested pipeline:\n\n```python\n{code}\n```\n\nPaste this into set_pipeline() to start."


@mcp.tool()
def extract_component(node: str, field: str, component: str, result_name: str = "") -> str:
    """Extract a single component from a vector field as a new scalar array.

    This modifies the named node's output in-place, adding a new scalar array.
    Useful for isolating X/Y/Z components of velocity, vorticity, etc.

    Args:
        node: Name of the pipeline variable holding the data.
        field: Name of the vector field (e.g., "velocity").
        component: Component index ("0","1","2") or name ("x","y","z").
        result_name: Name for the new scalar. Defaults to "{field}_{component}".
    """
    vtk_objects = _current_ctx().vtk_objects
    if node not in vtk_objects:
        available = sorted(vtk_objects.keys())
        return f"Node '{node}' not found. Available: {available}"

    # Parse component
    from .filters import COMPONENT_NAME_MAP, COMPONENT_INDEX_MAP
    comp = component.strip().lower()
    if comp in COMPONENT_NAME_MAP:
        comp_idx = COMPONENT_NAME_MAP[comp]
        comp_label = comp
    else:
        try:
            comp_idx = int(comp)
            comp_label = COMPONENT_INDEX_MAP.get(comp_idx, str(comp_idx))
        except ValueError:
            return f"Invalid component '{component}'. Use 0/1/2 or x/y/z."

    if not result_name:
        result_name = f"{field}_{comp_label}"

    try:
        from .filters import extract_component as _extract_comp
        _, status = _extract_comp(vtk_objects[node], field, comp_idx, result_name)
        return (
            f"Extracted component {comp_idx} of '{field}' as '{result_name}'.\n"
            f"Range: [{status['range'][0]:.6g}, {status['range'][1]:.6g}], "
            f"{status['num_tuples']} tuples.\n"
            f"You can now use '{result_name}' in color_by, threshold, etc."
        )
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def make_vector(node: str, cx: str, cy: str, cz: str, result: str = "velocity") -> str:
    """Assemble three named scalar arrays into a 3-component vector array.

    This is the general primitive for constructing vector fields from scalar
    components.  It creates the vector in-place on the node's output data, much
    like ``extract_component``.

    Use this tool after ``set_pipeline()`` has run — the named node must
    already exist in the pipeline output.  The vector array is added to the
    node's dataset and will be available for coloring, streamlines, etc. in
    subsequent operations.

    For use inside pipeline.py DSL code, call ``make_vector(...)`` directly.

    Args:
        node: Name of the pipeline variable holding the data (must be a
              key returned after the last ``set_pipeline`` call).
        cx: Name of the scalar array for the X component.
        cy: Name of the scalar array for the Y component.
        cz: Name of the scalar array for the Z component.
        result: Name for the resulting vector array (default "velocity").
    """
    vtk_objects = _current_ctx().vtk_objects
    if node not in vtk_objects:
        available = sorted(vtk_objects.keys())
        return f"Node '{node}' not found. Available: {available}"

    alg = vtk_objects[node]
    try:
        alg.Update()
        data = alg.GetOutput()
    except Exception as e:
        return f"Could not get output for node '{node}': {e}"

    # Use vtkArrayCalculator to assemble the vector
    import vtk as _vtk
    calc = _vtk.vtkArrayCalculator()
    calc.SetInputData(data)
    for c in (cx, cy, cz):
        calc.AddScalarArrayName(c)
    calc.SetFunction(f"{cx}*iHat + {cy}*jHat + {cz}*kHat")
    calc.SetResultArrayName(result)
    try:
        calc.Update()
        out = calc.GetOutput()
    except Exception as e:
        return f"Calculator error: {e}"

    arr = out.GetPointData().GetArray(result)
    if arr is None:
        arr = out.GetCellData().GetArray(result)
    if arr is None:
        return (
            f"make_vector failed: result array '{result}' not found after calculation. "
            f"Check that '{cx}', '{cy}', '{cz}' exist on node '{node}'."
        )

    n = arr.GetNumberOfTuples()
    return (
        f"Created vector array '{result}' with {n} tuples on node '{node}'.\n"
        f"Components: ({cx}, {cy}, {cz}).\n"
        f"Use '{result}' as a vector field for streamlines, glyphs, or curl()."
    )


@mcp.tool()
def curl(node: str, result: str = "vorticity", vector: bool = True) -> str:
    """Compute the curl of a vector field on a named pipeline node.

    The node must already have an active vector array (set by ``make_vector``
    or ``compute_velocity`` in the DSL, or read directly from the file).

    This is the general curl operator.  ``compute_vorticity`` in the DSL is a
    thin wrapper around this primitive.

    For use inside pipeline.py DSL code, call ``curl(...)`` directly.

    Args:
        node: Name of the pipeline variable holding the vector field.
        result: Name for the output array (default "vorticity").
        vector: If True (default), output is a 3-component curl vector.
                If False, output is the scalar magnitude ||curl||.
    """
    vtk_objects = _current_ctx().vtk_objects
    if node not in vtk_objects:
        available = sorted(vtk_objects.keys())
        return f"Node '{node}' not found. Available: {available}"

    alg = vtk_objects[node]
    import vtk as _vtk

    # vtkCellDerivatives -> vtkCellDataToPointData -> optional rename/magnitude
    deriv = _vtk.vtkCellDerivatives()
    deriv.SetInputConnection(alg.GetOutputPort())
    deriv.SetVectorModeToComputeVorticity()
    deriv.SetTensorModeToPassTensors()

    c2p = _vtk.vtkCellDataToPointData()
    c2p.SetInputConnection(deriv.GetOutputPort())

    calc = _vtk.vtkArrayCalculator()
    calc.SetInputConnection(c2p.GetOutputPort())
    calc.AddVectorArrayName("Vorticity")
    if vector:
        calc.SetFunction("Vorticity")
    else:
        calc.SetFunction("mag(Vorticity)")
    calc.SetResultArrayName(result)

    try:
        calc.Update()
        out = calc.GetOutput()
    except Exception as e:
        return f"Curl computation failed: {e}"

    arr = out.GetPointData().GetArray(result)
    if arr is None:
        arr = out.GetCellData().GetArray(result)
    if arr is None:
        return (
            f"curl failed: result array '{result}' not found. "
            f"Ensure '{node}' has an active vector field."
        )

    n = arr.GetNumberOfTuples()
    nc = arr.GetNumberOfComponents()
    kind = "vector" if vector else "scalar magnitude"
    return (
        f"Computed curl ({kind}) as '{result}' with {n} tuples, "
        f"{nc} component(s) on node '{node}'."
    )


@mcp.tool()
def reset_pipeline() -> str:
    """Clear the entire scene and reset to empty state.

    Use this to start fresh without restarting the server.
    """
    ctx = _current_ctx()
    renderer = ctx.renderer
    def _impl():
        global _vtk_objects, _current_code
        renderer.clear()
        ctx.vtk_objects = {}
        ctx.current_code = ""
        # Keep legacy globals in sync
        _vtk_objects = {}
        _current_code = ""
        renderer.render()
        return "Pipeline cleared. Scene is empty."
    result = renderer.run_on_main_thread(_impl)
    return _with_screenshot(result)


@mcp.tool()
def screenshot() -> Image:
    """Render the current scene and return the image.

    Call this after set_pipeline to see the current visualization.
    """
    ctx = _current_ctx()
    renderer = ctx.renderer
    screenshot_path = f".vislang/latest_{ctx.name}.png"
    def _take():
        renderer.render()
        return renderer.screenshot(screenshot_path)
    path = renderer.run_on_main_thread(_take)
    return Image(path=path)


@mcp.tool()
def camera_orbit(node: str = "", n_frames: int = 8, elevation: float = 30.0) -> list:
    """Orbit the camera around the scene and return a series of screenshots.

    Captures views evenly spaced around the focal point at the given elevation
    angle, giving a turntable-style tour of the 3D scene.  Useful for
    understanding spatial structure that is hard to read from a single angle.

    The original camera state is restored after all frames are captured.

    Args:
        node: Unused — kept for API consistency. Leave empty.
        n_frames: Number of views to capture (default 8, clamped to 1–16).
        elevation: Camera elevation angle in degrees above the focal plane
                   (default 30.0, clamped to -89–89).

    Returns:
        A flat list alternating text descriptions and Image objects:
        [description_0, Image_0, description_1, Image_1, ...]
    """
    import math

    # Clamp parameters to sensible ranges
    n_frames = max(1, min(16, int(n_frames)))
    elevation = max(-89.0, min(89.0, float(elevation)))

    ctx = _current_ctx()
    renderer = ctx.renderer

    def _orbit():
        # Save original camera state so we can restore it
        original = renderer.get_camera_state()

        focal = original["focal_point"]
        cam_pos = original["position"]

        # Compute camera distance from focal point
        dx = cam_pos[0] - focal[0]
        dy = cam_pos[1] - focal[1]
        dz = cam_pos[2] - focal[2]
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if distance < 1e-9:
            # Camera is at the focal point — fall back to a default distance
            distance = 1.0

        elev_rad = math.radians(elevation)
        cos_e = math.cos(elev_rad)
        sin_e = math.sin(elev_rad)

        results = []
        for i in range(n_frames):
            azimuth = 2.0 * math.pi * i / n_frames
            px = focal[0] + distance * math.cos(azimuth) * cos_e
            py = focal[1] + distance * math.sin(azimuth) * cos_e
            pz = focal[2] + distance * sin_e

            renderer.set_camera(
                position=[px, py, pz],
                focal_point=focal,
                up=[0.0, 0.0, 1.0],
            )
            renderer.render()

            frame_path = f".vislang/orbit_{ctx.name}_frame{i:02d}.png"
            renderer.screenshot(frame_path)

            az_deg = round(math.degrees(azimuth), 1)
            desc = (
                f"Frame {i + 1}/{n_frames} — "
                f"azimuth {az_deg}°, elevation {elevation}°"
            )
            results.append(desc)
            results.append(Image(path=frame_path))

        # Restore original camera
        renderer.set_camera(
            position=original["position"],
            focal_point=original["focal_point"],
            up=original["up"],
        )
        renderer.render()

        return results

    return renderer.run_on_main_thread(_orbit)


@mcp.tool()
def get_array_info(node: str = "") -> str:
    """List all arrays on a node's output (or root data source if node is empty).

    Returns array names, types, component counts, and value ranges.
    Use this first to understand what fields are available before building visualizations.
    """
    data, err = _get_data_or_error(node)
    if err:
        return err
    return queries.get_array_info(data)


@mcp.tool()
def describe_data(node: str = "", file_path: str = "") -> str:
    """Get a comprehensive overview of a dataset: dimensions, bounds, all fields with statistics.

    This is the recommended first call after loading data. Returns everything
    you need to start building a visualization, including per-field percentiles
    (p1, p25, p50, p75, p99), distribution shape, and coordinate info.
    No follow-up calls needed for basic exploration.

    Can be called in three ways:
    - describe_data() -- uses the active pipeline's first node
    - describe_data(node="nodename") -- uses a named node in the active pipeline
    - describe_data(file_path="myfile.vts") -- reads the file directly, no pipeline needed

    When file_path is given it takes precedence over node and the active pipeline.
    Supported file extensions: .vts, .vti, .vtp, .vtu, .vtr
    """
    source_label = node or "data"

    if file_path:
        # Load directly from file, no pipeline required
        data, error = _load_file_directly(file_path)
        if error:
            return error
        source_label = file_path
    else:
        data, err = _get_data_or_error(node)
        if err:
            return err

    lines = ["=== Dataset Overview ==="]
    lines.append(f"  Points: {data.GetNumberOfPoints():,}")
    lines.append(f"  Cells: {data.GetNumberOfCells():,}")
    lines.append(f"  Type: {data.GetClassName()}")

    if hasattr(data, "GetDimensions"):
        dims = [0, 0, 0]
        data.GetDimensions(dims)
        lines.append(f"  Dimensions: {dims[0]} x {dims[1]} x {dims[2]}")

    bounds = data.GetBounds()
    lines.append(
        f"  Bounds: X=[{bounds[0]:.1f}, {bounds[1]:.1f}] (range {bounds[1]-bounds[0]:.1f}), "
        f"Y=[{bounds[2]:.1f}, {bounds[3]:.1f}] (range {bounds[3]-bounds[2]:.1f}), "
        f"Z=[{bounds[4]:.1f}, {bounds[5]:.1f}] (range {bounds[5]-bounds[4]:.1f})"
    )

    # Spacing info for structured data
    if hasattr(data, "GetDimensions"):
        dims = [0, 0, 0]
        data.GetDimensions(dims)
        spacing_parts = []
        for axis, label, d in [(0, "X", dims[0]), (1, "Y", dims[1]), (2, "Z", dims[2])]:
            extent = bounds[2 * axis + 1] - bounds[2 * axis]
            if d > 1:
                avg_spacing = extent / (d - 1)
                spacing_parts.append(f"{label}~{avg_spacing:.2g}")
        if spacing_parts:
            lines.append(f"  Avg spacing: {', '.join(spacing_parts)}")

    # Rich field statistics
    lines.append("")
    lines.append("=== Fields (with percentiles and distribution shape) ===")
    field_stats = queries.get_rich_field_stats(data)
    lines.append(queries.format_rich_field_stats(field_stats))

    # Volume rendering readiness
    data_type = data.GetClassName()
    if data_type in ("vtkImageData", "vtkUniformGrid"):
        lines.append("")
        lines.append("=== Volume Rendering ===")
        lines.append("  Data is vtkImageData - volume rendering works directly (no resampling).")
        lines.append('  Quick: show(node, "vol", representation="Volume", opacity_function="ct_bone")')
    elif data_type in ("vtkStructuredGrid", "vtkRectilinearGrid"):
        lines.append("")
        lines.append("=== Volume Rendering ===")
        lines.append("  Data will be auto-resampled for volume rendering (set volume_resolution).")
        lines.append("  Tip: threshold first to reduce data, then volume render the subset.")

    lines.append("")
    lines.append("=== Quick Start ===")
    lines.append(f"  Call quick_start(\"{source_label}\") for a starter pipeline")
    lines.append("  Use suggest_isosurface(node, field) for contour values")
    lines.append("  Use suggest_opacity(node, field) for volume rendering opacity")

    return "\n".join(lines)


@mcp.tool()
def get_field_summary(node: str, field: str) -> str:
    """Get comprehensive summary of a field: stats, percentiles, and opacity suggestion.

    Combines get_statistics + suggest_scalar_range + suggest_opacity in one call.
    Use this when exploring a field before visualization.
    """
    data, err = _get_data_or_error(node)
    if err:
        return err
    parts = [
        queries.get_statistics(data, field),
        "",
        queries.suggest_scalar_range(data, field),
        "",
        queries.suggest_opacity_function(data, field, max_opacity=0.6),
    ]
    return "\n".join(parts)


@mcp.tool()
def get_node_info(node: str) -> str:
    """Get detailed information about a specific pipeline node's output.

    Shows point count, cell count, bounds, and all arrays with ranges.
    More detailed than get_array_info for a specific node.
    """
    data, err = _get_data_or_error(node)
    if err:
        return err

    lines = [f"Node '{node}':"]
    lines.append(f"  Type: {data.GetClassName()}")
    lines.append(f"  Points: {data.GetNumberOfPoints():,}")
    lines.append(f"  Cells: {data.GetNumberOfCells():,}")

    if hasattr(data, "GetDimensions"):
        dims = [0, 0, 0]
        data.GetDimensions(dims)
        lines.append(f"  Dimensions: {dims[0]} x {dims[1]} x {dims[2]}")

    bounds = data.GetBounds()
    lines.append(f"  Bounds: X=[{bounds[0]:.2f}, {bounds[1]:.2f}], Y=[{bounds[2]:.2f}, {bounds[3]:.2f}], Z=[{bounds[4]:.2f}, {bounds[5]:.2f}]")

    pd = data.GetPointData()
    if pd.GetNumberOfArrays() > 0:
        lines.append(f"  Point arrays ({pd.GetNumberOfArrays()}):")
        for i in range(pd.GetNumberOfArrays()):
            arr = pd.GetArray(i)
            name = pd.GetArrayName(i)
            nc = arr.GetNumberOfComponents()
            if nc == 1:
                rng = arr.GetRange()
                lines.append(f"    {name}: range=[{rng[0]:.6g}, {rng[1]:.6g}]")
            else:
                lines.append(f"    {name}: {nc} components")

    cd = data.GetCellData()
    if cd.GetNumberOfArrays() > 0:
        lines.append(f"  Cell arrays ({cd.GetNumberOfArrays()}):")
        for i in range(cd.GetNumberOfArrays()):
            name = cd.GetArrayName(i)
            lines.append(f"    {name}")

    return "\n".join(lines)


@mcp.tool()
def get_bounds(node: str = "") -> str:
    """Get spatial bounds of a node's output data."""
    data, err = _get_data_or_error(node)
    if err:
        return err
    return queries.get_bounds(data)


@mcp.tool()
def get_statistics(node: str, field: str) -> str:
    """Get min, max, mean, std for a field on a node's output.

    Use this to understand value ranges before setting thresholds, isosurface values,
    or color map ranges.
    """
    data, err = _get_data_or_error(node)
    if err:
        return err
    return queries.get_statistics(data, field)


@mcp.tool()
def query_stats(node: str, field: str, condition: str) -> str:
    """Compute statistics for a field filtered by a condition on another field.

    Answers questions like:
      - "mean updraft velocity where theta > 400"
      - "min/max oxygen where fuel_density > 0.1"
      - "volume (count) where temperature >= 500"

    The *condition* string must be in the form "<field> <op> <value>" where
    op is one of: >, <, >=, <=, ==, !=

    Examples:
        query_stats("", "w", "theta > 400")
        query_stats("thresh1", "O2", "fuel_density >= 0.1")
        query_stats("", "temperature", "temperature != 0")

    Returns count of matching points plus mean, min, max, std, and percentiles
    (p1, p25, p50, p75, p99) of the target field within the matching region.

    Args:
        node: Pipeline node to query (empty string for root source).
        field: Scalar field to compute statistics on.
        condition: Condition string like "theta > 400" (field op value).
    """
    import re

    data, err = _get_data_or_error(node)
    if err:
        return err

    # Parse the condition string: "<field> <op> <value>"
    # Operators ordered longest-first so ">=" is matched before ">"
    pattern = r"^\s*(.+?)\s*(>=|<=|!=|==|>|<)\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*$"
    m = re.match(pattern, condition)
    if not m:
        return (
            f"Could not parse condition '{condition}'. "
            "Expected format: '<field> <op> <value>' where op is >, <, >=, <=, ==, or !=. "
            "Example: 'theta > 400'"
        )

    cond_field = m.group(1).strip()
    cond_op = m.group(2)
    try:
        cond_value = float(m.group(3))
    except ValueError:
        return f"Could not parse numeric value from condition '{condition}'."

    return queries.query_stats(data, field, cond_field, cond_op, cond_value)


@mcp.tool()
def get_histogram(node: str, field: str, bins: int = 20) -> str:
    """Get a text histogram of a field's value distribution.

    Useful for understanding data distribution before choosing visualization parameters.
    """
    data, err = _get_data_or_error(node)
    if err:
        return err
    return queries.get_histogram(data, field, bins)


@mcp.tool()
def get_spatial_extent(node: str, field: str, min_value: float, max_value: float) -> str:
    """Find the bounding box where a field is within a given range.

    Useful for positioning seed points for streamlines, focusing cameras,
    or understanding where features are located in 3D space.
    """
    data, err = _get_data_or_error(node)
    if err:
        return err
    return queries.get_spatial_extent(data, field, min_value, max_value)


def sample_point(node: str, x: float, y: float, z: float) -> str:
    """Sample field values at the nearest grid point to (x, y, z).

    Use sample_points() with a single point instead — it does the same thing
    and supports batching multiple points in one call.
    """
    data, err = _get_data_or_error(node)
    if err:
        return err
    return queries.sample_point(data, x, y, z)


@mcp.tool()
def sample_points(
    node: str,
    points: list[list[float]],
    fields: list[str] = None,
) -> str:
    """Sample field values at multiple (x, y, z) locations in one call.

    Probes all requested points efficiently using a single spatial index,
    avoiding the round-trip cost of calling sample_point N times.

    Returns a structured text report: one block per input point showing
    the nearest grid point, field values (scalar or vector), and whether
    the query coordinate was outside the dataset bounds.

    Args:
        node: Pipeline node to sample from (empty string for root source).
        points: List of [x, y, z] coordinates to probe.
        fields: Optional list of field names to return. If omitted, all
                point-data fields are returned.

    Example:
        sample_points("", [[0,0,0],[1,1,1]], fields=["temperature","density"])
    """
    data, err = _get_data_or_error(node)
    if err:
        return err

    if not points:
        return "No points provided."

    for i, pt in enumerate(points):
        if len(pt) != 3:
            return f"Point {i} must be [x, y, z] (3 values), got {len(pt)}."

    results = queries.sample_points(data, [tuple(p) for p in points], fields or None)
    return queries.format_sample_points(results)


@mcp.tool()
def sample_line(
    node: str,
    point1: list[float],
    point2: list[float],
    fields: list[str],
    resolution: int = 100,
) -> str:
    """Extract a 1-D profile of field values along a line between two points.

    Samples the dataset at evenly spaced points along the line from point1
    to point2 using a probe filter. Returns a table of values with distance
    along the line, plus summary statistics (min, max, mean, trend) for each field.

    Great for extracting profiles like "temperature vs. height through the plume
    center" or "density along a horizontal transect."

    Args:
        node: Name of the pipeline node to sample from (empty string for root source).
        point1: Start point [x, y, z].
        point2: End point [x, y, z].
        fields: List of field names to extract (e.g. ["temperature", "density"]).
        resolution: Number of sample points along the line (default 100).
    """
    data, err = _get_data_or_error(node)
    if err:
        return err

    if len(point1) != 3 or len(point2) != 3:
        return "point1 and point2 must each be [x, y, z] (3 values)."

    probe_output = queries.sample_line(data, tuple(point1), tuple(point2), resolution)
    return queries.get_line_probe_data(probe_output, fields)


@mcp.tool()
def get_ground_z(node: str, x: float, y: float) -> str:
    """Return the Z coordinate at (x, y) for the lowest layer of a structured grid.

    Useful for any 3D structured grid where the Z coordinate of the bottom
    layer varies with position — for example terrain-following grids or
    curvilinear meshes. Use this before placing seed points for streamlines
    to ensure they are inside the grid.

    Returns an error message if the data is not a structured grid.
    """
    data, err = _get_data_or_error(node)
    if err:
        return err
    return queries.get_ground_z(data, x, y)


@mcp.tool()
def suggest_scalar_range(node: str, field: str, percentile_low: float = 1.0, percentile_high: float = 99.0) -> str:
    """Suggest a useful scalar range for a field based on its value distribution.

    Returns percentile-based ranges that avoid extreme outliers compressing
    the colormap. Useful before setting scalar_range in show().
    """
    data, err = _get_data_or_error(node)
    if err:
        return err
    return queries.suggest_scalar_range(data, field, percentile_low, percentile_high)


@mcp.tool()
def suggest_opacity(node: str, field: str, scalar_range: list[float] = None, max_opacity: float = 0.8) -> str:
    """Suggest opacity transfer function control points for volume rendering.

    Analyzes the field histogram to make common values transparent and rare
    values opaque. Returns control points you can paste into show()'s
    opacity_function parameter.

    Args:
        node: Pipeline node to query (empty string for root source).
        field: Scalar field to analyze.
        scalar_range: Optional [min, max] range to restrict analysis. If omitted,
                      uses the full data range.
        max_opacity: Maximum opacity value in the returned transfer function (default 0.8).
    """
    data, err = _get_data_or_error(node)
    if err:
        return err
    sr = None
    if scalar_range is not None and len(scalar_range) == 2:
        sr = (float(scalar_range[0]), float(scalar_range[1]))
    return queries.suggest_opacity_function(data, field, scalar_range=sr, max_opacity=max_opacity)


@mcp.tool()
def suggest_isosurface(node: str, field: str, num_values: int = 3) -> str:
    """Suggest good isosurface values for a field.

    Analyzes the field histogram to find transition points that produce
    meaningful isosurfaces. Returns values you can use in Isosurfaces=[].
    """
    data, err = _get_data_or_error(node)
    if err:
        return err
    return queries.suggest_isosurface(data, field, num_values)


@mcp.tool()
def suggest_camera(style: str = "overview") -> str:
    """Suggest a camera position based on visible actors.

    Styles: "overview" (default), "closeup", "top_down", "side"

    Returns camera parameters you can paste into set_pipeline's camera() call.
    """
    renderer = _current_ctx().renderer
    result = renderer.run_on_main_thread(lambda: renderer.suggest_camera(style))
    if result is None:
        return "No actors in the scene. Call set_pipeline first."
    pos = tuple(round(x, 1) for x in result["position"])
    fp = tuple(round(x, 1) for x in result["focal_point"])
    up = result["up"]
    return (
        f"Suggested camera ({style}):\n"
        f"  camera(position={pos}, focal_point={fp}, up={up})\n\n"
        f"Copy this into your pipeline code."
    )


@mcp.tool()
def set_camera(
    position: list[float] = None,
    focal_point: list[float] = None,
    up: list[float] = None,
    zoom: float = 0,
) -> str:
    """Set the camera position without rebuilding the pipeline.

    Much faster than modifying camera() in set_pipeline. Pass coordinates
    as numeric lists, e.g. position=[100, -500, 400].

    Args:
        position: Camera position as [x, y, z].
        focal_point: Camera focal point as [x, y, z].
        up: Camera up vector as [x, y, z] (default [0, 0, 1]).
        zoom: Zoom factor (> 0 to apply, e.g. 1.5 to zoom in).
    """
    renderer = _current_ctx().renderer
    def _impl():
        kwargs = {}
        if position is not None:
            kwargs["position"] = tuple(float(x) for x in position)
        if focal_point is not None:
            kwargs["focal_point"] = tuple(float(x) for x in focal_point)
        if up is not None:
            kwargs["up"] = tuple(float(x) for x in up)
        if zoom > 0:
            kwargs["zoom"] = zoom
        if not kwargs:
            return "Specify at least position or focal_point."
        renderer.set_camera(**kwargs)
        renderer.render()
        cam = renderer.get_camera_state()
        return (
            f"Camera updated.\n"
            f"  position={[round(x,1) for x in cam['position']]}\n"
            f"  focal_point={[round(x,1) for x in cam['focal_point']]}"
        )
    return _with_screenshot(renderer.run_on_main_thread(_impl))


@mcp.tool()
def set_opacity(name: str, opacity: float) -> str:
    """Set the opacity of a named actor in the scene (0.0 = invisible, 1.0 = opaque).

    Fast way to adjust transparency without rebuilding the pipeline.
    """
    import vtk
    renderer = _current_ctx().renderer
    def _impl():
        actor = renderer._actors.get(name)
        if actor is None:
            available = sorted(renderer._actors.keys())
            return f"Actor '{name}' not found. Available: {available}"
        if isinstance(actor, vtk.vtkVolume):
            # For volumes, scale the opacity transfer function
            prop = actor.GetProperty()
            otf = prop.GetScalarOpacity()
            # Rebuild with scaled opacity
            new_otf = vtk.vtkPiecewiseFunction()
            for i in range(otf.GetSize()):
                node = [0.0] * 4
                otf.GetNodeValue(i, node)
                new_otf.AddPoint(node[0], node[1] * opacity)
            prop.SetScalarOpacity(new_otf)
        else:
            actor.GetProperty().SetOpacity(opacity)
        renderer.render()
        return f"'{name}' opacity set to {opacity}."
    return _with_screenshot(renderer.run_on_main_thread(_impl))


@mcp.tool()
def set_colormap(name: str, lut: str = "", scalar_range: list[float] = None) -> str:
    """Change the colormap of a named actor without rebuilding.

    Accepts preset names: "fire", "terrain", "wind", "cool_to_warm",
    "blue_to_red", "grayscale", "oxygen", "heat".
    Optionally update scalar range at the same time.

    Args:
        name: Name of the actor to update.
        lut: Colormap preset name (e.g. "fire", "cool_to_warm").
        scalar_range: Optional [min, max] to set the scalar range at the same time.
    """
    import vtk
    renderer = _current_ctx().renderer
    def _impl():
        actor = renderer._actors.get(name)
        if actor is None:
            available = sorted(renderer._actors.keys())
            return f"Actor '{name}' not found. Available: {available}"
        if isinstance(actor, vtk.vtkVolume):
            return "Use set_pipeline() to change volume colormaps."

        mapper = actor.GetMapper()
        if not mapper:
            return f"'{name}' has no mapper."

        sr = None
        if scalar_range is not None and len(scalar_range) == 2:
            sr = (float(scalar_range[0]), float(scalar_range[1]))
            mapper.SetScalarRange(*sr)
        else:
            sr = mapper.GetScalarRange()

        if lut:
            from .colormaps import build_lut
            new_lut = build_lut(lut, scalar_range=sr)
            mapper.SetLookupTable(new_lut)

        renderer.render()
        return f"'{name}' colormap set to '{lut}'" + (f" with range ({sr[0]}, {sr[1]})" if sr else "") + "."
    return _with_screenshot(renderer.run_on_main_thread(_impl))


def set_color_range(name: str, scalar_range: list[float]) -> str:
    """Set the scalar color range of a named actor without rebuilding.

    Prefer set_colormap(name, scalar_range=[min, max]) which does the same
    thing and also lets you change the colormap at the same time.

    Args:
        name: Name of the actor to update.
        scalar_range: [min, max] range for the colormap.
    """
    import vtk
    renderer = _current_ctx().renderer
    def _impl():
        actor = renderer._actors.get(name)
        if actor is None:
            available = sorted(renderer._actors.keys())
            return f"Actor '{name}' not found. Available: {available}"
        if isinstance(actor, vtk.vtkVolume):
            # For volumes, update the color and opacity transfer functions
            return f"Use set_pipeline() to change volume scalar range (requires transfer function rebuild)."
        if scalar_range is None or len(scalar_range) != 2:
            return "scalar_range must be a list of two values: [min, max]."
        min_val, max_val = float(scalar_range[0]), float(scalar_range[1])
        mapper = actor.GetMapper()
        if mapper:
            mapper.SetScalarRange(min_val, max_val)
        renderer.render()
        return f"'{name}' scalar range set to ({min_val}, {max_val})."
    return _with_screenshot(renderer.run_on_main_thread(_impl))


@mcp.tool()
def get_actor_info(name: str) -> str:
    """Get information about a specific actor/volume in the scene.

    Shows type, visibility, bounds, scalar range, and opacity.
    """
    import vtk
    renderer = _current_ctx().renderer
    actor = renderer._actors.get(name)
    if actor is None:
        available = sorted(renderer._actors.keys())
        return f"Actor '{name}' not found. Available: {available}"

    lines = [f"Actor '{name}':"]
    is_vol = isinstance(actor, vtk.vtkVolume)
    lines.append(f"  Type: {'Volume' if is_vol else 'Actor'}")
    lines.append(f"  Visible: {bool(actor.GetVisibility())}")
    bounds = actor.GetBounds()
    lines.append(f"  Bounds: [{bounds[0]:.1f},{bounds[1]:.1f}] x [{bounds[2]:.1f},{bounds[3]:.1f}] x [{bounds[4]:.1f},{bounds[5]:.1f}]")

    if is_vol:
        prop = actor.GetProperty()
        lines.append(f"  Shade: {bool(prop.GetShade())}")
        lines.append(f"  Ambient: {prop.GetAmbient()}, Diffuse: {prop.GetDiffuse()}, Specular: {prop.GetSpecular()}")
    else:
        mapper = actor.GetMapper()
        if mapper:
            sr = mapper.GetScalarRange()
            lines.append(f"  Scalar range: ({sr[0]:.6g}, {sr[1]:.6g})")
        prop = actor.GetProperty()
        lines.append(f"  Opacity: {prop.GetOpacity()}")

    return "\n".join(lines)


@mcp.tool()
def toggle_visibility(name: str) -> str:
    """Toggle visibility of a named actor/volume in the scene.

    Use this to show/hide specific layers without rebuilding the pipeline.
    """
    renderer = _current_ctx().renderer
    def _impl():
        actor = renderer._actors.get(name)
        if actor is None:
            available = sorted(renderer._actors.keys())
            return f"Actor '{name}' not found. Available: {available}"
        current = actor.GetVisibility()
        actor.SetVisibility(0 if current else 1)
        renderer.render()
        state = "visible" if actor.GetVisibility() else "hidden"
        return f"'{name}' is now {state}."
    return _with_screenshot(renderer.run_on_main_thread(_impl))


@mcp.tool()
def set_window_size(width: int, height: int) -> str:
    """Set the render window size for higher/lower resolution screenshots.

    Default is 1920x1080. Use 3840x2160 for 4K publication quality.
    """
    renderer = _current_ctx().renderer
    def _impl():
        renderer._render_window.SetSize(width, height)
        renderer.render()
        return f"Window size set to {width}x{height}."
    return _with_screenshot(renderer.run_on_main_thread(_impl))


@mcp.tool()
def set_background(r: float, g: float, b: float) -> str:
    """Set the scene background color without rebuilding the pipeline.

    Values are 0.0-1.0 RGB. Common presets: dark=(0.02,0.02,0.06),
    light=(0.85,0.85,0.9), black=(0,0,0), white=(1,1,1).
    """
    renderer = _current_ctx().renderer
    def _impl():
        renderer.set_background(r, g, b)
        renderer.render()
        return f"Background set to ({r}, {g}, {b})."
    return _with_screenshot(renderer.run_on_main_thread(_impl))


@mcp.tool()
def list_actors() -> str:
    """List all actors/volumes in the current scene with their visibility and type.

    Useful for knowing what layers exist for toggle_visibility/set_opacity.
    """
    import vtk
    renderer = _current_ctx().renderer
    if not renderer._actors:
        return "No actors in scene. Call set_pipeline() first."
    lines = ["Scene actors:"]
    for name, actor in sorted(renderer._actors.items()):
        atype = "Volume" if isinstance(actor, vtk.vtkVolume) else "Actor"
        visible = "visible" if actor.GetVisibility() else "hidden"
        bounds = actor.GetBounds()
        lines.append(f"  {name}: {atype}, {visible}, bounds=[{bounds[0]:.0f},{bounds[1]:.0f},{bounds[2]:.0f},{bounds[3]:.0f},{bounds[4]:.0f},{bounds[5]:.0f}]")
    return "\n".join(lines)


@mcp.tool()
def list_versions() -> str:
    """List all saved pipeline versions with timestamps.

    Each set_pipeline call creates a new version. Use restore_version(n)
    to go back to a previous version.
    """
    ctx = _current_ctx()
    versions = sorted(ctx.history_dir.glob("v*/pipeline.py"))
    if not versions:
        return "No versions saved yet. Call set_pipeline() to create the first version."
    lines = [f"Pipeline versions for view '{ctx.name}' ({len(versions)} total):"]
    for v in versions:
        ver_num = int(v.parent.name[1:])
        code = v.read_text()
        first_line = code.strip().split("\n")[0][:80] if code.strip() else "(empty)"
        has_screenshot = (v.parent / "screenshot.png").exists()
        lines.append(f"  v{ver_num}: {first_line}{'...' if len(first_line) >= 80 else ''}"
                     f" {'[screenshot]' if has_screenshot else ''}")
    lines.append(f"\nCurrent: v{ctx.version}")
    lines.append("Use restore_version(n) to restore a previous version.")
    return "\n".join(lines)


@mcp.tool()
def restore_version(version: int) -> str:
    """Restore a previous pipeline version by number.

    Use this to go back to an earlier visualization state.
    """
    ctx = _current_ctx()
    ver_dir = ctx.history_dir / f"v{version:04d}"
    spec_file = ver_dir / "pipeline.py"
    if not spec_file.exists():
        # List available versions
        versions = sorted(ctx.history_dir.glob("v*/pipeline.py"))
        if versions:
            nums = [int(v.parent.name[1:]) for v in versions]
            return f"Version {version} not found. Available: {nums}"
        return f"Version {version} not found. No versions saved yet."
    # Write the restored code to the view's pipeline file, then call set_pipeline with the path.
    # set_pipeline() expects a file path, not code content.
    pipeline_path = Path(ctx.pipeline_file)
    pipeline_path.write_text(spec_file.read_text())
    return set_pipeline(str(pipeline_path))


@mcp.tool()
def get_pipeline() -> str:
    """Return the current DSL pipeline spec text.

    Use this to see the current pipeline and modify it incrementally.
    """
    ctx = _current_ctx()
    if not ctx.current_code:
        return "No pipeline set yet. Use set_pipeline() to create one."
    header = f"# Pipeline v{ctx.version}\n"
    return header + ctx.current_code


def benchmark_pipeline(file: str = "pipeline.py") -> str:
    """Time a pipeline build without rendering or taking screenshots.

    Developer diagnostic tool — not exposed as an MCP tool.
    Returns timing breakdown for pipeline construction, useful for
    optimizing complex pipelines.

    Args:
        file: Path to the pipeline .py file (default: pipeline.py)
    """
    try:
        code = Path(file).read_text()
    except FileNotFoundError:
        return f"File not found: {file}"
    except Exception as e:
        return f"Error reading {file}: {e}"

    import time as _time
    t0 = _time.monotonic()

    try:
        from .dsl import interpret
        # Create a no-render renderer for benchmarking
        class _NoRender:
            def clear(self): pass
            def add_actor(self, name, actor): pass
            def add_volume(self, name, vol): pass
            def set_camera(self, **kw): pass
            def set_background(self, r, g, b): pass
            def reset_camera(self): pass
            def render(self): pass
            _render_window = type('', (), {'GetSize': lambda s: (1920, 1080)})()
            _renderer = type('', (), {'AddActor2D': lambda s, a: None})()

        bench_renderer = _NoRender()
        vtk_objs, node_statuses, show_statuses, builder = interpret(code, bench_renderer)
        t1 = _time.monotonic()

        lines = [f"Pipeline benchmark: {t1-t0:.3f}s total"]
        lines.append(f"  Nodes: {len(node_statuses)}")
        lines.append(f"  Shows: {len(show_statuses)}")
        for node_id, status in sorted(node_statuses.items()):
            name = status.get("name", f"node_{node_id}")
            pts = status.get("num_points", 0)
            lines.append(f"  {name}: {pts:,} pts")

        return "\n".join(lines)

    except Exception as e:
        t1 = _time.monotonic()
        return f"Benchmark failed after {t1-t0:.3f}s: {type(e).__name__}: {e}"


@mcp.tool()
def export_standalone(path: str = "visualization.py") -> str:
    """Export the current pipeline as a standalone Python script.

    The exported script can run independently without the MCP server.
    """
    ctx = _current_ctx()
    if not ctx.current_code:
        return "No pipeline to export. Use set_pipeline() first."

    script = f'''#!/usr/bin/env python3
"""Standalone visualization generated by VisLang."""
import sys
sys.path.insert(0, ".")
from vislang.renderer import Renderer
from vislang.dsl import interpret

renderer = Renderer(1920, 1080, offscreen="--offscreen" in sys.argv)

code = """
{ctx.current_code}
"""

objs, node_statuses, show_statuses, builder = interpret(code, renderer)

# Print build status
for node_id, status in sorted(node_statuses.items()):
    name = status.get("name", f"node_{{node_id}}")
    if "error" in status:
        print(f"  {{name}}: ERROR - {{status['error']}}")
    else:
        print(f"  {{name}}: {{status['class']}} -> {{status['num_points']}} pts")

# Save screenshot
path = renderer.screenshot("visualization.png")
print(f"Screenshot saved to {{path}}")

# If interactive, keep window open
if "--offscreen" not in sys.argv:
    renderer.run_event_loop()
'''

    with open(path, "w") as f:
        f.write(script)

    return f"Exported to {path}. Run with: python {path} [--offscreen]"


@mcp.tool()
def list_capabilities() -> str:
    """List available VTK filter classes, colormap presets, and DSL functions.

    Call this first if you're unsure what's available in the DSL.
    Use get_examples() to see working code patterns.
    """
    from .filters import WHITELISTED_CLASSES
    from .colormaps import PRESETS, OPACITY_PRESETS

    sources = sorted(k for k in WHITELISTED_CLASSES if "Source" in k or "Reader" in k)
    filters = sorted(k for k in WHITELISTED_CLASSES if k not in set(sources))
    colormap_names = ", ".join(f'"{n}"' for n in sorted(PRESETS))
    opacity_preset_names = ", ".join(f'"{n}"' for n in sorted(OPACITY_PRESETS))

    lines = [
        "=== Sources/Readers ===",
        ", ".join(sources),
        "",
        "=== Filters ===",
        ", ".join(filters),
        "",
        "=== Colormaps ===",
        colormap_names,
        "",
        "=== Data Prep ===",
        "  threshold(input=, ThresholdBy=, ThresholdRange=[min,max])",
        "  extract_region(input=, bounds=[xmin,xmax,ymin,ymax,zmin,zmax])  # or voi=[i,j,k indices]",
        "  calculator(input=, Function=, ResultArrayName=, AddScalarArrayName=[])",
        "  cell_to_point(input=), point_to_cell(input=)",
        "  resample_to_image(input=, dimensions=(nx,ny,nz))",
        "  probe(input=, source=node)",
        "  elevation(input=, low_point=, high_point=)",
        "",
        "=== Derived Fields ===",
        "  make_vector(input=, components=('cx','cy','cz'), result='velocity')",
        "  compute_velocity(input=, components=('u','v','w'), result='velocity')",
        "  compute_vorticity(input=, result='vorticity_magnitude', vector=False)",
        "  compute_magnitude(input=, components=('u','v','w'), result='speed')",
        "  curl(vector_field=node, result='vorticity', vector=True)",
        "  gradient(input=, GradientField=, ResultArrayName=)",
        "  compute_gradient_magnitude(input=, field=, result=)",
        "  extract_component(input=, field=, component=0, result_name=)",
        "",
        "=== Geometry ===",
        "  contour(input=, ContourBy=, Isosurfaces=[])  # alias: isosurface()",
        "  slice(input=, origin=(x,y,z), normal=(nx,ny,nz))",
        "  clip(input=, origin=, normal=, inside_out=False)",
        "  clip_box(input=, bounds=(xmin,xmax,ymin,ymax,zmin,zmax))",
        "  clip_sphere(input=, center=, radius=, inside_out=True)",
        "  surface(input=), smooth(input=, iterations=20)",
        "  warp_vector(input=, ...), warp_scalar(input=, ...)",
        "  outline(input=)",
        "",
        "=== Flow / Particles ===",
        "  stream_tracer(input=, SeedSource=, Vectors=, ...)",
        "  seeds_near(input=, field=, min_val=, max_val=, num_seeds=, offset_z=)",
        "  tube(input=, Radius=, NumberOfSides=)",
        "  glyph(input=, GlyphSource=, OrientationArray=, ScaleArray=, ScaleFactor=)",
        "  mask_points(input=, OnRatio=, RandomMode=)",
        "",
        "=== Display ===",
        "  show(node, name, color_by=, scalar_range=, lut=, opacity=, component=0/1/2)",
        "  show(..., representation='Volume', opacity_function=[(val,opacity),...],",
        f"       volume_resolution=256, gradient_opacity=True, shade=True)",
        f"  Volume opacity presets: \"ramp_up\", \"gaussian\", \"step\", {opacity_preset_names}",
        "  Use suggest_opacity() to get histogram-guided opacity functions",
        "  camera(position=, focal_point=, up=, zoom=)",
        "  background(r, g, b), scene_preset('dark'|'light'|'black'|'white')",
        "  title(text, position=, font_size=, color=)",
    ]

    return "\n".join(lines)


@mcp.tool()
def list_data_files() -> str:
    """List available data files (.vts, .vti, .vtk, .vtp) in the current directory.

    Call this first to see what datasets are available to visualize.
    """
    import glob
    patterns = ["*.vts", "*.vti", "*.vtk", "*.vtp", "*.vtu", "*.vtr", "*.raw"]
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat))
        files.extend(glob.glob(f"data/{pat}"))  # Also search data/ subdirectory

    if not files:
        return "No VTK data files found in current directory or data/ subdirectory."

    lines = ["Available data files:"]
    for f in sorted(files):
        size = os.path.getsize(f)
        if size > 1e9:
            size_str = f"{size/1e9:.1f} GB"
        elif size > 1e6:
            size_str = f"{size/1e6:.1f} MB"
        else:
            size_str = f"{size/1e3:.1f} KB"
        lines.append(f"  {f} ({size_str})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Named-view management tools
# ---------------------------------------------------------------------------

@mcp.tool()
def new_view(name: str) -> str:
    """Create a new independent render context (view) and make it current.

    Each view has its own pipeline, camera, version history, and annotations.
    All existing tools (set_pipeline, set_camera, etc.) operate on the current
    view after calling this.

    Args:
        name: Unique name for the new view (e.g. "temperature", "detail").
              Cannot be an existing view name.
    """
    global _views, _current_view
    if name in _views:
        return f"View '{name}' already exists. Use focus('{name}') to switch to it."
    renderer = Renderer(offscreen=_offscreen)
    ctx = ViewContext(name, renderer)
    ctx.history_dir.mkdir(parents=True, exist_ok=True)
    _views[name] = ctx
    _current_view = name
    logger.info("new_view(): created view '%s' (total views: %d)", name, len(_views))
    return f"Created view '{name}' and switched to it. Use set_pipeline() to build a visualization."


@mcp.tool()
def focus(name: str) -> str:
    """Switch which view all tools target (make a named view current).

    After calling this, all tools (set_pipeline, set_camera, screenshot, etc.)
    will operate on the named view. Returns a screenshot of the focused view.

    Args:
        name: Name of the view to switch to.
    """
    global _current_view
    if name not in _views:
        available = sorted(_views.keys())
        return f"View '{name}' not found. Available views: {available}"
    _current_view = name
    logger.info("focus(): switched to view '%s'", name)
    img = _auto_screenshot()
    msg = f"Switched to view '{name}'."
    if img is not None:
        return [msg, img]
    return msg


@mcp.tool()
def close_view(name: str) -> str:
    """Close and remove a named view.

    Cannot close the last remaining view. Clears all VTK resources for that view.
    If the closed view was current, focus switches to the first remaining view.

    Args:
        name: Name of the view to close.
    """
    global _views, _current_view, _renderer
    if name not in _views:
        available = sorted(_views.keys())
        return f"View '{name}' not found. Available views: {available}"
    if len(_views) <= 1:
        return f"Cannot close view '{name}': it is the only remaining view."
    # Clean up the renderer
    ctx = _views.pop(name)
    try:
        ctx.renderer.clear()
    except Exception:
        pass
    # If we closed the current view, switch to the first remaining
    if _current_view == name:
        _current_view = next(iter(_views))
        # Keep legacy _renderer in sync
        _renderer = _views[_current_view].renderer
    logger.info("close_view(): closed '%s', current view is now '%s'", name, _current_view)
    return f"Closed view '{name}'. Current view is now '{_current_view}'."


@mcp.tool()
def list_views() -> str:
    """List all named views and which one is currently active.

    Returns view names, pipeline status, and version numbers.
    """
    if not _views:
        return "No views initialized (call main() first)."
    lines = [f"Views ({len(_views)} total, current: '{_current_view}'):"]
    for vname, ctx in sorted(_views.items()):
        marker = " *" if vname == _current_view else ""
        has_pipeline = bool(ctx.vtk_objects)
        pipeline_info = f"v{ctx.version}, {len(ctx.vtk_objects)} nodes" if has_pipeline else "no pipeline"
        lines.append(f"  {vname}{marker}: {pipeline_info}")
    return "\n".join(lines)


@mcp.tool()
def annotate(
    x: float,
    y: float,
    z: float,
    label: str,
    color: str = "white",
    font_size: int = 14,
) -> str:
    """Add a text annotation label at a 3D position in the scene.

    Uses billboard text that always faces the camera, so it remains readable
    from any viewing angle. Annotations persist across camera changes and
    accumulate until clear_annotations() is called.

    If an annotation with the same label already exists it is replaced.

    Args:
        x: World-space X coordinate for the label.
        y: World-space Y coordinate for the label.
        z: World-space Z coordinate for the label.
        label: Text to display. Also used as the unique key for this annotation.
        color: Text color — named CSS color ("white", "red", "yellow", …) or
               hex string ("#ff8800").  Defaults to "white".
        font_size: Font size in points.  Defaults to 14.
    """
    import vtk

    def _parse_color(color_str):
        """Return (r, g, b) floats in [0,1] from a color name or hex string."""
        named = {
            "white": (1, 1, 1),
            "black": (0, 0, 0),
            "red": (1, 0, 0),
            "green": (0, 1, 0),
            "blue": (0, 0, 1),
            "yellow": (1, 1, 0),
            "cyan": (0, 1, 1),
            "magenta": (1, 0, 1),
            "orange": (1, 0.5, 0),
            "purple": (0.5, 0, 0.5),
            "gray": (0.5, 0.5, 0.5),
            "grey": (0.5, 0.5, 0.5),
            "pink": (1, 0.75, 0.8),
            "lime": (0, 1, 0),
            "brown": (0.65, 0.16, 0.16),
        }
        s = color_str.strip().lower()
        if s in named:
            return named[s]
        if s.startswith("#") and len(s) == 7:
            r = int(s[1:3], 16) / 255.0
            g = int(s[3:5], 16) / 255.0
            b = int(s[5:7], 16) / 255.0
            return (r, g, b)
        # Fallback — white
        return (1, 1, 1)

    ctx = _current_ctx()
    renderer = ctx.renderer
    def _impl():
        r, g, b = _parse_color(color)
        actor = vtk.vtkBillboardTextActor3D()
        actor.SetInput(label)
        actor.SetPosition(x, y, z)
        tp = actor.GetTextProperty()
        tp.SetColor(r, g, b)
        tp.SetFontSize(font_size)
        tp.SetBold(False)
        tp.SetItalic(False)
        tp.SetShadow(True)

        # Remove old actor with same label if present
        if label in ctx.annotations:
            renderer._renderer.RemoveActor(ctx.annotations[label])

        ctx.annotations[label] = actor
        renderer._renderer.AddActor(actor)
        renderer.render()
        return f"Annotation '{label}' added at ({x}, {y}, {z})."

    result = renderer.run_on_main_thread(_impl)
    return _with_screenshot(result)


@mcp.tool()
def clear_annotations() -> str:
    """Remove all text annotations from the scene.

    Annotations are added with annotate(). This removes every label that
    was placed since the last clear.
    """
    ctx = _current_ctx()
    renderer = ctx.renderer
    def _impl():
        count = len(ctx.annotations)
        for actor in ctx.annotations.values():
            renderer._renderer.RemoveActor(actor)
        ctx.annotations.clear()
        renderer.render()
        return f"Cleared {count} annotation(s)."

    result = renderer.run_on_main_thread(_impl)
    return _with_screenshot(result)


@mcp.tool()
def render_chart(
    chart_type: str,
    node: str = "",
    field: str = "",
    data: str = "",
    title: str = "",
    x_label: str = "",
    y_label: str = "",
) -> list:
    """Render a 2D chart (histogram or line plot) and return it as an image.

    This tool produces a PNG chart from field data in the pipeline or from
    raw x/y values, and returns the image alongside a text description.

    Chart types:
      "histogram" -- histogram of a scalar field's values. Requires ``node``
                     and ``field``. Uses the pipeline to fetch the data.
      "line"      -- line plot. Either:
                       (a) pass ``data`` as a JSON string ``{"x": [...], "y": [...]}``
                           for arbitrary x/y series, or
                       (b) pass ``node`` and ``field`` to plot field values vs.
                           point index along a line probe output.

    Args:
        chart_type: One of "histogram" or "line".
        node: Pipeline node name to read field data from (empty = root source).
              Used by histogram and line (option b).
        field: Scalar field name to read from the node. Used by histogram and
               line (option b).
        data: JSON string containing ``{"x": [...], "y": [...]}`` arrays for a
              line plot (option a). Ignored for histogram.
        title: Optional chart title.
        x_label: Optional x-axis label.
        y_label: Optional y-axis label.

    Returns:
        A list of [description_text, Image(png)] on success, or an error string
        on failure.
    """
    import io
    import json

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from vtk.util.numpy_support import vtk_to_numpy

    chart_type_lower = chart_type.lower().strip()
    if chart_type_lower not in ("histogram", "line"):
        return f"Unknown chart_type '{chart_type}'. Choose 'histogram' or 'line'."

    fig = None
    try:
        fig, ax = plt.subplots(figsize=(8, 5))

        if chart_type_lower == "histogram":
            # --- histogram: pull field from VTK pipeline ---
            if not field:
                plt.close(fig)
                return "histogram requires 'field' to be specified."
            vtk_data, err = _get_data_or_error(node)
            if err:
                plt.close(fig)
                return err

            # Try point data first, then cell data
            arr = vtk_data.GetPointData().GetArray(field)
            if arr is None:
                arr = vtk_data.GetCellData().GetArray(field)
            if arr is None:
                plt.close(fig)
                available = [
                    vtk_data.GetPointData().GetArrayName(i)
                    for i in range(vtk_data.GetPointData().GetNumberOfArrays())
                ]
                return (
                    f"Field '{field}' not found. "
                    f"Available point arrays: {available}"
                )

            np_arr = vtk_to_numpy(arr)
            if np_arr.ndim > 1:
                # Vector field — use magnitude
                np_arr = np.linalg.norm(np_arr, axis=1)

            n_bins = min(50, max(10, int(np.sqrt(len(np_arr)))))
            ax.hist(np_arr, bins=n_bins, edgecolor="black", linewidth=0.4)
            ax.set_xlabel(x_label or field)
            ax.set_ylabel(y_label or "Count")
            ax.set_title(title or f"Histogram of {field}")

            description = (
                f"Histogram of '{field}' "
                f"(n={len(np_arr):,}, min={np_arr.min():.4g}, "
                f"max={np_arr.max():.4g}, mean={np_arr.mean():.4g})"
            )

        else:  # line
            if data:
                # --- line plot: from JSON x/y data ---
                try:
                    xy = json.loads(data)
                except json.JSONDecodeError as exc:
                    plt.close(fig)
                    return f"Could not parse 'data' as JSON: {exc}"

                if "x" not in xy or "y" not in xy:
                    plt.close(fig)
                    return "JSON 'data' must contain 'x' and 'y' keys."

                x_vals = np.asarray(xy["x"], dtype=float)
                y_vals = np.asarray(xy["y"], dtype=float)

                if len(x_vals) != len(y_vals):
                    plt.close(fig)
                    return (
                        f"x and y arrays must have equal length "
                        f"(got {len(x_vals)} vs {len(y_vals)})."
                    )

                ax.plot(x_vals, y_vals, linewidth=1.5)
                ax.set_xlabel(x_label or "x")
                ax.set_ylabel(y_label or "y")
                ax.set_title(title or "Line Plot")
                description = (
                    f"Line plot: {len(x_vals)} points, "
                    f"x=[{x_vals.min():.4g}, {x_vals.max():.4g}], "
                    f"y=[{y_vals.min():.4g}, {y_vals.max():.4g}]"
                )

            else:
                # --- line plot: field values from pipeline node vs. index ---
                if not field:
                    plt.close(fig)
                    return (
                        "line chart requires either 'data' (JSON x/y) "
                        "or both 'node' and 'field'."
                    )
                vtk_data, err = _get_data_or_error(node)
                if err:
                    plt.close(fig)
                    return err

                arr = vtk_data.GetPointData().GetArray(field)
                if arr is None:
                    arr = vtk_data.GetCellData().GetArray(field)
                if arr is None:
                    plt.close(fig)
                    available = [
                        vtk_data.GetPointData().GetArrayName(i)
                        for i in range(vtk_data.GetPointData().GetNumberOfArrays())
                    ]
                    return (
                        f"Field '{field}' not found. "
                        f"Available point arrays: {available}"
                    )

                np_arr = vtk_to_numpy(arr)
                if np_arr.ndim > 1:
                    np_arr = np.linalg.norm(np_arr, axis=1)

                x_vals = np.arange(len(np_arr))
                ax.plot(x_vals, np_arr, linewidth=1.5)
                ax.set_xlabel(x_label or "Index")
                ax.set_ylabel(y_label or field)
                ax.set_title(title or f"{field} vs. Index")
                description = (
                    f"Line plot of '{field}' vs. index "
                    f"(n={len(np_arr):,}, min={np_arr.min():.4g}, "
                    f"max={np_arr.max():.4g})"
                )

        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        # Render to PNG bytes
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120)
        plt.close(fig)
        buf.seek(0)
        png_bytes = buf.read()

        return [description, Image(data=png_bytes, format="png")]

    except Exception as exc:
        logger.exception("render_chart() failed")
        if fig is not None:
            try:
                plt.close(fig)
            except Exception:
                pass
        return f"render_chart error: {type(exc).__name__}: {exc}"


@mcp.tool()
def get_examples() -> str:
    """Get example pipeline patterns for common visualization tasks.

    Use this to see how to build various types of visualizations.
    Substitute your own file names, field names, and value ranges.
    Use describe_data() and get_statistics() to find the right values.
    """
    return '''=== Common Pipeline Patterns ===

These patterns are generic — substitute your own file names, field names,
and value ranges. Use describe_data() and get_statistics() to find the
right values for your dataset.

1. LOAD AND SHOW A FIELD:
data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
show(data, "field", color_by="fieldname", scalar_range=(lo, hi))
scene_preset("dark")

2. EXTRACT A SURFACE SLICE (e.g., ground plane of a structured grid):
surface = filter("vtkExtractGrid", input=data, VOI=[0,NX,0,NY,0,0])
show(surface, "surface", color_by="fieldname", scalar_range=(lo, hi), lut="cool_to_warm")

2b. EXTRACT A SUB-REGION BY PHYSICAL COORDINATES (no index guessing needed):
# Use describe_data() or get_bounds() to find the physical bounds, then:
region = extract_region(input=data, bounds=[xmin, xmax, ymin, ymax, zmin, zmax])
show(region, "region", color_by="fieldname", scalar_range=(lo, hi))
# Works for vtkStructuredGrid and vtkImageData; picks the right VTK filter automatically.
# Use voi= if you already know the grid indices:
# region = extract_region(input=data, voi=[imin, imax, jmin, jmax, kmin, kmax])

3. ISOSURFACE:
# Use suggest_isosurface() to find good values
iso = contour(input=data, ContourBy="fieldname", Isosurfaces=[value])
show(iso, "iso", color_by="fieldname", scalar_range=(lo, hi))

4. THRESHOLD (extract a value range):
region = threshold(input=data, ThresholdBy="fieldname", ThresholdRange=[lo, hi])
show(region, "region", color_by="fieldname", scalar_range=(lo, hi))

5. CROSS-SECTION SLICE:
cut = slice(input=data, origin=(x, y, z), normal=(1, 0, 0))
show(cut, "section", color_by="fieldname", scalar_range=(lo, hi), opacity=0.5)

6. STREAMLINES (vector field):
# First compute a vector from scalar components
velocity = compute_velocity(input=data, components=("vx", "vy", "vz"), result="velocity")
# Create seeds — use a line, plane, or seeds_near()
line_seed = source("vtkLineSource", Point1=(x1,y1,z1), Point2=(x2,y2,z2), Resolution=30)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=line_seed, Vectors="velocity", IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=500)
tubes = tube(input=streams, Radius=1.0, NumberOfSides=8)
show(tubes, "flow", color_by="velocity", opacity=0.7)

7. STREAMLINES WITH PLANAR SEED GRID:
plane_seeds = source("vtkPlaneSource",
    Origin=(x0,y0,z0), Point1=(x1,y1,z1), Point2=(x2,y2,z2),
    XResolution=10, YResolution=8)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=plane_seeds, Vectors="velocity", IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=500)
tubes = tube(input=streams, Radius=1.0, NumberOfSides=8)
show(tubes, "flow", color_by="velocity", opacity=0.6)

8. VOLUME RENDERING (with explicit opacity):
# Use suggest_opacity() to get good opacity control points for your field
region = threshold(input=data, ThresholdBy="fieldname", ThresholdRange=[lo, hi])
show(region, "volume", representation="Volume", color_by="fieldname",
    scalar_range=(lo, hi), lut="cool_to_warm",
    opacity_function=[(lo, 0.0), (mid, 0.1), (hi, 0.5)],
    volume_resolution=200)

9. VOLUME RENDERING (image data — no resampling needed):
data = source("vtkXMLImageDataReader", FileName="data/volume.vti")
show(data, "vol", representation="Volume", color_by="Scalars_",
    scalar_range=(0, 255), lut="grayscale",
    opacity_function=[(0, 0.0), (30, 0.0), (80, 0.01), (120, 0.05), (180, 0.2), (255, 0.6)],
    gradient_opacity=True)

10. RAW BINARY VOLUME:
data = raw_source("data/volume.raw",
    dimensions=(256, 256, 128), scalar_type="unsigned_short")
show(data, "vol", representation="Volume", opacity_function="ct_bone",
    gradient_opacity=True)

11. VECTOR GLYPHS (arrows):
velocity = compute_velocity(input=data, components=("vx","vy","vz"), result="velocity")
speed = compute_magnitude(input=data, components=("vx","vy","vz"), result="speed")
sub = filter("vtkExtractGrid", input=speed, VOI=[...], SampleRate=[8,8,2])
arrow = source("vtkArrowSource", TipResolution=6, ShaftResolution=6)
glyphs = filter("vtkGlyph3D", input=sub,
    GlyphSource=arrow, OrientationArray="velocity",
    ScaleArray="speed", ScaleFactor=5.0)
show(glyphs, "arrows", color_by="speed", scalar_range=(0, max_speed))

12. MULTIPLE ISOSURFACES (using loop):
values = [v1, v2, v3, v4]  # Use suggest_isosurface() to pick these
for val in values:
    iso = contour(input=data, ContourBy="fieldname", Isosurfaces=float(val))
    show(iso, f"iso_{val}", color_by="fieldname", scalar_range=(lo, hi))

13. VECTOR COMPONENT COLORING (e.g., show Z-velocity only):
# Color by a single component of a vector field instead of magnitude
# component accepts 0/1/2 or "x"/"y"/"z"
velocity = compute_velocity(input=data, components=("u", "v", "w"), result="velocity")
show(data, "vertical_wind", color_by="w", scalar_range=(-5, 5), lut="cool_to_warm")
# Or color by a component of an existing vector array:
show(velocity, "vz", color_by="velocity", component="z", lut="cool_to_warm")

14. MAKE VECTOR + CURL (general primitives):
# make_vector assembles scalars into a vector (same as compute_velocity):
vel = make_vector(input=data, components=("u", "v", "w"), result="velocity")
# curl computes the curl of any vector field:
vort = curl(vector_field=vel, result="vorticity", vector=True)         # 3-component curl
vort_mag = curl(vector_field=vel, result="vort_mag", vector=False)     # scalar magnitude
show(iso, "curl_mag", color_by="vort_mag", scalar_range=(lo, hi))
# compute_velocity and compute_vorticity are thin wrappers around these primitives.

15. SCENE ANNOTATIONS (label features with billboard text):
# After building a visualization, add text labels at 3D world positions.
# Labels always face the camera regardless of view angle.
annotate(x=500, y=300, z=50, label="Fire front", color="yellow", font_size=16)
annotate(x=200, y=400, z=10, label="Fuel bed", color="orange", font_size=14)
annotate(x=100, y=100, z=80, label="Smoke plume", color="white", font_size=14)
# Use get_bounds() or describe_data() to find coordinate ranges for placement.
# To remove all labels: clear_annotations()
# Labels persist across set_camera() calls — re-run annotate() to move a label,
# or call clear_annotations() and re-add all labels with new positions.

=== Tips ===
- Call describe_data() first for a full dataset overview
- Use get_statistics() to find field ranges before choosing scalar_range
- Use suggest_isosurface() to find meaningful contour values
- Use suggest_opacity() for histogram-guided volume rendering opacity
- Use make_vector/curl for general vector field construction and differential ops
- Use compute_velocity/vorticity/magnitude as convenient wrappers
- Use suggest_camera() for a good camera angle
- Use annotate() to label key features; clear_annotations() to start fresh
- Start simple and add layers incrementally
'''


def main():
    global _args, _renderer, _offscreen, _views, _current_view

    # Parse CLI args (only runs when main() is called, not on import)
    _args = _parse_args()
    _offscreen = _args.offscreen

    # Set up logging to file (stderr is used by MCP protocol)
    _log_dir = Path(".vislang")
    _log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.FileHandler(_log_dir / "server.log")],
    )

    logger.info("Starting VisLang server (offscreen=%s)", _offscreen)

    # Create the default "main" view and renderer
    _renderer = Renderer(offscreen=_offscreen)
    main_ctx = ViewContext("main", _renderer)
    main_ctx.history_dir.mkdir(parents=True, exist_ok=True)
    _views["main"] = main_ctx
    _current_view = "main"

    if _offscreen:
        logger.info("Running in offscreen mode")
        mcp.run()
    else:
        import threading

        logger.info("Running in interactive mode (MCP on background thread)")
        server_thread = threading.Thread(target=mcp.run, daemon=True)
        server_thread.start()
        _renderer.run_event_loop()


if __name__ == "__main__":
    main()
