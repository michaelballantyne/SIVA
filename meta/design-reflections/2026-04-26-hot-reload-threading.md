# Hot-Reload Threading: Adversarial Review

**Date**: 2026-04-26
**Scope**: `vislang/hot_reload.py` (BuildCoordinator + PipelineWatcher) and
its `vislang/server.py` integration. Adversarial bug-hunt.

## Findings

### 1. Deadlock — shutdown() while a build holds the work-queue (REAL, latent)

`BuildCoordinator.shutdown()` (line 178) sets `_shutdown=True`, signals
`_work_event`, and joins the worker for 5 s. That join can hang forever in
**interactive mode** if it is invoked from the main thread while a build is
mid-render-phase: the worker thread is blocked in
`renderer.run_on_main_thread(...)` (line 260/271), which sits on a
`result_queue.get()` waiting for the main thread to drain
`_shared_work_queue` (renderer.py:115). If the main thread is the one
calling `ctx.shutdown()` (via `destroy_view` / process teardown,
server.py:1620), the queue will never be drained and the join times out
after 5 s, leaving the worker stranded with a half-applied scene. The 5 s
timeout prevents a true hang but the renderer state is undefined. **Fix
sketch**: in `shutdown()`, after setting `_shutdown` but before `join`,
either (a) drain any pending main-thread callbacks if `threading.get_ident()
== _main_thread_id`, or (b) require shutdown to be called *off* the main
thread, with the event loop still running long enough to flush.

### 2. Lost update — pending replacement orphans (REAL)

The feedback note (#3) calls this out and labels it harmless. It is not.
In `request_build` (line 117), when a new hash arrives while another hash
is pending, `self._pending_record` is overwritten without setting
`_done_event` on the displaced record (lines 140-142). Any caller holding
the old record from a prior `request_build()` blocks in `record.wait()`
forever (or until its 120 s timeout, server.py:530). This bites
`wait_for_current` on rapid edits: caller A gets pending record R1, caller
B (or the watcher) replaces with R2 before the worker picks up R1, A's
`wait()` never returns from R1's perspective. `wait_for_current` returns
"timed out" to the user even though the file *did* build. **Fix sketch**:
when overwriting `_pending_record`, set `old._done_event` and copy
the eventual finished-record fields from R2 onto R1 once it completes —
or simpler, give the old record `status="superseded"` and have callers
re-resolve via `latest()` matching their original hash.

### 3. Stale result — `wait_for_current` race with concurrent save (LATENT)

`wait_for_current` (line 146) reads the file, hashes it, then takes the
lock. Between the read and the lock acquisition, the file can change. The
returned record's `source_hash` is then *not* the disk-current hash. In
practice this self-corrects on the next call, but a single
`run_pipeline()` can return stale output. **Fix sketch**: cheap — re-read
and re-hash inside the lock, or accept it as a documented one-call
staleness window (the watcher will queue another build).

### 4. File-system race — atomic-write read failure (REAL, low-frequency)

Editor atomic-writes (rename-over) trigger `on_created` / `on_modified`
on the new inode. The watcher then calls `coordinator.request_build()`
which calls `_read_file()` (line 188). On Linux this is fine; on
some macOS editor configurations the inode briefly does not exist between
`on_deleted` and `on_created`. `_read_file` catches `FileNotFoundError`
and returns `None`, which causes `request_build` to silently no-op. No
retry. Fine for the watcher (next event will fire), but
`wait_for_current` can return `None` to the caller and produce a
"File not found" MCP response one tick after the editor saved.
**Fix sketch**: in `_read_file`, retry once after ~20 ms on
`FileNotFoundError`.

### 5. Watcher path-filter precision (HANDLED)

`PipelineWatcher` resolves the watched path once (line 351) and compares
resolved paths in `_handle` (line 370). Sibling files (`view-main.py.tmp`,
`view-other.py`) are correctly filtered. Recursive=False. This is solid.

### 6. Renderer-thread starvation (NOT REAL)

Each build issues at most three `run_on_main_thread` calls (apply,
screenshot, plus camera read in `_build_report`). Worker is single-threaded
and only one build runs at a time, so the queue depth stays bounded by
~3 entries. Not a concern.

### 7. Crash recovery during render phase (LATENT)

If `_apply_to_renderer` raises mid-mutation (e.g. one actor added, next
one fails), the `except Exception` (line 295) marks the record as error
but leaves the renderer in a partially-mutated state and `ctx.vtk_objects`
not updated. The next successful build will overwrite, but
`pipeline_status` and the screenshot for the failed build show the
inconsistent intermediate. The cache itself isn't poisoned (cache writes
happen in `interpret_build` before the render phase, on a separate
keyspace). **Fix sketch**: have `_apply_to_renderer` build into a temp
dict, then atomically swap into `renderer._actors` only if it completes;
out of scope for this layer.

### 8. Double-watcher events on a single ViewContext (LATENT)

`ViewContext.__init__` (server.py:228) starts a watcher unconditionally.
If a test or new-view flow creates two ViewContexts pointed at the same
`pipeline_file`, both watchers fire on every save and both coordinators
race on the `_save_version_for` counter. Unlikely in production but easy
to hit in tests. Not urgent.

## Priority

1. **Pending-replacement orphans** (item 2) — actually wedges callers.
   Fix before relying on watcher-driven flows in agent sessions.
2. **Shutdown deadlock** (item 1) — only manifests during view teardown
   with an inflight build, but the 5 s silent timeout is misleading.
3. **wait_for_current re-hash** (item 3) — one-line fix, do it.
4. **Atomic-write retry** (item 4) — one editor config away from being
   user-visible.

Items 5-8 are fine to leave or defer.
