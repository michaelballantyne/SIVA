# Design Reflection: Maturity and Gaps

## Where we are

VisLang has crossed the threshold from prototype to functional tool. The
codebase is about 3,400 lines of Python across five modules, with 34 MCP
tools and 40+ DSL functions. It handles the wildfire dataset end to end --
all nine visualization targets are complete. Multiple agent sessions have
used it successfully. The development process itself (backlog, feedback
loop, design journal) is now established.

The earlier restructuring entry today focused on the pivot away from
heavyweight CLAUDE.md documentation toward self-describing tools. This
entry steps back to look at the broader picture: what kind of project
VisLang actually is at this point, and whether the architecture is
pointing in the right direction.

## The real product is the feedback loop, not the DSL

The most important insight from the accumulated feedback is that VisLang's
value is not primarily in the DSL syntax. It is in the tight loop between
the LLM and the visualization state: describe data, set pipeline, see
screenshot, adjust. The DSL is one piece of that loop, but the query tools
(describe_data, sample_point, get_field_summary, get_bounds) and the
feedback tools (screenshot, get_actor_info) matter just as much.

This reframes the backlog priorities. The highest-impact items are not DSL
sugar like snake_case properties or positional args -- those are polish.
The highest-impact items are the ones that make the feedback loop faster:

1. Auto-screenshot from state-changing tools (fewer round trips)
2. Rich describe_data with percentiles (fewer follow-up queries)
3. Batch point probing and line probes (quantitative analysis without N calls)

The DSL ergonomics items (load(), slab(), extract_component) matter too, but
they matter because they reduce errors in the "set pipeline" step, not because
shorter syntax is inherently valuable.

## The vector component problem is telling

The session 2 feedback documented an agent spending significant effort trying
to extract a single component from a computed vorticity vector. Every attempt
silently failed. This one scenario exposes three architectural weaknesses at
once:

- **Silent failures**: The pipeline reported "ok" when the computed field did
  not actually exist. This is the most damaging class of bug in an LLM-driven
  tool because the agent cannot recover from something it cannot detect.
- **Missing primitive operations**: Component extraction is fundamental to
  vector field analysis. Its absence forced workarounds through VTK calculator
  syntax that the DSL was supposed to abstract away.
- **VTK calculator fragility**: The vtkArrayCalculator's behavior around
  vector operations (iHat dot products, component suffixes) is inconsistent
  and poorly documented. Wrapping it in a DSL does not fix the underlying
  unreliability.

The fix is not to patch the calculator. It is to provide dedicated
extract_component() and compute_vorticity(vector=True) helpers that bypass
the calculator entirely and use numpy or VTK's built-in array operations.
More generally: when VTK's generic mechanisms are unreliable for a common
operation, the DSL should provide a reliable special-case path.

## Architecture is holding up, with one growing concern

The tear-down/rebuild model with reader caching continues to work well. The
reconciler from the original DESIGN.md remains unnecessary. The file-based
pipeline (write pipeline.py, execute it) is transparent and debuggable. These
were good calls.

The growing concern is server.py at 1,277 lines. It contains tool definitions,
MCP protocol handling, pipeline execution, file management, and session state.
As more tools get added (auto-screenshot, batch probing, line probes), this
file will keep growing. It would benefit from splitting tool handlers into
a separate module, keeping server.py focused on MCP protocol and routing.

The DSL module (515 lines) and filters module (735 lines) are reasonably
sized but will also grow as convenience functions accumulate. At some point
the DSL needs a clear boundary: what gets a dedicated function vs. what stays
as a generic filter() call. The current heuristic seems to be "anything that
came up in feedback gets a function," which is pragmatic but will not scale
indefinitely.

## The single-dataset problem

Every piece of feedback, every test, and every design decision has been shaped
by the wildfire dataset. This is a 600x500x61 structured grid with terrain-
following coordinates, 9 fields, one timestep. The backlog includes
"cross-domain generalization" but nothing concrete has been done.

This matters because several design choices might be wildfire-specific without
us knowing:

- The fire_region() helper is explicitly domain-specific (and correctly lives
  in domains/). But what about seeds_near()? It was designed for fire plume
  seed placement -- does it generalize to medical imaging or ocean modeling?
- The suggest_camera() heuristic was tuned for elongated terrain grids. Would
  it produce reasonable views for a spherical mesh or a cubic volume?
- The field summary statistics assume scalar ranges are meaningful. For data
  on a sphere (longitude wrapping) or cyclic fields (angles), the stats would
  be misleading.

Testing with a second dataset -- ideally something structurally different,
like medical imaging (regular grid, isotropic spacing, no terrain) -- would
reveal which parts of the system are actually general. This should be
a near-term priority, not a low-priority idea.

## What the project is not

It is worth being explicit about scope. VisLang is not:

- A ParaView replacement. It does not aim for the full breadth of ParaView's
  filter library or its GUI. It aims to make the most common visualization
  tasks accessible through conversation.
- A research contribution in visualization algorithms. The novelty, if any,
  is in the LLM-visualization interaction pattern: declarative specs, rich
  feedback, iterative refinement through an MCP interface.
- A production rendering system. Off-screen VTK rendering is adequate for
  exploration and communication, not for publication-quality figures (though
  it gets surprisingly close with good lighting and colormaps).

Keeping this boundary clear helps prioritize. Features that push toward
ParaView-completeness (arbitrary filter chaining, custom shader pipelines)
are lower value than features that make the conversation loop better.

## Near-term direction

In priority order:

1. **Fix silent failures everywhere.** Audit every path where a field name
   is used and ensure missing fields produce loud errors. This is the single
   most important reliability improvement.
2. **Auto-screenshot from state-changing tools.** This is a mechanical change
   with outsized UX impact.
3. **Add a second dataset.** Even a small medical imaging volume would stress-
   test generality. The datasets/ infrastructure already supports it.
4. **extract_component and vector coloring.** Unblocks the entire class of
   vector field analysis that session 2 could not do.
5. **Line probes.** The most-requested missing capability across both feedback
   sessions. Bridges the gap between "I can see it" and "I can quantify it."
