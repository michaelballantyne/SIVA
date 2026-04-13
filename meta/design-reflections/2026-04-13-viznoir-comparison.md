# VizNoir Comparison Analysis — 2026-04-13

Analysis of VizNoir (Imgyu Kim, 2025) and ideas worth adapting for
VisLang. Based on thorough source code review of the VizNoir repository.

Source: https://github.com/kimimgo/viznoir

## What VizNoir Is

VizNoir is a headless VTK rendering engine exposed as an MCP server,
targeting autonomous AI agent workflows for scientific visualization.
22 MCP tools, 12 resources, 4 prompts. Supports 50+ file formats via
VTK readers + meshio. Purely headless (EGL/OSMesa) — no interactive
render window.

The tagline: "Cinema-quality science visualization for AI agents."

## Fundamental Difference in Problem Framing

| | VizNoir | VisLang |
|---|---|---|
| Core problem | Autonomous headless rendering | Collaborative visualization authoring |
| Human role | Not present during rendering | Co-author observing and steering |
| Interaction model | Agent constructs pipeline JSON, gets images back | Conversational iteration on a shared artifact |
| Render model | Purely headless, images via MCP | Interactive window + live spec file |
| State model | Stateless — each call independent | Persistent renderer with version history |
| Pipeline structure | Linear filter chain (JSON list) | DAG of named nodes (Python bindings) |

VizNoir treats the human as a **consumer of rendered images**. VisLang
treats the human as a **co-creator of a visualization**.

## Architecture: Compiled Pipelines vs. Interpreted DSL

**VizNoir** uses a three-stage pipeline: `PipelineDefinition` (Pydantic
model) → `ScriptCompiler` (generates a VTK Python script string) →
`VTKRunner` (executes in-process or Docker). The compiler is a code
generator — it produces standalone Python scripts from the declarative
JSON spec.

**VisLang** uses an interpreter: the DSL file is executed as Python,
registering nodes in a desired-state graph, which the interpreter wires
into VTK rendering objects directly. No intermediate script generation.

**Tradeoff**: VizNoir's compilation approach enables Docker isolation
and prevents state leakage between runs — good for autonomous agents
where reliability matters. VisLang's interpreter approach enables
persistent renderer state, interactive manipulation, and reader
caching — good for interactive sessions where responsiveness matters.

## The Pipeline DSL

VizNoir's `PipelineDefinition`:
```python
class PipelineDefinition(BaseModel):
    source: SourceDef          # Single source file
    pipeline: list[FilterStep]  # Linear chain of filters
    output: OutputDef          # Single output (image/animation/data)
```

Each `FilterStep` is `{filter: str, params: dict}` — the output of
step N feeds into step N+1. No branching, no named references, no
DAG.

VisLang's DSL:
```python
data = source("vtkXMLStructuredGridReader", FileName="file.vts")
vel = make_vector(input=data, x="u", y="v", z="w")
vort = curl(input=vel)
ground_vort = extract_grid(input=vort, VOI=[0, 599, 0, 499, 0, 0])
show(ground_vort, "vorticity", color_by="curl_magnitude")
```

Named nodes, explicit DAG references, multiple `show()` calls
composing a single scene. The vorticity case study pipeline is not
expressible in VizNoir's linear filter chain.

## Physics-Aware Intelligence

This is where VizNoir has ideas worth studying carefully.

### Smart defaults (`engine/physics.py`)

Regex-based field name detection maps common names (pressure, velocity,
temperature, stress, vorticity, etc.) to recommended colormaps, camera
positions, representations, and visualization techniques. For example,
detecting a velocity field triggers streamline/glyph suggestions;
detecting stress triggers warp visualization suggestions.

VisLang has nothing like this. Our DSL reference tools describe what
forms exist and how to use them, but don't recommend *which* technique
to use based on the field type. The LLM figures this out from general
knowledge, which works but is fragile for unusual fields.

### Field topology analysis (`engine/topology.py`)

Q-criterion vortex detection with greedy clustering, critical point
detection (stagnation points, vortex centers), centerline probing,
gradient statistics. This is automated physical feature extraction —
"here are the vortices in your data, here are the stagnation points."

VisLang's query tools provide raw statistics (min, max, percentiles,
histograms) and parameter suggestions (isosurface values, opacity
transfer functions). We don't do feature extraction.

### Solver-specific context parsing (`context/` package)

For OpenFOAM cases: extracts boundary conditions, transport properties,
solver configuration, Reynolds number, mesh quality. For CGNS and
DualSPHysics: particle spacing, gravity, domain decomposition.

VisLang has domain files (`domains/wildfire.md`) with static knowledge
but no runtime extraction of solver metadata.

### Domain detection (`harness/domain_hints.py`)

Extension-based and field-name-based classification into CFD, FEA, SPH,
or generic domains. Drives which physics defaults apply.

## Animation and Cinematic Rendering

VizNoir has substantial animation support:
- Timestep-based animation with 7 presets and 17 easing functions
- Orbit camera animation
- Multi-pane synchronized animation (render + graph panes)
- Video compilation (MP4/WebM/GIF) via ffmpeg
- PCA-based auto-camera positioning
- 3-point lighting presets, SSAO, FXAA, PBR materials

VisLang has basic screenshot capture and no animation support. For
the paper's focus on interactive exploration this is fine, but for
producing communication artifacts (presentations, publications) it's
a gap.

## Auto-Postprocess (`harness/auto_postprocess.py`)

VizNoir has an autonomous agent loop: inspect data → detect domain →
produce 3-5 visualizations automatically → optionally evaluate and
refine. This is similar to what VisLang's AI does in the autonomous
phase of the case study, but codified as a built-in workflow.

## Ideas Worth Adapting for VisLang

### 1. Physics-aware technique suggestions

**What VizNoir does**: Maps field names to visualization technique
recommendations. Detecting velocity → suggest streamlines/glyphs.
Detecting stress → suggest warp visualization.

**What VisLang could do**: Add a `suggest_technique(node, field)` query
tool that analyzes field type (scalar/vector/tensor), name patterns,
and data characteristics to recommend DSL forms. This would sit
alongside `suggest_isosurface()` and `suggest_opacity()` as part of
the query workflow. The LLM already does this reasoning from general
knowledge, but encoding it in a tool would make it more reliable and
would also be valuable to the human through a future LSP channel.

### 2. Field topology / feature extraction

**What VizNoir does**: Q-criterion vortex detection, critical point
detection, gradient statistics — automated identification of
physically interesting features.

**What VisLang could do**: Add query tools like
`find_features(node, field)` that identify vortex cores, stagnation
points, extrema clusters, or other structures. These could feed into
the suggest workflow: "I found vortex structures at these locations;
consider streamline seeding near them." For the wildfire case study,
this could have automatically suggested seed placement near the fire
front for the streamline view — the insight the human had to
contribute manually.

This connects to the broader vision of the query layer encoding domain
knowledge that compensates for LLM weaknesses.

### 3. Solver/format-aware context extraction

**What VizNoir does**: Parses OpenFOAM case directories to extract
boundary conditions, transport properties, Reynolds number.

**What VisLang could do**: For supported formats, extract metadata
beyond what VTK readers provide. For structured grids, we already
detect terrain-following coordinates. We could extend this to detect
simulation type (from field names and file format), extract timestep
information, identify periodic boundaries, etc. This metadata would
enrich `describe_data()` output and help the AI make better-informed
initial choices.

### 4. Domain detection → default workflows

**What VizNoir does**: Classifies datasets into CFD/FEA/SPH/generic
and applies domain-specific defaults.

**What VisLang could do**: Domain detection could inform the MCP
system instructions dynamically. If we detect a CFD dataset, the
workflow guidance could emphasize velocity visualization, pressure
fields, streamlines. If we detect medical imaging, emphasize volume
rendering and transfer function tuning. Currently our domain knowledge
is static (the wildfire domain file). Making it data-driven would
generalize better.

### 5. Compiled pipeline export

**What VizNoir does**: Compiles declarative specs into standalone
Python/VTK scripts.

**What VisLang could do**: Add a `export_standalone()` tool that
compiles the current spec into a self-contained Python script (no
VisLang dependency). This supports the "exploration to communication"
vision — after building a visualization interactively, export it as
a portable script for colleagues who don't have VisLang installed.
The spec already contains all the information needed.

### 6. Animation as a first-class output

**What VizNoir does**: Timestep animation, orbit cameras, multi-pane
synchronized animation with graph overlays, video compilation.

**What VisLang could do**: For time-series data, add
`animate(timesteps, fps)` as a DSL form or MCP tool. The declarative
spec already describes a single timestep — extending it to a sequence
is natural. Orbit animation around a finished visualization would also
be useful for presentations. This is lower priority than the query
layer improvements but would strengthen the "exploration to
communication" story.

## Ideas That Don't Fit VisLang's Design

### Stateless execution
VizNoir's stateless model (fresh build every call, no persistent state)
is a feature for autonomous headless workflows but would be a
regression for VisLang. Our persistent renderer with reader caching
enables interactive responsiveness. The teardown/rebuild model is a
design choice we share, but we maintain the renderer across rebuilds.

### Purely headless rendering
VizNoir has no interactive render window by design. For VisLang, the
interactive window is central — the human's ability to rotate, zoom,
and visually inspect the scene is a core input channel that drives
the collaborative workflow.

### Docker isolation
VizNoir can run VTK scripts in Docker containers for safety. Our
sandboxing concern (noted in the paper's limitations) could be
addressed differently — a restricted Python interpreter for the DSL
rather than full containerization, since we need the VTK objects to
live in the same process as the renderer.

## Summary

VizNoir is the closest existing system to VisLang in technical
architecture, but optimized for a fundamentally different use case:
autonomous agent rendering vs. human-AI collaborative exploration.
Its physics-aware intelligence layer (technique suggestions, feature
extraction, solver context) is genuinely ahead of VisLang's query
tools in domain sophistication and represents the most actionable
set of ideas to adapt. The core differentiator — the shared readable
spec, interactive window, version history, and DAG-based composition
that enable collaborative authoring — remains VisLang's unique
contribution.
