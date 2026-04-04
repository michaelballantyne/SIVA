# VisLang

VisLang lets you build scientific visualizations by talking to an AI. You
describe what you want to see, the AI writes declarative pipeline code, and
you iterate together — exploring data, tuning parameters, and refining the
picture through conversation.

Here's what a pipeline file looks like — this volume-renders a CT scan of a
bonsai tree with a terrain colormap and gradient-enhanced opacity:

```python
data = source("vtkXMLImageDataReader", FileName="bonsai.vti")

show(data, "bonsai", representation="Volume",
    color_by="density", scalar_range=(20, 200), lut="terrain",
    opacity_function=[(0, 0.0), (18, 0.0), (26, 0.01), (36, 0.05),
                      (48, 0.13), (60, 0.26), (80, 0.48),
                      (110, 0.68), (150, 0.84), (220, 0.96)],
    gradient_opacity=True, shade=True)

background(0.02, 0.02, 0.05)
```

![Bonsai CT scan rendered with VisLang](docs/example.png)

VisLang has two parts:

1. **A pipeline DSL** — a concise Python syntax for VTK visualization.
   You describe sources, filters, and display properties declaratively;
   VisLang wires up the VTK objects for you.

2. **An MCP server** — exposes the DSL and data query tools to any AI assistant
   via [Model Context Protocol](https://modelcontextprotocol.io/). The assistant
   can load data, explore field statistics, execute pipeline code, adjust the
   camera, and see screenshots — all through tool calls.

## Setup

VisLang works with any MCP-compatible AI assistant (Claude Code, Claude Desktop,
etc.). Point your assistant at the VisLang server and start a conversation in
the directory where your data lives.

### 1. Install

```bash
cd /path/to/VisLang
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

The `.venv` lives inside the VisLang repo directory — `run_server.sh`
knows to look for it there and activates it automatically.

### 2. Configure your AI assistant

Add VisLang to your project's `.mcp.json` (or your assistant's MCP settings).
The `cwd` should point to the directory containing your data files:

```json
{
  "mcpServers": {
    "VisLang": {
      "command": "bash",
      "args": ["/path/to/VisLang/run_server.sh"],
      "cwd": "/path/to/your/data"
    }
  }
}
```

This opens a live VTK window where you can see the visualization update as
the AI builds it. The server discovers `.vts`, `.vti`, `.vtp`, `.vtu`, and
`.vtr` files in its working directory.

### 3. Start a conversation

Ask your AI assistant to visualize your data. A typical conversation might go:

> **You:** Load output.30000.vts and show me what's in it.
>
> **AI:** *(calls load(), describes fields, dimensions, value ranges)*
>
> **You:** Show me the temperature field — I want to see where it's hottest.
>
> **AI:** *(writes a pipeline with threshold + volume rendering, shows screenshot)*
>
> **You:** Can you add streamlines showing the wind flow through the hot region?
>
> **AI:** *(adds compute_velocity + stream_tracer to the pipeline, iterates)*

## Running pipelines directly

You can also run pipeline files without the MCP server:

```bash
# Open an interactive VTK window
python -m vislang.run pipeline.py

# Save a screenshot
python -m vislang.run pipeline.py -o output.png

# Custom resolution
python -m vislang.run pipeline.py -o output.png --size 3840x2160
```

This is useful for batch rendering, testing pipelines, or using VisLang
without an AI assistant.

## What it supports

- **Data formats:** VTK structured grids (`.vts`), image data (`.vti`),
  polydata (`.vtp`), unstructured grids (`.vtu`), rectilinear grids (`.vtr`),
  raw binary volumes
- **Visualization techniques:** isosurfaces, thresholds, volume rendering,
  cross-section slices, streamlines, glyphs, colormapping, clipping
- **Derived quantities:** vector fields from scalar components, vorticity,
  gradient magnitude, individual vector components
- **Interactivity:** camera control, opacity adjustment, colormap changes,
  text annotations, multi-view support

## Documentation

| Document | Description |
| -------- | ----------- |
| [Getting Started](docs/getting-started.md) | Architecture overview, workflow walkthrough, key patterns |
| [DSL Reference](docs/dsl-reference.md) | Complete reference for pipeline forms (`source`, `threshold`, `show`, etc.) |
| [MCP Tool Reference](docs/mcp-reference.md) | Complete reference for interactive tools (`describe_data`, `set_pipeline`, etc.) |
| [Server Instructions](docs/instructions.md) | The guidance string shown to AI assistants on connect |

Docs are auto-generated from source — run `python gen_docs.py` to regenerate
after code changes.

## Development

For contributors working on VisLang itself:

```
vislang/
  server.py      MCP server and tool definitions
  dsl.py         DSL forms and pipeline interpreter
  renderer.py    VTK renderer
  queries.py     Query tool implementations
  filters.py     VTK filter creation and special-case handling
  colormaps.py   Colormap presets and field defaults
datasets/        Sample datasets, each with a download.sh script
gen_docs.py      Documentation extraction script
docs/            Generated documentation
tests/           Test suite
```

```bash
# Run the test suite
python -m pytest tests/ -q

# Regenerate documentation
python gen_docs.py
```

See [CLAUDE.md](CLAUDE.md) for detailed development guidance and
[DESIGN.md](DESIGN.md) for architecture notes.
