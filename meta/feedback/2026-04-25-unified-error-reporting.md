# Unify validation error reporting through node_statuses

## Observation

VisLang has two error channels that behave very differently from the
caller's perspective:

1. **Wrapper-level validation** (e.g. `extract_region` raising `ValueError`
   if `bounds is None`, dsl.py:357; similar checks in `extract_component`,
   `line_probe`). These run during `interpret()` while the script is
   `exec`'d. They raise a normal Python exception, which halts script
   execution — any DSL calls textually after the bad one are never even
   recorded as nodes.

2. **Build-phase errors** (unknown VTK setter, wrong property type) and
   **empty-output diagnostics** (contour out of range, threshold range
   miss, stream tracer seeds below terrain) are caught per-node inside
   `_build_generic_node` / `_build_extract_region_node` /
   `_build_extract_component_node` and aggregated into `node_statuses`.
   The build loop continues, so independent branches all report.

From the agent's perspective there is no semantic difference between
"you forgot `bounds`" and "you forgot `ContourBy`" — both are missing
required arguments — but one halts the whole script with a traceback
and the other comes back as a structured warning alongside the
screenshot. The boundary between the two channels was drawn by
accident: it falls wherever a wrapper happened to write an explicit
`if x is None: raise` versus letting VTK complain later.

## Proposal

Collapse to a single error channel by deferring all validation into
the build phase.

- Wrappers never raise. Missing/invalid Python-level args become a
  recorded node with `node_statuses[id] = {"error": "extract_region
  requires bounds=[xmin, xmax, ...]"}`.
- The build loop's existing "Input node not built" check (dsl.py:1660,
  1690) already short-circuits descendants of a failed node. Extend it
  so a node whose status carries an `"error"` key also skips
  instantiation and propagates.
- Optionally include the upstream failing node id in the propagated
  marker so the agent can trace the chain in one read instead of
  scanning for the root cause.

## Why this is safe

The wrapper phase is pure bookkeeping — no VTK objects instantiated,
no `Update()` calls, no data touched. Running a few extra wrappers
after a bad one costs microseconds of Python. The expensive work
still gates on per-node status in the build loop, so total VTK work
is unchanged from today's "raise immediately" path.

## Why it's worth doing

- One error channel for the agent to read. Today an LLM caller has to
  handle both "tool returned a status dict with errors" and "tool
  raised a Python traceback," with no way to predict which.
- All independent errors surface in one turn, including Python-level
  ones. Today, a script with a missing-`bounds` early and a
  missing-`ContourBy` later only ever shows the first.
- Removes the implicit invariant that wrappers either validate eagerly
  or not at all. New wrappers can do whatever local checks make sense
  without changing the caller contract.

## Scope

Small. The eager Python checks are a short list (extract_region,
extract_component, line_probe, plus a handful of similar ones). Each
becomes a recorded error instead of a `raise`. The build loop change
is one branch added next to the existing "Input node not built" path.

## Related

- The existing class-specific empty-output diagnostics
  (filters.py:629–703) are the model for what good build-phase error
  messages look like. Wrapper-level validation errors should aim for
  the same level of specificity.
- See also feedback on agent-shaped error surfaces — this is the
  same theme: the audience is an LLM that gets one shot per turn to
  understand what went wrong.
