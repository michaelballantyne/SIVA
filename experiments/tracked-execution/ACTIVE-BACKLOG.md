# Tracked Execution — Active Backlog

Updated 2026-04-11.

## Current State

**Library:** 3482 lines, 290 tests + 1 xfailed, all passing.
**MCP server:** 6 tools (set_working_directory, create_view, inspect,
screenshot, list_views, close_view).
**Modes:** offscreen, interactive VTK, Trame (browser).
**Domains:** PyVista visualization (primary), pandas data processing (proof-of-concept).

## Completed

### Polished product (tagged at b5cbe74)
- Core library: TrackedProxy, DAG, dispatch, whitelist, executor, reconciler
- MCP server with file watching, error handling, shared read cache
- End-to-end tested with wildfire (18.3M pts) and bonsai CT (16.8M pts)
- Benchmarks, user docs, agent guide, CLAUDE.md, adapted VISION.md

### Speculative (after tag)
- Numpy proxy `__array__`/`__array_wrap__` fix
- Trame viewer prototype + MCP integration (--trame mode)
- LRU eviction for shared read cache
- Domain-independent `tracked_core` extraction
- Pandas domain proof-of-concept (8 tests)
- Merged pipeline_status into list_views (7→6 tools)
- Removed make_proxy factory

## Do Now

### Next simplification targets (from review)
- [ ] Move PyVista purity tests (that test PyVista behavior, not our library)
      from test_purity.py to PURITY-ANALYSIS.md
- [ ] Collapse `_BLACKLIST_REASONS` __i*__ entries to share one message
- [ ] Unify blocked stubs into one factory

### Interactive mode validation
- [ ] Manual test of interactive VTK mode (needs real display)
- [ ] Manual test of Trame mode (needs browser)

### Documentation
- [ ] Update docs/ to reflect 6-tool MCP (was 7)
- [ ] Update AGENT-GUIDE.md to remove pipeline_status references
- [ ] Consider consolidating overlapping docs

## Later
- [ ] Trame tabbed multi-view UI polish
- [ ] Pydantic Monty integration when opaque objects supported
- [ ] LSP for pipeline files
- [ ] Reconciler in-place property updates
