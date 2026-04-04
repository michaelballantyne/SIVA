# VisLang Work State
# Updated by Claude during independent work sessions.
# Read this file first when resuming work.

## Deadline
Sat Apr 4, 12:00 UTC 2026

## Status: WORKING
## Last checked: (update with `date -u` output)

## What's Done
- Full MCP server with 13 tools (set_pipeline, screenshot, queries, etc.)
- DSL interpreter with restricted exec namespace
- Headless VTK renderer with light kit
- 8 colormap presets
- Reader caching (7.5x speedup)
- Smart empty-output diagnostics
- 16 integration tests (all passing)
- 23 E2E MCP session checks (all passing)
- 8 showcase demo renders
- CHALLENGES.md with 8 pain points documented
- Tested: terrain, fire isosurfaces, streamlines, glyphs, vorticity, threshold

## What's In Progress
- Adding suggest_camera tool (code written, needs testing)
- Adding vtkCutter/vtkClipDataSet/vtkProbeFilter to whitelist (added, needs testing)
- Cross-section slicing support

## What's Next (priority order)
1. Test suggest_camera and cross-section tools
2. Add CutFunction property handling for vtkCutter (needs vtkPlane support)
3. Try more contest-winner visualization challenges
4. Add convenience DSL function for cross-sections: `slice(input, origin, normal)`
5. Improve the showcase demo with new visualizations
6. Try to create a multi-panel comparison view
7. Add more filter classes as needed

## Key Files
- vislang/server.py - MCP server (13 tools)
- vislang/dsl.py - DSL interpreter
- vislang/filters.py - VTK filter creation + whitelist
- vislang/renderer.py - Headless renderer
- vislang/queries.py - Data query tools
- vislang/colormaps.py - Color presets
- tests/test_integration.py - 16 tests
- demos/showcase.py - 8 demo renders
- CLAUDE.md - LLM reference
- CHALLENGES.md - Pain points

## Data
- output.30000.vts: 1.1GB, 600x500x61, 18.3M points, 9 fields
- Terrain-following grid, ground z varies 0.75-196
- Fire at x=[28,134], y=[-46,24], z=[132,221] (theta>400K, 3831 points)
