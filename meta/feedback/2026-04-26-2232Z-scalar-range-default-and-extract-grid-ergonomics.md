# scalar_range default falls through to (0, 1); extract_grid VOI is awkward for "just k=0"

Date: 2026-04-26

Two papercuts surfaced during a wildfire visualization session
(`hot-reload-2/view-main.py`, three nested theta isosurfaces over a
fuel-density ground layer).

---

## 1. Omitting `scalar_range` on a scalar `color_by` silently uses (0, 1)

### What happened

The pipeline had:

```python
ground = extract_grid(input=data, VOI=[251, 850, 0, 499, 0, 0])
show(ground, "ground", color_by="rhof_1", scalar_range=(0, 0.6),
     lut="terrain", scalar_bar="Fuel density (kg/m^3)")
```

I wondered aloud whether `scalar_range=(0, 0.6)` was redundant given the
`show()` docstring's claim of "smart defaults applied automatically (Vega-lite
style)." On `rhof_1` the ground node's actual range is `(0.017, 0.595)`, so I
expected omitting `scalar_range` to give effectively the same picture. It did
not. Removing it produced a visibly different render — the user noticed
immediately — and `describe_data` confirmed the field range was nowhere near
`(0, 1)`.

### Why

`vislang/filters.py:1611-1656`. After resolving `display_props`:

- Auto-detection of `scalar_range` only fires when `component is not None`
  (vector-component coloring). For plain scalar `color_by`, `scalar_range`
  stays `None`.
- `if scalar_range: mapper.SetScalarRange(...)` is then skipped, so VTK's
  built-in mapper default of `(0, 1)` takes over.
- `build_lut(lut_config, scalar_range=None)` builds the LUT without a data
  range, so the colormap stretches across `(0, 1)` regardless of what the
  data actually contains.

`_apply_smart_defaults` (around `filters.py:1510-1554`) does compute
`arr.GetRange()` — but only for *signed* fields, in service of building a
symmetric diverging range. Unsigned scalar fields fall through with no range
inference at all.

### Why this matters

The `show()` docstring (`dsl.py` around the show() definition, also visible
via `get_dsl_reference("show")`) advertises "smart defaults applied
automatically." For unsigned scalars + named lut, that advertisement is
wrong: the only thing that happens is VTK's `(0, 1)` fallback. Users who
trust the docstring (as I did, after reading the get_dsl_reference output)
will produce visibly broken renders without any warning, and the breakage is
non-obvious because the ground still draws and the scalar bar legend still
appears — it just maps the wrong range.

### What other tools do

PyVista's `Plotter.add_mesh` defaults `clim` to
`[np.nanmin(scalars), np.nanmax(scalars)]` when the user omits it
(`pyvista/plotting/mapper.py`, `set_scalars`). ParaView "rescale to data
range" is the default behavior for newly-displayed representations. Both
ecosystems treat data-range as the obvious default for an unspecified
scalar range. (Both also have the well-known outlier-domination footgun;
neither uses percentile clipping by default.)

### Suggested fix

In the surface-actor branch (the same place vector-component auto-detection
lives), when `color_by` is set and `scalar_range is None`, pull
`arr.GetRange()` from the input dataset and use that. This matches PyVista
and ParaView, and matches what the `show()` docstring already implies is
happening.

A fancier version would clip to `(p1, p99)` to avoid outlier domination, but
that's a separate decision — getting off `(0, 1)` is the load-bearing fix.
The current behavior is, effectively, a bug masquerading as a default.

---

## 2. `extract_grid` for "the ground layer" requires reciting the full i/j extent

### What happened

The session opened with this idiom for the ground:

```python
ground = extract_grid(input=data, VOI=[251, 850, 0, 499, 0, 0])
```

The `[251, 850, 0, 499]` part is the dataset's full i/j extent. The only
*selective* thing the call does is `k=[0, 0]` — pick the bottom layer.
Everything else is just a verbose way of saying "don't crop in i or j."
`describe_data()` even prints the literal VOI string for the user to copy,
which is helpful but underscores how mechanical the pattern is — the user is
copying boilerplate, not making a choice.

### Why this matters

The user's reaction: "it'd be nice if we could just specify we want k=0."
That's the right framing. The current API forces the user to know and recite
the full dataset extent for a slicing operation that has nothing to do with
i/j. It's also fragile: if the dataset extent changes between runs (different
file, different crop upstream), the hardcoded `[251, 850, 0, 499]` becomes
silently wrong rather than re-anchoring to "the full layer."

### Suggested fix

A keyword-style overload would be much nicer:

```python
ground = extract_grid(input=data, k=0)              # bottom layer, full i/j
top    = extract_grid(input=data, k=-1)             # top layer (last k)
slab   = extract_grid(input=data, k=(0, 5))         # bottom 6 layers
slice_ = extract_grid(input=data, i=100)            # one i-slice
```

Semantics: any axis the user omits defaults to the dataset's full extent on
that axis (read from the input's `GetExtent()`). Ranges accept ints or
`(lo, hi)` tuples; negative indices count from the end. `VOI=[...]` should
keep working for users who want explicit control.

This is purely an ergonomic wrapper — `vtkExtractGrid` itself stays the
same — but it eliminates the most common copy-from-`describe_data` ritual
and makes "the ground" or "the top slab" expressible as the geometric idea
they actually are.

---

## Common thread

Both papercuts are about **the gap between what the user means and what they
have to say**. In both cases the user has a clear high-level intent ("color
by this field across its actual range," "give me the bottom layer") and the
DSL forces them to either recite low-level VTK details or accept a silently
wrong default. PyVista/ParaView nailed the scalar-range one decades ago;
the extract-grid one is a smaller fish but the shape is the same.

The scalar-range one is more urgent because the docstring actively
mispromises smart defaults. Fixing the implementation to match the
docstring is a one-screen change in `filters.py`. The extract-grid one is
a nice-to-have that would tighten the most-typed line of every pipeline that
extracts a layer.
