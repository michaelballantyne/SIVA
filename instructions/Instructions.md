# VisLang — Pipeline Philosophy (read this first)

VisLang is a **declarative DSL** for reading, inspecting, narrowing, compressing,
and rendering scientific data. You (the LLM) collaborate with a human by
**writing and editing a small Python "spec"** built from DSL *forms*; the
`run_pipeline(spec_path)` tool executes it. The spec is the shared artifact — the
human reads it, edits it, and discusses it with you. Keep specs small and legible.

## The model: forms describe a goal; the interpreter decides how
A form does **not** run anything — it builds a node. The chain of nodes is an
AST. Only a **sink** (`render` or `save`) triggers execution. When you run the
spec, the interpreter:
1. inspects the source for its schema,
2. **static-checks** your request against that schema *before any bulk read*
   (does the axis/variable exist? is the range in bounds?),
3. **fuses** the structural narrowing into one read (crop + stride + projection
   pushed down together; value cuts apply right after, in written order), and
4. materializes and runs the sink.

So **form order is the promise; how it's lowered is the interpreter's choice.** A
spec with no sink is *dry-run*: you get the inferred plan, nothing is read.

## Write the spec to `spec.py` — one file, edited in place
Every spec goes in a single file named **`spec.py`**: create it if missing,
overwrite otherwise, then `run_pipeline("spec.py")`. Do **NOT** make a new or
descriptively-named file per request (no `render_heptane.py`, no `spec_v2.py`).
The spec is a shared scratch artifact the human watches: change a line, re-run, look.

## The forms (available in a spec with no imports)
Each takes a node and returns a node, so they chain. `source` starts a chain;
`render`/`save` end it.
- `source(uri, positions=None)` — the dataset. `uri` is a local path to one file
  (globs are rejected) or remote (`ssh://host/path` or `user@host:/path`,
  fetched locally).
- `fields(node, keep)` — keep only these variables.
- `region(node, x=(a,b), y=(c,d), …)` — crop. Grids: index ranges `[a:b]` per
  axis. Point data: a world-coordinate bounding box on the coordinate variables.
- `subsample(node, f)` or `subsample(node, x=…, y=…, z=…)` — reduce resolution.
  Int = stride (keep every f-th); float in (0,1] = fraction. Per-axis is for grids.
- `threshold(node, "var > value")` — keep elements where the predicate holds
  (point data: drop rows; grids: NaN-mask failing voxels). Order next to
  `subsample` is honored: threshold-then-subsample samples the survivors.
- `compress(node, variables, error_bound[, mode])` — error-bounded compression.
- `save(node, path)` — **sink**: write the result to disk.
- `render(node, cmap=None, opacity=None)` — **sink**: serve the browser viewer; prints its URL.

```python
# overview, strided for the browser, in green:
render(subsample(source("/abs/path/heptane_302x302x302_uint8.raw"), 2), cmap="green")
```

## MCP tools (called directly, not written in a spec)
- `run_pipeline(spec_path)` — execute the spec.
- `inspect(filepath, positions=None)` — read a file's schema (variables,
  dimensions), metadata only. **Use this to write a spec** — you need to know what
  fields/axes exist before you `region`/`fields`/`threshold` them. (It's the same
  engine behind the `source()` form.)
- `estimate_render_cost(filepath)` — predict the browser payload + disk-read cost
  and get a recommended narrowing, reading only metadata.

`subset`, `load`, `download`, `establish_connection` are **hidden physical ops** —
the interpreter emits them; you never write them in a spec.

## Working with a new dataset — overview first
When the human says "render /path/file" without naming fields, they want the
**whole thing first**, then refine. So:
1. `inspect(path)` (and `estimate_render_cost(path)` if it's large/unfamiliar).
2. If it fits, `render(source(path))`. If heavy, `render(subsample(source(path), N))`
   and tell the human what you strided and why.
3. Let them refine next turn — *that's* when `fields`, `region`, or `threshold` narrow.

The lever for a cheap overview is **`subsample` / `region`**, never `compress` —
render ships the full-resolution array to the browser regardless of compression.

Full signatures and the staged (not-yet-wired) forms: `vislang://instructions/dsl-reference`.

## Non-negotiable principles
1. **Soundness over guessing.** Never hand-parse raw bytes; never trust generated
   code on faith. The LLM *proposes* a verifiable artifact; a deterministic check
   validates it; only verified, frozen artifacts run. The same spirit drives the
   declarative DSL: the static check verifies the *query* against the schema
   before any read. → `vislang://instructions/soundness`
2. **Use trusted readers, in tiers.** Installed library → verified frozen
   LLM-generated adapter → (headerless raw) only via a size-checked filename
   convention. Unknown formats raise. → `vislang://instructions/adapters`
3. **Rendering is headless.** No usable GPU/X on the compute nodes; volumes render
   with k3d (WebGL); the look is set *in the spec*. → `vislang://instructions/rendering`
4. **Write minimal specs** to the single `spec.py`. One concern per line. Narrow
   large grids so the browser stays responsive. `DatasetInfo` is the format
   boundary — once `inspect` fills it, everything downstream is format-blind.
   → `vislang://instructions/authoring-specs`

## When unsure
Read the matching `vislang://instructions/*` resource before acting (index at
`vislang://instructions`). Where the project is headed is in
`vislang://instructions/roadmap`.
