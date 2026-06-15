# VisLang - Development Guide

This file is for Claude Code when developing the VisLang MCP server and DSL.
Users of the MCP don't see this file -- they get guidance from the MCP server
instructions, tool descriptions, and domain files.

## Design Philosophy

VisLang is in active prototyping and design phase. There are no backwards
compatibility constraints. Demos, tests, and examples exist to validate
ideas -- they do not lock us into bad behavior. If a better design emerges,
change the API and update everything that depends on it. Prefer consistency
and correctness over preserving existing patterns.

## Python Environment

All dependencies are installed in `.venv/`. Always use `.venv/bin/python`
(or activate the venv) when running scripts, tests, or the server directly.

### Worktree / subagent setup

Git worktrees don't share the parent's `.venv/`. If `.venv/bin/python`
doesn't exist (e.g. you're in a fresh worktree), create it first:

```bash
python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"
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

This installs Xvfb, creates the `.venv/`, and pip-installs the project.

## Server Launch Modes

```bash
# Interactive (default) -- opens a live VTK window
python -m vislang.server

# Off-screen -- headless rendering, returns screenshots only
python -m vislang.server --offscreen

# Run with a specific working directory for scratch output
python -m vislang.server --workdir path/to/workspace
```

**For development and testing (CI, subagents, automated work), always use
`--offscreen`.** The interactive window requires a display and will block in
headless environments.

The server writes its scratch output -- `.vislang/` (logs and history),
`view-*.py` pipeline files, and screenshots -- relative to its working
directory. By default that's wherever the process was launched. Pass
`--workdir DIR` to relocate it into a dedicated subdirectory; relative paths
resolve from the launch directory, and the directory must already exist.

The server logs to `.vislang/server.log` in the working directory (DEBUG
level). Stderr is reserved for the MCP protocol, so all diagnostic output
goes to this file.

### Offscreen rendering requires Xvfb

VTK's offscreen rendering needs an X server for OpenGL context creation.
In headless environments (CI, remote servers, Claude Code web), use `xvfb-run`:

```bash
# Always prefix offscreen commands with xvfb-run in headless environments
xvfb-run -a python -m vislang.server --offscreen

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

**Any time you modify docstrings** in `vislang/server.py`, `vislang/dsl.py`,
or other source files that feed into docs, you must regenerate before you're
done:

```bash
python scripts/gen_docs.py
```

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
