# Tracked Execution — Active Backlog

Updated 2026-04-11 (end of independent session).

## Current State

**295 tests + 1 xfailed, all stable across multiple runs.**
**6 MCP tools, 3868 lines across 4 packages.**
**3 rendering modes: offscreen, interactive VTK, Trame browser.**

## What's Ready to Try

The system is ready for real use. See `docs/try-it-out.md` for setup.

## Remaining Work (prioritized)

### Polish (should do before trying with real users)
- [ ] Manual test of interactive VTK mode with real display
- [ ] Manual test of Trame mode with browser
- [ ] Consider if `_NUMPY_SINGLE_ARG` naming in executor is confusing

### Small improvements
- [ ] Collapse `_BLACKLIST_REASONS` __i*__ entries to share one message
- [ ] Move PyVista behavior tests from test_purity.py to docs
- [ ] Add execution timing to DAG (record wall time per operation for diagnostics)

### Bigger features (speculative)
- [ ] Reconciler in-place colormap updates (opacity done, colormap needs mapper rebuild)
- [ ] Trame tabbed multi-view UI with working tab switching
- [ ] Cost estimation from prior execution timing data
- [ ] Pydantic Monty integration when opaque objects are supported
- [ ] LSP for pipeline files (data-aware autocomplete)

## Completed (this session)

Everything below was done across two independent work sessions:

### Core
- TrackedProxy, DAG, dispatch, whitelist, executor, reconciler
- Content-addressed caching with hash consing
- Restricted exec namespace with agent-friendly errors
- File watching with debounce
- vtk_escape for raw VTK within tracked pipelines

### MCP Server
- 6 tools: set_working_directory, create_view, inspect, screenshot, list_views, close_view
- Interactive VTK mode with main-thread queue
- Trame mode with browser-based rendering
- Shared read cache with LRU eviction
- Pipeline change reporting in list_views

### Testing
- 295 tests + 1 xfailed
- Wildfire e2e (18.3M points), bonsai CT e2e (16.8M points)
- Complex workflow simulation (24-step wildfire session)
- Purity analysis (3 hazards documented, active_scalars enforced)
- Flaky watcher tests fixed (debounce-aware polling)

### Architecture
- Domain-independent tracked_core extracted
- Pandas domain proof-of-concept (proves generalization)
- Reconciler with in-place opacity updates

### Documentation
- README, CLAUDE.md, VISION.md at root
- 10 reference docs in docs/
- Try-it-out guide for setting up with Claude Code
- Agent guide for MCP pipeline authoring
- Purity analysis, VTK escape pattern, generalization sketch
