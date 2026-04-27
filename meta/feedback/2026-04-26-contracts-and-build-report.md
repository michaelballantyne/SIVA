# Contracts: hard-constraint checking and a soft-constraint build report

## Origin

Came out of a brainstorm for the VIS 2026 short paper on validation —
"how does the analyst know what they're seeing is meaningful?" — but the
ideas are about VisLang itself, not the paper. Recording here for the
backlog.

The framing is software contracts (Meyer; Findler-Felleisen). The agent's
reasoning is opaque and untrustworthy, but the spec it produces and the
rendering it yields have checkable boundaries. Split them into two tiers:
**hard constraints** that must hold (preconditions on operators,
postconditions on output) and **soft constraints** that produce warnings
fed back to the agent in a build report.

This generalizes the Draco (Moritz et al. 2019) discipline of encoding
visualization design knowledge as constraints, but adapted for a
scivis pipeline DSL with an LLM as the synthesizer. Two differences from
Draco worth keeping in mind:

- Many checks are over **data values**, not just schema (does the
  isovalue actually cross any cells; does the clip range cover the
  populated part of the distribution). Draco's Vega-Lite setting is
  mostly type-driven; VTK pipelines are value-driven.
- Postconditions can also run over the **rendered output** (pixel
  variance, occlusion, depth-buffer picking against named regions) —
  3D failure modes that don't exist in 2D infovis.

A lot of this overlaps with what `node_statuses` already does for
empty-output diagnostics on contour / threshold / stream tracer. The
proposal is to pull that pattern out into a first-class contract layer
that operators register checks against, and to add a second tier of
soft warnings that the agent sees on every turn.

## Hard constraints (preconditions + postconditions)

These extend what `node_statuses` already reports. Each pipeline
operator declares predicates that must hold; failure is surfaced the
same way today's empty-output warnings are, in the per-node status.

Cheap, dischargeable purely from spec + data arrays + bbox metadata:

- **Mapped scalar in range.** `isovalue ∈ [field.min, field.max]`;
  `clip_range ⊆ field.range`. Generalization of the existing contour
  out-of-range check to every operator with a numeric threshold.
- **Filter output non-empty.** Count crossing cells / passing cells
  before invoking VTK; report empty *and* near-empty (< 0.01% or
  > 50%, configurable) since both usually indicate a misplaced
  threshold rather than a true property of the data.
- **Clip / colormap range covers populated data.** The `[lo, hi]`
  window contains at least the 5th–95th percentile of values present;
  catches `[0, 1]` defaults applied to a field living in `[200, 300]`.
- **Colormap kind matches field semantics.** Diverging ⇒ field range
  straddles zero or is declared bipolar; categorical ⇒ field is
  integer/categorical; sequential otherwise.
- **Log scale legality.** Log transfer function or log colormap ⇒
  field min > 0.
- **Field reference well-typed.** Named field exists; its type matches
  the channel (scalar where scalar expected, 3-vector for streamlines).
- **Seed points inside domain.** Streamline / glyph seeds within
  dataset bbox and any active mask.
- **Transfer function has non-zero opacity in occupied bins.**
  ∫ opacity(v) · histogram(v) dv > ε. Catches valid-but-invisible
  transfer functions.

Postconditions over rendered output (need the framebuffer / depth
buffer, no semantics):

- **Image not blank/uniform.** Pixel variance > ε.
- **Camera frames non-empty geometry.** Active geometry's bbox
  intersects the view frustum; flag "rendered correctly, pointed at
  empty space."
- **Foreground coverage in a sane band.** ~1–80% non-background
  pixels — distinguishes "tiny speck in corner" and "fully occluded"
  from a normal render.
- **Per-actor pixel contribution.** With multiple actors, each
  contributes > 0 pixels (catches the agent layering an opaque outer
  surface over the thing the user wanted to see).
- **Named-region visibility.** When a named selection / bbox is in
  scope, at least one rendered pixel traces back via the depth
  buffer to a cell in that region. VTK picking gives this for free.

Implementation sketch: each builder registers a list of
`(predicate, severity, message)` tuples; `interpret()` runs them after
the node executes (or before, for preconditions) and folds the
results into `node_statuses`. Render-level postconditions run once
after the full pipeline executes. This is a natural extension of the
existing `node_statuses` channel rather than a new mechanism.

## Soft constraints as a build report fed back to the agent

Draco's soft constraints rank candidate completions for a synthesizer.
With an LLM there's no slate of candidates — just one output and the
option to ask for another. So soft constraints become **targeted
regeneration prompts**, not a scoring function.

The proposal: after every `interpret()`, return a structured **build
report** containing hard-constraint pass/fail and a list of soft
warnings, each with a short human-readable message. Example:

```
spec OK
hard:  all 14 checks passed
soft:  3 warnings
  - colormap "viridis" on diverging field (range straddles 0);
    prefer a diverging colormap
  - clip range [0, 1] covers 12% of value distribution;
    data lives in [0.04, 0.71]
  - camera framing leaves the isosurface in 4% of pixels;
    consider a closer view
```

The agent reads this on the next turn and revises (or doesn't). LLMs
are unusually good at "here's a checklist of complaints, address
them" loops — much better than at picking good defaults in the first
place. We don't need Draco's learned weights because we're not
ranking; we just need an ordering of warnings to address, which the
agent and user can negotiate.

Two failure modes to design against:

1. **Warning fatigue.** Cap at top N (5?) warnings; hard violations
   always preempt soft ones. Same lesson as compiler diagnostics.
2. **Agent gaming warnings.** Agent silences warning #1 by changing
   the colormap, but breaks something the user actually asked for.
   Mitigation requires provenance tagging on spec fields
   (user-specified vs. agent-defaulted vs. data-grounded — derivable
   mechanically from the session log: substring match against the
   user utterance for "user," appearance in an MCP tool result for
   "data-grounded," neither for "default"). The build report then
   marks warnings on user-specified parameters as **off-limits to
   automatic revision** — the agent has to either preserve that
   parameter or ask the user.

The provenance tagging is independently useful (it's what answers
"which parameters should the user scrutinize?") but earns a second
job here as the scope-of-revision signal.

## Why this composes

Four pieces, each doing one job:

- **Hard preconditions** = spec must be well-formed before render.
- **Hard postconditions** = spec must produce something non-trivial.
- **Soft constraints** = build-report warnings, fed back to the agent
  for one self-correction pass or surfaced to the user.
- **Provenance tags on spec fields** = scope of what the agent is
  allowed to revise in response to warnings; also independently
  useful for ambiguity surfacing in the UI.

The checker is mechanical; only the fixer is the LLM. That sidesteps
the LLM-grading-LLM circularity that weakens evaluator-agent
designs (e.g. CoDA).

## Relationship to existing code

- `node_statuses` already implements the empty-output flavor of hard
  postconditions for contour / threshold / stream tracer. Generalize
  the pattern: each builder registers checks; the dispatcher folds
  results.
- Wrapper-level `ValueError` checks (the
  `2026-04-25-unified-error-reporting.md` feedback) should land in
  the same channel — they're preconditions in disguise.
- `extract_component` / `extract_region` ergonomics work
  (`2026-04-26-2232Z-scalar-range-default-and-extract-grid-ergonomics.md`)
  shows the same theme: silent defaults → failures the user can't
  diagnose. Contracts make the silent defaults loud.

## Open questions

- Where does the build report live? Returned from each MCP tool call?
  A separate `get_build_report()` tool? Embedded in the screenshot
  response?
- How are soft constraints authored — Python predicates, or a
  declarative DSL of their own? Domain packs (CFD, medical) probably
  want a declarative form so they can ship without code review.
- Provenance tagging requires access to the user utterance for the
  current turn. The MCP server doesn't see that today; the client
  (Claude Code) does. Either the agent reports its own tags (weaker
  but easy) or we extend the protocol so the server can do it
  mechanically.
