# VisLang

**VisLang** is a declarative scientific visualization system built on VTK,
controlled through an MCP (Model Context Protocol) server.  It lets an AI
assistant — or any MCP client — build complex 3D visualizations through
conversation, without writing low-level VTK code.

## What it does

- Loads structured and unstructured VTK datasets (`.vts`, `.vti`, `.vtp`,
  `.vtu`, `.vtr`) as well as raw binary volumes
- Exposes a suite of **query tools** for exploring data ranges, distributions,
  and spatial extents
- Provides a **pipeline DSL** for composing filters (isosurfaces, thresholds,
  volume rendering, streamlines, glyphs, etc.) in plain Python
- Returns screenshots automatically after every state-changing operation
- Maintains version history of every pipeline run

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Run with a live VTK window
cd sessions/my-session && python -m vislang.server

# Run headless (offscreen rendering, screenshots only)
cd sessions/my-session && python -m vislang.server --offscreen
```

The MCP server discovers data files in its **working directory**.  Create a
session folder, symlink your dataset in, and start the server from there:

```bash
mkdir -p sessions/my-session
ln -s ../../datasets/wildfire/data/output.30000.vts sessions/my-session/
cd sessions/my-session && python -m vislang.server --offscreen
```

## Datasets

Each dataset lives in `datasets/<name>/` with a `download.sh` script.
Available datasets:

- **wildfire** — HIGRAD/FIRETEC fire simulation (curvilinear structured grid, ~1.1 GB)
- **bonsai** — Bonsai CT scan (regular image volume, ~16 MB)

## Documentation

| Document | Description |
| -------- | ----------- |
| [docs/reference.md](docs/reference.md) | Complete tool and DSL reference |
| [docs/instructions.md](docs/instructions.md) | MCP server guidance string |
| [docs/examples.md](docs/examples.md) | Example pipeline patterns |
| [DESIGN.md](DESIGN.md) | Architecture and design journal |

Docs are auto-generated from source — run `python gen_docs.py` to
regenerate them after code changes.

## Project structure

```
vislang/
  server.py      MCP server and tool definitions
  dsl.py         DSL builder functions and interpreter
  renderer.py    VTK renderer
  queries.py     Query tool implementations
  filters.py     VTK filter creation and special-case handling
datasets/        One folder per dataset, each with download.sh
sessions/        Working directories for MCP server instances
gen_docs.py      Documentation extraction script
docs/            Generated documentation
tests/           Test suite
domains/         Domain-specific knowledge files
meta/            Agentic development process files
  BACKLOG.md     Prioritized work items
  feedback/      Dated feedback entries
  design/        Design journal entries
```

## Development

```bash
# Run the test suite
python -m pytest tests/ -q

# Regenerate documentation
python gen_docs.py
```

See [CLAUDE.md](CLAUDE.md) for detailed development guidance.
