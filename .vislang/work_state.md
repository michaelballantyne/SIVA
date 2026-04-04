# VisLang Work State
# Updated by Claude during independent work sessions.
# Read this file first when resuming work.

## Deadline
Sat Apr 4, 12:00 UTC 2026

## Status: WORKING
## Last checked: Sat Apr 4 04:56 UTC 2026

## What's Done
- Full MCP server with 14 tools (set_pipeline, screenshot, queries, suggest_camera, list_capabilities, etc.)
- DSL interpreter with restricted exec namespace + slice() convenience
- Headless VTK renderer with light kit
- 8 colormap presets (terrain, fire, wind, cool_to_warm, oxygen, heat, etc.)
- Reader caching (7.5x speedup)
- Smart empty-output diagnostics (explains why filters produce 0 output)
- 22 integration tests (all passing)
- 23 E2E MCP session checks (all passing)
- 11 showcase demo renders (terrain, fire, oxygen, vorticity, cross-sections, etc.)
- Cross-section slicing with vtkCutter + slice() DSL function
- suggest_camera tool for auto-computing camera positions
- CHALLENGES.md with 8 pain points documented
- Work management: work_state.md, subagent delegation rules

## What's In Progress
- Fixing horizontal slice render (blank image - camera issue)
- Creating water vapor + streamlines combined visualization

## What's Next (priority order)
1. Review subagent output, commit
2. More contest-winner visualization challenges
3. Add convenience DSL features (e.g., auto-seed for streamlines)
4. Improve colormap defaults for common fields
5. Add sample_point to the showcase demo
6. Try reproducing more figures from the contest PDFs
7. Performance: profile and optimize the most expensive operations

## Architecture Notes
- Top-level agent manages only: check time, decide work, launch subagent, review, commit
- Subagents do ALL implementation work
- Use isolation: "worktree" for parallel subagents, or run sequentially
- Never run multiple non-isolated subagents in parallel

## Key Files
- vislang/server.py - MCP server (14 tools)
- vislang/dsl.py - DSL interpreter (+ slice() convenience)
- vislang/filters.py - VTK filter creation + whitelist + reader caching
- vislang/renderer.py - Headless renderer + light kit + suggest_camera
- vislang/queries.py - Data query tools + sample_point + get_ground_z
- vislang/colormaps.py - 8 color presets
- tests/test_integration.py - 22 tests
- demos/showcase.py - 8 demo renders
- CLAUDE.md - LLM reference
- CHALLENGES.md - Pain points

## Data
- output.30000.vts: 1.1GB, 600x500x61, 18.3M points, 9 fields
- Terrain-following grid, ground z varies 0.75-196
- Fire at x=[28,134], y=[-46,24], z=[132,221] (theta>400K, 3831 points)
