# Multi-frame visual feedback and the trigger problem

## Observation

Conversation surfaced a pattern about which MCP tools get used spontaneously
by the agent and which don't. Comparing tools that get used (without the
human asking) to tools that don't:

**Used spontaneously** — `suggest_isosurface`, `get_ground_z`,
`set_suggested_camera`. Their triggers are *propositional*: each maps to a
concrete question the agent's reasoning already produces ("what isovalue
should I contour at?", "what z for my seed at (x,y)?", "I want a sensible
initial framing"). The agent arrives at the question naturally and the
tool is the answer.

**Under-used** — `camera_orbit`. Its trigger is *metacognitive*: "I am
uncertain about the 3D structure of this scene." The agent has to first
notice it's uncertain, then attribute the uncertainty to spatial perception
specifically, then map that to "look from more angles." LLMs are generally
poor at metacognition about their own visual perception — they tend to
commit to an interpretation of an ambiguous image rather than flag
uncertainty about it.

The gap matters because the conditions that *warrant* multi-frame inspection
(occlusion ambiguity, lost spatial context, missing features) are exactly
the conditions the agent is least equipped to recognize from the
single-frame view that caused them.

## Specific failure mode raised in conversation

Beyond occlusion, a common observed failure mode is **lost context due to
too-close framing**: the agent ends up with a camera that's too zoomed in,
loses track of where it is relative to the dataset bounds, and reasons
incorrectly about subsequent steps. Single-screenshot feedback is impoverished
in this dimension as well — and the agent doesn't reliably notice.

## Two distinct multi-frame patterns

Worth thinking about as a single design category rather than separate tools:

- **Orbit** (N angles, same zoom) — disambiguates occlusion and depth.
- **Zoom survey** (N zoom levels, same angle) — restores spatial context
  when the current view is too tight.

`camera_orbit` exists today; a zoom-survey counterpart does not.

## Three remediation patterns for trigger problems

1. **Procedural trigger** — "after step X, do Y," regardless of whether
   the agent feels uncertain. Robust; costs occasional unnecessary calls.
   Example: *"After rendering an isosurface or other 3D extraction, call
   camera_orbit before declaring the scene complete."*

2. **Explicit metacognitive checklist** — instead of "when uncertain, do
   Y," enumerate observable indicators: *"Consider 3D structure ambiguous
   when (a) multiple actors overlap in the 2D image in ways that could be
   foreground or background, (b) an expected feature isn't visible despite
   the report confirming the actor was created, (c) the screenshot doesn't
   match what you predicted from the data."* The agent applies a checklist
   against the screenshot it just received, not against an internal feeling.

3. **Auto-inclusion in default responses** — sidestep the trigger problem
   entirely by always providing the richer feedback. For zoom context
   specifically: include a small overview thumbnail alongside the main
   screenshot in every build response. Marginal context cost is one small
   image; the "lost context" failure mode disappears as a category.

Auto-inclusion is the most robust where marginal context cost is
acceptable. It's the only pattern that doesn't depend on the agent
correctly recognizing a precondition.

## Concrete suggestions

Ordered by leverage / reversibility:

1. **Auto-include an overview thumbnail in every build response** (and
   every other tool that returns a screenshot, possibly). Low-resolution,
   rendered at "overview" framing, alongside the main camera-relative
   screenshot. Tests the hypothesis that trigger problems disappear when
   triggering isn't required. Small, reversible, easy to evaluate over a
   few sessions.

2. **Add an explicit metacognitive checklist to `camera_orbit`'s tool
   description**, listing observable indicators rather than relying on
   "when uncertain." If usage increases, that confirms explicit
   checklists work for metacognitive triggers. If not, the trigger needs
   to be procedural or auto-inclusion is the only path.

3. **Consider a `survey_zoom` tool** as a sibling to `camera_orbit`, or
   generalize both into `survey_view(modes=["orbit"|"zoom"|"both"])`.
   Probably worth doing only if (1) doesn't fully address the
   lost-context failure mode.

## Generalizable observation worth keeping

> Tools whose triggers map to concrete questions the agent is already
> asking get used spontaneously. Tools whose triggers require the agent
> to first recognize a meta-cognitive state about its own reasoning get
> under-used. Three remediation patterns scale from least to most robust:
> procedural triggers, explicit metacognitive checklists with observable
> indicators, and auto-inclusion in default responses. The last is the
> only one that doesn't depend on the agent correctly recognizing a
> precondition.

This is a small but credible ACI design lesson — fits in a discussion or
future-work paragraph if the paper has room, and is the kind of nuance
that strengthens the "interface design has observable effects on agent
behavior" claim.

## Caveats

- Single-session evidence so far. The pattern is plausible but would
  benefit from cross-session observation (ideally including weaker
  models, where metacognitive triggers presumably fail harder).
- "Auto-inclusion" experiments need to weigh the always-on context cost
  against the value. For overview thumbnails specifically, the cost
  seems low and the failure mode is common — but other auto-inclusions
  (per-turn orbit frames?) would be much harder to justify.
- The propositional/metacognitive distinction is descriptively useful
  but is itself a hypothesis. A tool with a propositional trigger that
  *the agent doesn't ask the underlying question of itself* would
  also go under-used. The framing should be tested, not assumed.
