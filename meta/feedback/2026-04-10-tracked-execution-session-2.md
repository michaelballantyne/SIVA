# Tracked Execution — Extended Session Feedback

Date: 2026-04-10 (continued independent work session)

## Session overview

This was a long independent session (~9 hours allocated) that took the
tracked execution prototype from "core library with demos" to a complete,
documented MCP server tested end-to-end with real scientific datasets.

## What was accomplished

### MCP server (7 tools, 57 tests)
Built a standalone FastMCP server with:
- `set_working_directory` — point at data
- `create_view` — watch a pipeline file, return data description
- `inspect` — ad-hoc queries on cached data
- `screenshot` — capture current render as PNG
- `list_views` / `close_view` / `pipeline_status` — view management

Key architectural decision: the agent writes pipeline files directly
(visible in the human's IDE), the server watches for changes. No
`update_pipeline` tool — the file IS the interface.

### End-to-end testing
- Wildfire: 18.3M point HIGRAD/FIRETEC simulation, threshold → surface → screenshot
- Bonsai CT: 16.8M point 256^3 CT scan, threshold segmentation → isosurface
- Complex workflows: multi-view, error recovery, inspect-driven refinement, vtk_escape

### Interactive VTK window support
Main-thread queue pattern for VTK thread safety. All VTK operations
marshaled from background threads (MCP handlers, watchers) to the main
thread event loop. Same pattern as the original VisLang renderer.

### Correctness fixes
- Watcher debounce race (moved reload inside lock)
- `__iter__` bypassing whitelist (routed through dispatch)
- `set_active_vectors/tensors` blacklisted (same hazard as scalars)
- `active_scalars` enforcement changed from warning to ValueError

### 6 simplification rounds
Progressive cleanup: merged modules, extracted helpers, removed aliases,
auto-generated operator boilerplate, deleted dead code, synced docs.
Net effect: same functionality in less code with consistent patterns.

### Documentation
- 4 user-facing docs (getting-started, mcp-reference, pipeline-reference, architecture)
- Agent-facing guide (AGENT-GUIDE.md)
- Adapted vision document (VISION.md)
- Design principles (CLAUDE.md)

## Key design decisions and why

### "File is the artifact" (not a tool parameter)
The human sees the pipeline file in their editor. The agent writes it
via normal file operations. The watcher picks up changes. This means:
- IDE tools render agent edits naturally
- No `update_pipeline` MCP tool needed
- Version history is git, not a custom mechanism
- Both human and agent can edit the same file

### "One path, no redundancy"
Started with `inspect_exec` alias → removed. Had `describe_file` tool →
merged into `create_view` output. Had `add_mesh` in namespace → removed
(just `show`). Every duplicate API surface was eliminated.

### "Fail loudly"
The `scalars=` issue went from "include in hash cleverly" → "emit a
warning" → "raise ValueError." Each step was more honest. The final
approach catches bugs rather than hiding them.

### "Design for interactive windows"
Even though testing was all offscreen, the architecture supports
interactive VTK with the main-thread queue. The reconciler exists for
smooth updates. The threading model is correct even if not exercised
in CI.

## What I learned about the development process

### Subagent orchestration
The most productive pattern: brief a subagent with specific context,
let it work, review the result lightly (git log + test count), push,
move on. Key lessons:
- **Worktree isolation** is essential for parallel agents
- **WIP commits** keep the stop hook happy without disrupting work
- **One reviewer at a time** — three parallel reviewers was wasteful
- **Review after milestones, not continuously** — review overhead can
  dominate implementation time

### Human feedback integration
The most valuable moments were when the human caught design issues that
automated review wouldn't: "shouldn't the agent not need to think about
threading?", "do we need both describe_file AND create_view?", "fail
loudly rather than work around."

These translated into CLAUDE.md principles that improved subsequent
agent work — the principles compound.

### The value of building before designing
The original VisLang had a detailed VISION.md written before the tracked
execution experiment. The experiment invalidated several assumptions
(need for custom DSL, need for many MCP tools) and validated others
(need for caching, need for interactive windows). Building the prototype
first, then writing the vision, produced a more grounded document.

## What's still rough

1. **Numpy proxy interop** — `np.sqrt(proxy)` returns a proxy that can't
   be assigned to mesh fields. The `__array__` protocol fix is in progress.

2. **Shared read cache never evicts** — memory leak for long sessions.
   Needs LRU or reference-counted eviction.

3. **No Trame integration** — prototype in progress but not validated.
   Key question: can Trame and the MCP server share an event loop?

4. **Watcher reliability** — works in tests but untested in real
   interactive sessions where timing matters.

5. **VTK passthrough hazard** — when all points pass a threshold, VTK
   shares source buffers. Source mutation can corrupt cached results.
   Documented but not fixed.

## Metrics

- Total tests: 271 + 1 xfail
- Library: ~2680 lines (including MCP server)
- Test code: ~5000+ lines
- Documentation: ~1500 lines (4 user docs + agent guide + vision + claude.md)
- Benchmarks: 5 scenarios
- Examples: 8 demos
- Datasets tested: wildfire (18.3M pts), bonsai CT (16.8M pts)
- MCP tools: 7 (down from 45 in original VisLang)
- Caching speedup: 1x to 4000x depending on edit type
