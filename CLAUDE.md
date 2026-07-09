# SIVA - Development Guide

This file is for Claude Code when developing the SIVA MCP server and DSL.
Users of the MCP don't see this file -- they get guidance from the MCP server
instructions, tool descriptions, and domain files.

## Design Philosophy

SIVA is in active prototyping and design phase. There are no backwards
compatibility constraints. Demos, tests, and examples exist to validate
ideas -- they do not lock us into bad behavior. If a better design emerges,
change the API and update everything that depends on it. Prefer consistency
and correctness over preserving existing patterns.

## Python Environment

The project is managed with [uv](https://docs.astral.sh/uv/): dependencies
are declared in `pyproject.toml`, pinned in the committed `uv.lock`, and
installed into `.venv/` by `uv sync` (which also installs the `dev`
dependency group, i.e. pytest). Always use `.venv/bin/python` (or activate
the venv) when running scripts, tests, or the server directly.

If you change dependencies in `pyproject.toml`, run `uv lock` and commit the
updated `uv.lock` alongside it.

Where uv isn't available, the classic route still works:
`python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/pip install pytest`.

### Worktree / subagent setup

Git worktrees don't share the parent's `.venv/`. If `.venv/bin/python`
doesn't exist (e.g. you're in a fresh worktree), create it first:

```bash
uv sync   # seconds from the warm uv cache; creates .venv/ with dev deps
```

**When working in a worktree, always pass the worktree's absolute path to
subagents** (in prompts, `path` parameters for Glob/Grep/Read, etc.). The
outer repo may have diverged — reading files or searching from the default
directory can silently pick up stale code from the wrong copy.

### Cloud / web environment setup

When running in Claude Code cloud or web environments where the venv and
system dependencies aren't already installed, run:

```bash
bash scripts/cloud-env-setup.sh
```

This installs Xvfb and creates the `.venv/` via `uv sync` (falling back to
venv + pip where uv isn't installed).

## Server Launch Modes

```bash
# Interactive (default) -- opens a live VTK window
python -m siva.server

# Off-screen -- headless rendering, returns screenshots only
python -m siva.server --offscreen

# Browser-based live views via trame (needs `uv sync --extra trame`; note a
# plain `uv sync` later removes the extra again -- sync is exact);
# --trame-port pins the single port everything is served on (default: auto-pick)
python -m siva.server --trame

# Run with a specific working directory for scratch output
python -m siva.server --workdir path/to/workspace
```

In `--trame` mode every view shares one asyncio event loop that runs on the
process main thread (required by VTK's Cocoa backend on macOS — see
`siva/trame_backend.py`'s module docstring) and **one trame server on a
single localhost port**: each view is a trame layout addressed by
`/?ui=<name>`, and an index page listing all views (`siva/view_index.py`)
is mounted on the same port at `/views`. `--trame-port` pins the port so a
single static port-forward (e.g. out of a container) reaches everything;
`--trame-host 0.0.0.0` widens the bind from loopback to all interfaces,
required when the port is published with `docker run -p`.
URLs are proxy-aware (`VSCODE_PROXY_URI`, code-server).
Like offscreen mode, trame rendering under headless Linux needs `xvfb-run -a`.

**For development and testing (CI, subagents, automated work), always use
`--offscreen`.** The interactive window requires a display and will block in
headless environments.

The server writes its scratch output -- `.siva/` (logs and history),
`view-*.py` pipeline files, and screenshots -- relative to its working
directory. By default that's wherever the process was launched. Pass
`--workdir DIR` to relocate it into a dedicated subdirectory; relative paths
resolve from the launch directory, and the directory must already exist.

The server logs to `.siva/server.log` in the working directory (DEBUG
level). Stderr is reserved for the MCP protocol, so all diagnostic output
goes to this file.

### Offscreen rendering requires Xvfb

VTK's offscreen rendering needs an X server for OpenGL context creation.
In headless environments (CI, remote servers, Claude Code web), use `xvfb-run`:

```bash
# Always prefix offscreen commands with xvfb-run in headless environments
xvfb-run -a python -m siva.server --offscreen

# For running tests that involve rendering
xvfb-run -a python -m pytest tests/ -q
```

Without `xvfb-run`, VTK will segfault on `render()` or `screenshot()` calls.
This applies to subagent sessions too — any script that calls the renderer
directly needs the `xvfb-run -a` wrapper.

**Local macOS development does not need `xvfb-run`** — VTK uses Cocoa
directly and `xvfb-run` isn't installed. Just run commands with the venv
Python (`.venv/bin/python -m pytest …`).

See [TESTING.md](TESTING.md) for the test-level strategy (unit → stateful →
MCP protocol → manual), headless/interactive launch modes, and how to write
new tests.

## Project Structure

- `README.md` -- User-facing project description and setup guide.
- `domains/` -- Domain-specific knowledge files (e.g. wildfire interpretation).
- `datasets/` -- One folder per dataset, each with a `download.sh` and `README.md`.
- `docs/` -- Generated documentation. **Do not edit directly.** See below.
- `scripts/gen_docs.py` -- Generates `docs/` and parts of `README.md` from source docstrings.

## Documentation

Files in `docs/` (including `mcp-reference.md`, `dsl-reference.md`,
`getting-started.md`, `instructions.md`) are **generated** by `scripts/gen_docs.py`.
Never edit them by hand — your changes will be overwritten. `README.md` is
hand-written and should be edited directly.

**Any time you modify docstrings** in `siva/server.py`, `siva/dsl.py`,
or other source files that feed into docs, you must regenerate before you're
done:

```bash
python scripts/gen_docs.py
```

`siva/spec_api.py` (plus its typing foundation `siva/_spec_api_props.py`) is a
separate pair of generated files: an editor-facing stub that mirrors every DSL
verb's real signature and docstring, so an editor's language server
(Pylance/pyright) can resolve DSL names in spec (`view-*.py`) files that begin
with the mandatory `from siva.spec_api import *` header (see `siva/sandbox.py`'s
module docstring for the runtime rewrite this enables). Beyond the signatures it
also encodes `Literal` types for closed-enum arguments (`lut`, `representation`,
`scalar_type`, `background()` presets) and one `TypedDict` per whitelisted VTK
class, driving `source`/`filter`/wrapper-verb `**props` completions and typo
checking off the same VTK introspection the runtime validator uses. Never edit
either file by hand. Any time you add, remove, rename, or change the
signature/docstring of a `PipelineBuilder` method in `siva/dsl.py`, or change
`WHITELISTED_CLASSES` / the colormap or scalar-type registries, regenerate both:

```bash
python scripts/gen_spec_api.py
```

`tests/test_spec_api.py` fails CI if either checked-in file drifts from a fresh
regeneration, so this isn't optional busywork — it's enforced.

## Datasets

Each dataset lives in `datasets/<name>/` with a `download.sh` that fetches
files into `datasets/<name>/data/` (gitignored). If a dataset isn't present
locally, run its download script first.

Available datasets:
- `datasets/wildfire/` -- HIGRAD/FIRETEC fire simulation (output.30000.vts, ~1.1 GB, StructuredGrid)
- `datasets/bonsai/` -- CT scan of a bonsai tree (uint8 volume, ImageData)
- `datasets/cthead/` -- Stanford CT head scan (256x256x113, uint16 raw binary)
- `datasets/foot/` -- Rotational C-arm x-ray of a human foot (.vti, ImageData)
- `datasets/hydrogen_atom/` -- Electron probability distribution in magnetic field (uint8, ImageData)
- `datasets/synthetic/` -- Procedurally generated 64x64x64 test volume with temperature, density, velocity fields (no download needed, runs generate.py)
