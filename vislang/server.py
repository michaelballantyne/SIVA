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

# Set up logging to file (stderr is used by MCP protocol)
_log_dir = Path(".vislang")
_log_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[logging.FileHandler(_log_dir / "server.log")],
)
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


_args = _parse_args()
logger.info("Starting VisLang server (offscreen=%s)", _args.offscreen)

# Initialize
mcp = FastMCP(
    "VisLang",
    instructions="""VisLang: Declarative VTK scientific visualization via conversation.

WORKFLOW:
1. Call list_data_files() and describe_data() to see what's available
2. Write pipeline code to pipeline.py, then call set_pipeline()
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
  terrain-following grids
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
- Camera too far/close: use suggest_camera("overview") or set_camera()

Call list_data_files() to see available datasets.

Available tools: set_pipeline, screenshot, describe_data, get_array_info,
get_field_summary, get_node_info, get_bounds, get_statistics, get_histogram,
get_spatial_extent, sample_point, sample_line, get_ground_z, suggest_scalar_range,
suggest_opacity, suggest_isosurface, suggest_camera, quick_start,
set_camera, set_opacity, set_color_range, set_background, set_window_size,
toggle_visibility, list_actors, get_actor_info,
list_data_files, list_capabilities, list_versions, get_examples,
get_pipeline, restore_version, reset_pipeline, export_standalone,
benchmark_pipeline""",
)

# Global state
_renderer = Renderer(offscreen=_args.offscreen)
_vtk_objects = {}  # name -> vtk algorithm
_current_code = ""
_version = 0
_history_dir = Path(".vislang/history")

# Ensure directories exist
_history_dir.mkdir(parents=True, exist_ok=True)


def _get_data(node_name=""):
    """Get VTK data output for a named node, or root source."""
    if node_name and node_name in _vtk_objects:
        obj = _vtk_objects[node_name]
        obj.Update()
        return obj.GetOutput()
    if node_name and node_name not in _vtk_objects:
        available = sorted(_vtk_objects.keys())
        return None  # caller handles this
    # Return the first source's output if no name given
    for name, obj in _vtk_objects.items():
        obj.Update()
        return obj.GetOutput()
    return None


def _available_nodes_hint():
    if _vtk_objects:
        return f"Available nodes: {sorted(_vtk_objects.keys())}"
    return "No pipeline is active. Call set_pipeline() first to load data."


def _save_version(code, screenshot_path):
    """Save pipeline spec and screenshot to version history."""
    global _version
    _version += 1
    ver_dir = _history_dir / f"v{_version:04d}"
    ver_dir.mkdir(parents=True, exist_ok=True)
    (ver_dir / "pipeline.py").write_text(code)
    if screenshot_path and os.path.exists(screenshot_path):
        import shutil
        shutil.copy2(screenshot_path, ver_dir / "screenshot.png")
    return _version


def _auto_screenshot():
    """Capture and return an Image of the current scene for auto-screenshot.

    Uses run_on_main_thread to ensure correctness in both offscreen and
    interactive modes.
    """
    try:
        def _take():
            _renderer.render()
            return _renderer.screenshot(".vislang/latest.png")
        path = _renderer.run_on_main_thread(_take)
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
def set_pipeline(file: str = "pipeline.py") -> str:
    """Execute a VisLang DSL pipeline from a file. Clears the scene and rebuilds.

    Write your pipeline code to the file first, then call this tool.
    The code uses builder functions: source(), filter(), show(), camera(), background().
    Returns a status report with per-node output info.

    Args:
        file: Path to the pipeline .py file (default: pipeline.py)
    """
    try:
        code = Path(file).read_text()
    except FileNotFoundError:
        return f"File not found: {file}\n\nWrite your pipeline code to this file first, then call set_pipeline()."
    except Exception as e:
        return f"Error reading {file}: {e}"
    result = _renderer.run_on_main_thread(lambda: _set_pipeline_impl(code))
    return _with_screenshot(result)


def _set_pipeline_impl(code: str) -> str:
    global _vtk_objects, _current_code

    logger.info("set_pipeline called (%d chars)", len(code))
    t0 = time.monotonic()
    try:
        vtk_objs, node_statuses, show_statuses, builder = interpret(code, _renderer)
        t_interpret = time.monotonic() - t0
        logger.info("Pipeline interpreted in %.2fs: %d nodes, %d show directives",
                     t_interpret, len(vtk_objs), len(show_statuses))
        _vtk_objects = vtk_objs
        _current_code = code

        # Take screenshot
        t_ss = time.monotonic()
        screenshot_path = _renderer.screenshot(".vislang/latest.png")
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
        cam = _renderer.get_camera_state()
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
        if show_statuses and not any(n.endswith("_bar") for n in _renderer._actors):
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
    readers = {
        "vts": "vtkXMLStructuredGridReader",
        "vti": "vtkXMLImageDataReader",
        "vtp": "vtkXMLPolyDataReader",
        "vtu": "vtkXMLUnstructuredGridReader",
        "vtr": "vtkXMLRectilinearGridReader",
    }
    reader_class = readers.get(ext)
    if reader_class is None:
        return (f"Unknown file extension '.{ext}'. "
                f"Supported: {sorted(readers.keys())}. "
                "For .raw files, use raw_source() in set_pipeline().")

    # Load the data to inspect it
    try:
        from .filters import create_vtk_filter
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
def extract_component(node_name: str, field: str, component: str, result_name: str = "") -> str:
    """Extract a single component from a vector field as a new scalar array.

    This modifies the named node's output in-place, adding a new scalar array.
    Useful for isolating X/Y/Z components of velocity, vorticity, etc.

    Args:
        node_name: Name of the pipeline variable holding the data.
        field: Name of the vector field (e.g., "velocity").
        component: Component index ("0","1","2") or name ("x","y","z").
        result_name: Name for the new scalar. Defaults to "{field}_{component}".
    """
    if node_name not in _vtk_objects:
        available = sorted(_vtk_objects.keys())
        return f"Node '{node_name}' not found. Available: {available}"

    # Parse component
    comp = component.strip().lower()
    _name_map = {"x": 0, "y": 1, "z": 2}
    if comp in _name_map:
        comp_idx = _name_map[comp]
        comp_label = comp
    else:
        try:
            comp_idx = int(comp)
            comp_label = {0: "x", 1: "y", 2: "z"}.get(comp_idx, str(comp_idx))
        except ValueError:
            return f"Invalid component '{component}'. Use 0/1/2 or x/y/z."

    if not result_name:
        result_name = f"{field}_{comp_label}"

    try:
        from .filters import extract_component as _extract_comp
        _, status = _extract_comp(_vtk_objects[node_name], field, comp_idx, result_name)
        return (
            f"Extracted component {comp_idx} of '{field}' as '{result_name}'.\n"
            f"Range: [{status['range'][0]:.6g}, {status['range'][1]:.6g}], "
            f"{status['num_tuples']} tuples.\n"
            f"You can now use '{result_name}' in color_by, threshold, etc."
        )
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def reset_pipeline() -> str:
    """Clear the entire scene and reset to empty state.

    Use this to start fresh without restarting the server.
    """
    global _vtk_objects, _current_code
    def _impl():
        global _vtk_objects, _current_code
        _renderer.clear()
        _vtk_objects = {}
        _current_code = ""
        _renderer.render()
        return "Pipeline cleared. Scene is empty."
    result = _renderer.run_on_main_thread(_impl)
    return _with_screenshot(result)


@mcp.tool()
def screenshot() -> Image:
    """Render the current scene and return the image.

    Call this after set_pipeline to see the current visualization.
    """
    def _take():
        _renderer.render()
        return _renderer.screenshot(".vislang/latest.png")
    path = _renderer.run_on_main_thread(_take)
    return Image(path=path)


@mcp.tool()
def get_array_info(node: str = "") -> str:
    """List all arrays on a node's output (or root data source if node is empty).

    Returns array names, types, component counts, and value ranges.
    Use this first to understand what fields are available before building visualizations.
    """
    data = _get_data(node)
    if data is None:
        if node:
            return f"Node '{node}' not found. {_available_nodes_hint()}"
        return _available_nodes_hint()
    return queries.get_array_info(data)


@mcp.tool()
def describe_data(node: str = "") -> str:
    """Get a comprehensive overview of a dataset: dimensions, bounds, all fields with statistics.

    This is the recommended first call after loading data. Returns everything
    you need to start building a visualization, including per-field percentiles
    (p1, p25, p50, p75, p99), distribution shape, and coordinate info.
    No follow-up calls needed for basic exploration.
    """
    data = _get_data(node)
    if data is None:
        if node:
            return f"Node '{node}' not found. {_available_nodes_hint()}"
        return _available_nodes_hint()

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
    lines.append(f"  Call quick_start(\"{node or 'filename.ext'}\") for a starter pipeline")
    lines.append("  Use suggest_isosurface(node, field) for contour values")
    lines.append("  Use suggest_opacity(node, field) for volume rendering opacity")

    return "\n".join(lines)


@mcp.tool()
def get_field_summary(node: str, field: str) -> str:
    """Get comprehensive summary of a field: stats, percentiles, and opacity suggestion.

    Combines get_statistics + suggest_scalar_range + suggest_opacity in one call.
    Use this when exploring a field before visualization.
    """
    data = _get_data(node)
    if data is None:
        if node:
            return f"Node '{node}' not found. {_available_nodes_hint()}"
        return _available_nodes_hint()
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
    data = _get_data(node)
    if data is None:
        if node:
            return f"Node '{node}' not found. {_available_nodes_hint()}"
        return _available_nodes_hint()

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
    data = _get_data(node)
    if data is None:
        if node:
            return f"Node '{node}' not found. {_available_nodes_hint()}"
        return _available_nodes_hint()
    return queries.get_bounds(data)


@mcp.tool()
def get_statistics(node: str, field: str) -> str:
    """Get min, max, mean, std for a field on a node's output.

    Use this to understand value ranges before setting thresholds, isosurface values,
    or color map ranges.
    """
    data = _get_data(node)
    if data is None:
        if node:
            return f"Node '{node}' not found. {_available_nodes_hint()}"
        return _available_nodes_hint()
    return queries.get_statistics(data, field)


@mcp.tool()
def get_histogram(node: str, field: str, bins: int = 20) -> str:
    """Get a text histogram of a field's value distribution.

    Useful for understanding data distribution before choosing visualization parameters.
    """
    data = _get_data(node)
    if data is None:
        if node:
            return f"Node '{node}' not found. {_available_nodes_hint()}"
        return _available_nodes_hint()
    return queries.get_histogram(data, field, bins)


@mcp.tool()
def get_spatial_extent(node: str, field: str, min_value: float, max_value: float) -> str:
    """Find the bounding box where a field is within a given range.

    Useful for positioning seed points for streamlines, focusing cameras,
    or understanding where features are located in 3D space.
    """
    data = _get_data(node)
    if data is None:
        if node:
            return f"Node '{node}' not found. {_available_nodes_hint()}"
        return _available_nodes_hint()
    return queries.get_spatial_extent(data, field, min_value, max_value)


@mcp.tool()
def sample_point(node: str, x: float, y: float, z: float) -> str:
    """Sample field values at the nearest grid point to (x, y, z).

    Returns all field values at that location. Useful for understanding
    what's happening at a specific point in the simulation.
    """
    data = _get_data(node)
    if data is None:
        if node:
            return f"Node '{node}' not found. {_available_nodes_hint()}"
        return _available_nodes_hint()
    return queries.sample_point(data, x, y, z)


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
    data = _get_data(node)
    if data is None:
        if node:
            return f"Node '{node}' not found. {_available_nodes_hint()}"
        return _available_nodes_hint()

    if len(point1) != 3 or len(point2) != 3:
        return "point1 and point2 must each be [x, y, z] (3 values)."

    probe_output = queries.sample_line(data, tuple(point1), tuple(point2), resolution)
    return queries.get_line_probe_data(probe_output, fields)


@mcp.tool()
def get_ground_z(node: str, x: float, y: float) -> str:
    """Find ground-level z-coordinate at a given x,y position.

    IMPORTANT: This grid is terrain-following. Z-coordinates at ground level
    vary from ~1 to ~196 depending on x,y location. Use this before placing
    seed points for streamlines to ensure they are inside the grid.
    """
    data = _get_data(node)
    if data is None:
        if node:
            return f"Node '{node}' not found. {_available_nodes_hint()}"
        return _available_nodes_hint()
    return queries.get_ground_z(data, x, y)


@mcp.tool()
def suggest_scalar_range(node: str, field: str, percentile_low: float = 1.0, percentile_high: float = 99.0) -> str:
    """Suggest a useful scalar range for a field based on its value distribution.

    Returns percentile-based ranges that avoid extreme outliers compressing
    the colormap. Useful before setting scalar_range in show().
    """
    data = _get_data(node)
    if data is None:
        if node:
            return f"Node '{node}' not found. {_available_nodes_hint()}"
        return _available_nodes_hint()
    return queries.suggest_scalar_range(data, field, percentile_low, percentile_high)


@mcp.tool()
def suggest_opacity(node: str, field: str, scalar_range_min: float = None, scalar_range_max: float = None, max_opacity: float = 0.8) -> str:
    """Suggest opacity transfer function control points for volume rendering.

    Analyzes the field histogram to make common values transparent and rare
    values opaque. Returns control points you can paste into show()'s
    opacity_function parameter.
    """
    data = _get_data(node)
    if data is None:
        if node:
            return f"Node '{node}' not found. {_available_nodes_hint()}"
        return _available_nodes_hint()
    sr = None
    if scalar_range_min is not None and scalar_range_max is not None:
        sr = (scalar_range_min, scalar_range_max)
    return queries.suggest_opacity_function(data, field, scalar_range=sr, max_opacity=max_opacity)


@mcp.tool()
def suggest_isosurface(node: str, field: str, num_values: int = 3) -> str:
    """Suggest good isosurface values for a field.

    Analyzes the field histogram to find transition points that produce
    meaningful isosurfaces. Returns values you can use in Isosurfaces=[].
    """
    data = _get_data(node)
    if data is None:
        if node:
            return f"Node '{node}' not found. {_available_nodes_hint()}"
        return _available_nodes_hint()
    return queries.suggest_isosurface(data, field, num_values)


@mcp.tool()
def suggest_camera(style: str = "overview") -> str:
    """Suggest a camera position based on visible actors.

    Styles: "overview" (default), "closeup", "top_down", "side"

    Returns camera parameters you can paste into set_pipeline's camera() call.
    """
    result = _renderer.run_on_main_thread(lambda: _renderer.suggest_camera(style))
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
def set_camera(position: str = "", focal_point: str = "", up: str = "(0,0,1)", zoom: float = 0) -> str:
    """Set the camera position without rebuilding the pipeline.

    Much faster than modifying camera() in set_pipeline. Pass coordinates
    as comma-separated strings, e.g. position="100,-500,400"
    """
    def _impl():
        kwargs = {}
        if position:
            kwargs["position"] = tuple(float(x) for x in position.split(","))
        if focal_point:
            kwargs["focal_point"] = tuple(float(x) for x in focal_point.split(","))
        if up:
            kwargs["up"] = tuple(float(x) for x in up.strip("() ").split(","))
        if zoom > 0:
            kwargs["zoom"] = zoom
        if not kwargs:
            return "Specify at least position or focal_point."
        _renderer.set_camera(**kwargs)
        _renderer.render()
        screenshot_path = _renderer.screenshot(".vislang/latest.png")
        cam = _renderer.get_camera_state()
        return (
            f"Camera updated.\n"
            f"  position={[round(x,1) for x in cam['position']]}\n"
            f"  focal_point={[round(x,1) for x in cam['focal_point']]}"
        )
    return _with_screenshot(_renderer.run_on_main_thread(_impl))


@mcp.tool()
def set_opacity(name: str, opacity: float) -> str:
    """Set the opacity of a named actor in the scene (0.0 = invisible, 1.0 = opaque).

    Fast way to adjust transparency without rebuilding the pipeline.
    """
    import vtk
    def _impl():
        actor = _renderer._actors.get(name)
        if actor is None:
            available = sorted(_renderer._actors.keys())
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
        _renderer.render()
        _renderer.screenshot(".vislang/latest.png")
        return f"'{name}' opacity set to {opacity}."
    return _with_screenshot(_renderer.run_on_main_thread(_impl))


@mcp.tool()
def set_colormap(name: str, lut: str = "", scalar_range_min: float = None, scalar_range_max: float = None) -> str:
    """Change the colormap of a named actor without rebuilding.

    Accepts preset names: "fire", "terrain", "wind", "cool_to_warm",
    "blue_to_red", "grayscale", "oxygen", "heat".
    Optionally update scalar range at the same time.
    """
    import vtk
    def _impl():
        actor = _renderer._actors.get(name)
        if actor is None:
            available = sorted(_renderer._actors.keys())
            return f"Actor '{name}' not found. Available: {available}"
        if isinstance(actor, vtk.vtkVolume):
            return "Use set_pipeline() to change volume colormaps."

        mapper = actor.GetMapper()
        if not mapper:
            return f"'{name}' has no mapper."

        sr = None
        if scalar_range_min is not None and scalar_range_max is not None:
            sr = (scalar_range_min, scalar_range_max)
            mapper.SetScalarRange(*sr)
        else:
            sr = mapper.GetScalarRange()

        if lut:
            from .colormaps import build_lut
            new_lut = build_lut(lut, scalar_range=sr)
            mapper.SetLookupTable(new_lut)

        _renderer.render()
        _renderer.screenshot(".vislang/latest.png")
        return f"'{name}' colormap set to '{lut}'" + (f" with range ({sr[0]}, {sr[1]})" if sr else "") + "."
    return _with_screenshot(_renderer.run_on_main_thread(_impl))


@mcp.tool()
def set_color_range(name: str, min_val: float, max_val: float) -> str:
    """Set the scalar color range of a named actor without rebuilding.

    Fast way to adjust the colormap range for better contrast.
    """
    import vtk
    def _impl():
        actor = _renderer._actors.get(name)
        if actor is None:
            available = sorted(_renderer._actors.keys())
            return f"Actor '{name}' not found. Available: {available}"
        if isinstance(actor, vtk.vtkVolume):
            # For volumes, update the color and opacity transfer functions
            return f"Use set_pipeline() to change volume scalar range (requires transfer function rebuild)."
        mapper = actor.GetMapper()
        if mapper:
            mapper.SetScalarRange(min_val, max_val)
        _renderer.render()
        _renderer.screenshot(".vislang/latest.png")
        return f"'{name}' scalar range set to ({min_val}, {max_val})."
    return _with_screenshot(_renderer.run_on_main_thread(_impl))


@mcp.tool()
def get_actor_info(name: str) -> str:
    """Get information about a specific actor/volume in the scene.

    Shows type, visibility, bounds, scalar range, and opacity.
    """
    import vtk
    actor = _renderer._actors.get(name)
    if actor is None:
        available = sorted(_renderer._actors.keys())
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
    def _impl():
        actor = _renderer._actors.get(name)
        if actor is None:
            available = sorted(_renderer._actors.keys())
            return f"Actor '{name}' not found. Available: {available}"
        current = actor.GetVisibility()
        actor.SetVisibility(0 if current else 1)
        _renderer.render()
        _renderer.screenshot(".vislang/latest.png")
        state = "visible" if actor.GetVisibility() else "hidden"
        return f"'{name}' is now {state}."
    return _with_screenshot(_renderer.run_on_main_thread(_impl))


@mcp.tool()
def set_window_size(width: int, height: int) -> str:
    """Set the render window size for higher/lower resolution screenshots.

    Default is 1920x1080. Use 3840x2160 for 4K publication quality.
    """
    def _impl():
        _renderer._render_window.SetSize(width, height)
        _renderer.render()
        return f"Window size set to {width}x{height}."
    return _with_screenshot(_renderer.run_on_main_thread(_impl))


@mcp.tool()
def set_background(r: float, g: float, b: float) -> str:
    """Set the scene background color without rebuilding the pipeline.

    Values are 0.0-1.0 RGB. Common presets: dark=(0.02,0.02,0.06),
    light=(0.85,0.85,0.9), black=(0,0,0), white=(1,1,1).
    """
    def _impl():
        _renderer.set_background(r, g, b)
        _renderer.render()
        _renderer.screenshot(".vislang/latest.png")
        return f"Background set to ({r}, {g}, {b})."
    return _with_screenshot(_renderer.run_on_main_thread(_impl))


@mcp.tool()
def list_actors() -> str:
    """List all actors/volumes in the current scene with their visibility and type.

    Useful for knowing what layers exist for toggle_visibility/set_opacity.
    """
    import vtk
    if not _renderer._actors:
        return "No actors in scene. Call set_pipeline() first."
    lines = ["Scene actors:"]
    for name, actor in sorted(_renderer._actors.items()):
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
    versions = sorted(_history_dir.glob("v*/pipeline.py"))
    if not versions:
        return "No versions saved yet. Call set_pipeline() to create the first version."
    lines = [f"Pipeline versions ({len(versions)} total):"]
    for v in versions:
        ver_num = int(v.parent.name[1:])
        code = v.read_text()
        first_line = code.strip().split("\n")[0][:80] if code.strip() else "(empty)"
        has_screenshot = (v.parent / "screenshot.png").exists()
        lines.append(f"  v{ver_num}: {first_line}{'...' if len(first_line) >= 80 else ''}"
                     f" {'[screenshot]' if has_screenshot else ''}")
    lines.append(f"\nCurrent: v{_version}")
    lines.append("Use restore_version(n) to restore a previous version.")
    return "\n".join(lines)


@mcp.tool()
def restore_version(version: int) -> str:
    """Restore a previous pipeline version by number.

    Use this to go back to an earlier visualization state.
    """
    ver_dir = _history_dir / f"v{version:04d}"
    spec_file = ver_dir / "pipeline.py"
    if not spec_file.exists():
        # List available versions
        versions = sorted(_history_dir.glob("v*/pipeline.py"))
        if versions:
            nums = [int(v.parent.name[1:]) for v in versions]
            return f"Version {version} not found. Available: {nums}"
        return f"Version {version} not found. No versions saved yet."
    code = spec_file.read_text()
    return set_pipeline(code)


@mcp.tool()
def get_pipeline() -> str:
    """Return the current DSL pipeline spec text.

    Use this to see the current pipeline and modify it incrementally.
    """
    if not _current_code:
        return "No pipeline set yet. Use set_pipeline() to create one."
    header = f"# Pipeline v{_version}\n"
    return header + _current_code


@mcp.tool()
def benchmark_pipeline(file: str = "pipeline.py") -> str:
    """Time a pipeline build without rendering or taking screenshots.

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
    if not _current_code:
        return "No pipeline to export. Use set_pipeline() first."

    script = f'''#!/usr/bin/env python3
"""Standalone visualization generated by VisLang."""
import sys
sys.path.insert(0, ".")
from vislang.renderer import Renderer
from vislang.dsl import interpret

renderer = Renderer(1920, 1080, offscreen="--offscreen" in sys.argv)

code = """
{_current_code}
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
    """
    from .filters import WHITELISTED_CLASSES
    from .colormaps import PRESETS

    lines = ["=== Available VTK Classes ==="]
    sources = [k for k in sorted(WHITELISTED_CLASSES.keys()) if "Source" in k or "Reader" in k]
    filters = [k for k in sorted(WHITELISTED_CLASSES.keys()) if k not in sources]
    lines.append(f"Sources: {sources}")
    lines.append(f"Filters: {filters}")

    lines.append("")
    lines.append("=== Colormap Presets ===")
    for name in sorted(PRESETS.keys()):
        lines.append(f"  \"{name}\"")

    lines.append("")
    lines.append("=== DSL Builder Functions ===")
    lines.append("  source(vtk_class, **props) -> NodeRef")
    lines.append("  filter(vtk_class, input=node, **props) -> NodeRef")
    lines.append("  contour(input=, ContourBy=, Isosurfaces=[])")
    lines.append("  calculator(input=, Function=, ResultArrayName=, AddScalarArrayName=[])")
    lines.append("  threshold(input=, ThresholdBy=, ThresholdRange=[])")
    lines.append("  extract_grid(input=, VOI=[], SampleRate=[])")
    lines.append("  stream_tracer(input=, SeedSource=, Vectors=, ...)")
    lines.append("  tube(input=, Radius=, NumberOfSides=)")
    lines.append("  glyph(input=, GlyphSource=, OrientationArray=, ScaleArray=, ScaleFactor=)")
    lines.append("  slice(input=, origin=(x,y,z), normal=(nx,ny,nz))")
    lines.append("  seeds_near(input=, field=, min_val=, max_val=, num_seeds=, offset_z=)")
    lines.append("  warp_vector(input=, ...)")
    lines.append("  mask_points(input=, OnRatio=, RandomMode=)")
    lines.append("  gradient(input=, GradientField=, ResultArrayName=)")
    lines.append("  compute_velocity(input=, components=('u','v','w'), result='velocity')")
    lines.append("  compute_magnitude(input=, components=('u','v','w'), result='speed')")
    lines.append("  compute_vorticity(input=, result='vorticity_magnitude', vector=False)")
    lines.append("    vector=True returns full 3-component vorticity vector")
    lines.append("  extract_component(input=, field=, component=0, result_name=)")
    lines.append("  compute_gradient_magnitude(input=, field=, result=)")
    lines.append("  clip(input=, origin=, normal=, inside_out=False)")
    lines.append("  clip_sphere(input=, center=, radius=, inside_out=True)")
    lines.append("  clip_box(input=, bounds=(xmin,xmax,ymin,ymax,zmin,zmax))")
    lines.append("  probe(input=, source=node)")
    lines.append("  resample_to_image(input=, dimensions=(nx,ny,nz))")
    lines.append("  raw_source(filename, dimensions=, scalar_type=, header_size=)")
    lines.append("  cell_to_point(input=)")
    lines.append("  point_to_cell(input=)")
    lines.append("  outline(input=)")
    lines.append("  elevation(input=, low_point=, high_point=)")
    lines.append("  isosurface(input=, ...)")
    lines.append("  surface(input=)")
    lines.append("  smooth(input=, iterations=20)")
    lines.append("  warp_scalar(input=, ...)")
    lines.append("  show(node, name, color_by=, scalar_range=, lut=, opacity=, component=, ...)")
    lines.append("    component: color by a single vector component (0/1/2 or 'x'/'y'/'z')")
    lines.append("    When component is set, scalar_range auto-detects from that component")
    lines.append("  camera(position=, focal_point=, up=, zoom=)")
    lines.append("  background(r, g, b)")
    lines.append("  scene_preset('dark'|'light'|'black'|'white')")
    lines.append("  title(text, position=, font_size=, color=)")

    from .colormaps import OPACITY_PRESETS
    lines.append("")
    lines.append("=== Volume Rendering ===")
    lines.append("  show(node, name, representation=\"Volume\", color_by=, scalar_range=,")
    lines.append("    lut=, opacity=, opacity_function=, volume_resolution=256,")
    lines.append("    gradient_opacity=True, shade=True, clip_planes=[...])")
    lines.append(f"  Opacity presets: \"ramp_up\", \"gaussian\", \"step\", {sorted(OPACITY_PRESETS.keys())}")
    lines.append("  Use suggest_opacity() tool to get histogram-guided opacity functions")
    lines.append("  Use suggest_opacity() or explicit opacity_function=[(val, opacity), ...]")

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


@mcp.tool()
def get_examples() -> str:
    """Get example pipeline patterns for common visualization tasks.

    Use this to see how to build various types of visualizations.
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

=== Tips ===
- Call describe_data() first for a full dataset overview
- Use get_statistics() to find field ranges before choosing scalar_range
- Use suggest_isosurface() to find meaningful contour values
- Use suggest_opacity() for histogram-guided volume rendering opacity
- Use compute_velocity/vorticity/magnitude for common derived fields
- Use suggest_camera() for a good camera angle
- Start simple and add layers incrementally
'''


def main():
    if _args.offscreen:
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
