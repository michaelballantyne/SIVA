# Vega-Lite Style Inference for the DSL

Vega-lite's core principle: infer scale type, color scheme, and axis properties
from data characteristics (type, range, cardinality) so the user only specifies
intent, not rendering mechanics. VisLang should do the same for its display
properties.

## Inferences worth implementing

**Diverging colormap + symmetric range from field statistics**
If `color_by` is omitted or the field spans both positive and negative values,
and no `lut` is specified, infer a diverging colormap (e.g. `"cool_to_warm"`)
and set `scalar_range=(-r, r)` where `r = max(|min|, |max|)`. If the field is
strictly positive, default to a sequential lut. This mirrors vega-lite's
scale-type inference from data type. The session specs show this being done
manually every time (`(-2,2)`, `(-3,3)`, `(-5,5)` for vorticity fields).

**Auto scalar_bar from color_by**
Vega-lite automatically generates a legend for any color encoding. VisLang
should add a `scalar_bar` whenever `color_by` is specified and no `scalar_bar`
is given, using the field name as the title. Currently the agent has to
remember to add it explicitly; the session specs do this inconsistently.

**Auto scalar_bar title from field name + units**
Vega-lite derives axis/legend titles from field names, incorporating
transformation info. VisLang could do the same: if the field name is
`"omega_y"` the bar title defaults to `"omega_y"`; the user can override with
a formatted string. Eliminates boilerplate like `scalar_bar="omega_y (1/s)"`.

**Glyph ScaleFactor from domain extent / field magnitude**
Vega-lite infers a linear scale domain from data range. For `glyph()`, infer
`ScaleFactor` as `(domain_extent / N) / typical_magnitude` where N is a target
arrow length in grid cells (~2-3). The session's `ScaleFactor=5.0` was
hand-tuned for a 600-unit domain with ~10 m/s winds.

**mask_points OnRatio from grid dimensions**
Target a fixed arrow count (e.g. 600). Infer `OnRatio = total_points /
target_count`. Currently hand-set to 500 for a 600×500 grid.

## Lower priority / harder

**Context layer opacity**: when a dataset appears more than once in a spec
(e.g. ground terrain shown first as primary, then again as background context),
default the second instance to `opacity=0.3`. Hard to infer automatically since
it requires understanding user intent.

**VOI for ground slice**: `extract_grid(..., VOI=[0,W,0,H,0,0])` for z=0 is
the most common usage. A `ground_slice()` convenience form could eliminate the
need to specify grid dimensions explicitly.
