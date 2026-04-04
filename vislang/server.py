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

STRATEGY - Build incrementally, not all at once:
1. Call get_array_info() to see what fields and ranges exist
2. Start with JUST data + terrain: set_pipeline, then screenshot() to verify
3. Add ONE layer at a time (fire, then streamlines, then vorticity...)
4. After each set_pipeline, check screenshot() before adding more
5. Use get_pipeline() to see current code, modify it, resubmit

Do NOT try to build a complex multi-layer pipeline in one shot. It will
likely fail due to wrong value ranges, bad seed positions, or field name
typos, and debugging is harder.

CRITICAL RULES:
- Always query field ranges with get_statistics() BEFORE choosing isosurface
  values, threshold ranges, or scalar_range for coloring
- This is a terrain-following grid: z-coordinates at ground vary by location.
  Use get_ground_z() or seeds_near() instead of guessing z values
- Use seeds_near() for streamline seeds, not manual coordinates
- Call get_examples() to see working pipeline patterns you can copy
- Known fields (theta, rhof_1, O2, u, v, w) have auto-defaults for colormap
  and scalar_range -- you can omit lut= and scalar_range= for these fields

VOLUME RENDERING:
- Use representation="Volume" in show() for volumetric rendering
- Call suggest_opacity() to get histogram-guided opacity transfer functions
- Use gradient_opacity=True for edge-enhanced volume rendering
- Threshold data first to focus on regions of interest

Call list_data_files() to see available datasets.

Available tools: set_pipeline, screenshot, get_array_info, get_field_summary,
get_bounds, get_statistics, get_histogram, get_spatial_extent, sample_point,
get_ground_z, suggest_scalar_range, suggest_opacity, suggest_isosurface,
suggest_camera, set_camera, set_opacity, toggle_visibility,
list_data_files, list_capabilities, get_examples, get_pipeline, restore_version""",
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


@mcp.tool()
def set_pipeline(code: str) -> str:
    """Execute a VisLang DSL pipeline spec. Clears the scene and rebuilds from the code.

    The code uses builder functions: source(), filter(), show(), camera(), background().
    Returns a status report with per-node output info.

    Example:
        data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")
        show(data, "terrain", color_by="theta", scalar_range=(300, 1200))
        camera(position=(0, -500, 500), focal_point=(0, 0, 0), up=(0, 0, 1))
        background(0.15, 0.15, 0.2)
    """
    return _renderer.run_on_main_thread(lambda: _set_pipeline_impl(code))


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

        return "\n".join(report_lines)

    except SyntaxError as e:
        logger.warning("DSL syntax error: %s", e)
        return f"DSL syntax error: {e}\n\nCheck your pipeline code for syntax issues."
    except Exception as e:
        logger.exception("Pipeline error")
        tb = traceback.format_exc()
        # Extract just the last few lines of traceback
        tb_lines = tb.strip().split("\n")
        short_tb = "\n".join(tb_lines[-3:])
        return f"Pipeline error: {type(e).__name__}: {e}\n\n{short_tb}"


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
    return _renderer.run_on_main_thread(_impl)


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
    """Get a comprehensive overview of a dataset: dimensions, bounds, all fields.

    This is the recommended first call after loading data. Returns everything
    you need to start building a visualization.
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
    lines.append(f"  Bounds: X=[{bounds[0]:.1f}, {bounds[1]:.1f}], Y=[{bounds[2]:.1f}, {bounds[3]:.1f}], Z=[{bounds[4]:.1f}, {bounds[5]:.1f}]")

    lines.append("")
    lines.append("=== Fields ===")
    pd = data.GetPointData()
    from .colormaps import FIELD_DEFAULTS
    for i in range(pd.GetNumberOfArrays()):
        arr = pd.GetArray(i)
        name = pd.GetArrayName(i)
        ncomp = arr.GetNumberOfComponents()
        rng = arr.GetRange() if ncomp == 1 else None
        dtype = arr.GetDataTypeAsString()
        field_info = f"  {name}: {dtype}"
        if ncomp > 1:
            field_info += f", {ncomp} components"
        else:
            field_info += f", range=[{rng[0]:.6g}, {rng[1]:.6g}]"
        if name in FIELD_DEFAULTS:
            field_info += " (has auto-defaults)"
        lines.append(field_info)

    lines.append("")
    lines.append("=== Quick Start ===")
    lines.append("  Use get_field_summary(node, field) for detailed field analysis")
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
    return _renderer.run_on_main_thread(_impl)


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
    return _renderer.run_on_main_thread(_impl)


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
    return _renderer.run_on_main_thread(_impl)


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
    return _renderer.run_on_main_thread(_impl)


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
    lines.append("  compute_vorticity(input=, result='vorticity_magnitude')")
    lines.append("  clip(input=, origin=, normal=, inside_out=False)")
    lines.append("  probe(input=, source=node)")
    lines.append("  resample_to_image(input=, dimensions=(nx,ny,nz))")
    lines.append("  raw_source(filename, dimensions=, scalar_type=, header_size=)")
    lines.append("  show(node, name, color_by=, scalar_range=, lut=, opacity=, ...)")
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
    lines.append("  Known fields auto-apply colormap + range (see FIELD_DEFAULTS)")

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

1. BASIC TERRAIN + FIRE (field defaults auto-apply colormap + range):
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")
terrain = filter("vtkExtractGrid", input=data, VOI=[0,599,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta")
camera(position=(80, -600, 500), focal_point=(80, -10, 160), up=(0, 0, 1))
scene_preset("dark")

2. ADD STREAMLINES (with auto-seed):
velocity = compute_velocity(input=data)
auto_seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=auto_seeds, Vectors="velocity", IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=600, InitialIntegrationStep=0.3)
tubes = filter("vtkTubeFilter", input=streams, Radius=1.5, NumberOfSides=8)
show(tubes, "wind", color_by="u", scalar_range=(-10, 25), lut="wind", opacity=0.7)

3. CROSS-SECTION SLICE:
yz_cut = slice(input=data, origin=(80, 0, 0), normal=(1, 0, 0))
show(yz_cut, "section", color_by="theta", scalar_range=(298, 600), lut="fire", opacity=0.5)

4. VORTICITY ANALYSIS (simplified with compute_vorticity):
vort = compute_vorticity(input=data)
vort_iso = filter("vtkContourFilter", input=vort,
    ContourBy="vorticity_magnitude", Isosurfaces=[3.5])
show(vort_iso, "vortex", color=(0.3, 0.5, 1.0), opacity=0.4)

5. WIND GLYPHS:
velocity = compute_velocity(input=data)
speed = compute_magnitude(input=data, result="speed")
sub = filter("vtkExtractGrid", input=speed, VOI=[220,380,200,300,0,12], SampleRate=[8,8,2])
arrow = source("vtkArrowSource", TipResolution=6, ShaftResolution=6)
glyphs = filter("vtkGlyph3D", input=sub,
    GlyphSource=arrow, OrientationArray="velocity",
    ScaleArray="speed", ScaleFactor=6.0)
show(glyphs, "arrows", color_by="speed", scalar_range=(0, 20), lut="wind")

6. VOLUME RENDERED FIRE:
hot = filter("vtkThreshold", input=data, ThresholdBy="theta", ThresholdRange=[350.0, 1200.0])
show(hot, "fire_vol", representation="Volume", color_by="theta",
    scalar_range=(350.0, 1200.0), lut="fire",
    opacity_function=[(350, 0.0), (400, 0.02), (500, 0.1), (700, 0.3), (1000, 0.6), (1200, 0.8)],
    volume_resolution=200)

7. VOLUME RENDERED VORTICITY:
# (after building vort_mag from pattern 4 above)
show(vort_mag, "vorticity_vol", representation="Volume",
    color_by="vorticity_magnitude", scalar_range=(0.5, 5.0), lut="cool_to_warm",
    opacity_function=[(0.0, 0.0), (0.5, 0.0), (1.0, 0.01), (2.0, 0.05), (3.5, 0.2), (5.0, 0.5)],
    volume_resolution=150)

8. CT SCAN VOLUME RENDERING:
data = source("vtkXMLImageDataReader", FileName="data/ctBones.vti")
show(data, "ct_vol", representation="Volume", color_by="Scalars_",
    scalar_range=(0, 255), lut="grayscale",
    opacity_function=[(0, 0.0), (30, 0.0), (80, 0.01), (120, 0.05), (180, 0.2), (255, 0.6)],
    gradient_opacity=True)
bone = filter("vtkContourFilter", input=data, ContourBy="Scalars_", Isosurfaces=[140.0])
show(bone, "bone", color=(0.9, 0.85, 0.7), opacity=0.3)
camera(position=(400, -200, 300), focal_point=(128, 128, 128), up=(0, 0, 1))

=== Tips ===
- Always call get_array_info() first to see available fields
- Use seeds_near() instead of manually placing streamline seeds
- Use suggest_camera() to get a good camera angle
- Use suggest_opacity() to get histogram-guided opacity transfer functions
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
