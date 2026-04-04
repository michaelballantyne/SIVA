# Design Reflection: April 4, 2026

## Where we are

VisLang is a working system: an MCP server exposing ~34 tools over a
declarative VTK DSL with ~40 builder functions. It handles structured grids,
image data, polydata, and raw binary volumes. Volume rendering, streamlines,
isosurfaces, slices, glyphs, and computed fields all work. 46 tests pass.
The wildfire dataset targets (9/9) are all checked off.

The system has been tested by multiple agent sessions and the feedback is
rich (see `feedback/`). The original DESIGN.md and PLAN.md (Phase 1) are
largely implemented, though the reconciler was replaced by a simpler
tear-down/rebuild approach that's proven fast enough.

## What we've learned

### The DSL is valuable but needs refinement
Feedback consistently says the DSL reduces boilerplate vs raw VTK, but the
API still leaks VTK conventions (PascalCase properties, VTK class names in
`source()` calls, grid-index VOI). The DSL should hide more of VTK —
`load("file.vts")` instead of `source("vtkXMLStructuredGridReader", FileName=...)`,
`slab(data, k=0)` instead of `filter("vtkExtractGrid", VOI=[0,599,0,499,0,0])`.

### Round trips are the main UX bottleneck
Two feedback sessions independently identified excessive round trips as the
biggest pain point. The main culprits:
- `set_pipeline` not returning a screenshot (always needs a follow-up call)
- `describe_data` not including percentile distributions (forces N follow-up
  `get_field_summary` calls)
- Single-point probing requiring N calls for N points

### Silent failures are trust-destroying
The calculator silently producing no output, and `show()` silently falling
back to default coloring when a field doesn't exist, were the most
frustrating experiences reported. Loud failures are better than silent
wrong answers.

### The MCP is largely self-describing
We removed ~500 lines from CLAUDE.md that duplicated what `list_capabilities()`,
`get_examples()`, and tool descriptions already provide. The MCP server
instructions + tool descriptions + domain files are sufficient for users.

## Architecture reflections

The tear-down/rebuild approach works well enough thanks to reader caching.
The reconciler from the original design is probably not worth building unless
rebuild times become a problem with more complex pipelines.

The `pipeline.py` file-based approach (write code to a file, then execute)
works but could evolve toward a session-based workspace model where each
visualization exploration gets its own folder with history.

## Strategic direction

**Near-term: reduce round trips and fix silent failures.** These are the
highest-impact changes for user experience. Specifically:
1. Auto-screenshot from state-changing tools
2. Rich `describe_data` with percentiles
3. Loud validation errors throughout the pipeline

**Medium-term: DSL ergonomics.** Make the common case shorter —
`load()`, positional args, auto scalar ranges, snake_case properties.
The DSL should feel like a domain language, not a VTK wrapper.

**Longer-term: cross-domain generalization.** The wildfire demo is solid.
Testing with CT scans and other datasets will reveal what's actually
general vs. what's wildfire-specific.

## Prior design documents

The original `DESIGN.md` contains the full architectural specification
including the reconciler design, ParaView XML metadata approach, and
multi-timestep support. `THREATS.md` (now archived in `feedback/`) examined
where the design might fail. Key open questions from those documents:
- Is a DSL better than raw VTK + good feedback? (Needs empirical testing)
- Is reconciliation worth it? (Current evidence says no, rebuild is fine)
- How general is "any VTK filter"? (Many special cases discovered)
