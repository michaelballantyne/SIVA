"""MCP server for VisLang - declarative VTK visualization via conversation."""

import os
import traceback
from pathlib import Path
from mcp.server.fastmcp import FastMCP, Image

from .renderer import Renderer
from .dsl import interpret
from . import queries

# Initialize
mcp = FastMCP("VisLang")

# Global state
_renderer = Renderer()
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
    global _vtk_objects, _current_code

    try:
        vtk_objs, node_statuses, show_statuses, builder = interpret(code, _renderer)
        _vtk_objects = vtk_objs
        _current_code = code

        # Take screenshot
        screenshot_path = _renderer.screenshot(".vislang/latest.png")
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
        return f"DSL syntax error: {e}\n\nCheck your pipeline code for syntax issues."
    except Exception as e:
        tb = traceback.format_exc()
        # Extract just the last few lines of traceback
        tb_lines = tb.strip().split("\n")
        short_tb = "\n".join(tb_lines[-3:])
        return f"Pipeline error: {type(e).__name__}: {e}\n\n{short_tb}"


@mcp.tool()
def screenshot() -> Image:
    """Render the current scene and return the image.

    Call this after set_pipeline to see the current visualization.
    """
    _renderer.render()
    path = _renderer.screenshot(".vislang/latest.png")
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
def suggest_camera(style: str = "overview") -> str:
    """Suggest a camera position based on visible actors.

    Styles: "overview" (default), "closeup", "top_down", "side"

    Returns camera parameters you can paste into set_pipeline's camera() call.
    """
    result = _renderer.suggest_camera(style)
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
    lines.append("  show(node, name, color_by=, scalar_range=, lut=, opacity=, ...)")
    lines.append("  camera(position=, focal_point=, up=, zoom=)")
    lines.append("  background(r, g, b)")

    return "\n".join(lines)


@mcp.tool()
def get_examples() -> str:
    """Get example pipeline patterns for common visualization tasks.

    Use this to see how to build various types of visualizations.
    """
    return '''=== Common Pipeline Patterns ===

1. BASIC TERRAIN + FIRE:
data = source("vtkXMLStructuredGridReader", FileName="output.30000.vts")
terrain = filter("vtkExtractGrid", input=data, VOI=[0,599,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta", scalar_range=(350.0, 1200.0), lut="fire")
camera(position=(80, -600, 500), focal_point=(80, -10, 160), up=(0, 0, 1))
background(0.03, 0.03, 0.08)

2. ADD STREAMLINES (with auto-seed):
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat", ResultArrayName="velocity")
auto_seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=auto_seeds, Vectors="velocity", IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=600, InitialIntegrationStep=0.3)
tubes = filter("vtkTubeFilter", input=streams, Radius=1.5, NumberOfSides=8)
show(tubes, "wind", color_by="u", scalar_range=(-10, 25), lut="wind", opacity=0.7)

3. CROSS-SECTION SLICE:
yz_cut = slice(input=data, origin=(80, 0, 0), normal=(1, 0, 0))
show(yz_cut, "section", color_by="theta", scalar_range=(298, 600), lut="fire", opacity=0.5)

4. VORTICITY ANALYSIS:
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat", ResultArrayName="velocity")
vorticity = filter("vtkCellDerivatives", input=velocity,
    VectorMode="ComputeVorticity", TensorMode="PassTensors")
vort_pts = filter("vtkCellDataToPointData", input=vorticity)
vort_mag = filter("vtkArrayCalculator", input=vort_pts,
    AddVectorArrayName=["Vorticity"], Function="mag(Vorticity)",
    ResultArrayName="vorticity_magnitude")
vort_iso = filter("vtkContourFilter", input=vort_mag,
    ContourBy="vorticity_magnitude", Isosurfaces=[3.5])
show(vort_iso, "vortex", color=(0.3, 0.5, 1.0), opacity=0.4)

5. WIND GLYPHS:
speed = filter("vtkArrayCalculator", input=velocity,
    AddScalarArrayName=["u", "v", "w"],
    Function="sqrt(u*u + v*v + w*w)", ResultArrayName="speed")
sub = filter("vtkExtractGrid", input=speed, VOI=[220,380,200,300,0,12], SampleRate=[8,8,2])
arrow = source("vtkArrowSource", TipResolution=6, ShaftResolution=6)
glyphs = filter("vtkGlyph3D", input=sub,
    GlyphSource=arrow, OrientationArray="velocity",
    ScaleArray="speed", ScaleFactor=6.0)
show(glyphs, "arrows", color_by="speed", scalar_range=(0, 20), lut="wind")

=== Tips ===
- Always call get_array_info() first to see available fields
- Use seeds_near() instead of manually placing streamline seeds
- Use suggest_camera() to get a good camera angle
- Start simple and add layers incrementally
'''


if __name__ == "__main__":
    mcp.run()
