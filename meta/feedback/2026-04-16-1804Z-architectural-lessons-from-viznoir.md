# Architectural lessons from viznoir -- speculative

Date: 2026-04-16

**Status: speculative. Not human-agreed. Not a plan.** This entry is a
read on architectural patterns viznoir has and VisLang doesn't, written
to seed a conversation rather than guide implementation. Several of the
suggestions below would be significant refactors, some contradict
VisLang's current design choices, and at least one (the IR) is an open
research question that would need real prototyping before committing.
Treat this as opinionated framing for discussion, not a TODO list.

Third entry in today's viznoir-comparison thread, following:
- `2026-04-16-1746Z-security-hardening-from-viznoir.md` -- concrete
- `2026-04-16-1754Z-big-data-opportunity.md` -- partly concrete, partly
  speculative
- This one -- mostly speculative

Explicitly excluded: viznoir's feature set (cinematic rendering,
3-point lighting, compare tool, auto-postprocess, etc.). Those are
product decisions, not architectural lessons. This entry is about
*how viznoir is built*, not *what it does*.

---

## 1. Pipeline as data, not as code (the big one)

**Viznoir:** the pipeline is a Pydantic tree (`PipelineDefinition`).
`core/compiler.py` walks the tree and emits executable Python as a
string, which the runner executes. The user-facing artifact is
*structured data*.

**VisLang:** the pipeline is executable Python. The LLM writes builder
calls; `exec()` runs them. The user-facing artifact is *code*.

This divergence has ripple effects beyond the security considerations
already covered. Several things that are cheap in viznoir are awkward
or impossible in VisLang:

- **Inspection is semantic, not textual.** "Does this pipeline read
  `temperature`?" is a tree walk in viznoir, a parse-or-execute in
  VisLang.
- **Rewriting is tractable.** If we want to auto-insert a decimate
  step before render when a dataset is big, viznoir edits a tree;
  VisLang would have to regenerate Python source.
- **Diffs are meaningful.** `list_versions` / `restore_version`
  currently diff Python strings. In a tree-DSL they'd diff structured
  steps ("step 3: clip_box bounds changed").
- **Serialization is free.** Send the pipeline over the wire, cache
  it, hash it for memoization.

**Speculative proposal: an intermediate representation.**

VisLang shouldn't adopt a flat Pydantic schema wholesale -- that kills
the expressive DSL (see the security entry). But a middle path:

- Keep the DSL frontend: LLM writes `PipelineBuilder` calls as
  Python.
- Don't `exec()` directly. Instead, either
  (a) walk the AST and *interpret* it against a builder that records
  intent into a structured IR, or
  (b) keep `exec()` but have the builder methods record their own
  calls into an IR as a side effect.
- The IR is then what the renderer consumes, what gets diffed by
  versioning, what the big-data layer pattern-matches against to
  insert pushdown, what gets shipped to an HPC backend.

Option (a) dovetails with the AST-validator from the security entry
-- if you're already walking the code to validate it, walk it to
interpret it. Option (b) is cheaper to land but keeps the "effectful
builder" shape that `exec()` currently requires.

**Honest caveats:**

- This is a big shift. "Pipeline is Python code" is load-bearing for
  at least the `export_standalone` tool, which is valued by users.
- VisLang's `tracked_core` is *sort of* an IR already (DAG of
  tracked computations). It might already be the substrate, just not
  framed that way. Worth looking at whether tracked_core's DAG can
  *be* the pipeline IR rather than a parallel structure.
- Several downstream proposals in this entry (#2, #4, and parts of
  the big-data entry) depend on some form of IR existing. If the IR
  doesn't happen, those get harder.
- I have not prototyped this. It's plausible there's a subtle reason
  VisLang went code-not-data that I haven't understood. If there's a
  post-hoc rationale for the current design, it should be captured
  before any refactor.

---

## 2. Executor interface with multiple backends

**Viznoir:** `core/runner.py` is a `VTKRunner` interface with
`InProcessExecutor` and a Docker-based executor behind the same
contract. `core/worker.py` even picks between subprocess and
in-process based on a measured latency tradeoff (500ms -> 50ms).
Pipeline definitions don't know where they run.

**VisLang:** execution is "this Python process, this VTK instance."
The interactor, the renderer, and the DSL are all bolted to the same
process.

**Why it matters:** VISION.md mentions HPC execution as a future
direction. VisLang's reconciler / tracked-execution work is already
gesturing at "pipelines could run on different backends." But
"pipelines run here" is baked into VisLang's call graph, and
retrofitting a seam is much more expensive than putting it in with
only one backend.

**Speculative proposal:** define a minimal `PipelineExecutor` protocol
-- something like `execute(ir, context) -> result` -- and make the
current in-process path the first implementation. Even with only one
backend, the protocol forces clarity about what state crosses the
boundary (pipeline, session, cwd, env) vs. what stays local
(interactor handle, GPU context). That clarity is valuable even if
no second backend ever lands.

**Caveats:**

- The interactor goal complicates this. A backend that runs remotely
  can't hold an interactor window on the user's desktop. The seam
  probably has to be between "compute the geometry" and "render it,"
  not at the full pipeline boundary. That's a harder line to draw.
- Depends on the IR from #1. Without structured pipeline data,
  there's nothing to ship to an alternate backend.
- "Build abstractions for potential future backends" is the kind of
  advice that often ages badly. If no second backend is coming,
  skip.

---

## 3. Typed error hierarchy

**Viznoir:** `errors.py` defines `ViznoirError` (base),
`FileFormatError`, `FieldNotFoundError`, `EmptyOutputError`,
`RenderError`. Errors thrown from the engine layer carry specific
types that propagate up to the MCP tool responses.

**VisLang:** mostly relies on Python's stock exceptions and string
messages. Some custom types exist (I saw `VisLangPathError` suggested
in the security entry) but there's no systematic taxonomy.

**Why it matters:** the primary consumer of VisLang tool errors is an
LLM. "FieldNotFoundError: 'temperatur' -- did you mean 'temperature',
'temperatura', 'temp'?" is a signal Claude can act on. A bare
`KeyError` or stringified `RuntimeError` is not. The LLM's
self-correction loop is proportional to how much structure the error
carries.

**Speculative proposal:** a small taxonomy, maybe:

- `VisLangError` (base) -- everything the LLM might reasonably
  encounter
- `InputShapeError` subtree -- `FileNotFound`, `UnsupportedFormat`,
  `FieldMissing`, `TimestepOutOfRange`
- `SemanticError` subtree -- `EmptyResult`, `IsovalueOutOfRange`,
  `DatasetTooLarge`
- `ExecutionError` subtree -- `VTKSegfault`, `GPUUnavailable`,
  `RenderTimeout`
- User-error vs system-error as a cross-cutting attribute, so the
  MCP layer knows whether to surface the error to the LLM with
  recovery guidance or treat it as infrastructure.

**Caveats:**

- This is boring infrastructure work that's easy to under-invest in.
  Viznoir's five-class hierarchy is on the light side; a heavier
  taxonomy gets cumbersome. Start with the five most common error
  patterns observed in feedback, not a theoretical hierarchy.
- "Did you mean" suggestions require work beyond just the
  exception class. They're a payoff of the typed hierarchy, not
  the hierarchy itself.
- This would benefit from empirical grounding: what errors does
  the LLM actually hit in feedback sessions, and which ones does
  it fail to recover from? The taxonomy should be data-driven.

---

## 4. Tool-boundary typing

**Viznoir:** MCP tool arguments are Pydantic models with constrained
types. fastmcp generates JSON schemas from those models and passes
them to the LLM, so the tool description carries the type information
natively.

**VisLang:** most tool args are bare `str`, `float`, `int`. The LLM
gets type hints from the function signature but no richer structure.

Already covered in the security entry as a security win. Mentioning
here because it's also an architectural win independent of security:

- LLM gets better auto-generated tool descriptions.
- Conversion logic (relative path -> validated absolute, string
  colormap name -> enum) has a natural home.
- Versioning is structural, not ad-hoc.
- `get_dsl_reference` output quality scales with how much structure
  the signatures carry.

**Caveats:**

- Mostly mechanical. Less speculative than the rest of this entry.
- The cost is in the migration, not the pattern. 45 tools times
  "convert args to Pydantic" is real work.

---

## 5. Resource lifecycle discipline

A cluster of small viznoir patterns that look like boring
infrastructure but are the kind of thing you kick yourself for not
doing early:

- **Singleton VTK resources with explicit lifecycle.** Viznoir keeps
  one `_RENDER_WINDOW`, reuses it across renders, recreates every
  100 renders to cap GPU leak. VisLang's interactor + long-running
  session will accumulate leaks without similar discipline.
- **stdout channel isolation (`_protect_stdout`).** Dup fd 1,
  redirect to `/dev/null`, JSON-RPC on the preserved fd. VTK will
  write warnings and sometimes binary output to fd 1; those corrupt
  MCP frames. VisLang will hit this.
- **Env-var configuration with a documented table.** Viznoir's
  CLAUDE.md has nine env vars in a table with defaults and
  purposes. VisLang has env-var config but it's scattered. As we
  run in more environments (interactive local, offscreen CI, cloud,
  Docker), a canonical table is load-bearing documentation.
- **"Gotchas" callouts in CLAUDE.md.** Viznoir explicitly documents
  "Dual Registry Gotcha," "`vtkEGLRenderWindow()` causes SIGSEGV,"
  "fd 1 binary junk." This is institutional memory for future devs
  including future Claude. VisLang's CLAUDE.md leans toward "how to
  develop here" rather than "here are the landmines."

**Caveats:**

- Viznoir's singleton+recycle-every-100 may not transfer directly to
  an interactive interactor -- recreating the window every 100
  renders would be a disruptive UX event. The right shape for
  VisLang is probably "singleton, recycled on explicit session
  boundaries," not "recycled every N renders."
- stdout protection is easier to land than to diagnose the bugs it
  prevents. Land it before the first mysterious MCP-frame-corruption
  bug.
- None of these are speculative in the same way #1-#2 are. They're
  just deferred hygiene.

---

## What I'd start with, if anything

Ordered by confidence, highest first:

1. **#5 (lifecycle discipline)**: least speculative, least
   disruptive, pays for itself quickly. Land stdout protection and
   the env-var table now regardless of what else happens.
2. **#4 (Pydantic tool args)**: mechanical work, overlaps with the
   security hardening, ~45 tools to migrate.
3. **#3 (typed errors)**: medium speculative. Depends on observed
   LLM failure modes -- worth an empirical pass across recent
   feedback entries first.
4. **#2 (executor interface)**: speculative and premature without a
   concrete second backend in mind. Revisit when HPC execution has
   a champion.
5. **#1 (pipeline IR)**: most speculative. Research-grade. Would
   need a prototype in `experiments/` before any commitment. But
   it's the unlock that makes several other directions (big-data
   lazy sources, semantic versioning, rewrite-based optimization,
   alternate backends) tractable at once.

If #1 ever happens, it should probably start by asking whether
`tracked_core`'s DAG already *is* the IR we want. If yes, the work
is mostly re-framing. If no, the work is building a parallel
structure and possibly consolidating later.

---

## Things NOT to learn from viznoir

For completeness. These are architectural choices viznoir made that
VisLang should avoid:

- **Dual registry.** Viznoir's own CLAUDE.md flags this as a gotcha
  (parallel PascalCase and snake_case registries that must stay in
  sync). Don't replicate.
- **Pydantic-tree as the frontend DSL.** Discussed in the security
  entry. Kills VisLang's expressive-DSL goal. Use Pydantic at the
  *tool boundary*, not as the pipeline language.
- **Docker as primary isolation.** Discussed in the security entry.
  VisLang's native interactor goal makes Docker-on-macOS awkward;
  in-process hardening is the right posture.

---

## Meta: the overall framing

Viznoir and VisLang occupy different design points on purpose.
Viznoir is conservative, validated, and production-shaped; VisLang
is research, expressive, and prototype-shaped. The lessons above are
*not* "VisLang should become viznoir." They're specific disciplines
that are cheap to adopt even in a research codebase and that tend to
pay off once the project leaves the prototype phase.

The biggest of them (pipeline IR, executor interface) are the ones
that would change VisLang's identity, and those should be treated as
research questions with explicit prototype-first gates, not as
architectural to-do items. The smaller ones (lifecycle, typed
errors, tool-boundary Pydantic) are more like hygiene and can land
incrementally.

Whoever picks this up: treat it as a starting point for a
conversation with the human, not a mandate. At least two of the
proposals (especially #1) would benefit from a design reflection or
backlog-refinement pass before any implementation work starts.
