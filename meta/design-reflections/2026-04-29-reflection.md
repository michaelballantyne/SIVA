# Design Reflection — 2026-04-29

Focused, post-diagnostic-spine pass. The big April-26 push (cascade-skip,
unified wrapper validation, structured per-node status, property-typo
checking, empty-output hints, terse run reports, hot-reload simplification)
all landed. This reflection asks what the *next* layer of friction looks
like now that those mechanical wins are done.

Inputs sampled: `git log -30`, `meta/BACKLOG.md`, the four most recent
design reflections (especially `2026-04-25-2004Z` and the four April-26
entries), the three most recent feedback entries, and quick scans of
`server.py` (2,294 lines), `dsl.py` (2,413), `filters.py` (1,719),
`hot_reload.py` (707).

---

## Section 1 — Code quality

The diagnostic-spine cluster has visibly improved internal contracts.
Every node status now goes through `vislang/diagnostics.py` helpers;
`_validate_vtk_kwargs` is centralized in `filters.py`; introspection
helpers consolidated in `_vtk_introspect.py`. The mechanical hygiene is
in good shape.

Three structural concerns remain.

**`server.py` is still the elephant (2,294 lines, 26 `@mcp.tool`
handlers).** The April-25 reflection deferred splitting until the tool
surface stabilizes. It's now stabilized: tool count has been at 25–26
for two weeks and recent commits add nothing new (`scene_preset` was
*folded into* `background`, not added). Time to revisit the deferral.
Reasonable split: `tools/data.py` (`load`, `list_data_files`,
`describe_data`, `query_stats`, `get_histogram`, …), `tools/views.py`
(`new_view`, `focus`, `close_view`, `list_views`,
`set_camera`/`get_camera`/`set_window_size`, `set_suggested_camera`),
`tools/pipeline.py` (`wait_for_pipeline`, `pipeline_status`,
`screenshot`, `list_versions`, `restore_version`, `camera_orbit`),
`tools/reference.py` (`get_dsl_overview`, `get_dsl_reference`),
plus `instructions.py` for the `FastMCP(instructions=…)` block. The
WORKFLOW/HOT RELOAD/CRITICAL RULES/TROUBLESHOOTING text alone is ~100
lines crowding the top of the module.

**`dsl.py` `PipelineBuilder` is also large (2,413 lines, 35+ public
builder methods plus 5 `_build_*_node` dispatchers).** The auto-populated
namespace via `inspect.getmembers` (`dsl.py:2334`) is elegant, but it
means every method on the class becomes a DSL form unless it starts
with `_`. That's a contract worth documenting more loudly — silent
exposure surface is fragile. The `_build_*_node` dispatchers also live
on the same class as the user-facing forms; cleaner to push the
build-graph machinery into a separate `BuildExecutor` collaborator and
keep `PipelineBuilder` as pure declarative API.

**`_EXAMPLES` registry vestige** (`server.py:1631`). 41 entries appended
to `get_dsl_reference()` output as a separate "--- Example ---" block.
`scripts/gen_docs.py` ignores it; `docs/dsl-reference.md` already shows
only docstring examples. This is dual sources of truth for one thing,
and it's already in the backlog. Worth doing now — it's small, it
removes a drift risk, and it cleans the largest non-handler block in
`server.py` before any module split.

**Test coverage.** 720+ tests across 36 files; fixtures consolidated in
`conftest.py`. Good shape. Two known issues are tracked: full-suite
runtime (`test_mcp_protocol.py` ~37 s alone), and macOS segfault in
`test_stateful_integration.py` from off-main-thread `Renderer`
construction. Both are real but sit at the test-infrastructure level,
not the production code.

**Dead/orphan dispatch risk.** `_build_line_probe_node` was orphaned
for some time (existed but never reached because `_build_pipeline`
didn't dispatch). The April-26 reflection noted this; the audit-for-
others item is in the backlog cluster but not yet done. A 10-minute
grep is worth doing — every orphaned `_build_*_node` is a silent
behavior gap waiting to be hit.

---

## Section 2 — API surface

26 MCP tools today (was 25 — the `pipeline_status` companion to
`wait_for_pipeline` justified the addition). The shape feels right:
data-aware queries + small mutation surface + reference tools.
`describe_data(node, file_path, field)` remains the canonical example
of mode-merging, and the rename from `run_pipeline → wait_for_pipeline`
(commit `b4e3230`) better matches the post-hot-reload mental model.

**MCP-level gaps that the recent feedback names:**

- *Lazy view creation* (`headsq-hu` feedback Part 2). Server eagerly
  creates `main` at startup with renderer + watcher + window. Four
  idle Claude Code sessions = four live VTK windows. Backlog item is
  high-priority and well-mapped. This is the single most concrete
  architectural fix on the queue.

- *`survey()` and `parallax_views()` for orientation/depth*
  (`parallax-and-being-lost` feedback). These are genuinely
  tool-shaped: multi-frame capture with state save/restore, and
  encoded knowledge (5° cross is empirically validated). Both
  proposals are scoped and the rationale for *not* having
  `dolly()`/`reframe()` is articulated. Worth picking up — they target
  the top metacognitive failure mode (camera lostness) head-on.

- *Camera staleness signaling* (existing backlog item, well thought
  through). Latch + piggyback on next tool result, no push channel.
  Sidesteps the "channels are research-preview" blocker.

**DSL surface inconsistencies (still pending the "vibecode pass"):**

- snake_case wrapper args + CamelCase VTK passthrough mixed in same
  call (`contour(input=data, ContourBy=…, Isosurfaces=…)`).
- `extract_grid` vs `extract_region` overlap (`dsl.py:399` vs
  `:439`); `extract_grid` requires reciting full i/j extent for
  k=0 slicing (the `scalar-range-default-and-extract-grid-ergonomics`
  feedback).
- `clip` vs `clip_sphere`/`clip_box` `inside_out` polarity mismatch
  (`dsl.py:1027` vs `:1064`/`:1096`).
- Three data-loading entry points: `source` (`dsl.py:98`), `load`
  (server tool), `raw_source` (`dsl.py:1475`).
- `scalar_range` on surface `color_by` falls through to `(0, 1)`
  silently when omitted (`filters.py:1611-1656`); volume path
  auto-detects (`filters.py:1209`). Asymmetry directly contradicts
  the "smart defaults" claim in `show()`'s docstring.

The April-25 reflection already flagged most of this. Nothing has
moved on it. The cost is real (the scalar-range issue is a *bug*,
not a polish item — the docstring promises behavior the code doesn't
deliver). I'd promote the scalar-range fix out of the
"DSL vibecode pass" bucket and ship it on its own.

**Instructions string (server.py:89-188).** Mostly accurate post-
`3fb9960` (HOT RELOAD section landed). One frayed edge: WORKFLOW step
4 still says "Add show() calls to view-main.py — saving the file
triggers a build automatically; call wait_for_pipeline()…" which
implies the file-save *and* the explicit call are both routine. The
April-26 `agent-ux` reflection's recommendation — only call
`wait_for_pipeline` when you want to block on a result, prefer
`pipeline_status` during tight loops — has been partially
incorporated, but the WORKFLOW lead-in still nudges agents toward
the explicit call. Worth a small tightening.

**`get_dsl_reference` instability.** The split between
`get_dsl_overview` and `get_dsl_reference(form)` is right; but the
`_EXAMPLES` registry plus docstring `Example::` blocks plus the
overview's "VTK first paragraph" injection (`06d2447`) plus the
algebra-vs-mesh tip (`a069193`) means there are now four
documentation channels glued together at runtime. Folding `_EXAMPLES`
into docstrings (backlog) is the right cleanup; doing it now would
also let `get_dsl_reference` become a much shorter formatter.

---

## Section 3 — Design direction

### What's working

The post-paper diagnostic-spine cluster (April-25 → April-26) shipped
exactly what it set out to. Cascade leaks are gone. Wrapper validation
no longer raises into an out-of-band channel — every error is a
structured `{"status": "error", "kind": …, …}` dict on a node. Every
warning tells the agent the field range. Property typos suggest similar
names. Hot reload is fast, content-hashed, and agent-legible. This is
the highest-quality stretch of work in the project's history and it
landed in two weeks.

The recent post-shipping feedback (`headsq-hu`, `parallax-and-being-lost`)
is doing something different from the April-25 wave: it's no longer
about "the language is broken in these places," it's about "the *agent's
loop* is broken in these places." Lostness, depth ambiguity, the
metacognitive gap. That's a healthy shift — the system is mature
enough that the bottlenecks have moved up the stack.

### Tensions with VISION.md

VISION.md was partially refreshed in the April-26 round (the
architecture diagram and tool-listing sections were updated;
`wait_for_pipeline` rename is in). Remaining drift is small but real:

- Part 1 still describes `pipeline_status` informally and
  `wait_for_pipeline` is reflected, but the "MCP server exposes 25
  tools" line is now 26 (the `pipeline_status` addition).
- Part 2's "Spatial-region statistics" section describes work that's
  arguably subsumed by `query_stats(condition=)` and `sample_points`;
  worth confirming whether it's a still-pending design item or
  retired.
- Part 1 doesn't mention the structured-status schema (`status`,
  `kind`, `class`, `message`) as a foundational diagnostic
  contract — given the April-26 work, this is now a load-bearing
  feature of the system, not an implementation detail.
- The section titled "Liveness — where we are" (Part 2) cites
  Tanimoto level 3 and aspires to level 4 via parameter scrubbing.
  Worth revisiting: hot reload + content-hashed cache puts us
  closer to level 4 than the doc suggests, since visual edits
  rebuild in ~1ms.

I am explicitly not editing VISION.md as part of this reflection. The
backlog item "VISION.md refresh" is the right venue, with human review.

### Where the next phase points

Three threads emerge, in priority order:

**1. Close the scalar-range / smart-defaults gap.** This is the only
"the docstring lies" issue I noticed. PyVista and ParaView both
default to data-range; the volume path here does too; the surface
path doesn't. Fix the asymmetry in `_apply_smart_defaults` /
`create_show`. Half a screen of code, immediately user-visible. Don't
wait for the broader DSL vibecode pass.

**2. Lazy view creation.** Most concrete architectural fix on the
queue. The four-windows-from-four-sessions case is a real footgun;
the redesign sketch in `headsq-hu-and-lazy-view-creation` (Part 2)
is detailed enough to start. Start with offscreen mode; tackle the
interactive event-loop refactor second.

**3. The metacognitive gap.** This is the most ACI-research-interesting
direction and the freshest finding. The `parallax-and-being-lost`
feedback's lever-2 (server-side render warnings: "camera inside data
bounds," "image has <N% non-background pixels," "edge entropy
suspiciously low") is the highest-leverage version because it
removes the agent-introspection requirement. Pair with `survey()`
and `parallax_views()` to give the agent useful actions once it's
been alerted. This is a small cluster of items but they compose: the
warning tells the agent "you're lost," `survey()` shows it the menu
of recoveries, `parallax_views()` resolves depth ambiguity at a
chosen camera. They should be designed together, not piecemeal.

**Defer until those three settle:** the broader DSL vibecode pass,
NodeRef output schemas, the reconciler integration, the auto-overview
thumbnail experiment (it's in the backlog with a sharp implementation
constraint about not mutating the live window). All are real
improvements; none is the bottleneck right now.

### Idea worth more thought before backloging

The `parallax-and-being-lost` feedback closes with: "tool design is
half the answer; the other half is the agent learning when to call
the tools." That's a deeper point than any single tool addition and
probably deserves a dedicated process or design reflection. The
levers (tool-description hints, server-side warnings, MCP
instructions, auto-pre-empt at view creation) span every ACI
decision the project has made. If we're going to invest in
metacognitive scaffolding, doing it consistently — same mechanism
across `screenshot`, `set_camera`, `wait_for_pipeline`,
`describe_data` — would be more valuable than a one-off warning
for camera-inside-bounds. Not yet a backlog item; flag as a
strategic question.

---

## Closing — Proposed backlog items, prioritized

**Highest leverage (small, ship soon):**

- **Fix scalar_range fallthrough on surface `color_by`.** Use
  `arr.GetRange()` when `scalar_range is None`, matching the volume
  path. Eliminates the docstring-vs-behavior gap; ~10 lines in
  `filters.py`. Already in feedback; not yet in backlog as its own
  item — it's currently buried in the "DSL vibecode pass." Promote it.

- **Audit `_build_*_node` for orphaned dispatch.** Already in the
  April-26 cluster but unticked. 10-minute grep of `_build_pipeline`
  vs the methods on `PipelineBuilder`. Closes a category of silent
  bug.

- **Fold `_EXAMPLES` into DSL docstrings.** Already in backlog at
  Medium. Worth doing before any `server.py` split; removes a dual
  source of truth.

**Architectural (next major effort):**

- **Lazy view creation.** Already top-of-backlog. Single most concrete
  improvement to the lifecycle model; prevents idle-session window
  proliferation.

- **Server-side metacognitive warnings + `survey()` + `parallax_views()`
  cluster.** Design and ship as a unit — the warning, the recovery
  menu, and the depth-disambiguation tool compose. Cluster of three
  small additions; together they address the "agent loops on a bad
  view" failure mode the April-27 session named.

**Continued cleanup:**

- **Split `server.py` into modules.** Tool surface has stabilized for
  two weeks. Suggested layout in §1 above. Defer the DSL-builder
  refactor; this one's lower-risk and higher-payoff.

- **DSL vibecode pass** (existing backlog). Now that the scalar-range
  bug is split out, this becomes pure ergonomic cleanup —
  `extract_grid` keyword overload, `clip*` polarity, `source`/
  `raw_source` consolidation, `curl` already split.

- **VISION.md refresh.** Already in backlog. Worth pairing with the
  next major architectural change (lazy views) so we update once,
  not twice.

**Strategic question (not yet a backlog item):**

- **Unified metacognitive-feedback layer.** Pick a single mechanism
  for "system tells the agent something is off" and apply it across
  all relevant tools. Worth a focused design reflection before
  becoming individual items.
