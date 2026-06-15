# Tracked Execution — Development Guide

## Design Principles

These principles override default behavior. Follow them exactly.

### One path, no redundancy
There should be exactly one way to do each thing. No aliases, no
backwards-compatible shims, no "alternative API for convenience."
If two tools/functions overlap, merge them or drop one. Every function
and tool must earn its place — if it doesn't pull its weight, remove it.

### Fail loudly, don't work around
When something could go wrong (e.g., missing `scalars=`, cache
inconsistency), raise an error with a clear message. Do NOT silently
paper over the problem with clever workarounds. An assertion that catches
a bug is better than a clever hash that hides one.

### The file is the artifact
Pipeline files are the shared representation between human and AI. The
human sees them in their editor; the agent writes them as normal files.
Don't hide file writes behind MCP tools — the agent writes the file, the
watcher picks it up. This means IDE tools render the agent's edits
naturally.

### Design for interactive windows, not just offscreen
The system must work with native VTK interactive windows, not just
offscreen rendering. Any design that only works offscreen is incomplete.
VTK is not thread-safe — scene updates from background threads must be
marshaled to the main thread. The reconciler exists for smooth updates
without flicker.

### Hide infrastructure from users
Agents and humans should never need to think about threading, locking,
cache internals, or proxy mechanics. If a user has to stop a watcher
or hold a lock, the abstraction is leaking. Fix the infrastructure,
don't document the workaround.

### Small, simple, and beautiful
Minimize API surface. A small number of well-designed tools that compose
is better than many specialized ones. Before adding a feature, ask: can
an existing tool cover this? Before adding a parameter, ask: is there a
good default?

### Error messages are for agents
Every error message should tell the agent: what went wrong, why, and
what to do instead. Include the method name, the type, and a concrete
workaround. Agents can't read stack traces intuitively — they need
actionable text.

## Development Process

### No backwards compatibility
This project has no users yet. There are no backwards compatibility
constraints. If a better name or design emerges, change it and update
everything that depends on it. Don't add aliases or shims.

### Periodic review, not continuous
After major milestones, run one review agent (not three) with a focused
lens. Rotate between: API consistency, architecture smells, unnecessary
complexity, agent UX. Act on critical findings immediately; defer minor
ones. Don't let review overhead dominate implementation work.

### Coherent commits
Every commit should be a working state. If usage limits interrupt the
session, the next session should be able to pick up cleanly from the
last commit. Use `WIP:` prefix for incomplete work with a status note
in the commit body.

## Environment

Same as the parent project — see `/home/user/SIVA/CLAUDE.md` for
Python environment, venv, xvfb-run requirements.

### Running tests
```bash
# Library tests (no display needed for most)
python3 -m pytest experiments/tracked-execution/tests/ -q

# MCP server tests (need xvfb for rendering)
xvfb-run -a python3 -m pytest experiments/tracked-execution/mcp_server/tests/ -q

# Everything
xvfb-run -a python3 -m pytest experiments/tracked-execution/ -q
```

### Project structure
```
tracked_execution/     # Core library: proxy, dispatch, DAG, cache, whitelist
mcp_server/            # Standalone MCP server wrapping the library
  server.py            # FastMCP server with tools
  tests/               # MCP-level tests including e2e with real datasets
tests/                 # Library-level unit and integration tests
examples/              # Runnable demos
benchmarks/            # Performance benchmarks
```
