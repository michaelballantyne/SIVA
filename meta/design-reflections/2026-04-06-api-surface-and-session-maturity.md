# Design Reflection: April 6, 2026

## Where we are

Three wildfire sessions in one day, each substantially better than the last.
The first session (wildfire-session) took 9 pipeline versions and a human
hint to extract a terrain-following ground surface correctly. The second
(wildfire-vls-session) worked from the first pipeline version thanks to the
terrain detection fix, but needed 8 iterations on vorticity thresholds. The
third (wildfire-vls-session-2) built 6 views with essentially no wasted
iterations -- proactive queries, correct patterns from the start, domain
reasoning that the user confirmed as accurate.

That trajectory is the strongest evidence so far that the feedback loop
(session observation -> feedback file -> code fix -> next session) is
working as a development methodology. The terrain-following grid detection
went from "invisible failure mode" to "correctly applied on first attempt"
in the space of two sessions and one code change.

The codebase has grown to about 6,500 lines across the core modules
(server.py at 3,048, dsl.py at 2,151, queries.py at 1,336), with 45
effective MCP tools and 34 DSL builder methods. Six datasets are available,
though the wildfire dataset has received the most session-level exercise.

## What we've learned

### The tool count problem is real but nuanced

The API reflection today counted 45 effective MCP tools and flagged this as
roughly 2x what an LLM should need to reason about. That's true as a raw
number, but the session evidence tells a more interesting story.

In the third wildfire session, Claude used a small core subset confidently:
`describe_data`, `suggest_isosurface`, `suggest_opacity`, `get_statistics`,
`get_ground_z`, `get_spatial_extent`, `set_pipeline`, `screenshot`,
`new_view`, `focus`, `get_camera`, `export_standalone`. That's about 12
tools. The other 33 were never called and apparently never confused the
selection process.

So the problem isn't that Claude can't find the right tool in a list of 45.
The problem is more structural: there are phantom tools (make_vector, curl
listed but not implemented as MCP tools), overlapping query tools (6 tools
that return subsets of describe_data's output), and mutation tools that
create divergence between the pipeline file and the rendered state. Reducing
the count matters, but the specific reductions matter more than the number.

The highest-value cuts are:
1. Remove phantoms (make_vector, curl from tool list) -- prevents errors
2. Merge get_array_info, get_node_info, get_bounds, get_statistics,
   get_field_summary into describe_data with optional parameters -- removes
   5 tools without losing capability
3. Remove mutation tools in favor of pipeline edits -- removes 6 tools and
   eliminates the file/state divergence problem

That gets from 45 to roughly 32, and more importantly removes the tools
that cause confusion or correctness problems.

### Session maturity follows a recognizable pattern

Looking across all the sessions documented in feedback (bonsai, three
wildfire), there's a repeating arc:

1. **Load and orient** (1-2 rounds): load, describe_data, maybe
   get_dsl_overview
2. **Proactive queries** (1-2 rounds): suggest_isosurface, suggest_opacity,
   get_statistics on key fields
3. **Initial build** (2-4 rounds): first pipeline with ground + primary
   feature
4. **Refinement** (variable): camera, coloring, thresholds, additional
   layers
5. **Investigation** (variable): derived quantities, new views, domain-
   driven exploration
6. **Archive** (1-2 rounds): save camera, export standalone

The sessions that go well compress phases 1-3 into a few rounds by using
query tools proactively. The sessions that struggle expand phase 4 due to
either wrong extraction methods (wildfire session 1) or blind parameter
sweeping (wildfire session 2, vorticity thresholds).

The design implication: the system should optimize for phase 1-3 compression
(fast orientation, confident first build) and phase 4-5 efficiency (data-
guided refinement, not guessing). The query tools and pattern documentation
serve the first goal. What's missing for the second is spatial/conditional
intelligence -- "where is this field strongest?" rather than just "what are
its global statistics?"

### The chicken-and-egg problem with fresh views

The third wildfire session exposed a real workflow gap: after creating a new
view with `new_view("streamlines")`, Claude tried to call `get_ground_z`
to plan seed placement, but got "Node 'data' not found. No pipeline is
active." The query tools require an active pipeline, but the whole reason
to query is to write the pipeline correctly the first time.

This is an argument for the `start_session` / `file_source` backlog item,
or at minimum for letting query tools reach across views to access data
that's been loaded elsewhere. The data itself doesn't change between views;
what changes is the pipeline that processes it. Ground z, field statistics,
and spatial extent are properties of the data, not of any particular view's
pipeline.

### set_pipeline output is too verbose for iteration

All three wildfire sessions and the API reflection noted this: after the
first successful build, subsequent `set_pipeline` calls repeat full array
lists for all nodes. In a 7-node pipeline, that's 15+ lines of array names
that Claude has already seen and doesn't need again. This isn't a crisis,
but it's unnecessary context consumption in long sessions.

A terse mode ("Pipeline v7 built. 7 nodes, all ok. Changes: updated
threshold range on 'fire'.") with verbose mode on demand would be better.
This connects naturally to the reconciler backlog item -- if the system
tracks what changed, it can report just the changes.

## Architecture reflections

### server.py at 3,048 lines is the most pressing structural issue

This file has nearly doubled since the April 4 reflection noted it at
1,700 lines. It contains MCP setup, tool definitions (45 of them), pipeline
execution, the DSL overview and reference documentation, example code, and
session state management. The module split was previously attempted and
stalled, but the growth rate makes it more urgent.

The split should follow the API's own categories: query tools, mutation
tools, meta tools, with server.py as a thin entry point. The tricky part
is the shared state (the current view context, the renderer, the loaded
data), which either needs to live in an explicit state object or be passed
through function parameters. The current pattern of module-level globals
accessed by every tool handler is what makes the split awkward.

### The DSL alias question deserves a decision, not more deliberation

The backlog has carried "remove alias DSL operations" for multiple days.
The API reflection identified `isosurface` (alias for `contour`),
`compute_velocity` (alias for `make_vector`), and `compute_vorticity`
(wrapper around `make_vector` + `curl`). Meanwhile, `compute_vorticity` was
used successfully in both wildfire VLS sessions, and its docs confusingly
call it "legacy."

The right answer is probably: keep `compute_vorticity` because vorticity is
a meaningful scientific concept that deserves a named operation, but stop
calling it "legacy." Remove `isosurface` and `compute_velocity` because they
genuinely add nothing. This should be a 30-minute change, not a recurring
backlog item.

### Mutation tool removal is the right strategic call

The pattern in successful sessions is clear: Claude writes the pipeline file
and calls `set_pipeline`. Mutation tools like `set_colormap` and
`set_opacity` were used occasionally for quick tweaks, but they create a
divergence between the pipeline file (which is the shared artifact, the
version-controlled record, the thing the human reads) and the actual render
state. Every time a mutation tool is used, `get_pipeline()` lies.

Removing them simplifies the model: the pipeline file is always the truth.
`set_camera` is the exception because camera position is inherently
interactive and doesn't belong in the pipeline unless explicitly frozen.
This was already identified in the backlog but deserves emphasis: it's not
just a tool count reduction, it's a correctness improvement.

## Strategic direction

### The next high-impact work is start_session and tool surface reduction

The `start_session` / `file_source` backlog item solves multiple problems
at once: it eliminates the chicken-and-egg problem for fresh views, it
gives the data a stable identity independent of any view's pipeline, and it
sets up the session directory structure that hot reload will need. It's the
foundation for both the immediate tool reduction (query tools can reach data
without an active pipeline) and the longer-term hot reload / LSP vision.

Tool surface reduction -- merging overlapping query tools, removing phantoms,
removing mutation tools -- is the other high-impact area. Not because 45
tools causes selection errors (the sessions suggest it doesn't), but because
the overlapping tools create maintenance burden, documentation confusion, and
the file/state divergence problem.

### Hot reload moves from "interesting idea" to "clear next step"

The April 5 design entry argued for hot reload as the foundation for the LSP
and human editing experience. The wildfire sessions strengthen this argument
from the LLM side too: Claude's workflow is already "write file, call
set_pipeline, read result." Eliminating the explicit set_pipeline call and
replacing it with "write file, read status file" is a strict simplification.
It removes a tool, removes a round trip, and unifies the human and LLM
editing experience.

The implementation path is also clearer now: watchdog or inotify on the
pipeline file, rebuild on save, write results to a status file. The status
file doubles as the build report that set_pipeline currently returns.

### Context bloat is a real constraint that should inform design

The bonsai session hit the 20MB API request limit from accumulated
screenshots. The auto-screenshot removal is correctly flagged as high
priority, but the broader lesson is that every tool response contributes
to context consumption, and long sessions (20+ pipeline versions across
4-6 views) will push against limits.

This argues for:
- Terse default output with verbose mode on demand
- Screenshot resolution control (low for iteration, high for final)
- Status file as an alternative to tool-response-based feedback
- The reconciler reporting changes rather than full state

These aren't separate features -- they're all expressions of the same
design principle: the system should optimize for long productive sessions,
not just individual tool calls.

## Ideas to explore

### Cross-view data sharing

The chicken-and-egg problem suggests a deeper architectural question: should
data loading be per-view or global? Currently, each view has its own pipeline
file and its own execution context. But the data source is almost always the
same file. If the loaded data were a session-level resource rather than a
per-view pipeline node, query tools could access it from any view context,
and the redundant reader caching logic could simplify.

This connects to the `start_session` design, which already proposes
separating the data path from the session path. The logical extension is
that `start_session` loads the data once, and all views reference it.

### Data-guided parameter suggestions beyond the initial build

The proactive query pattern works well for the initial build (suggest
isosurface values, suggest opacity). But during refinement, the guidance
drops off. The vorticity threshold sweeping in session 2 (4 iterations at
different values) happened because there's no tool for "what threshold
would isolate the interesting structure from noise?"

A more general capability would be something like: "given this field and
this visualization intent (isosurface, volume rendering, slice coloring),
suggest parameter values that would produce a meaningful result." The
`suggest_*` tools do this for the initial build, but they don't account
for what the user is trying to show (e.g., "I want to see the vorticity
dipoles, not the ambient noise").

This is hard to solve generically but might be approachable for specific
patterns: "suggest isosurface values that isolate the top N% of the field"
or "suggest scalar range that maximizes contrast in region X."

---

## VISION.md drift

### Factual inaccuracies

**Tool count and list.** Part 1 says "~35 tools" (line 170). The actual
count is 45 effective tools. The tool list in Part 1 includes
`get_statistics` as a separate tool, which the backlog proposes merging into
`describe_data`. The reference tools section lists `get_examples()` and
`list_capabilities()` (line 193-194), which were consolidated into
`get_dsl_overview()` -- these tool names no longer exist.

**server.py size.** The April 4 design entry mentioned 1,700 lines; the
file is now 3,048 lines. VISION.md doesn't cite a specific number but the
module split description should reflect the current scale.

**Spatial-region statistics.** Part 2 "Next Steps" describes this as
something being designed (line 342-351). It was implemented as
`query_stats(node, field, condition)` and is in the completed backlog. The
"next steps" section still frames it as future work.

**Auto-screenshot.** Part 2 describes the screenshot separation as a design
(line 329-339). The backlog marks this as high priority but it hasn't been
implemented yet. The description is accurate as a design, but VISION.md
readers might not know whether it landed or not.

### Structural drift

**Part 2 "Next Steps" needs updating.** The three items listed are: file-
watching hot reload (not yet built), screenshot separation (not yet built),
and spatial-region statistics (built and shipped as query_stats). One of
three is done, and the remaining two have been sitting as "next steps"
since April 4. New priorities from the sessions -- start_session/file_source,
tool surface reduction, the num_points reporting bug -- aren't represented.

**The mutation tools discussion is absent.** VISION.md's architecture section
describes set_colormap, set_opacity, toggle_visibility, etc. as normal tools
(lines 175-178). It doesn't acknowledge the file/state divergence problem or
the plan to remove them. This is a significant architectural decision that
should at least be flagged in the vision doc.

**The "programming system" framing has strengthened.** The sessions validate
the Part 1 "programming system, not just a language" section (lines 198-251)
quite strongly. The value really does come from the system as a whole --
query tools, feedback, the pipeline file as shared artifact -- not from the
DSL syntax alone. This section is accurate and arguably understated. The
session evidence (especially the terrain detection feedback loop) could be
cited as concrete validation.

### Missing concepts

**Session-level data loading.** VISION.md doesn't discuss the idea of
separating data loading from per-view pipelines. The `start_session` backlog
item represents a significant architectural shift that isn't reflected in the
vision.

**Tool surface area as a design concern.** VISION.md doesn't discuss the
tension between having many specialized tools and keeping the LLM's decision
space manageable. The sessions suggest this is a real design axis that
deserves mention.

**Context consumption.** The 20MB limit experience from the bonsai session
revealed that tool response verbosity is a system-level constraint, not just
a UX preference. VISION.md's discussion of tool output doesn't address this.
