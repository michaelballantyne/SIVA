# VisLang Work State
# Updated: Sat Apr 4 07:14 UTC 2026
# Deadline: Sat Apr 4, 12:00 UTC 2026

## Status: WORKING

## What's Done
- Full MCP server with 16 tools (added suggest_opacity)
- DSL interpreter with 20+ convenience wrappers including clip, probe, resample_to_image
- Headless VTK renderer with light kit + suggest_camera
- 8 colormap presets + scalar bar (color legend) support
- Reader caching (7.5x speedup) for both VTS and VTI readers
- Smart empty-output diagnostics (field-not-found, out-of-range, seed hints)
- MCP server instructions with incremental-build strategy for LLM guidance
- get_examples() tool with 8 copy-pasteable pipeline patterns
- 32 integration tests (all passing)
- Cross-domain: CT scan datasets work (bonsai 256³, ctBones 256³)
- work_state.md self-management system
- **Volume rendering support** (representation="Volume"):
  - vtkSmartVolumeMapper with auto vtkResampleToImage resampling
  - Configurable opacity transfer functions (custom, presets, auto-ramp)
  - Auto-opacity: histogram-guided transfer function generation
  - Gradient opacity for edge enhancement
  - Color transfer functions from all colormap presets
  - Clipping planes for volume cropping
  - Shade control, sample distance, material properties
  - Scalar bar support
  - 8 volume-specific integration tests
- suggest_opacity MCP tool for histogram-guided transfer functions
- Pipeline timing in set_pipeline reports
- 27+ VTK classes whitelisted (6 new: WarpVector, MaskPoints, PassArrays,
  AppendFilter, TransformFilter, GradientFilter, ResampleToImage)
- clip(), probe(), resample_to_image() DSL wrappers
- Contest-winner-style 5-layer visualization verified
- ctBones CT dataset download script
- CHALLENGES.md with 10 pain points

## What's In Progress
- Raw binary volume reader (vtkImageReader2) for klacansky.com datasets

## What's Next (priority order)
1. Test raw binary reader and download another dataset
2. Add radiative heat transfer visualization (frhosiesrad_1)
3. Reproduce more contest winner figures
4. Add stream surface support if available in VTK
5. Improve error messages for volume rendering edge cases
6. Performance: test rendering with GPU when available
7. Add animation/time series support for multi-timestep data

## Key Stats
- 16 MCP tools
- 32 integration tests
- 27+ VTK classes whitelisted
- 8 get_examples patterns
- 3 data domains tested (wildfire, CT bonsai, CT bones)
- Volume rendering with full transfer function pipeline
- 10 documented challenges
