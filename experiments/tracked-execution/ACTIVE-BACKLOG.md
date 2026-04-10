# Tracked Execution — Active Backlog

Updated 2026-04-10.

## Current State

**Library:** 2680 lines, 266 tests + 1 xfailed, all passing.

**MCP server:** 7 tools (set_working_directory, create_view, inspect,
screenshot, list_views, close_view, pipeline_status). Interactive VTK
window mode with main-thread queue. Shared read cache across views.

**Completed this session:**
- Core library: TrackedProxy, DAG, dispatch, whitelist, executor, reconciler
- MCP server with all tools, file watching, error handling
- Offscreen rendering validation (8 tests)
- Wildfire end-to-end agent test (4 tests, session log)
- Bonsai CT end-to-end agent test (4 tests, session log)
- Purity analysis: 3 hazards documented, active_scalars enforced via ValueError
- VTK escape hatch: vtk_escape + vtk_escape_multi, 24 tests, design doc
- Benchmarks: 5 scenarios showing 1x to 4000x speedup
- 5 simplification rounds (internal code, API surface, naming)
- Error messages made agent-friendly (44 tests)
- Active_scalars enforcement (ValueError, not warning)
- Shared read cache across views (11 tests)
- Interactive VTK window mode (main-thread queue + event loop)
- CLAUDE.md with design principles
- describe_file merged into create_view output

## Completed This Session

- [x] Extract domain-independent tracked_core from tracked_execution —
  Created tracked_core/ with DAG, TrackedProxy, stable_hash, _dag_call,
  _should_wrap, and generic dispatch(whitelist, blacklist, ...). tracked_execution
  now imports from tracked_core. Key: TrackedProxy stores dispatch_fn as a slot;
  tracked_execution.proxy registers PyVista dispatch as the default so 3-arg
  TrackedProxy(real, hash, dag) still works in all 229 tests + 1 xfailed.

## Do Now

### Simplification pass
- [ ] Remove `pipeline_status` if it overlaps too much with `list_views`
      (or merge the unique info into `list_views`)
- [ ] Check that `show` and `add_mesh` aren't both in the namespace
      unnecessarily — pick one
- [ ] Verify watcher callback doesn't need the lock around execute_pipeline
      (only around reconcile, which touches VTK)

### Documentation sync
- [ ] Ensure AGENT-GUIDE.md matches current MCP tool set (no describe_file,
      create_view returns data description, pipeline_status exists)
- [ ] Ensure README.md matches current module structure (no core.py)
- [ ] Update INSTRUCTIONS with interactive vs offscreen mode info

### Remaining review items (from REVIEW-ACTIONS.md)
- [ ] `_shared_read_cache` never evicts — add LRU or evict when no view refs
- [ ] `_shared_tracked_read` bypasses `_dag_call` — use consistent pattern
- [ ] Document view name derivation in INSTRUCTIONS
- [ ] `_NUMPY_SINGLE_ARG` naming misleading
- [ ] Lazy import in server.py

### Testing gaps
- [ ] Test interactive mode manually (not automated — needs real display)
- [ ] Test watcher picks up file changes and re-executes (not just unit tests)
- [ ] Stress test: rapid file saves, verify no race conditions

## Later

- [ ] Trame viewer integration
- [ ] Pydantic Monty integration when opaque objects are supported
- [ ] LSP for pipeline files
- [ ] Reconciler in-place property updates (change colormap without recreating actor)
- [ ] Defensive copy option for cached filter outputs (VTK passthrough hazard)
