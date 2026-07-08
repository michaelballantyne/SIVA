# SIVA

> [!WARNING]
> SIVA runs AI-generated code on your machine. SIVA itself is developed with AI-assisted programming and includes components that are not closely reviewed. We recommend treating it as untrusted software: deploy it only in a sandboxed environment without access to sensitive data, credentials, or networks.

SIVA lets you build scientific visualizations by talking to an AI. You
describe what you want to see, the AI writes declarative pipeline code, and
you iterate together — exploring data, tuning parameters, and refining the
picture through conversation.

Here's what a pipeline file looks like — this volume-renders a CT scan of a
bonsai tree with a terrain colormap and gradient-enhanced opacity:

```python
from siva.spec_api import *

data = source("vtkXMLImageDataReader", FileName="bonsai.vti")

show(data, "bonsai", representation="Volume",
    color_by="density", scalar_range=(20, 200), lut="terrain",
    opacity_function=[(0, 0.0), (18, 0.0), (26, 0.01), (36, 0.05),
                      (48, 0.13), (60, 0.26), (80, 0.48),
                      (110, 0.68), (150, 0.84), (220, 0.96)],
    gradient_opacity=True, shade=True)

background(0.02, 0.02, 0.05)
```

Every pipeline file begins with `from siva.spec_api import *` — that header
makes the SIVA DSL forms (`source`, `filter`, `show`, `threshold`, `contour`,
…) available.

![Bonsai CT scan rendered with SIVA](docs/example.png)

SIVA has two parts:

1. **A pipeline DSL** — a concise Python syntax for VTK visualization.
   You describe sources, filters, and display properties declaratively;
   SIVA wires up the VTK objects for you.

2. **An MCP server** — exposes the DSL and data query tools to any AI assistant
   via [Model Context Protocol](https://modelcontextprotocol.io/). The assistant
   can load data, explore field statistics, execute pipeline code, adjust the
   camera, and see screenshots — all through tool calls.

## Setup

SIVA works with any MCP-compatible AI assistant (Claude Code, Claude Desktop,
etc.). Point your assistant at the SIVA server and start a conversation in
the directory where your data lives.

### 1. Install

```bash
cd /path/to/SIVA
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Configure your AI assistant

Add SIVA to your project's `.mcp.json` (or your assistant's MCP settings),
pointing at the Python from the venv:

```json
{
  "mcpServers": {
    "SIVA": {
      "command": "/path/to/SIVA/.venv/bin/python",
      "args": ["-m", "siva.server", "--workdir", "/path/to/your/data"]
    }
  }
}
```

This opens a live VTK window where you can see the visualization update as
the AI builds it. `--workdir` sets the working directory for the session —
the server discovers data files there, and the pipeline files the AI writes
land there too. (Omit it to use the directory you launched the assistant
from.) To view in a browser instead of a native window — including from a
remote machine — see [Live views in the browser](#live-views-in-the-browser---trame).

**Model tip:** With Claude Code, Opus at low reasoning effort has given the
best balance of speed and skill in our experience — smarter pipeline choices
than Sonnet without the latency of higher reasoning effort.

### 3. Start a conversation

Open a chat with your AI assistant (Claude Code, Claude Desktop, etc.) and
ask it to visualize your data:

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
> **AI:** *(adds make_vector + stream_tracer to the pipeline, iterates)*

## Live views in the browser (`--trame`)

Instead of a native VTK window, SIVA can serve each view as an interactive
3-D view in your browser, using [trame](https://kitware.github.io/trame/)
(server-side rendering streamed over a websocket — large datasets never
leave the machine). This is the mode to use when SIVA runs somewhere your
display isn't: a remote server, a container, or behind
[code-server](https://github.com/coder/code-server).

Install the optional trame dependencies, then add `--trame` to the server
arguments:

```bash
pip install -e ".[trame]"
```

```json
"args": ["-m", "siva.server", "--trame", "--workdir", "/path/to/your/data"]
```

When the first view is ready, the AI relays a URL for the **view index
page** — one stable page listing every live view with a link and thumbnail,
updating automatically as views are added. Open it once and keep the tab;
each view opens in its own tab, where you can rotate, zoom, and inspect
while the AI keeps editing the pipeline. The URL is also logged to
`.siva/server.log`, and the AI can repeat it via the `view_url()` tool.

Options and notes:

- **Everything is served on a single port**: each view at `/?ui=<name>`,
  the index page at `/views`. `--trame-port N` pins that port (default:
  auto-pick) — useful when only a fixed port can be reached, e.g.
  forwarded out of a docker container or over SSH; one forwarded port
  covers every view.
- The port binds loopback only by default. **From a docker container**,
  publish it with `docker run -p N:N` *and* pass `--trame-host 0.0.0.0` —
  published ports arrive on the container's external interface, which a
  loopback-bound server won't accept. (The container's network namespace
  still keeps everything private except the ports you publish.)
- **Behind code-server / Coder** everything works through the built-in
  authenticated proxy with no extra setup: SIVA detects `VSCODE_PROXY_URI`
  and reports proxied `https://…/proxy/<port>/` URLs that work from your
  remote browser.
- **Headless Linux** needs an X server for OpenGL, same as offscreen mode:
  launch with `xvfb-run -a`.
- Screenshots and all other MCP tools behave exactly as in the native
  window mode.

## Sample data

The repository includes several sample datasets under `datasets/`, each with a
`download.sh` that fetches the data and, where needed, converts it into a
SIVA-ready `.vti`/`.vts` file. The conversion step uses VTK, so create the venv
first (see [Install](#1-install) above), then run the script for the dataset
you want:

```bash
datasets/bonsai/download.sh      # the bonsai CT scan used in the example above
```

The result lands in `datasets/<name>/data/`. Point the server's `--workdir` at
that directory (or symlink the file into your working directory) so the AI can
load it. Other datasets include `cthead`, `foot`, `hydrogen_atom`, and
`wildfire`, plus a `synthetic` generator that needs no download.

## Running pipelines directly

You can also run pipeline files directly, without the MCP server.
Activate the venv (or use its Python) and run:

```bash
# Open an interactive VTK window
python -m siva.run pipeline.py

# Save a screenshot
python -m siva.run pipeline.py -o output.png

# Custom resolution
python -m siva.run pipeline.py -o output.png --size 3840x2160
```

Useful for batch rendering, testing pipelines, or using SIVA without
an AI assistant.

### Editor support

Every pipeline file begins with `from siva.spec_api import *`. Open one in an
editor with a Python language server (VS Code with Pylance, or any pyright
setup) and point it at the SIVA virtualenv's interpreter: that header resolves
to a generated stub mirroring the DSL, so you get autocomplete and hover docs
for every form (`source`, `contour`, `slice`, …), class-specific `**props`
completions for `source`/`filter`, and type checking that flags misspelled
verbs, unknown properties, and bad colormap/representation values as you edit.
The stub is types only — it never executes; the runtime binds the real DSL.

#### VS Code setup

If you edit pipeline files in a folder *outside* the SIVA repo (e.g. a data or
project directory), add a `.vscode/settings.json` there:

```json
{
  "python.defaultInterpreterPath": "/path/to/SIVA/.venv/bin/python",
  "python.analysis.extraPaths": ["/path/to/SIVA"]
}
```

Reload the window (**Developer: Reload Window**) afterward. Both lines are
needed: the interpreter alone does not let the language server resolve
`from siva.spec_api import *` from an editable install. If you edit pipeline
files *inside* the SIVA repo, neither line is needed.

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
| [MCP Tool Reference](docs/mcp-reference.md) | Complete reference for interactive tools (`describe_data`, `wait_for_pipeline`, etc.) |
| [Server Instructions](docs/instructions.md) | The guidance string shown to AI assistants on connect |

Docs are auto-generated from source — run `python scripts/gen_docs.py` to regenerate
after code changes.

## Development

For contributors working on SIVA itself:

```
siva/
  server.py      MCP server and tool definitions
  dsl.py         DSL forms and pipeline interpreter
  renderer.py    VTK renderer
  queries.py     Query tool implementations
  filters.py     VTK filter creation and special-case handling
  colormaps.py   Colormap presets and field defaults
datasets/        Sample datasets, each with a download.sh script
scripts/         Development scripts (gen_docs.py, etc.)
docs/            Generated documentation
tests/           Test suite
```

```bash
# Install the project with dev dependencies (adds pytest)
pip install -e ".[dev]"

# Run the test suite
python -m pytest tests/ -q

# Regenerate documentation
python scripts/gen_docs.py
```

See [CLAUDE.md](CLAUDE.md) for detailed development guidance.


## Copyright
© 2025. Triad National Security, LLC. All rights reserved.

This program was produced under U.S. Government contract 89233218CNA000001 for Los Alamos National Laboratory (LANL), which is operated by Triad National Security, LLC for the U.S. Department of Energy/National Nuclear Security Administration. All rights in the program are reserved by Triad National Security, LLC, and the U.S. Department of Energy/National Nuclear Security Administration. The Government is granted for itself and others acting on its behalf a nonexclusive, paid-up, irrevocable worldwide license in this material to reproduce, prepare. derivative works, distribute copies to the public, perform publicly and display publicly, and to permit others to do so.