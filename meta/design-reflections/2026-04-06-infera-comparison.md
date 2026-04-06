# InferA Comparison Analysis — 2026-04-06

Analysis of InferA (Tam et al., LANL, SC Workshops 2025) and how it
compares to VisLang's approach and broader vision.

Source: https://github.com/lanl/InferA
Paper: https://doi.org/10.1145/3731599.3767342 (arXiv: 2510.12920)

## What InferA Is

InferA is a multi-agent data analysis assistant for terabyte-scale
cosmological simulation data (HACC). It uses LangGraph to orchestrate
5+ specialized LLM agents (Planner, Supervisor, DataLoader, SQL, Python,
Visualization, QA, Documentation) that decompose natural-language queries
into executable analysis workflows. Built on GPT-4o + LangChain, evaluated
on 20 questions across difficulty levels with 10 runs each.

The HACC dataset: multiple simulations × hundreds of timesteps × billions
of particles, terabytes total. Column names like `sod_halo_MGas500c` that
require domain expertise to interpret.

## Fundamental Difference in Problem Framing

| | InferA | VisLang |
|---|---|---|
| Core problem | Automated data analysis at scale | Interactive visualization authoring |
| Primary output | Answers + matplotlib/ParaView plots | A live, manipulable 3D scene |
| Human role | Asks questions, approves plans | Co-authors the visualization |
| Interaction model | Query → plan → execute → results | Conversational iteration on a shared artifact |
| Data scale | Terabytes, hundreds of timesteps | Single files, GB scale |
| Statefulness | Workflow state (LangGraph checkpoints) | Visual scene state (VTK pipeline) |

InferA treats the human as a **consumer of analysis results**. VisLang
treats the human as a **co-creator of a visualization**.

## Architecture: Orchestrated Agents vs. Single-Agent + Rich Tools

**InferA** decomposes intelligence across multiple agents with narrow roles.
Each agent has limited context — it receives only its delegated task. Good
for token efficiency (~40k tokens/run, ~$0.09) but no agent understands
the full picture.

**VisLang** uses a single LLM with ~35 MCP tools. The LLM holds full
context — data understanding, user intent, visualization history — and
makes all decisions itself.

**Tradeoff**: InferA's multi-agent approach scales to complex multi-step
analyses but loses coherence across steps. VisLang's single-agent approach
maintains coherence but can't easily decompose 10-step analysis plans.
For visualization authoring, coherence matters more — the same intelligence
that chose a threshold value needs to know why the colormap was picked.

## The Shared Artifact Question

This is where the systems diverge most, and it's the heart of VisLang's
vision.

**InferA produces code as a byproduct.** Python/matplotlib code is generated,
executed in a sandbox, and saved for provenance. It's not designed to be
read or edited by the scientist. The human never touches the code.

**VisLang produces code as the primary artifact.** The pipeline file is
designed for dual readership — the scientist can audit
`threshold(..., ThresholdRange=[20, 145])`. It's the communication medium,
version-controlled, and the human is expected to eventually co-author it.

InferA has no equivalent. It produces *results*, not *understanding*.

## Data-Aware Intelligence

Both systems recognize LLMs need domain context, but implement it differently:

**InferA**: RAG over metadata dictionaries. Column names mapped to
natural-language descriptions. Per-column chunking (80 tokens max) for
precise retrieval. This is a **pre-analysis** layer — helping the system
understand what data exists.

**VisLang**: Runtime query tools (`describe_data()`, `get_statistics()`,
`suggest_isosurface()`, `get_histogram()`). These operate on live data,
providing statistics, histograms, and data-driven parameter suggestions.
This is an **in-analysis** layer — helping make informed visualization choices.

These are complementary: InferA's approach is better for "finding the right
data" (which columns from terabytes?), VisLang's is better for "making the
right visualization choices" (what threshold reveals the structure?).

## Human-in-the-Loop Design

**InferA**: Gated interaction — plan approval and feedback at designated
checkpoints. Without human feedback (evaluation mode), reliability drops.

**VisLang**: Continuous interaction — the human observes the render window,
edits the pipeline, and converses throughout. Spatial understanding
(rotating, zooming, spotting features) is continuous input.

## Provenance vs. Version History

- **InferA**: Every intermediate DataFrame, code snippet, agent decision.
  Purpose: reproducibility and auditing.
- **VisLang**: Pipeline + screenshot pairs per version. Purpose: exploration
  and rollback.

InferA's provenance is stronger for scientific rigor. VisLang's version
history is stronger for interactive iteration. Both are valuable for
different phases of scientific work.

## Where InferA is Stronger

1. **Scale**: Handles terabytes across hundreds of timesteps. Reduces 11.2 TB
   to 18 GB database + 1.4 MB CSVs. VisLang operates on single files.
2. **Automated multi-step analysis**: Decomposes complex queries into 5+
   automated steps.
3. **Token efficiency**: ~40k tokens per analysis run vs. VisLang's
   potentially much longer interactive sessions.
4. **Provenance**: Every intermediate result saved and auditable.
5. **Sandboxed execution**: Code runs in isolated FastAPI server with
   read-only data access. VisLang currently executes pipeline files as Python.

## Where VisLang is Stronger

1. **The shared artifact**: Pipeline file as readable, editable communication
   medium. InferA has no equivalent.
2. **Interactive 3D visualization**: Full VTK scene with direct manipulation,
   volume rendering, streamlines. InferA produces static plots.
3. **Data-driven parameter intelligence**: Runtime queries informing
   visualization choices.
4. **Declarative state management**: Tear-down/rebuild ensures consistency.
5. **The broader vision**: LSP + MCP dual-channel intelligence, bidirectional
   editing, parameter scrubbing, the pipeline as a learning artifact.

## Ideas Worth Noting for VisLang

1. **Data reduction as first-class concern**: InferA's staged loading
   (DataLoader → SQL filter → Python) is a practical solution for the same
   problem our "scale independence" section addresses abstractly. To work at
   scale, VisLang needs a similar data-reduction pipeline before visualization.

2. **RAG over dataset metadata**: InferA's column-description dictionaries
   are what our domain knowledge files (`domains/`) aim to be. Per-column
   chunking for retrieval is a practical technique.

3. **Plan-then-execute pattern**: InferA's explicit planning stage could
   complement VisLang's free-form conversational model for complex multi-step
   explorations. Our "workflow patterns" future direction aligns.

4. **Provenance tracking**: Could inform our "exploration to communication"
   vision — tracking not just what was shown but why decisions were made.

## Positioning for the Paper

VisLang should cite InferA as a multi-agent scientific data analysis system
that shares the goal of LLM-assisted scientific data work but differs in:

- Interaction model (query→answer vs. co-authoring a shared artifact)
- Visualization depth (static plots vs. interactive 3D scenes)
- Intelligence architecture (distributed agents vs. single agent + rich tools)
- Design philosophy (automated analysis vs. programming system for human+AI)

InferA validates the need for data-aware LLM assistance in scientific
computing. VisLang's contribution is the specific claim that a declarative
DSL + rich runtime feedback + shared artifact is the right architecture for
the visualization case, and that the same intelligence should serve both
human and AI.

The systems are more complementary than competitive. InferA solves "how do
I ask questions of terabytes of data?" VisLang solves "how do I build a
rich, interactive visualization through conversation?" A combined system
might use InferA-style data reduction to prepare data, then VisLang-style
interactive visualization for exploration and communication.
