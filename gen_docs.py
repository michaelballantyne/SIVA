#!/usr/bin/env python3
"""Generate Markdown documentation from VisLang source code.

Extracts:
- docs/dsl-reference.md    — DSL forms reference (pipeline .py files)
- docs/mcp-reference.md    — MCP tool reference (interactive operations)
- docs/instructions.md     — MCP server instructions string
- docs/getting-started.md  — Two-layer architecture walkthrough and examples
- README.md                — Project overview linking to the generated docs

Run from anywhere:
    python gen_docs.py

The script is idempotent — running it twice produces the same output.
"""

import inspect
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Resolve project root so the script works from any working directory
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Mock mcp and vislang.renderer so server.py imports cleanly without a
# display or MCP runtime (same pattern used in tests/test_server_tools.py)
# ---------------------------------------------------------------------------

def _stub_mcp_and_renderer():
    if "mcp" not in sys.modules:
        mcp_mock = MagicMock()
        fake_fastmcp = MagicMock()
        fake_fastmcp.tool.return_value = lambda f: f
        mcp_mock.server.fastmcp.FastMCP.return_value = fake_fastmcp
        mcp_mock.server.fastmcp.Image = MagicMock
        sys.modules["mcp"] = mcp_mock
        sys.modules["mcp.server"] = mcp_mock.server
        sys.modules["mcp.server.fastmcp"] = mcp_mock.server.fastmcp

    if "vislang.renderer" not in sys.modules:
        renderer_mock = MagicMock()
        sys.modules["vislang.renderer"] = renderer_mock


_stub_mcp_and_renderer()

import vislang.server as srv  # noqa: E402  (after stub)
import vislang.dsl as dsl     # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_signature(func):
    """Return a compact signature string like (node: str, field: str = 'x')."""
    try:
        sig = inspect.signature(func)
    except (ValueError, TypeError):
        return "()"
    parts = []
    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        annotation = ""
        if param.annotation is not inspect.Parameter.empty:
            ann = param.annotation
            if hasattr(ann, "__name__"):
                annotation = f": {ann.__name__}"
            else:
                annotation = f": {ann}"
                # Simplify common generics for readability
                annotation = annotation.replace("typing.", "")
        default = ""
        if param.default is not inspect.Parameter.empty:
            default = f" = {param.default!r}"
        parts.append(f"{pname}{annotation}{default}")
    return "(" + ", ".join(parts) + ")"


def _format_docstring(func):
    """Return the cleaned docstring, or an empty string."""
    doc = inspect.getdoc(func)
    return doc or ""


def _format_tool_entry(func):
    """Format a single MCP tool as a Markdown section."""
    name = func.__name__
    sig = _format_signature(func)
    doc = _format_docstring(func)
    lines = [f"### `{name}{sig}`", ""]
    if doc:
        lines.append(doc)
        lines.append("")
    return "\n".join(lines)


def _format_dsl_entry(name, func):
    """Format a single PipelineBuilder method as a Markdown section."""
    sig = _format_signature(func)
    doc = _format_docstring(func)
    lines = [f"### `{name}{sig}`", ""]
    if doc:
        lines.append(doc)
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Categorize tools
# ---------------------------------------------------------------------------

# These are the function names decorated with @mcp.tool() in server.py.
# Grouped manually to keep related tools together.

_QUERY_TOOLS = [
    "describe_data",
    "get_array_info",
    "get_field_summary",
    "get_node_info",
    "get_bounds",
    "get_statistics",
    "query_stats",
    "get_histogram",
    "get_spatial_extent",
    "sample_points",
    "profile",
    "get_ground_z",
    "suggest_scalar_range",
    "suggest_opacity",
    "suggest_isosurface",
    "suggest_camera",
]

_MUTATION_TOOLS = [
    "load",
    "set_pipeline",
    "reset_pipeline",
    "set_camera",
    "set_opacity",
    "set_colormap",
    "set_background",
    "set_window_size",
    "toggle_visibility",
    "make_vector",
    "curl",
    "annotate",
    "clear_annotations",
]

_META_TOOLS = [
    "screenshot",
    "camera_orbit",
    "quick_start",
    "list_actors",
    "get_actor_info",
    "list_versions",
    "get_pipeline",
    "restore_version",
    "export_standalone",
    "list_capabilities",
    "list_data_files",
    "get_examples",
    "get_dsl_reference",
    "new_view",
    "focus",
    "close_view",
    "list_views",
    "render_chart",
]


def _get_tool_func(name):
    """Look up a tool function from the server module."""
    return getattr(srv, name, None)


# ---------------------------------------------------------------------------
# DSL form groupings
# ---------------------------------------------------------------------------

_DSL_GROUPS = [
    ("Data Sources", [
        "source",
        "raw_source",
    ]),
    ("Filtering & Clipping", [
        "threshold",
        "contour",
        "isosurface",
        "slice",
        "clip",
        "clip_box",
        "clip_sphere",
        "extract_region",
        "extract_grid",
        "surface",
        "smooth",
    ]),
    ("Derived Fields", [
        "make_vector",
        "compute_velocity",
        "compute_magnitude",
        "compute_vorticity",
        "curl",
        "gradient",
        "compute_gradient_magnitude",
        "extract_component",
        "calculator",
    ]),
    ("Flow Visualization", [
        "stream_tracer",
        "seeds_near",
        "tube",
        "glyph",
        "mask_points",
        "line_probe",
    ]),
    ("Data Conversion", [
        "cell_to_point",
        "point_to_cell",
        "probe",
        "resample_to_image",
        "elevation",
        "outline",
        "warp_vector",
        "warp_scalar",
    ]),
    ("Display", [
        "show",
        "camera",
        "background",
        "scene_preset",
        "title",
    ]),
    ("Generic", [
        "filter",
    ]),
]


def _get_builder_methods():
    """Return dict of name -> func for all public PipelineBuilder methods."""
    return {
        name: func
        for name, func in inspect.getmembers(dsl.PipelineBuilder, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


# ---------------------------------------------------------------------------
# Generate docs/dsl-reference.md
# ---------------------------------------------------------------------------

def gen_dsl_reference():
    lines = [
        "# VisLang DSL Reference",
        "",
        "> Auto-generated from source by `python gen_docs.py`.",
        "> Do not edit by hand — changes will be overwritten.",
        "",
        "---",
        "",
        "## Overview",
        "",
        "A VisLang pipeline file is a plain Python script that uses DSL forms to",
        "describe what you want to visualize. Pipeline files are executed by the",
        "MCP tool `set_pipeline('pipeline.py')`, which builds and renders the scene.",
        "",
        "### Compositional structure",
        "",
        "```python",
        "# 1. Load data with source()",
        "data = source('vtkXMLStructuredGridReader', FileName='mydata.vts')",
        "",
        "# 2. Apply filter forms — each takes input= and returns a node reference",
        "region = threshold(input=data, ThresholdBy='field', ThresholdRange=[lo, hi])",
        "iso    = contour(input=data, ContourBy='field', Isosurfaces=[value])",
        "",
        "# 3. Add things to the scene with show()",
        "show(region, 'region', color_by='field', scalar_range=(lo, hi))",
        "show(iso,    'iso',    color_by='field', lut='hot')",
        "",
        "# 4. Set up the scene with camera(), background(), or scene_preset()",
        "camera(position=(x,y,z), focal_point=(fx,fy,fz))",
        "scene_preset('dark')",
        "```",
        "",
        "All DSL forms are available as module-level functions inside the pipeline file.",
        "You do not need to import anything — `set_pipeline()` injects them automatically.",
        "",
        "---",
        "",
        "## Contents",
        "",
    ]

    for group_name, _ in _DSL_GROUPS:
        anchor = group_name.lower().replace(" ", "-").replace("&", "").replace("/", "").replace("--", "-")
        lines.append(f"- [{group_name}](#{anchor})")
    lines.append("")
    lines.append("---")
    lines.append("")

    builder_methods = _get_builder_methods()

    for group_name, form_names in _DSL_GROUPS:
        anchor_label = group_name
        lines += [
            f"## {anchor_label}",
            "",
        ]

        for form_name in form_names:
            func = builder_methods.get(form_name)
            if func is not None:
                lines.append(_format_dsl_entry(form_name, func))
            else:
                lines.append(f"### `{form_name}(...)`\n\n*(Not found in PipelineBuilder)*\n")

    # Special section: show() display_props documented in full
    lines += [
        "---",
        "",
        "## `show()` Display Properties Reference",
        "",
        "The `show()` form accepts these keyword arguments for controlling appearance:",
        "",
        "### Surface / Actor Display Props",
        "",
        "| Property | Type | Description |",
        "| -------- | ---- | ----------- |",
        "| `color_by` | str | Field name to color by. If omitted, uses VTK default. |",
        "| `scalar_range` | (lo, hi) | Min/max values for colormap mapping. |",
        "| `lut` | str | Colormap preset name (see `list_capabilities()` for options). |",
        "| `opacity` | float | Overall actor opacity (0.0–1.0). |",
        "| `color` | (r,g,b) | Solid color (floats 0–1). Used when `color_by` is not set. |",
        "| `component` | int or str | For vector fields: which component to color by. 0/1/2 or 'x'/'y'/'z'. |",
        "| `representation` | str | 'Surface' (default), 'Wireframe', 'Points', or 'Volume'. |",
        "| `specular` | float | Specular highlight intensity (0–1). |",
        "| `specular_power` | float | Specular highlight sharpness. |",
        "| `line_width` | float | Line width for wireframe / streamlines. |",
        "| `scalar_bar` | bool or str | Show a color legend. Pass True or a title string. |",
        "",
        "### Volume Rendering Props (representation='Volume')",
        "",
        "| Property | Type | Description |",
        "| -------- | ---- | ----------- |",
        "| `opacity_function` | list or str | Control points `[(value, opacity), ...]` or a preset name like `'fire'`, `'ct_bone'`. |",
        "| `gradient_opacity` | bool or list | Edge-enhanced opacity. True uses a default ramp; list for custom `[(grad, opacity), ...]`. |",
        "| `volume_resolution` | int | Resampling resolution for non-image data (default 256, max 512). |",
        "| `shade` | bool | Enable shading for volume rendering (default True). |",
        "| `sample_distance` | float | Ray casting step size; smaller = higher quality but slower. |",
        "| `clip_planes` | list | List of `{'origin': ..., 'normal': ...}` dicts to clip the volume. |",
        "",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generate docs/mcp-reference.md
# ---------------------------------------------------------------------------

def gen_mcp_reference():
    lines = [
        "# VisLang MCP Tool Reference",
        "",
        "> Auto-generated from source by `python gen_docs.py`.",
        "> Do not edit by hand — changes will be overwritten.",
        "",
        "---",
        "",
        "## Overview",
        "",
        "MCP tools are interactive operations called by an AI assistant or MCP client.",
        "They query data, execute pipelines, adjust the scene, and return screenshots.",
        "",
        "`set_pipeline()` is the bridge between the MCP layer and the DSL layer — it",
        "executes a DSL pipeline file and renders the result. After loading data, you",
        "write a pipeline `.py` file using DSL forms and call `set_pipeline()` to run it.",
        "",
        "For DSL form documentation, see [dsl-reference.md](dsl-reference.md).",
        "",
        "---",
        "",
        "## Contents",
        "",
        "- [Query Tools](#query-tools)",
        "- [Mutation Tools](#mutation-tools)",
        "- [Meta / Utility Tools](#meta--utility-tools)",
        "",
        "---",
        "",
        "## Query Tools",
        "",
        "Query tools read data without changing the scene.  "
        "They all require an active pipeline (loaded via `set_pipeline()` or `load()`) "
        "unless otherwise noted.",
        "",
    ]

    for tname in _QUERY_TOOLS:
        func = _get_tool_func(tname)
        if func is not None:
            lines.append(_format_tool_entry(func))

    lines += [
        "---",
        "",
        "## Mutation Tools",
        "",
        "Mutation tools change scene state (load data, rebuild pipeline, adjust actors).  "
        "Most return an auto-screenshot alongside their text result.",
        "",
    ]

    for tname in _MUTATION_TOOLS:
        func = _get_tool_func(tname)
        if func is not None:
            lines.append(_format_tool_entry(func))

    lines += [
        "---",
        "",
        "## Meta / Utility Tools",
        "",
        "Meta tools manage server state, versions, views, and output.",
        "",
    ]

    for tname in _META_TOOLS:
        func = _get_tool_func(tname)
        if func is not None:
            lines.append(_format_tool_entry(func))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generate docs/instructions.md
# ---------------------------------------------------------------------------

def gen_instructions():
    """Extract the MCP server instructions string from server.py source."""
    # Read server.py as text and extract the instructions string directly,
    # since the FastMCP instance is a mock at import time.
    server_src = (PROJECT_ROOT / "vislang" / "server.py").read_text()

    # Find the instructions="""...""" block
    marker = 'instructions="""'
    start = server_src.find(marker)
    if start == -1:
        return "# VisLang MCP Server Instructions\n\n*(Could not extract instructions string.)*\n"

    start += len(marker)
    end = server_src.find('"""', start)
    if end == -1:
        return "# VisLang MCP Server Instructions\n\n*(Could not find end of instructions string.)*\n"

    instructions_text = server_src[start:end].strip()

    lines = [
        "# VisLang MCP Server Instructions",
        "",
        "> Auto-generated from source by `python gen_docs.py`.",
        "> Do not edit by hand — changes will be overwritten.",
        "",
        "This is the system-level guidance string shown to the AI assistant when the",
        "VisLang MCP server starts.  It describes the workflow, critical rules, and",
        "troubleshooting tips.",
        "",
        "---",
        "",
        "```",
        instructions_text,
        "```",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generate docs/getting-started.md
# ---------------------------------------------------------------------------

def gen_getting_started():
    examples_text = srv.get_examples()

    lines = [
        "# Getting Started with VisLang",
        "",
        "> Auto-generated from source by `python gen_docs.py`.",
        "> Do not edit by hand — changes will be overwritten.",
        "",
        "---",
        "",
        "```",
        examples_text,
        "```",
        "",
        "---",
        "",
        "## Further Reading",
        "",
        "- [dsl-reference.md](dsl-reference.md) — Complete DSL form reference",
        "- [mcp-reference.md](mcp-reference.md) — Complete MCP tool reference",
        "- [instructions.md](instructions.md) — MCP server guidance string",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generate README.md
# ---------------------------------------------------------------------------

def gen_readme():
    lines = [
        "# VisLang",
        "",
        "**VisLang** is two things working together:",
        "",
        "1. A **DSL** for declarative scientific visualization — lets you describe what",
        "   you want to see (isosurfaces, streamlines, volume rendering, thresholds)",
        "   in plain Python without writing low-level VTK code.",
        "",
        "2. An **MCP server** that makes visualization conversational — an AI assistant",
        "   can query your data, explore field ranges and distributions, execute pipeline",
        "   code, and iterate on the visualization interactively.",
        "",
        "Built on VTK, controlled through an MCP (Model Context Protocol) server.",
        "",
        "## What it does",
        "",
        "- Loads structured and unstructured VTK datasets (`.vts`, `.vti`, `.vtp`,",
        "  `.vtu`, `.vtr`) as well as raw binary volumes",
        "- Exposes a suite of **query tools** for exploring data ranges, distributions,",
        "  and spatial extents",
        "- Provides a **pipeline DSL** for composing filters (isosurfaces, thresholds,",
        "  volume rendering, streamlines, glyphs, etc.) in plain Python",
        "- Returns screenshots automatically after every state-changing operation",
        "- Maintains version history of every pipeline run",
        "",
        "## Quick start",
        "",
        "```bash",
        "# Install dependencies",
        "pip install -r requirements.txt",
        "",
        "# Run with a live VTK window",
        "cd sessions/my-session && python -m vislang.server",
        "",
        "# Run headless (offscreen rendering, screenshots only)",
        "cd sessions/my-session && python -m vislang.server --offscreen",
        "```",
        "",
        "The MCP server discovers data files in its **working directory**.  Create a",
        "session folder, symlink your dataset in, and start the server from there:",
        "",
        "```bash",
        "mkdir -p sessions/my-session",
        "ln -s ../../datasets/wildfire/data/output.30000.vts sessions/my-session/",
        "cd sessions/my-session && python -m vislang.server --offscreen",
        "```",
        "",
        "## Datasets",
        "",
        "Each dataset lives in `datasets/<name>/` with a `download.sh` script.",
        "Available datasets:",
        "",
        "- **wildfire** — HIGRAD/FIRETEC fire simulation (curvilinear structured grid, ~1.1 GB)",
        "- **bonsai** — Bonsai CT scan (regular image volume, ~16 MB)",
        "",
        "## Documentation",
        "",
        "| Document | Description |",
        "| -------- | ----------- |",
        "| [docs/getting-started.md](docs/getting-started.md) | Two-layer architecture walkthrough and key patterns |",
        "| [docs/dsl-reference.md](docs/dsl-reference.md) | Complete DSL form reference (pipeline files) |",
        "| [docs/mcp-reference.md](docs/mcp-reference.md) | Complete MCP tool reference (interactive operations) |",
        "| [docs/instructions.md](docs/instructions.md) | MCP server guidance string |",
        "| [DESIGN.md](DESIGN.md) | Architecture and design journal |",
        "",
        "Docs are auto-generated from source — run `python gen_docs.py` to",
        "regenerate them after code changes.",
        "",
        "## Project structure",
        "",
        "```",
        "vislang/",
        "  server.py      MCP server and tool definitions",
        "  dsl.py         DSL builder functions and interpreter",
        "  renderer.py    VTK renderer",
        "  queries.py     Query tool implementations",
        "  filters.py     VTK filter creation and special-case handling",
        "datasets/        One folder per dataset, each with download.sh",
        "sessions/        Working directories for MCP server instances",
        "gen_docs.py      Documentation extraction script",
        "docs/            Generated documentation",
        "tests/           Test suite",
        "domains/         Domain-specific knowledge files",
        "meta/            Agentic development process files",
        "  BACKLOG.md     Prioritized work items",
        "  feedback/      Dated feedback entries",
        "  design/        Design journal entries",
        "```",
        "",
        "## Development",
        "",
        "```bash",
        "# Run the test suite",
        "python -m pytest tests/ -q",
        "",
        "# Regenerate documentation",
        "python gen_docs.py",
        "```",
        "",
        "See [CLAUDE.md](CLAUDE.md) for detailed development guidance.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    docs_dir = PROJECT_ROOT / "docs"
    docs_dir.mkdir(exist_ok=True)

    outputs = [
        (docs_dir / "dsl-reference.md",    gen_dsl_reference,    "docs/dsl-reference.md"),
        (docs_dir / "mcp-reference.md",    gen_mcp_reference,    "docs/mcp-reference.md"),
        (docs_dir / "instructions.md",     gen_instructions,     "docs/instructions.md"),
        (docs_dir / "getting-started.md",  gen_getting_started,  "docs/getting-started.md"),
        (PROJECT_ROOT / "README.md",       gen_readme,           "README.md"),
    ]

    for path, generator, label in outputs:
        content = generator()
        path.write_text(content, encoding="utf-8")
        print(f"  wrote {label}  ({len(content):,} chars)")

    # Remove old files that have been replaced
    old_files = [
        docs_dir / "reference.md",
        docs_dir / "examples.md",
    ]
    for old_path in old_files:
        if old_path.exists():
            old_path.unlink()
            print(f"  removed {old_path.relative_to(PROJECT_ROOT)}")

    print("Done.")


if __name__ == "__main__":
    main()
