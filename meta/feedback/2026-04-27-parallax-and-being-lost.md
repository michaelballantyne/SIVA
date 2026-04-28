# Parallax for depth, and the "I'm lost" recovery problem

Session exploring two related issues:

1. Single screenshots flatten depth — would multiple offset views help?
2. Agents (including me) sometimes end up with bad/uninformative camera
   states and don't always notice. What's the right recovery story?

Tested both interactively against `view-brain.py` (headsq.vti volume
render) and `view-fire.py` (wildfire plume + terrain). Conclusions
below, plus three proposals and a meta-finding.

---

## Part 1 — Parallax for depth perception

### Setup
A model viewing static screenshots can't fuse stereo the way a human
does. But it can **diff frames** — if I see two views from slightly
different angles, features at different depths shift differently and
that disparity is a depth cue. Tested by manually computing
Rodrigues-rotated camera positions around the focal point.

### What worked

- **±5° offset is the sweet spot** for a 3-frame horizontal sweep.
  ±1.5° is below the noise floor of volume-render translucency — the
  frames look identical. ±8° starts feeling like sequential views
  rather than parallax (I lose the "same scene, slightly shifted"
  frame and start re-orienting).

- **3 views > 2 views.** The center frame as a stationary reference
  is genuinely valuable: I can ask "did this feature move?" rather
  than just "are these two different?" With only 2 views I can't tell
  which features are at focal-plane depth.

- **Cross pattern (5 frames: center + ±h + ±v) > single axis.** The
  test case made this stark: a near-frontal view of a head has
  bilateral symmetry along the vertical screen axis, so horizontal
  parallax barely disambiguates depth (left/right symmetric features
  are at similar depths). Vertical parallax revealed the inside of
  the cranial vault, the brow ridges as forward-protruding shelves,
  and the underside of the chin — completely different information
  than the horizontal pair surfaced.

- **No single "right axis."** Whichever direction the parallax goes,
  features aligned with that axis won't disambiguate. Cross pattern
  is the safe default; the cost of 2 extra frames is small compared
  to "guess wrong axis and see nothing." Heuristics like "rotate
  perpendicular to the longest screen-space extent" work in some
  cases but are unnecessary when you can just do both axes.

### Concrete depth facts I learned that single screenshots had hidden

On the brain coronal-ish view:
- A thin tube near the upper-right was clearly *in front of* the head
  body, not embedded in it (parallax shift much larger than head bulk).
- A pale elliptical disc at the bottom was a *cut plane* at the data
  boundary, not a basin in the anatomy (no parallax motion of its rim).
- Features along one edge of the head shifted more than the other,
  telling me which side was nearer the camera.

On the frontal head view (vertical parallax only):
- Brow ridges read as shelves protruding forward.
- The cranial vault interior became visible as a dome with concentric
  rings when the camera tilted up — invisible in any single frame.

These aren't novel insights about anatomy; they're examples of depth
ambiguity that a single render leaves and that ±5° parallax resolves.

### Proposal: `parallax_views(degrees=5.0, axis="cross")`

A new MCP tool. Returns 5 frames for `cross` (center + ±deg h + ±deg v),
3 for `horizontal`/`vertical`. Camera restored after.

Justified by two things:
1. **Multi-frame capture in one call.** The 5 frames need to be
   produced server-side with state save/restore; doing it manually is
   5 round-trips and 5 camera commits.
2. **Encoded knowledge.** The 5° cross is the empirically-validated
   default. Without baking it in, every agent rediscovers it (or
   doesn't). Also hides the Rodrigues rotation math, which is
   non-trivial to compute inline.

Implementation: ~50 lines, follow the `camera_orbit` pattern in
`server.py:650`. ~1 hour with a basic test.

---

## Part 2 — Reframing when the camera is bad

### Several failure modes
- Camera inside the volume → fog with no silhouette (brain at d=67).
- Camera outside but volume fills frame → no silhouette (brain at d=200).
- Camera way too far → feature is a tiny dot (naive 4r reset on the
  wildfire plume).
- Camera over-zoomed on a feature so the feature is cropped (wildfire
  initial state).

### What I tried that didn't work

**Naive "fit full bounding box at distance ≈ 4r"** fails badly on
feature-focused work. The wildfire dataset has a 10× scale mismatch
between the full data (radius ~915) and the plume of interest (radius
~85). Fitting the full bbox throws away the user's feature entirely —
plume becomes a tiny speck, frame mostly empty.

**Feature detection heuristics** (smallest visible node by bbox,
node containing focal point, etc.) all turned out brittle on the
wildfire test case. I confidently proposed "smallest bbox containing
focal point" only to discover the focal point (Y=70) was *outside*
the plume bbox (Y=[-48, 26]). The user had pointed the camera near
but not at the plume centroid. None of my candidate auto-detection
rules would have caught this case cleanly.

### What does work

The user's framing observation: **the agent can compute camera moves
itself.** All the dolly math is one line of vector arithmetic
(`pos = focal + factor * (pos - focal)`); reframing math is
similarly trivial. The agent already has `set_camera` and `Bash` for
calculation. A "smart reframe" tool would just hide trivial math
behind feature-detection heuristics that don't generalize.

So: **no `dolly()` tool, no `reframe()` tool.** Drop those proposals.

### Proposal: `survey()` for orientation recovery

Once the agent recognizes it's in a bad view, the natural action is
"see all my orientation options at once and jump to the best." That's
genuinely tool-shaped:

- 3 standard overviews (top-down, side, front) — composition over
  `set_suggested_camera` for each style
- 1-2 pulled-back-from-current frames (4×, 16× along view ray) —
  preserves the user's view direction in case they were on the right
  thing but over-zoomed
- Each frame returned with its `camera_state` so the agent can
  `set_camera(**chosen.camera_state)` to commit

Why this beats sequential `set_suggested_camera` calls:
- **Non-destructive.** Doesn't mutate camera state during the
  evaluation; the agent looks at the menu, picks one, *then* commits.
- **One turn, not three or four.** Sequential trial-and-error is
  3-4 turns each mutating state.
- **Includes a frame `set_suggested_camera` can't generate.** The
  pulled-back-from-current view preserves user intent; standard
  styles do not.

Implementation: a couple hours. Mostly orchestration — the
suggested-camera logic and rendering primitives all already exist.

---

## Part 3 — The metacognitive gap

The harder finding from this session: **the tools only help if they
get called.** And in this session I confirmed I don't reliably notice
when I'm lost.

Two failures from today:
- Brain view at d=67 (camera inside the volume): I took the screenshot,
  saw foggy translucency, and would have proceeded to take parallax
  samples *of the fog* if the user hadn't asked "does this make sense?"
- Unfiltered wildfire volume render at 32 seconds: I didn't flag the
  slowness as a problem; the user did. The fix (threshold first) was
  obvious in retrospect.

The pattern: I accept the tool output and move on. The cue that
something is wrong is available in the screenshot/timing, but I don't
escalate it.

### Possible levers
None of these were tested in this session — listing as design space:

1. **Tool description hints.** `screenshot()` could say something like
   "if the result is uniform color, low-contrast, or lacks clear
   edges, the camera may be inside the volume — call `survey()` for
   orientation." Same for `set_camera` — describe likely failure
   modes. Cheap, immediate, no code changes.

2. **Server-side render warnings.** The renderer can cheaply check
   "is the camera position inside the data bounding box?" and append
   a warning to the result text. Possibly also "image has <N%
   non-background pixels" or "edge entropy is suspiciously low."
   Puts the metacognition in the system rather than relying on agent
   introspection.

3. **System prompt / MCP instructions.** Add an explicit "noticing
   when you're lost" criterion to the workflow guidance. Less
   localized than tool descriptions but easier to write once.

4. **Auto-pre-empt at view creation.** Make sure `new_view()` always
   starts with a sensible overview camera. Doesn't help mid-session
   when the camera drifts, but covers the most common starting
   failure (the d=67 brain case was a session that started with the
   live VTK window's default camera, which was inside the volume).

My intuition: lever #2 (server-side warnings) is the most
high-leverage because it doesn't require the agent to introspect —
the system tells the agent something is off. Worth trying first.
But #1 is the cheapest and could be added immediately.

This is the deepest finding of the session: tool design is half the
answer; the other half is the agent learning when to call the tools.
The two `parallax_views` and `survey()` proposals address the
"what tools" half. The "when to call them" half is open.

---

## Rejected ideas (and why)

- **`dolly(factor)` tool.** The math (`pos = focal + factor * (pos -
  focal)`) is one line. Agent can compute it inline and call
  `set_camera`. Wrapping it as a tool would add surface area without
  encoded knowledge or multi-frame value.

- **`reframe()` / smart auto-fit tool.** Tried several feature-detection
  heuristics ("smallest visible node," "node containing focal point,"
  "smallest bbox containing focal"). All brittle on the wildfire test
  case where the focal point was *outside* the plume bbox. No clean
  rule emerged. Better to expose primitives (`set_camera`,
  `set_suggested_camera`, `survey`) and let the agent choose, possibly
  with explicit `node` parameter on `set_suggested_camera` later.

- **`zoom_sweep()` as a standalone tool.** Subsumed by `survey()`,
  which includes pulled-back frames in its menu. A pure zoom sweep
  alone has narrower utility — the "I'm lost" use case is rarely just
  "wrong distance," it's also "wrong angle." Survey covers both.

- **Auto-detection of "the feature" by closest-bbox-to-screen-center
  or ray-cast through center.** Discussed but not implemented because
  the simpler answer (let the user/agent pick a feature explicitly,
  or do a survey) handles the same cases without the implementation
  complexity. Worth revisiting if a real failure mode emerges that
  *only* an automated detector solves.

- **Camera-bbox heuristic at fixed distance ≈ 4r based on full data
  bounds.** Actively wrong for feature-focused work, as the wildfire
  10× scale-mismatch test demonstrates. Mentioning it explicitly so
  it doesn't get re-proposed.
