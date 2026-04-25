# Annotations Redesign — Design Reflection

Date: 2026-04-25
Commit reviewed: f6541e7

## Summary

The redesign is a clear win: a 3-D scene-labeling primitive belongs in the
declarative DSL, not as an imperative MCP tool with its own per-view state
dict. The shape of `annotate(x, y, z, text, color, font_size)` reads naturally,
sits next to `title()` and `axes()`, and is correctly cleaned up by the existing
overlay-actor lifecycle. That said, the change shipped with three concrete
defects worth fixing: annotations stretch the cube-axes bounds, the form is
absent from the docs index and the DSL overview, and `_parse_color` in
`server.py` is now genuinely unreachable.

## What works well

- `_annotations` is initialized in `__init__` (dsl.py:78), so `_apply_scene_settings`
  is unconditional and there's no "is this attribute set?" branching. Same shape
  as `_shows`.
- Cleanup is free: `Renderer.clear()` (renderer.py:153–167) already drains
  `_overlay_actors`, and `apply_to_renderer` calls `clear()` first
  (dsl.py:1958). The "rebuild without `annotate()` -> labels disappear" semantic
  works without new code, and the test at test_annotations.py:251 verifies it
  end-to-end with a real renderer.
- `_make_namespace` (dsl.py:1979–1995) auto-discovers the new method via
  `inspect.getmembers`, so `annotate` reached the DSL namespace with zero plumbing.
- `_coerce_color` accepts the three input shapes an LLM is most likely to write
  (named string, hex, tuple) and falls back to white rather than crashing on a
  typo — appropriate for a labeling primitive where the cost of an unknown color
  is "label looks wrong" not "pipeline build fails."
- Docstring on `annotate()` (dsl.py:1607–1641) explicitly explains the
  declarative semantic ("simply omitting `annotate()` calls in the next rebuild
  removes all previous labels") — this is the key mental-model shift from the
  old MCP tool, and calling it out is the right call.

## Concrete issues (ranked)

### 1. Annotations distort cube-axes bounds

- **Severity**: important
- **Location**: dsl.py:1879–1890 (annotation actor creation), dsl.py:1892–1895
  (axes uses `ComputeVisiblePropBounds()`)
- **Observation**: `vtkBillboardTextActor3D` is a `vtkProp3D` with `UseBounds=True`
  by default. After `SetPosition(x, y, z)` its bounds collapse to a single point
  `(x,x, y,y, z,z)`. Because annotation actors are added (via `add_overlay_actor`)
  *before* the cube-axes is constructed in `_apply_scene_settings`, the call to
  `renderer._renderer.ComputeVisiblePropBounds()` at dsl.py:1895 includes those
  points. Place a label at `(2*xmax, 0, 0)` and the X axis stretches twice as far
  as the data. Title is unaffected because `vtkTextActor` is a `vtkProp2D`. This
  is the first user-visible bug a user with `axes()` + `annotate()` will hit —
  and labeling features inside an axes-annotated scene is the obvious use case.
- **Recommendation**: Call `actor.UseBoundsOff()` immediately after constructing
  the billboard at dsl.py:1880. Add a regression test: build a sphere with bounds
  ~(-1,1)^3, add an axes() and an annotate(100, 0, 0, "far"), assert the cube-axes
  X-bounds remain ~[-1, 1].

### 2. `annotate` is missing from the DSL overview and dsl-reference.md

- **Severity**: important (discoverability — agents won't reach for what they
  don't see)
- **Location**: server.py:1544–1554 (Display section in `get_dsl_overview()`),
  scripts/gen_docs.py:182–188 (`_DSL_GROUPS["Display"]`)
- **Observation**: `get_dsl_overview()` lists `show`, `camera`, `background`,
  `scene_preset`, `title`, `axes` under Display — but not `annotate`. Same in
  `_DSL_GROUPS` so it's also absent from the generated docs/dsl-reference.md
  table of contents and body (verified: only one literal "annotate" appears in
  that file, inside `title()`'s "see also"). The `title()` docstring tells users
  to "use `annotate()` instead" for 3-D labels, but the entry it points to is
  invisible in the overview an LLM agent reads first.
- **Recommendation**: Add `"annotate"` to the Display section of
  `get_dsl_overview()` (one-liner: `"annotate(x, y, z, text, color=, font_size=)
  — 3-D billboard label at a world-space position"`) and to `_DSL_GROUPS["Display"]`
  in gen_docs.py, then regenerate docs.

### 3. Dead `_parse_color` in server.py

- **Severity**: minor
- **Location**: server.py:214–242
- **Observation**: `_parse_color` is only defined and never called — `grep`
  confirms a single occurrence in the file. The redesign moved color parsing
  into the DSL (`_coerce_color` in dsl.py:7–44) but left the server-side helper
  behind. BACKLOG.md:59 actually lists this exact cleanup as `[x]` done, but the
  function is still there, so the backlog entry is also stale.
- **Recommendation**: Delete server.py:214–242 outright. The two functions are
  near-identical; only `_coerce_color` (which also accepts tuples) is needed.

### 4. `get_dsl_reference("annotate")` returns a docstring with no example or
   related-forms list

- **Severity**: minor
- **Location**: server.py:1965–1990 (`_EXAMPLES`), server.py:2450–2489 (`_RELATED`)
- **Observation**: The `_EXAMPLES` and `_RELATED` dicts in `get_dsl_reference`
  have entries for every Display form except `annotate`. `title`'s related list
  is `["show", "scene_preset"]` — should mention `annotate`. Reference output
  for `annotate` therefore drops the "--- Example ---" and "--- Related forms ---"
  sections that every other form provides. The docstring at dsl.py:1627–1631
  has a perfectly good 3-line example that should also live in `_EXAMPLES`.
- **Recommendation**: Add an `_EXAMPLES["annotate"]` (extract the docstring
  example: origin + x-axis + fire-front), add `_RELATED["annotate"] = ["title",
  "axes"]`, and add `"annotate"` to `_RELATED["title"]` and `_RELATED["axes"]`.

### 5. Test file is ~40% redundant; missing one valuable scenario

- **Severity**: minor
- **Location**: tests/test_annotations.py
- **Observation**: 30 tests, but several are low-information:
  - `TestCoerceColor` has 8 named-string tests (lines 23–39) that all exercise
    the same dictionary lookup. Two would suffice.
  - `test_initial_annotations_is_empty_list` (line 115) is testing `__init__`,
    not the feature.
  - `test_default_color_and_font_size` (line 87) and the four `*_override`
    tests (lines 93–98) are pure dict-storage tests on a builder that just
    calls `list.append`.

  Missing scenarios that would actually catch regressions:
  - `_coerce_color` on adversarial input: `None`, empty string, 4-tuple,
    out-of-range tuple `(2.0, -1.0, 0.5)`, hex without leading `#`, hex of wrong
    length. Today the function silently returns `(1,1,1)` for some of these and
    crashes on others (`None.strip()`); the test suite documents none of it.
  - **Coexistence with `axes()`** — would have caught issue #1.
  - Annotations + screenshot: build a scene with one annotation, screenshot,
    check the JPEG file exists and has nonzero size. None of the 30 tests
    actually render to pixels.
- **Recommendation**: Compress the named-color tests to two
  (one-string + one-hex), drop the trivial getter tests, and add the three
  scenarios above. The file would shrink from 292 to ~180 lines and be
  strictly more trustworthy.

### 6. `_coerce_color` accepts list/tuple without validation

- **Severity**: nit
- **Location**: dsl.py:17–18
- **Observation**: `tuple(c)` passes through any iterable unchanged — including
  a 4-tuple `(r,g,b,a)`, a 2-tuple, components > 1.0, negative components.
  `tp.SetColor(r, g, b)` will then raise an unintelligible VTK error or
  silently saturate. For the LLM use case, the failure mode is "label
  invisible" with no diagnostic.
- **Recommendation**: Either validate (length 3, components clamped to [0,1]
  with a one-line warning) or document the constraint in the docstring. Given
  the "minimal scope" goal of the redesign, documentation is probably enough.

### 7. Two color helpers exist; only one is reachable

- **Severity**: nit
- **Location**: dsl.py:7–44 (`_coerce_color`), server.py:214–242 (`_parse_color`)
- **Observation**: Same hard-coded named-color table in both. Once `_parse_color`
  is deleted (issue #3) this becomes moot, but until then the duplication is a
  drift hazard — adding "teal" to one and not the other will now produce
  different colors in title (server-side) vs annotate (DSL-side)... except
  title's color comes from the DSL too, so actually the `server._parse_color`
  is purely vestigial. See issue #3.

## Things considered but ruled out

- **`pos=(x,y,z)` instead of three positional floats.** Tempting for symmetry
  with `camera(position=...)`, but `annotate(x, y, z, text)` reads more
  naturally for a labeling primitive — calling sites usually have the
  coordinates as three separate numbers (e.g. from `get_spatial_extent` output)
  rather than as a tuple. Not worth the change.
- **Adding `anchor`, `background`, `leader_line` affordances now.** These are
  the affordances a polished annotation system has, but adding them now means
  guessing how an LLM will want to use them. Better to ship the minimal form,
  watch real usage in feedback entries, and add what's actually requested. The
  declarative shape makes additions easy: add a kwarg to `annotate()`, add it
  to the dict, read it in `_apply_scene_settings`. No state-machine surgery.
- **Tracking annotation actors separately from `_overlay_actors`.** The current
  shared-list approach is simpler and correct, because `clear()` runs once per
  rebuild and there's no scenario where you'd want to keep annotations while
  clearing the title. Don't split until there is.
- **Renaming `text` to `label`.** Either is fine; `text` matches `title(text=)`
  and `vtkTextActor.SetInput()` semantics, so the current choice is consistent.

## Open questions

- **Is `font_size` in points the right unit for a billboard 3-D actor?** In
  VTK, `vtkTextProperty.SetFontSize` is in points and the billboard scales the
  glyphs to roughly that size on screen regardless of camera distance — which
  is what users want. But the perceived size is also affected by render-window
  resolution; a label at font_size=14 in a 640×800 window vs a 1920×2400 window
  will be at the same pixel size, not the same fraction of the screen. For a
  primitive whose whole point is "stay readable from any angle," is
  pixel-locked the right behavior, or should it scale with window size? I
  don't have a strong opinion; flag for the next feedback session.
- **Should `annotate()` accept a `node=` ref to anchor to a feature's centroid
  rather than absolute coordinates?** Imagine `annotate(node=fire_iso, "fire
  front")` instead of looking up coordinates manually. Would compose well with
  `get_spatial_extent`/`describe_data`, but introduces a non-trivial design
  decision (what's the centroid of an isosurface? bounds center? COM?) that
  shouldn't gate the shipped form. Worth considering once there's feedback
  from agents using the current API.
