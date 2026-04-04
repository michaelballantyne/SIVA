# Threats to Validity

Critical examination of where the VisLang design might fail to work, fail to
improve on alternatives, or fail to be novel.

## The DSL might hurt more than help

The LLM already knows VTK Python from training data. The DSL is a new
language with less training coverage. vtk-prompt shows that LLMs can write
raw VTK Python with RAG + error feedback. If the lesson from the Idris
compiler feedback paper is "good feedback matters more than language
familiarity," then maybe we should provide good feedback on raw VTK code
rather than inventing a DSL.

The DSL adds indirection -- the LLM has to map its VTK knowledge through an
unfamiliar layer. This could cause more errors than it prevents. The
ablation study should test this directly: same feedback tools, but raw VTK
code generation vs DSL spec writing.

**Counter-argument:** the DSL is simpler than raw VTK (no mapper/actor
boilerplate, no pipeline wiring), so there's less to get wrong. And the
declarative property (describe desired state, not steps) reduces the
reasoning burden. But this needs empirical validation.

## Reconciliation might not matter

If rebuilding the full pipeline takes <2 seconds (plausible for many
visualizations -- the expensive part is filter execution, not object
creation), incremental update provides no perceptible benefit over
tear-down/rebuild. Phase 1 (rebuild from scratch each time, let VTK's own
demand-driven pipeline handle caching) might be "good enough" forever.

The reconciler is the most complex component in the design. If it provides
negligible speedup in practice, Phases 2-3 are wasted effort.

**Counter-argument:** reconciliation matters for large datasets where reader
initialization is expensive, and for maintaining camera/interaction state
that would be lost on rebuild. But these might be solvable with simpler
mechanisms (cache the reader, save/restore camera).

## The enriched feedback might overwhelm

The reconciliation report includes output stats, array ranges, valid
downstream filters, show options -- potentially hundreds of tokens per node.
In a 10-node pipeline, that's a wall of text. The Idris paper found smaller
models are "more sensitive to code that appears near the end of the context
window." More feedback isn't always better; noise can drown signal.

A simpler system with just errors + screenshots might outperform the
enriched version. The ablation study should test feedback richness levels
to find the sweet spot.

**Counter-argument:** the LLM can ignore irrelevant parts of the report. And
the "next options" guidance prevents separate query round-trips. But token
budget is real, and relevance filtering might be needed.

## Starlark might be over-engineering safety

The threat model is mild. The LLM is the only code author, the user can
observe every spec submission in the terminal, and the VTK process doesn't
have access to secrets or sensitive systems. A restricted `exec()` namespace
(only builder functions available, no imports) might be "safe enough."

Starlark adds: a compiled Rust dependency, a fork to maintain (for
`names()`), restrictions that might frustrate legitimate use (no `while`, no
recursion, no classes), and an unfamiliar execution environment to debug.

**Counter-argument:** auto-approved MCP tools mean the user does NOT see
each spec before execution. And defense in depth is good practice. But the
marginal security benefit vs the engineering cost is debatable.

## "Any VTK filter" generality is likely a mirage

In practice, each filter has quirks needing special handling:
- `vtkStreamTracer` needs a seed source object, not just parameters
- `vtkGlyph3D` needs a glyph source connection
- `vtkArrayCalculator` needs `AddScalarArrayName` calls
- `vtkCellDerivatives` has enum modes set via `SetVectorModeTo*` methods

The ParaView XML metadata helps but doesn't eliminate special cases. We'll
likely accumulate workarounds until the "generic" story is mostly fiction
with a long tail of per-filter handling. At that point the advantage over
hand-written tool wrappers (like paraview-mcp) is organizational, not
fundamental.

**Counter-argument:** even partial generality (80% of filters work via
Set-property convention, 20% need special handling) is better than 100%
hand-written wrappers. And the XML metadata covers many of the special
cases formally.

## Variable-name-as-identity is fragile

If the LLM renames `fire` to `flame` between versions, the reconciler sees:
delete `fire`, create `flame` -- full teardown/rebuild of that branch. LLMs
rename things casually during iteration. List comprehensions produce nodes
identified by index, so reordering causes unnecessary churn.

The reconciler's effectiveness depends on name stability that the LLM might
not reliably provide. This could make reconciliation less effective than the
design suggests.

**Counter-argument:** the CLAUDE.md can instruct the LLM to keep names
stable. And even with occasional renames, reconciliation still helps for all
the unchanged nodes. But it's a fragile property to depend on.

## The user might not care about the spec

We claim the pipeline spec is a readable, versionable artifact that the user
learns from and eventually co-authors. But domain scientists often want
pictures, not code. They might never look at the spec file. The "shared
artifact" story might be wishful thinking.

If the user only interacts via natural language and the render window, the
spec is just an internal representation -- and a JSON blob would serve that
purpose without the overhead of a readable DSL.

**Counter-argument:** reproducibility and transparency matter for scientific
publications. And some users WILL engage with the spec. But we shouldn't
assume all will.

## Novelty is thin

- Terraform does declarative-spec-with-reconciliation for infrastructure
- React does UI tree reconciliation
- The Gamma formalizes live incremental evaluation for data exploration
- ChatLSP does type-directed LLM context
- vtk-prompt does LLM-generated VTK code with RAG

We are combining known ideas in a new domain. A reviewer could reasonably
say "this is Terraform for VTK with an MCP wrapper" and not be entirely
wrong. The contribution is the combination and the domain application, not
any individual mechanism.

**Counter-argument:** the combination IS the contribution. No existing
system combines declarative specs, reconciliation, runtime semantic feedback,
version history, user interaction capture, and cost estimation into a
coherent LLM collaboration environment. And the empirical evaluation
(ablation study) would demonstrate that the combination matters. But we
should be honest that the individual pieces are not new.

## LLMs improve fast

By the time we build and publish this, LLMs might write correct VTK Python
on the first try with just a screenshot for feedback. The DSL abstraction
becomes unnecessary overhead. The reconciler solves a problem that no longer
exists because the LLM gets it right the first time.

**Counter-argument:** even with perfect code generation, data introspection
tools (histograms, spatial extent) and interactive iteration (user marking
points) remain valuable. And large datasets will always be expensive enough
to benefit from incremental update. But the DSL and reconciler specifically
might become less important relative to better models.

## The evaluation is hard to make convincing

Ablation studies without user studies are suggestive but not conclusive.
Comparing against one-shot code generation (the weakest baseline) is easy to
win. The interesting comparisons are harder:

- vs a skilled ParaView user (different modality entirely)
- vs vtk-prompt with the same RAG corpus (isolates DSL vs raw code)
- vs an LLM writing raw VTK with the same feedback tools (isolates the
  declarative property from the feedback)

Small differences in these comparisons would undermine the claims. And
visualization quality is subjective -- automated metrics (image similarity)
don't capture whether a visualization is scientifically meaningful.

**Counter-argument:** the ablation design progressively adds features,
showing marginal contribution of each. And SciVisAgentBench provides
standardized tasks with expert evaluation criteria. But we should expect
modest rather than dramatic improvements on the harder comparisons.

## Cost estimation might actively mislead

Big-O models calibrated on one machine with one dataset could be wildly
wrong for different data or hardware. If the LLM trusts an estimate saying
"2s" that actually takes 60s, or avoids a useful operation because the
estimate says "30s" when it would take 0.5s, the estimates cause worse
decisions than having no estimates at all.

**Counter-argument:** even order-of-magnitude estimates prevent the worst
failures (volume rendering 18M cells). And the feedback loop (estimated vs
actual time in reconciliation reports) enables calibration over time. But
overconfident estimates are dangerous.

## Multi-resolution preview could mislead

If the LLM iterates toward a spec that looks good on a 1/200th subsampled
preview but fails at full resolution (features disappear, topology changes,
artifacts emerge), the preview iterations were wasted or worse -- they
converged on wrong parameters. This could be worse than just running at
full resolution from the start.

**Counter-argument:** the framework can warn about resolution-dependent
features and validate at full resolution before finalizing. But the failure
mode is real and subtle.
