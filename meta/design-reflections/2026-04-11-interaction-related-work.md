# Related Work: Agent Interaction Model and System Presentation

*Design reflection — April 11, 2026*

*This document covers related work for VisLang's agent interaction
model — how the LLM communicates with the visualization system, how
the human stays in the loop, and how domain knowledge informs the
process. This is the "paper 1" side of the related work, distinct
from the data management and compilation survey.*

## 1. LLM-driven scientific visualization

**VizGenie** (accepted IEEE VIS 2025) is the closest existing system
to VisLang's paper 1 scope. It is an agentic framework where users
issue natural-language queries ("visualize the skull"), and a
single-agent orchestrator backed by GPT-4o selects among pre-built
tools (threshold, slice, statistical analysis) or dynamically
generates VTK Python scripts for tasks beyond its baseline. Generated
scripts undergo automated validation and are cached for reuse. A
fine-tuned Llama-Vision model provides visual question answering on
rendered output. The system self-refines asynchronously, periodically
validating generated modules and expanding its visual knowledge base.

VizGenie evaluated on four volumetric datasets (CT head, Hurricane
Isabel, astrophysics turbulence, asteroid impact) and demonstrated
significant reduction in cognitive overhead for iterative
visualization tasks. Code generation tasks take 15-23 seconds;
pre-existing tool calls take 2-4 seconds.

*Where VisLang differs:*
- **Persistent pipeline vs one-shot generation.** VizGenie generates
  VTK scripts per query; VisLang maintains a persistent, editable
  pipeline file that accumulates the visualization over multiple
  iterations. The pipeline is diffable, cacheable, and inspectable
  — it's an artifact, not a transcript.
- **Content-hashed caching for iteration speed.** VisLang's
  tracked-execution DAG means that when the agent changes one
  parameter, only the affected subtree re-executes. VizGenie
  regenerates scripts from scratch (though it caches validated
  modules).
- **Scale ambitions.** VizGenie works on single moderate-size
  volumes (up to 600x248x248). VisLang's paper 2 targets TB-scale
  multi-timestep data with compilation-based data management. Even
  paper 1 establishes the pipeline architecture that paper 2 builds
  on.
- **The human's role.** VizGenie is primarily agent-to-system;
  the human issues queries and views results. VisLang's design
  emphasizes the human-agent-system triangle: the pipeline file is
  a readable artifact the human can inspect and edit directly, and
  the plan report (paper 2) is a surface the human reviews.

*What to learn from VizGenie:*
- Dynamic tool generation is valuable — the system shouldn't be
  limited to pre-built operations. VisLang's `apply()` escape
  hatch and `vtk_escape` serve a similar role.
- Fine-tuned vision models for VQA on rendered output could
  augment VisLang's screenshot-based feedback loop — the agent
  could "ask questions" about what it sees.
- VizGenie's RAG over domain documents and historical interactions
  validates VisLang's domain knowledge file approach.

**LIDA** (Microsoft Research) and **PlotGen** target 2D chart
generation from tabular data. LIDA's multi-stage architecture
(summarize → explore goals → generate code → evaluate/repair)
and PlotGen's multimodal feedback (the agent sees the chart)
demonstrate effective LLM orchestration patterns, but neither
addresses 3D scientific data, persistent pipeline editing, or
large-data management.

**ChatGPT Advanced Data Analysis (Code Interpreter)** is the
most widely deployed agent-driven visualization system. The user
uploads data, the agent writes and executes Python in a sandbox,
and iterates based on output. The interaction model — iterative
code generation with execution feedback — is the baseline VisLang
improves on. Key differences: Code Interpreter has no persistent
pipeline (each code block is independent), no content-hashed
caching (re-executes from scratch), no domain knowledge (the agent
relies on general training), and no support for 3D scientific
visualization.

## 2. MCP as interaction architecture

VisLang uses the **Model Context Protocol (MCP)** as its
agent-system interface. MCP, introduced by Anthropic in November
2024 and now an industry standard under the Linux Foundation,
provides a structured protocol for LLM agents to discover and
invoke tools, receive structured results, and access contextual
resources.

*What MCP gives VisLang:*
- **Tool discovery.** The agent discovers available operations
  (run pipeline, take screenshot, query stats, edit file) through
  the protocol rather than through prompt engineering.
- **Structured results.** Tool outputs (screenshots, DAG
  inspection results, plan reports) return as typed protocol
  messages, not unstructured text.
- **Resource exposure.** Domain knowledge files, the workspace
  manifest, and pipeline inspection results can be exposed as MCP
  resources the agent accesses on demand.
- **Client agnosticism.** Any MCP-compatible client (Claude,
  GPT with MCP support, open-source agents) can drive VisLang.
  The system isn't locked to one LLM.

*How VisLang's MCP design compares to typical MCP apps:*
Most MCP servers expose simple CRUD operations on external
services (file access, database queries, API calls). VisLang's
MCP server is more opinionated — it structures the agent's
workflow into phases (exploration via code execution, pipeline
editing, visual feedback) and provides tools designed for an
iterative scientific visualization workflow rather than
general-purpose operations. The tool descriptions carry domain
guidance, and the server's instructions teach the agent how to
approach visualization problems. This is closer to a
domain-specific agent framework than a generic tool server.

## 3. Computational notebooks and iterative data exploration

**Jupyter Agent** (Hugging Face) and **Jupiter** (Microsoft
Research) train LLMs to work within notebook environments —
generating cells, executing them, observing outputs, and
iterating. The notebook model provides: persistent state (earlier
cells' results are available to later cells), visible intermediate
results (the agent sees dataframes, plots, errors), and
nonlinear editing (the agent can go back and modify earlier cells).

**Cursor + notebooks** allows AI agents to generate notebook
cells and observe their output, creating an iterative exploration
loop where the agent recommends analysis directions.

*How VisLang relates:*
VisLang's MCP interaction model provides similar affordances to
the notebook model — persistent state (the pipeline file and DAG
cache), visible intermediate results (screenshots, inspection
output), and iterative editing. But it adds structure that
notebooks lack:
- The pipeline file is a single coherent artifact rather than a
  sequence of independent cells
- Content-hashed caching means edits don't re-execute the whole
  notebook
- The whitelist and restricted execution catch errors that would
  silently produce wrong visualizations in a notebook
- The exploration/pipeline split separates "figuring out what the
  data looks like" from "building the visualization," where
  notebooks intermingle both

The notebook model is the natural baseline for evaluation in
paper 1: VisLang's MCP-driven pipeline approach vs agent-in-a-
notebook, measuring iteration speed, result quality, and failure
modes.

## 4. Domain knowledge and visualization guidance

VisLang uses domain knowledge files (e.g., `domains/wildfire.md`)
to give the agent context about what's scientifically meaningful
in a dataset — what features to look for, what parameter ranges
matter, what visualization techniques are appropriate.

**Transfer function knowledge bases** in volume rendering research
store expert-designed transfer functions indexed by data type,
allowing retrieval of appropriate rendering parameters for new
datasets. Content-based retrieval methods automatically match new
volumes to stored transfer functions. This is the same pattern as
VisLang's domain files, but specialized to one aspect of
visualization (opacity/color mapping) rather than the full
visualization design process.

**VizGenie's RAG** uses retrieval over domain documents and
historical interactions to provide context-driven parameter
suggestions. This validates the general approach of domain
knowledge retrieval for visualization, though VizGenie's RAG is
more automated (retrieval-augmented generation at query time)
while VisLang's domain files are more curated (loaded as MCP
resources the agent reads proactively).

*What to adapt:* The domain knowledge approach should eventually
grow beyond static files toward something more like VizGenie's
RAG — retrieving relevant knowledge from a larger corpus based
on the current dataset and visualization task. But the static
file approach is the right starting point: it's simple, auditable,
and sufficient for the grant's target domains.

## 5. Bidirectional programming and human-in-the-loop editing

**Sketch-n-Sketch** (Chugh et al., UChicago) is an output-directed
programming system for SVG where direct manipulation of the
program's graphical output propagates back as source code edits.
The user can drag shapes, adjust sizes, change colors in the
rendered output, and the system infers "small" program changes
to match. Edits not possible with the mouse can still be made
through text editing of the source.

*Why this matters for VisLang:*
The pipeline file is a program; the rendered visualization is its
output. Bidirectional editing would mean: the scientist adjusts
the camera by rotating the 3D view, and the pipeline file's
camera parameters update; the scientist drags an isosurface
threshold slider, and the `contour(values=[...])` argument
updates; the scientist picks a colormap from a visual palette,
and the `cmap="hot"` argument updates.

This is how the human stays in the loop even when the agent is
the primary author. The scientist doesn't have to read or write
pipeline code — they review the rendered result, make direct
adjustments, and the system propagates changes back to the source.
The agent can then see the updated pipeline and reason about the
scientist's intent.

For paper 2's custom DSL, bidirectional editing is more tractable
than for arbitrary Python — a simple, structured language has a
more predictable mapping between output properties and source
constructs. This is one of the practical arguments for the custom
DSL beyond ease of compilation.

**Projection Boxes** (Lerner et al., CHI 2020) provide
on-the-fly reconfigurable visualization of runtime values in
live programming environments. The programmer sees inline
previews of intermediate results next to the code that produces
them.

*What to adapt:* Inline previews of intermediate pipeline results
— hovering over a filter chain shows a thumbnail of that stage's
output, hovering over a stats query shows the cached value — is a
natural IDE feature for VisLang. Combined with the plan report
(showing the compiler's strategy per node), this gives the author
fine-grained visibility into what the pipeline does without
executing and inspecting manually.

## 6. Mixed-initiative and human-AI collaborative analysis

The broader HCI literature on human-AI collaboration emphasizes
**mixed-initiative** systems where the human and the AI each
contribute what they're best at. In VisLang's design:

- The **agent** is good at: exploring parameter spaces quickly,
  writing pipeline code, interpreting domain knowledge, iterating
  on visual feedback
- The **human** is good at: judging whether a visualization is
  scientifically meaningful, noticing unexpected features,
  providing qualitative feedback ("make the fire more prominent"),
  deciding when the visualization is "done"
- The **system** is good at: caching, incremental execution,
  managing large data, ensuring consistency across frames

The interaction model should let each participant do what they're
best at. The pipeline file is the shared artifact all three
interact with: the agent writes it, the human reviews it (via the
rendered output and bidirectional editing), and the system executes
it (via the DAG, compiler, and workspace). The plan report (paper
2) adds a second shared artifact for negotiating execution
strategy.

This three-way collaboration — human, agent, system — is
relatively unexplored in the visualization literature, which
tends to frame the interaction as either human-system (traditional
interactive visualization) or agent-system (automated
visualization generation). VisLang's model, where the agent
mediates between human intent and system capability while the
human retains direct manipulation access, may be a contribution
in itself.
