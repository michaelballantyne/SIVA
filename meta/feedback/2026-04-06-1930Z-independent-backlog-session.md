# Independent Backlog Work Session — 2026-04-06

## What was accomplished

Cleared the entire "Do Now (Independent)" High-priority backlog (10 items)
and most of the Medium-priority items (8 of 9). Total: 19+ items completed
in a single session using parallel worktree agents.

### High-priority (all done)
- Phantom tools removal, instructions fix
- KeyError in pipeline status reporting
- run.py return-value unpacking
- Dead code removal (sample_point, set_color_range, benchmark_pipeline)
- camera_orbit node parameter removal
- DSL alias removal (isosurface, compute_velocity, compute_vorticity)
- Title overlay clearing on rebuild
- Legacy global state / _LegacyCtx removal
- get_ground_z output improvement
- Minor server.py cleanups (_parse_color, imports, GetDimensions)

### Medium-priority (all done except server.py split)
- Auto-populate DSL namespace via inspect
- Unify histogram-guided opacity + numpy conversion
- Extract scalar bar builder
- Separate 2D/3D actors in Renderer
- MCP protocol-level tests (105 tests)
- Stateful integration tests (24 tests)
- Field name validation before pipeline execution (28 tests)
- Detect user-closed windows
- VTK whitelist expansion (in progress)
- server.py module split Phase 1 (in progress)

## What worked well

1. **Parallel worktree agents** — Running 3-4 agents simultaneously in isolated
   worktrees was highly effective. Most agents completed independently without
   conflicts. Total throughput was 3-4x what sequential work would achieve.

2. **Small, well-scoped tasks** — The backlog items were well-decomposed. Most
   agents completed in 2-5 minutes with clean commits.

3. **Incremental commit + push** — Committing after each agent batch and pushing
   kept the remote in sync and made recovery easy.

## What didn't work well

1. **Worktree merge conflicts** — When worktree agents modify the same file
   (especially server.py and filters.py), the changes arrive as unstaged diffs
   in the main repo. This requires manual inspection to ensure nothing was
   overwritten. The opacity unification agent's copy of filters.py overwrote
   the scalar bar builder extraction, for example.

2. **Worktree branches have unrelated histories** — `git merge` fails with
   "unrelated histories." Cherry-picking or file copying is needed instead.
   This is friction that could be reduced.

3. **Agent redundancy** — Several agents "confirmed" work was already done
   (spending tokens on verification) when the work had been committed by
   another agent. This is a minor efficiency issue.

## Observations

- The codebase went from ~450 tests to ~620+ tests this session
- server.py shrank by ~300 lines (dead code, aliases, legacy state)
- The DSL namespace is now self-maintaining (inspect-based)
- Field name validation catches typos before expensive VTK Update() calls
- The Renderer now cleanly separates 3D geometry from 2D overlays
