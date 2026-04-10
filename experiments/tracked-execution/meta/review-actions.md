# Review Action Items — 2026-04-10

Consolidated from three parallel reviews: API consistency, architecture, UX.

## High Priority (correctness / blocking)

1. **[DONE] Watcher debounce race** — moved reload inside lock.
2. **[DONE] `__iter__` bypasses whitelist** — routed through dispatch.
3. **[DONE] `screenshot()` error type** — returns error string like all other tools.
4. **[DONE] Blacklist `set_active_vectors`/`set_active_tensors`** — blocked.

## High Priority (UX / agent experience)

5. **[WONTFIX] `update_pipeline` tool** — the agent writes files directly
   (visible in IDE). The watcher picks up changes. No MCP tool needed.
6. **[DONE] `describe_file` → merged into `create_view`** — one path.
7. **[DONE] INSTRUCTIONS improvements** — colormaps, "display params free",
   view name derivation documented.

## Medium Priority

8. **[DONE] `_shared_read_cache` eviction** — LRU with max 10 entries.
9. **[DONE] `_shared_tracked_read` via `_dag_call`** — uses consistent pattern.
10. **[DONE] File-path heuristic** — uses `os.path.exists()`.
11. **[DONE] `ExecutionResult.ok` removed** — dead code.
12. **[DONE] View name derivation** — documented in INSTRUCTIONS.

## Low Priority / Deferred

13. **[DONE] `core.py` re-export shim** — replaced by tracked_core extraction.
14. **`_NUMPY_SINGLE_ARG` naming** — minor, not worth changing.
15. **`inspect_pipeline` lazy import** — minor.

## All critical and high-priority items resolved.
