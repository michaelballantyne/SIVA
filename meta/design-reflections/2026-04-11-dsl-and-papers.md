# DSL Alternatives and Publication Strategy

*Design reflection — April 11, 2026*

*Summary of a design conversation about the DSL implementation
options for VisLang and how they sequence into a publication plan.*

## Three approaches to the pipeline language

### 1. Proxy-based execution (what exists today)

Real Python, real PyVista, with TrackedProxy wrappers that intercept
method calls, build a content-hashed DAG, and cache results.

**What it gets you:**
- Already built and working in tracked-execution
- Full PyVista compatibility — agents write real PyVista, it runs
- Content-hashed caching makes re-execution fast on edits
- Scene reconciliation gives incremental rendering updates
- Agent's PyVista training data is fully leveraged

**Where it falls short:**
- Executes eagerly — no place for a compiler to sit between spec
  and execution
- Can't defer computed values (mesh.center is a real tuple, not a
  symbolic node you can plan around)
- Scope (global vs per-frame) is implicit in what the author happened
  to load, not structurally visible in the DAG
- Can't do lazy DAG construction — every operation actually runs,
  which means the "spec" is inseparable from the execution
- Works fine at moderate scale; can't support compilation-based data
  management at TB scale

**Role:** The right approach for paper 1. It works now, it's fast
enough for single-file datasets, and it demonstrates the agent
interaction model without requiring new language infrastructure.

### 2. Custom Python interpreter for PyVista+NumPy subset

A custom interpreter that parses Python syntax but gives it
DAG-construction semantics. Every PyVista method call records a node
instead of executing. NumPy operations produce symbolic nodes.

**What it gets you:**
- PyVista-compatible surface syntax — agents write familiar code
- Fully lazy DAG construction — the compiler gets a complete graph
  to plan against
- Computed properties (mesh.center, mesh.bounds) flow as symbolic
  nodes through the DAG, creating edges the compiler can reason about
- Good error messages at the boundary of the supported subset
  (because the interpreter controls all evaluation)
- The Plotter accumulation pattern reinterprets naturally as
  declarative scene construction

**Where it falls short:**
- Building a Python interpreter, even for a subset, is genuinely
  hard — Python's evaluation semantics (scoping, object protocols,
  unpacking, comprehensions, etc.) are complex
- Most of that complexity has nothing to do with visualization
- PyVista's API inconsistencies (different return types, varying
  argument names, implicit state) each need hand-mapped semantics
- The "almost compatible" problem: agents draw on PyVista training
  data and constantly probe the boundary of the supported subset,
  creating friction even with good error messages
- NumPy's broadcasting and indexing rules are intricate to
  reimplement symbolically

**Role:** A possible future front-end once the compiler and workspace
are proven. Not the fastest path to a working compiler.

### 3. Custom DSL with uniform DAG mapping

A purpose-built language that borrows PyVista's filter vocabulary
(the names and concepts agents know) but uses a custom syntax
designed for compilation — every construct maps 1:1 to a DAG node.

**What it gets you:**
- Trivial to parse compared to Python
- Mechanical mapping from syntax to DAG — no ambiguity, no
  unsupported subset to guard against
- Static validation (field names vs manifest, type checking of
  filter arguments) is straightforward because the grammar is small
- Scope is visually obvious: `fire.field(...)` is dataset-level,
  `mesh.contour(...)` is per-frame
- Scene composition structure is explicit in the syntax rather than
  reinterpreted from imperative Plotter calls
- For agents: the language is closed — everything in it works,
  nothing outside it compiles, no "is this pattern supported?"
  guessing
- Much faster to build than a Python interpreter

**Where it falls short:**
- Agents have no training data for the specific syntax (but LLMs
  learn small languages quickly from system prompt examples)
- Scientists can't read it as PyVista (but the filter vocabulary
  transfers, and the agent is the primary author)
- Another language to design, document, and maintain
- Risk of premature language design if the vocabulary isn't yet
  stable

**Role:** Likely the right first front-end for the paper 2 compiler
work. Gets the compiler and workspace operational fastest. The
PyVista-compatible interpreter can be added later as a second
front-end against the same DAG IR, if needed.

### How they sequence

Paper 1 uses approach 1 (proxies). Paper 2 uses approach 3 (custom
DSL) as the primary front-end, with approach 2 (Python interpreter)
as a possible future addition. The DAG-as-IR architecture means
front-ends are additive — the compiler doesn't care which one
produced the DAG.

## Publication strategy

### Paper 1: Agent-driven scientific visualization

**Contribution:** An MCP-based interaction model where an LLM agent
iteratively builds 3D scientific visualizations through pipeline
editing, with proxy-based execution providing content-hashed caching,
incremental scene updates, and good error feedback.

**System components needed:**
- Tracked-execution proxy (exists)
- MCP server with pipeline editing tools (exists)
- Scene reconciler (exists)
- Whitelist and error guidance (exists, needs polish)
- Evaluation harness

**What makes it more than "LLM writes PyVista":**
- The content-hashed DAG makes iteration fast (cache hits on
  unchanged subtrees) and inspectable (the agent queries the DAG)
- The restricted execution model (whitelist, purity checks) catches
  errors the agent would otherwise hit at VTK runtime
- The scene reconciler makes updates incremental rather than
  rebuilding the renderer from scratch
- The MCP tool design (separate exploration vs pipeline editing)
  structures the agent's workflow

**Evaluation:** Agents build visualizations on several datasets
(fire sim, bonsai CT, medical imaging). Compare against (a) raw
PyVista in a notebook/code-execution environment, (b) LIDA-style
one-shot generation. Measure iterations, time, result quality,
failure modes.

**Additional angles that strengthen the paper:**

*Domain knowledge bases.* The agent's effectiveness depends on
domain knowledge — knowing that fire simulations have temperature
fronts worth isosurfacing, that CT data benefits from transfer
function tuning, that velocity fields call for streamlines. VisLang's
domain files (e.g., `domains/wildfire.md`) give the agent this
context. Evaluating how domain knowledge affects visualization
quality is a natural part of the paper 1 study: with vs without
domain guidance, measuring whether the agent makes better parameter
choices and produces more scientifically meaningful visualizations.

*User preferences and iterative refinement.* The MCP interaction
model supports a human-in-the-loop workflow where the scientist
reviews screenshots and gives natural-language feedback ("make the
fire more prominent," "I can't see the terrain through the volume
rendering"). The agent edits the pipeline in response. This
preference-driven iteration is a key part of the interaction model
and should be evaluated: how many human feedback rounds to reach a
satisfactory visualization, and how well does the agent interpret
qualitative feedback?

**Venue:** IEEE VIS or CHI.

### Paper 2: Compilation-based data management for scale-invariant visualization

**Contribution:** A compiler that takes a declarative visualization
spec (expressed in a custom DSL), plans execution against a
workspace (stats DB, pyramid, feature DB), and produces a plan that
adapts the same spec from MB to TB data — with scope inferred from
DAG dependencies rather than declared by the author.

**System components needed:**
- Custom DSL parser (new, but simple)
- DAG IR with richer node types (extends tracked-execution's DAG)
- Compiler with plan generation (new, core contribution)
- Workspace: manifest, stats DB, pyramid, feature DB (new)
- Plan report format (new)
- MCP tools for hints, job management, workspace admin (new)

**What makes it novel:**
- First system to apply the Mosaic/VegaPlus compilation pattern to
  3D scientific visualization
- Scope-from-DAG-dependencies (global vs per-frame inferred from
  data flow, not annotations) appears to be new
- Scale-invariant specs (same pipeline, MB to TB, compiler adapts)
  haven't been demonstrated for scientific viz
- The combination of yt-style derived field management, Falcon-style
  progressive refinement, and Cinema-style feature extraction in a
  single compiled system

**Evaluation:** Same pipeline executed against datasets at different
scales. Measure: compiler plan quality, interactive latency,
correctness of global statistics, time to animation, comparison
against manual ParaView workflows.

**Additional angles:**

*Bidirectional programming and LSP.* The custom DSL opens the door
to IDE-level tooling: an LSP server that provides inline plan
feedback (hover over a node to see the compiler's strategy and
estimated cost), live DAG visualization alongside the code,
bidirectional editing where changes in a visual representation
(dragging a threshold slider, adjusting camera) propagate back to
the DSL source. This keeps the human in the loop even though the
agent is the primary author — the scientist can inspect, adjust, and
override at the spec level rather than only through natural-language
feedback. This is where the custom DSL pays off vs the Python
interpreter: a simple, structured language is much easier to build
bidirectional tooling for.

*The plan report as a conversation surface.* The plan report isn't
just a diagnostic — it's where the agent, the compiler, and the
human negotiate about scheduling. The agent summarizes it for the
human, flags surprising decisions, proposes hints. This three-way
conversation (human intent → agent spec → compiler plan → human
review) is a novel interaction pattern worth foregrounding.

**Venue:** IEEE VIS or EuroVis.

### How they connect

Paper 1 establishes: agents can build scientific visualizations
effectively through iterative pipeline editing.

Paper 2 extends: to scale that workflow to TB data, we compile the
spec against a workspace, getting automatic data management without
changing the agent's workflow.

The domain knowledge, user preferences, and bidirectional editing
threads weave through both papers but land in different ways:
paper 1 focuses on the agent-human collaboration for building a
single visualization; paper 2 focuses on the agent-compiler-human
collaboration for managing execution at scale.
