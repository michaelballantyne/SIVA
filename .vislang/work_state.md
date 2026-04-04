# VisLang Work State
# Updated: Sat Apr 4 07:31 UTC 2026
# Deadline: Sat Apr 4, 12:00 UTC 2026

## Status: WORKING

## What's Done
- Full MCP server with 16 tools (including suggest_opacity)
- DSL interpreter with 25+ convenience wrappers (clip, probe, resample_to_image,
  raw_source, scene_preset, etc.)
- Headless VTK renderer with light kit + suggest_camera
- 8 colormap presets + scalar bar (color legend) support
- Reader caching (7.5x speedup) for both VTS and VTI readers
- Smart empty-output diagnostics (field-not-found, out-of-range, seed hints)
- MCP server instructions with incremental-build strategy for LLM guidance
- get_examples() tool with 9 copy-pasteable pipeline patterns
- 41 integration tests (all passing)
- Cross-domain: 4 datasets tested (wildfire, bonsai, ctBones, cthead)
- work_state.md self-management system
- **Volume rendering support** (representation="Volume"):
  - vtkSmartVolumeMapper with auto vtkResampleToImage resampling
  - Configurable opacity transfer functions (custom, presets, auto-ramp)
  - Auto-opacity: histogram-guided transfer function generation
  - Gradient opacity for edge enhancement
  - Clipping planes for volume cropping
  - Shade control, sample distance, material properties
  - Scalar bar support
  - Empty data error guard with diagnostic hints
- suggest_opacity MCP tool for histogram-guided transfer functions
- Pipeline timing in set_pipeline reports
- 28+ VTK classes whitelisted (including vtkImageReader2 for raw binary)
- raw_source() DSL function for loading raw binary volume files
- clip(), probe(), resample_to_image() DSL wrappers
- scene_preset() for quick scene configuration
- Field-specific default colormaps and scalar ranges (11 fields)
- Multiple scalar bar positioning (side-by-side)
- All 9 wildfire dataset targets achieved:
  1. Basic wildfire demo ✓
  2. Wind vector glyphs ✓
  3. Vorticity visualization ✓
  4. O2 depletion ✓
  5. Combined multi-layer ✓
  6. Radiative heat transfer ✓
  7. Cross-section slices ✓
  8. Volume rendered fire plume ✓
  9. Volume rendered vorticity ✓
- 3 download scripts for CT datasets
- Contest-winner-style 5-layer visualization verified
- CHALLENGES.md with 10 documented pain points

## What's In Progress
- Looking for more high-value improvements

## What's Next (priority order)
1. Improve volume rendering quality defaults
2. Add animation/time series support concept
3. Performance profiling across different pipelines
4. Try reproducing more contest winner figures
5. Explore vtkStreamSurface or similar advanced VTK features

## Key Stats
- 16 MCP tools
- 41 integration tests (all passing)
- 28+ VTK classes whitelisted
- 9 get_examples patterns
- 4 datasets tested (wildfire, bonsai CT, ctBones CT, cthead raw)
- 3,465 lines of Python
- ~30 commits on branch
- All 9 wildfire visualization targets achieved
