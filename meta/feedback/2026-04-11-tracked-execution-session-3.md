# Tracked Execution — Final Session Summary

Date: 2026-04-11 (continuation of independent work session)

## Session scope

This was the continuation session after a usage limit interruption.
Picked up from the polished product checkpoint (b5cbe74) and continued
with both speculative exploration and production polish.

## What was accomplished this session

### Speculative features (after polished product tag)
- **Numpy proxy `__array__`/`__array_wrap__` fix** — np.sqrt(proxy) now works
  correctly, can assign results to mesh fields
- **Trame viewer prototype** — TrameViewer class with tabbed views, VtkRemoteView
  widget, background-thread startup
- **Trame + MCP integration** — `--trame` mode wired into server with view
  registration, update notification, and cleanup on close
- **LRU eviction for shared read cache** — prevents memory leak in long sessions
- **Domain-independent `tracked_core` extraction** — DAG, TrackedProxy, dispatch
  separated into reusable core package
- **Pandas domain proof-of-concept** — 8 tests proving tracked_core works for
  non-visualization domain (data processing with caching)
- **Reconciler in-place opacity updates** — smooth property changes without
  actor removal in interactive VTK windows

### Simplification and cleanup
- **Merged pipeline_status into list_views** — 7 → 6 MCP tools
- **Removed make_proxy factory** — unnecessary indirection
- **Unified blocked stubs** — one factory instead of three functions
- **Updated error message tests** — PermissionError for blocked inspect stubs
- **Removed phantom add_mesh from docs** — it was never in the namespace

### Documentation
- **Consolidated** 13 root .md files → 3 (README, CLAUDE, VISION)
- **Organized** docs/ (10 reference files) and meta/ (3 project files)
- **Try-it-out guide** — step-by-step for setting up the MCP with Claude Code
- **Updated all docs** for 6-tool MCP

### Testing
- **Full 24-step session simulation** — simulates a realistic agent workflow
  on wildfire data: setup, exploration, visualization, refinement, multi-view, cleanup
- **293 tests + 1 xfailed** — all passing

## Project by the numbers

| Metric | Value |
|--------|-------|
| Total tests | 293 + 1 xfailed |
| MCP tools | 6 |
| Library code (tracked_core) | 669 lines |
| Library code (tracked_execution) | 1727 lines |
| MCP server | 1173 lines |
| Pandas domain (POC) | 299 lines |
| Root .md files | 3 |
| Reference docs | 10 |
| Examples | 10 |
| Benchmarks | 7 |
| Commits this session | ~25 |
| Total commits (both sessions) | ~130 |

## Architecture summary

```
tracked_core/         669 lines — domain-independent
  dag.py              — DAG with GC
  proxy.py            — TrackedProxy with dispatch_fn slot
  dispatch.py         — stable_hash, _dag_call, generic dispatch

tracked_execution/    1727 lines — PyVista visualization domain
  dispatch.py         — PyVista-specific whitelist wrapper
  executor.py         — execute_pipeline, inspect_pipeline, tracked_read
  proxy.py            — registers PyVista dispatch as default
  reconciler.py       — SceneReconciler with in-place property updates
  runner.py           — Session class
  vtk_escape.py       — vtk_escape, vtk_escape_multi
  watcher.py          — file watching with debounce
  whitelist.py        — curated PyVista/numpy whitelist

mcp_server/           1173 lines — MCP server
  server.py           — 6 tools, shared read cache, main-thread queue
  run.py              — entry point (offscreen/interactive/trame modes)
  trame_viewer.py     — TrameViewer for browser-based rendering

tracked_data/         299 lines — pandas proof-of-concept
  dispatch.py, executor.py, whitelist.py + 8 tests
```

## Key design principles applied

1. **One path, no redundancy** — 6 tools instead of 45, no aliases
2. **Fail loudly** — ValueError for missing scalars=, not clever workaround
3. **File is the artifact** — pipeline files visible in IDE, not hidden behind tools
4. **Design for interactive** — main-thread queue even in offscreen mode
5. **Hide infrastructure** — agents don't know about threading or caching
6. **Small and beautiful** — each module has one job

## What a user should try

1. Clone the branch
2. Follow `docs/try-it-out.md` to configure Claude Code
3. Point at wildfire or bonsai data
4. Let Claude explore and build visualizations
5. Watch the caching in action (second run is instant)
