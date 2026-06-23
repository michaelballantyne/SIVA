# Authoring Specs

A spec is the unit of collaboration between you and the human. It should read
like a short, honest description of *what to do with this dataset*.

## Shape of a good spec
```python
info = inspect("/abs/path/to/data.ext")
view = subset(info, variables=[...], dimensions={...})   # narrow; metadata only
render(load(view), cmap=...)                              # load, then render
```
For a first look at an unfamiliar dataset, skip the `subset` and just
`render(load(inspect(path)))` to see the whole thing, then narrow on the next turn.
- **One concern per line.** Inspect, (subset), load, then render/compress.
- **Absolute paths.** Specs are exec'd by a long-running server with its own cwd.
- **Stride large grids / subsample particles** in `subset` so a render stays
  responsive (see `vislang://instructions/rendering`).
- **Comment the *why*, not the obvious** — e.g. why a particular grid stride or
  error bound.

## Do
- For an unfamiliar or large file, call `estimate_render_cost(path)` first and
  apply its recommended `subset(...)` so the first overview stays responsive.
- Let `inspect` choose the adapter; trust the trust ladder.
- Put rendering choices (colormap, opacity, resolution) *in the spec* so they
  are visible and editable by the human.
- Raise / surface a clear error when something is ambiguous (e.g. particle
  position variables can't be detected) instead of guessing.

## Don't
- Don't import readers or hand-parse bytes in a spec — use the DSL verbs.
- Don't hardcode magic values you invented; if metadata is missing, say so.
- Don't render full resolution "just in case" — it's the most common cause of a
  sluggish or blank browser. Note `compress()` is for storage, not for cheapening
  a render: k3d still ships the full-res array, so striding/subsampling in
  `subset` is the lever.

## The iteration loop
Write the spec → `run_pipeline` → look at the result with the human → change one
line → re-run. Because the view updates in place, the conversation stays at the
spec level: "make it green" becomes `cmap='green'`; "show more detail" becomes a
finer `{'grid': N}` in `subset`. Keep the diff small and explain each change.
