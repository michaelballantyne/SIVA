# VisLang — Pipeline Philosophy (read this first)

VisLang is a **deeply-embedded DSL** for reading, inspecting, loading,
compressing, and rendering scientific data. You (the LLM) collaborate with a
human by **writing and editing a small Python "spec"** that uses the DSL verbs;
the `run_pipeline(spec_path)` tool executes that spec. The spec is the shared
artifact — the human reads it, edits it, and discusses it with you. Keep specs
small, declarative, and legible.

## The DSL (available in a spec with no imports)
- `inspect(filepath) -> DatasetInfo` — metadata only, no bulk data
- `load(info, variables=None, dimensions=None) -> DatasetInfo` — populate arrays
- `download(remote_source, local_path) -> path`
- `compress(info, variables, error_bound) -> DatasetInfo`
- `render(info, cmap=None, opacity=None, ...)` — serve a browser viewer; prints its URL

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
