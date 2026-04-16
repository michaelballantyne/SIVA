# MCP primitives -- tools, resources, prompts, and the context budget

Date: 2026-04-16

This entry comes from a conversation that started with a different project
(viznoir) where the user noticed that viznoir's MCP server exposes not just
tools but also *resources* (e.g. `viznoir://physics-defaults`,
`viznoir://cinematic`) and *prompts* (e.g. `cfd_postprocess(simulation_type)`).
VisLang today uses only tools. The discussion widened into: does that matter,
and if so, what specifically should change?

This is not a "copy viznoir" proposal. VisLang's tool-heavy shape is mostly
fine. But the conversation surfaced a real, currently under-managed cost --
every-session system context -- that's worth addressing independently of
whether the project ever adopts resources or prompts.

---

## The three MCP primitives, sorted by context cost

| Primitive             | Always-loaded? | Cost                |
|-----------------------|----------------|---------------------|
| Server `instructions` | Yes            | Every session       |
| Tool name + docstring | Yes            | Every session       |
| Tool return value     | Only if called | Pay-per-call        |
| Resource content      | Only if fetched| Pay-per-fetch       |
| Prompt content        | Only if invoked| Pay-per-invoke      |

The top two are paid by every session that has the MCP server configured,
including sessions where the user never touches VisLang (e.g. starts Claude
Code in the vislang repo to do git archaeology or edit docs). The bottom
three are "pay to play" -- the tokens only show up when the user/model
actually exercises that path.

Resources and prompts are not inherently better than tools. What makes them
useful is that they are **opt-in context**. Anything you put there costs
nothing on sessions that don't touch it.

---

## Current state in VisLang

### Server instructions (server.py around line 110-161)

The `instructions=` string passed to FastMCP runs ~50 lines. It covers the
full workflow (call `get_dsl_overview()` first, then `list_data_files()`,
then `load()`, then edit pipeline, then `set_pipeline()`), volume-rendering
tips, troubleshooting hints, and a redirect to `get_dsl_reference()`. Some
of this overlaps with `get_dsl_overview()` itself.

**Cost:** loaded every session, regardless of whether the user actually
uses the MCP in that session.

**Question it answers:** "what does this server do, and how do I start?"
That question genuinely needs a cheap in-context answer -- but probably
one sentence, not fifty lines.

### Tool descriptions

Most tools have one-line summaries plus parameter docs -- appropriate.
A handful have chunky bodies:

- `set_pipeline` (server.py:459-505): 45 lines, including a full
  `Example workflow::` block, notes on versioning, notes on empty-output
  diagnostics, notes on which tools do/don't require re-execution.
- `query_stats` (server.py:1055-1077): 23 lines with "Answers questions
  like..." motivational examples and a worked `Examples:` block.
- `load` (server.py:416-428): 13 lines, enumerates every supported
  extension inline (though the error path at line 438 also returns the
  list on failure, so the inline copy is redundant).
- `get_dsl_overview` (server.py:1657-1669): 13 lines describing itself,
  which is fine, but the tool's *return value* is 150+ lines that
  duplicate content already baked into server `instructions`.

**Cost:** every tool docstring is loaded every session. 45 lines on one
tool is not a crisis, but across 47 tools the long tail adds up.

### The tool catalog breadth

47 tools is a lot. From the shape of the list, several look like state
management that could be consolidated or dropped from the public surface:
`toggle_visibility`, `clear_annotations`, `close_view`, `list_views`,
`list_versions`, `restore_version`, `get_actor_info`, `list_actors`,
possibly `get_pipeline` (overlaps with reading the pipeline file).

This is an orthogonal issue from primitive choice, but it's the larger
context-budget lever. Each tool costs its name + signature + docstring
every session. Dropping 10 tools from the surface likely saves more tokens
than any prompt/resource refactor.

---

## What to do about it

In priority order from highest ROI to lowest:

### 1. Trim `instructions` to a pointer, not a manual

The string is too long for what it needs to do. A minimum viable
`instructions`:

> "VisLang lets you build VTK visualizations via a DSL. Call
> `get_dsl_overview()` for the workflow and DSL form catalog. Query
> data ranges before choosing thresholds -- `set_pipeline()` with wrong
> ranges produces empty nodes."

One sentence of what, one pointer to how, one critical rule. The 150-line
overview lives in `get_dsl_overview()`'s return value (which it already
does), paid only by sessions that actually use the MCP. Delete the
duplicate copy from `instructions`.

Risk: model doesn't call `get_dsl_overview()` when it should. Mitigation:
make the pointer explicit ("call `get_dsl_overview()` first"), and keep
tool docstrings good enough that the model can pick correct tools even
without the overview.

### 2. Trim chunky tool docstrings; move worked examples elsewhere

For each tool with a docstring > ~15 lines, separate:
- **Must stay inline** (prevents misuse from wrong mental model): key
  constraints, param semantics, gotchas. Example: `query_stats` needs
  inline "single-condition only, format `<field> <op> <value>`" because
  the model will try compound conditions otherwise.
- **Can move to pay-per-call** (improves usage but isn't required for
  correctness): worked examples, motivational use cases, cross-references
  to related tools. Example: `set_pipeline`'s `Example workflow::` block
  belongs in `get_dsl_reference('set_pipeline')` or a per-tool reference,
  not in the docstring.

Concrete candidates for this trimming pass:
- `set_pipeline` -- move the Example workflow block, keep the DSL-form
  list and the "ranges produce empty nodes" warning.
- `query_stats` -- keep the grammar, move the "answers questions like..."
  block.
- `load` -- drop the inline extension list; the error message on the
  unsupported-ext path already lists them.

### 3. Cut the tool surface

Separate audit needed, but the candidates above (`list_views`,
`toggle_visibility`, `list_actors`, etc.) look like they could either be
dropped from the public surface, consolidated into a single
`view_state()` inspector, or replaced by letting the model read the
pipeline file directly.

Independently of primitives, this is probably the biggest per-session
token win on the table.

### 4. Consider per-tool reference for complex tools (the `get_dsl_reference`
    precedent)

The `get_dsl_reference(form)` pattern works for DSL forms. An analogous
`get_tool_reference(tool)` (or equivalently, a
`vislang://tools/{name}` resource template) could carry the worked
examples and patterns for the 3-5 complex tools (`set_pipeline`,
`query_stats`, maybe `render_chart`). Thin inline docstring + "call
`get_tool_reference('set_pipeline')` before first use" pointer.

Caveats worth noting before doing this:
- Constraint-level warnings (the kind that prevent a wrong call, not just
  an un-optimal one) must stay inline. If the model only reads the
  reference *after* a bad call, the warning has to already have fired.
- Models sometimes skip the reference lookup even when the description
  points to it. Don't assume the pointer is load-bearing; assume the
  thin description plus return-value errors are enough to eventually
  converge, and treat the reference as a speed-up, not a correctness
  mechanism.
- If done as a resource template rather than a tool, it keeps the tool
  count down. Claude Code exposes resource reads via `ReadMcpResourceTool`,
  so ergonomics are the same from the model side.

This is worth doing only after (1)-(3). If the docstring trim in (2) is
aggressive enough, the complex tools may not need a separate reference
surface at all -- the existing `get_dsl_reference` could absorb them by
supporting tool names as well as form names.

### 5. Consider prompts for workflow kickoff

`quick_start(filename)` is interesting because it mixes two shapes:
- A tool-shaped half (inspects the file, proposes a pipeline).
- A prompt-shaped half (the return value is instructions like "Paste
  this into `set_pipeline()` to start").

An MCP prompt -- invoked as `/mcp__vislang__kickoff filename=foo.vtk` --
would surface as a slash command in Claude Code, making the workflow
discoverable to the user rather than requiring the model to know
`quick_start` exists. The prompt template could tell Claude to call
`describe_data`, then propose a starter pipeline, then stop for user
review.

This is a stylistic choice more than a cost issue. It's nice if VisLang
wants user-driven entry points. It's not necessary.

---

## What not to do

- **Don't convert `get_dsl_overview()` to a resource.** It's already in the
  right shape as a tool (or, after step 1, as the expanded version behind
  a thin `instructions` pointer). Making it a resource doesn't change the
  cost model; Claude Code still has to fetch it via `ReadMcpResourceTool`,
  same as a tool call.
- **Don't split `get_dsl_reference` into a resource wholesale.** The
  introspection part (`inspect.signature()`) needs to run at call time.
  The static `_EXAMPLES` dict could move to a resource in principle, but
  the payoff is small for the complexity added.
- **Don't try to be viznoir.** VisLang's tool-heavy shape reflects its
  design goals (LLM-writes-DSL-directly, stateful sessions, interactive
  rendering). Adding resources and prompts wholesale to look more like
  viznoir doesn't serve those goals. The only changes worth making are
  the ones that pay for themselves in context budget or user ergonomics.

---

## Summary

The things that matter for VisLang:

1. `instructions` and chunky tool docstrings are always-loaded context paid
   by every session, including sessions that never touch VisLang. Trim them.
2. 47 tools is a lot. Cut the catalog before or instead of restructuring
   primitives.
3. Resources and prompts are worth adopting only when they serve a specific
   purpose (user-invoked workflows, per-tool reference for complex tools),
   not as a cargo-culted pattern.

The tools/resources/prompts distinction isn't wrong to think about, but
the bigger near-term lever is ruthlessly trimming what's in the always-
loaded budget.
