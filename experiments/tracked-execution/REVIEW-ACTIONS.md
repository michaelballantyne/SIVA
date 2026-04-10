# Review Action Items — 2026-04-10

Consolidated from three parallel reviews: API consistency, architecture, UX.

## High Priority (correctness / blocking)

1. **[DONE] Watcher debounce race** — two concurrent OS events can both pass the
   debounce gate. Fix: move the reload call inside the lock, or use a
   single-shot timer that resets on each event.

2. **[DONE] `__iter__` bypasses whitelist** — iterating a proxy skips blacklist
   checks and cache accounting. Fix: route iteration through dispatch.

3. **[DONE] `screenshot()` raises ValueError, others return error strings** —
   inconsistent. Fix: return error string like all other tools.

4. **[DONE] Blacklist `set_active_vectors`/`set_active_tensors`** — same hidden
   state hazard as `set_active_scalars`.

## High Priority (UX / agent experience)

5. **Add `update_pipeline(file, code)` tool** — writes code to file, waits
   for watcher, returns result. Closes the loop so agent works entirely
   via MCP.

6. **Add `describe_file(data_file)` tool** — read file, return fields/dims/
   bounds without needing a pipeline or view.

7. **Add "display params are free" + colormap info to INSTRUCTIONS** — key
   performance insight missing from the MCP description.

## Medium Priority

8. **`_shared_read_cache` never evicts** — memory leak. Fix: LRU eviction
   or evict when no view references the entry.

9. **`_shared_tracked_read` bypasses `_dag_call`** — duplicates bookkeeping.
   Fix: use `_dag_call` or add `DAG.inject()`.

10. **[DONE] File-path heuristic fragile** — `"\n" not in code and code.endswith(".py")`
    misclassifies one-line code. Fix: use `Path.exists()`.

11. **[DONE] Remove `ExecutionResult.ok`** — dead code, errors always raise.

12. **Document view name derivation** in INSTRUCTIONS (basename sans extension).

## Low Priority / Deferred

13. **`core.py` is a re-export shim** — delete and update imports.
14. **`_NUMPY_SINGLE_ARG` naming misleading** — some take >1 arg.
15. **`inspect_pipeline` lazy import in server.py** — move to top-level.
16. **watchdog Observer type leaked** — wrap in thin `Watcher` class.
