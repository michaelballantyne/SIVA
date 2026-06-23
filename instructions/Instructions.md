# VisLang — Pipeline Philosophy (read this first)

VisLang is a **deeply-embedded DSL** for reading, inspecting, loading,
compressing, and rendering scientific data. You (the LLM) collaborate with a
human by **writing and editing a small Python "spec"** that uses the DSL verbs;
the `run_pipeline(spec_path)` tool executes that spec. The spec is the shared
artifact — the human reads it, edits it, and discusses it with you. Keep specs
small, declarative, and legible.

## The DSL (available in a spec with no imports)
- `inspect(filepath) -> DatasetInfo` — metadata only, no bulk data
- `subset(info, variables=None, dimensions=None) -> DatasetInfo` — narrow (remove
  fields / record a slice policy); metadata only, no bulk data
- `load(info) -> DatasetInfo` — materialize what `info` describes (call before
  render/compress, which act on already-loaded data)
- `download(remote_source, local_path) -> path`
- `compress(info, variables, error_bound) -> DatasetInfo`
- `render(info, cmap=None, opacity=None, ...)` — serve a browser viewer; prints its URL

Narrowing is `subset`'s job alone; `load` materializes whatever the info
describes. `render(load(inspect(path)))` shows the whole dataset;
`render(load(subset(info, variables=[...])))` reads only those fields.

## MCP tools (called directly, not in a spec)
- `run_pipeline(spec_path)` — execute a spec.
- `estimate_render_cost(filepath, budget_mb=64)` — predict the browser payload +
  disk-read cost and get a recommended `subset(...)`, reading only metadata.

## Working with a new dataset — overview first
When the human says "render /path/file" without naming fields, they want to see
the **whole thing first**, then react and refine ("now just density", "color by
mass"). So:
1. `estimate_render_cost(path)` before rendering an unfamiliar or large file.
2. If it fits the budget, `render(load(inspect(path)))`. If it's heavy, apply the
   recommended slice: `render(load(subset(inspect(path), dimensions=<recommended>)))`,
   and tell the human what you strided and why.
3. Let them refine on the next turn — *that's* when `subset(..., variables=[...])`
   narrows fields.

The lever for a cheap overview is **striding/subsampling** (`subset` dimensions),
never `compress()` — render ships the full-resolution array to the browser
regardless of compression. On read-full formats (GenericIO), subsampling trims
the browser payload + memory but not the disk read.

Full signatures and examples: resource `vislang://instructions/dsl-reference`.

## Non-negotiable principles
1. **Soundness over guessing.** Never hand-parse raw bytes, and never trust
   generated code on faith. The LLM *proposes* an artifact (a declarative
   binding or trusted-library glue); a deterministic, hand-written check
   verifies it against the file's own metadata; only verified artifacts are
   used, and they are frozen so the runtime path has no LLM. Lean toward formal
   soundness, away from "the LLM might do it right or might not."
   → `vislang://instructions/soundness`
2. **Use trusted readers, in tiers.** Installed library reader → a verified,
   frozen, LLM-generated adapter → (headerless raw bytes) only via a
   size-checked filename convention. Unknown formats raise; they do not guess.
   → `vislang://instructions/adapters`
3. **Rendering is headless.** The compute nodes have no usable GPU/X. Volumes
   render with k3d (WebGL in the browser); the look — colormap, opacity, grid
   stride — is set *in the spec*, not hardcoded.
   → `vislang://instructions/rendering`
4. **Write minimal specs.** Write to file called "spec.py", create it if it doesn't exist. 
   One concern per line. Stride large grids so  the
   browser stays responsive. Raise a clear error instead of falling back to a
   guess. `DatasetInfo` is the format boundary — once `inspect` fills it,
   everything downstream is format-blind.
   → `vislang://instructions/authoring-specs`

## When unsure
Read the matching `vislang://instructions/*` resource before acting (the full
list is at `vislang://instructions`). Where the project is headed (the query DSL
and predicate pushdown) is in `vislang://instructions/roadmap`.
