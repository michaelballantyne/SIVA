# Annotations Redesign — Round 2 Reflection

Date: 2026-04-25
Reviewed: commit 8c39de1 (after round 1 iteration)

## Verdict

Diminishing returns reached. The iteration addressed all six round-1 findings
correctly, the feature is internally consistent, and the remaining
imperfections are nits. Ship it and move on.

## Round 1 follow-through

### #1 — Annotations distort cube-axes bounds. **Fully fixed.**
`actor.UseBoundsOff()` is called at dsl.py:1895, immediately after
`SetPosition`. The placement is correct: `add_overlay_actor` appends to the
renderer, but bounds exclusion is a property of the actor itself, so order
inside `_apply_scene_settings` doesn't matter. The new
`test_axes_bounds_unaffected_by_far_annotation` (test_annotations.py:224–257)
exercises the actual `ComputeVisiblePropBounds()` invariant rather than just
checking the flag, which is what the round-1 recommendation asked for. Good.

### #2 — Discoverability. **Fully fixed.**
`annotate` appears in `get_dsl_overview()` Display section (server.py:1525)
and in `_DSL_GROUPS["Display"]` (gen_docs.py:188). Bonus: `axes` was also
added to gen_docs (it had been missing — so the iteration caught a related
defect not in round 1). docs/dsl-reference.md:1469 has a full entry generated
from the docstring. The instructions string and overview are now consistent.

### #3 — Dead `_parse_color`. **Fully fixed.**
`grep _parse_color vislang/server.py` returns nothing. Color coercion is
unified in `_coerce_color` (dsl.py:7). The drift hazard from round-1 #7 is
also resolved by deletion.

### #4 — Missing `_EXAMPLES` and `_RELATED` for `annotate`. **Fully fixed.**
`_EXAMPLES["annotate"]` (server.py:2362) shows three idioms: origin/axis
labels, hex color, tuple color tied to a sphere center. The third example
is more useful than the docstring's because it shows annotate composing
with `source` + `show`. `_RELATED["annotate"] = ["title", "axes", "show"]`,
and `title` and `axes` both back-reference `annotate`. Symmetric.

### #5 — Test trim. **Mostly fixed; one round-1 ask not implemented.**
Down from 30 tests / 292 lines to 23 tests / 261 lines. The eight
named-color tests collapsed into one `subTest` loop, the trivial init test
became `test_initial_state_empty` (one line, fine to keep), and adversarial
inputs (`None`, empty string, bad hex, out-of-range, 4-tuple, 2-tuple) are
covered. The missing piece: round 1 also asked for an annotation +
screenshot-to-pixels test, which still doesn't exist. None of the 23 tests
actually render to a JPEG. Given that the bounds invariance test does
construct a real `Renderer`, this is a small gap but not a regression — the
feature was never pixel-tested. See "What to leave alone."

### #6 — `_coerce_color` validation. **Fully fixed, no surprising new behavior.**
The hardening at dsl.py:7–28 is conservative and well-documented:

- `None` returns white (instead of `AttributeError` on `.strip()`).
- `len < 3` sequences return white (used to crash on unpacking).
- 4-tuple silently drops alpha (documented in docstring).
- Components are clamped to `[0, 1]` (used to pass through, then VTK would
  saturate or raise).

The fall-through behavior for unknown strings and bad hex is unchanged
(silent white). The docstring at dsl.py:8–18 lists every fallback case, so
the "silent" behavior is at least discoverable. No regressions: existing
named-color, hex, and tuple call sites still produce the same outputs.

## New observations

None worth acting on. Two things I noticed but explicitly do not recommend
fixing — listed under "What to leave alone."

## What to leave alone

- **No screenshot/pixel test for annotations.** Round 1 asked for one and
  the iteration didn't add it. The bounds-invariance test (test_annotations.py:224)
  exercises the rendering path far enough to catch any
  `vtkBillboardTextActor3D` initialization regressions; an actual
  pixel-comparison test would be high-cost (xvfb dependency, image diff
  tolerance, JPEG flake) for low marginal value. If a future feedback
  entry says "annotations didn't show up in the screenshot," revisit.

- **Silent fall-through to white on bad input.** A typo in a color name
  produces a white label with no warning. For an LLM caller this is mildly
  unfriendly — they can't tell from the screenshot whether the color "took."
  Adding a logger warning is tempting but introduces a new failure mode
  (log spam if the model retries with the same bad input across rebuilds)
  and the white-label outcome is recoverable. Document-only is the right
  call, and the docstring already does it.

- **Three positional floats vs `pos=(x,y,z)` tuple.** Round 1 ruled this
  out and the call still stands. `annotate(0, 0, 50, "fire front")` reads
  more naturally than `annotate(pos=(0, 0, 50), text="fire front")` for
  the LLM use case where coordinates come from `get_spatial_extent` as
  three separate numbers.

- **`font_size` in points vs window-relative.** Open question from round 1.
  Still open, still not blocking. Wait for a feedback entry where someone
  actually complains about label size before redesigning the unit.

- **`title` vs `annotate` overlap.** Both are "text on the screen" but
  `title` is screen-space-anchored 2D and `annotate` is world-space-anchored
  3D billboard. The names are fine; the cross-references in `_RELATED` and
  the docstring "Notes" sections (dsl.py:1610–1613, 1646–1647) make the
  distinction clear. Don't merge.

- **Test `test_initial_state_empty` (test_annotations.py:116).** Tests
  `__init__`, not the feature, so technically low-value. But it's two lines,
  and removing it now would just be churn. Leave it.

- **The `_DSL_GROUPS` Display section ordering** puts `title`, `annotate`,
  `axes` after `scene_preset`, which is semantically reasonable (background
  first, then overlays). Could argue `axes` belongs before `title`/`annotate`
  since axes is structural and the others are decoration. Pure preference;
  leave as-is.

## Summary

The round 1 reflection identified six concrete issues, the iteration fixed
all six (one only mostly — the screenshot test), and no new issues surfaced
in this pass. The feature is in a good state. Future work on annotations
should be feedback-driven (anchor-to-node refs, leader lines, label
collision detection) rather than reflection-driven. The next reflection
cycle should turn its attention elsewhere in the codebase.
