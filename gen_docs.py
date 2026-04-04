#!/usr/bin/env python3
"""Generate Markdown documentation from VisLang source code.

Extracts:
- docs/reference.md  — MCP tool and DSL builder function reference
- docs/instructions.md — MCP server instructions string
- docs/examples.md  — Output of get_examples()
- README.md         — Project overview linking to the generated docs

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
    "extract_component",
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
# Generate docs/reference.md
# ---------------------------------------------------------------------------

def gen_reference():
    lines = [
        "# VisLang Tool and DSL Reference",
        "",
        "> Auto-generated from source by `python gen_docs.py`.",
        "> Do not edit by hand — changes will be overwritten.",
        "",
        "---",
        "",
        "## Contents",
        "",
        "- [Query Tools](#query-tools)",
        "- [Mutation Tools](#mutation-tools)",
        "- [Meta / Utility Tools](#meta--utility-tools)",
        "- [DSL Pipeline Builder](#dsl-pipeline-builder)",
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

    # DSL section
    lines += [
        "---",
        "",
        "## DSL Pipeline Builder",
        "",
        "The DSL is used inside `pipeline.py` (or any file passed to `set_pipeline()`).  "
        "It is a thin Python layer that declares a VTK pipeline using builder functions.  "
        "All builder methods are available as module-level functions inside the pipeline file.",
        "",
        "### Core declarations",
        "",
        "| Function | Description |",
        "| -------- | ----------- |",
        "| `source(vtk_class, **props)` | Create a data source (reader or generator) |",
        "| `filter(vtk_class, input=..., **props)` | Apply a VTK filter to a node |",
        "| `show(node, name, **display_props)` | Add a node to the rendered scene |",
        "| `camera(position, focal_point, up)` | Set the camera for this pipeline |",
        "| `background(r, g, b)` | Set background color (0–1 floats) |",
        "| `scene_preset(name)` | Apply a named scene preset (e.g. `\"dark\"`) |",
        "",
        "### Pipeline builder methods",
        "",
    ]

    # Collect public methods from PipelineBuilder, excluding dunder and private
    builder_methods = [
        (name, func)
        for name, func in inspect.getmembers(dsl.PipelineBuilder, predicate=inspect.isfunction)
        if not name.startswith("_")
    ]
    builder_methods.sort(key=lambda x: x[0])

    for name, func in builder_methods:
        lines.append(_format_dsl_entry(name, func))

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
# Generate docs/examples.md
# ---------------------------------------------------------------------------

def gen_examples():
    examples_text = srv.get_examples()

    lines = [
        "# VisLang Pipeline Examples",
        "",
        "> Auto-generated from source by `python gen_docs.py`.",
        "> Do not edit by hand — changes will be overwritten.",
        "",
        "These patterns are generic — substitute your own file names, field names,",
        "and value ranges.  Use `describe_data()` and `get_statistics()` to find the",
        "right values for your dataset.",
        "",
        "---",
        "",
        "```python",
        examples_text,
        "```",
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
        "**VisLang** is a declarative scientific visualization system built on VTK,",
        "controlled through an MCP (Model Context Protocol) server.  It lets an AI",
        "assistant — or any MCP client — build complex 3D visualizations through",
        "conversation, without writing low-level VTK code.",
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
        "| [docs/reference.md](docs/reference.md) | Complete tool and DSL reference |",
        "| [docs/instructions.md](docs/instructions.md) | MCP server guidance string |",
        "| [docs/examples.md](docs/examples.md) | Example pipeline patterns |",
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
        (docs_dir / "reference.md",    gen_reference,    "docs/reference.md"),
        (docs_dir / "instructions.md", gen_instructions, "docs/instructions.md"),
        (docs_dir / "examples.md",     gen_examples,     "docs/examples.md"),
        (PROJECT_ROOT / "README.md",   gen_readme,       "README.md"),
    ]

    for path, generator, label in outputs:
        content = generator()
        path.write_text(content, encoding="utf-8")
        print(f"  wrote {label}  ({len(content):,} chars)")

    print("Done.")


if __name__ == "__main__":
    main()
