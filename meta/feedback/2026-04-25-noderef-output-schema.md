# NodeRef should carry an output schema, not just a graph identity

## Observation

The DSL's `NodeRef` is a graph-node abstraction: it knows its VTK class,
its properties, and its input edge. It does *not* know what arrays its
output will carry. Field-name validation happens only at build time,
inside VTK, after the whole pipeline has run.

For a human in a notebook this is fine — you run the pipeline, look at
the data, fix typos. For an LLM caller writing whole pipelines before
any execution, this means the most common typo class (mis-named field
references) is caught only after a multi-second pipeline build, and
the diagnostic comes back inside a long status payload alongside
unrelated nodes.

## Concrete example from a real session

In `-Users-michaelballantyne-code-VisDemo-mcp-paper/55198298-...jsonl`
the agent wrote:

```python
vort = curl(vector_field=velocity, result="vorticity", vector=True)
vmag = curl(vector_field=velocity, result="vmag",      vector=False)
wz   = extract_component(input=vmag, field="vorticity", component=2,
                         result_name="omega_z")
```

The mistake is semantic: `curl(..., vector=False)` produces a scalar
magnitude. Asking for `component=2` of it is meaningless. The agent
meant to read from `vort` (the 3-vector) but typed `vmag`, and also
typed `field="vorticity"` matching what `vort` produced, not what `vmag`
produced.

The build report eventually reported `Field 'vorticity' not found` with
the available array list, and the agent recovered in one turn — that
part of the design works. But the typo could have been caught at script
execution time, before any VTK ran.

## Why the syntax made the mistake easy

1. **`curl(vector=True/False)` is one call with two completely different
   output schemas.** Same name, same arg list. From the call site you
   can't see whether you got a 3-vector or a scalar. A user-facing
   `curl_vector(...)` and `curl_magnitude(...)` (or `vorticity()` /
   `vorticity_magnitude()`) would put the schema in the call name.

2. **Field references are bare strings, untethered to the node they
   describe.** `extract_component(input=vmag, field="vorticity")` has
   no static link between `vmag` and `"vorticity"`. If the wrapper
   accepted `field=vmag.vorticity` (or `vmag["vorticity"]`) and the
   `NodeRef` declared its output arrays, a typo would be a Python
   `AttributeError`/`KeyError` at script execution — same turn as the
   typo, no VTK round trip.

3. **Result-name collisions across nodes go unflagged.** `vort` and
   `vmag` both exist in scope and `vort` does have a `vorticity`
   array. The agent's mental model fused the two. A schema-aware
   wrapper would notice "`vmag`'s output doesn't have an array called
   'vorticity'" even if some other node in the script does.

4. **VTK intermediate array names leak through.** `curl(vector=True)`
   is internally `vtkCellDerivatives` -> `vtkCellDataToPointData` ->
   calculator. `vtkCellDerivatives` adds an array literally named
   `Vorticity` (capital V) regardless of the user's `result=` name.
   So nodes downstream of `curl` carry both the user-named array
   *and* the leaked VTK-internal one. Earlier in the same session the
   agent got tripped up by exactly this capitalization. The wrapper's
   opinionated layer should rename or strip the leak; right now it
   just lets it through.

## Proposal (tentative — has real tradeoffs)

This is the most ambitious item in this batch and it's in tension with
the broader design instinct that the system's strength is structured
*build-time* feedback rather than thicker wrappers. Adding a schema
layer is "doing more" in exactly the place the dsl-design-critique
argues for restraint, and the agent in the session above recovered in
one turn from the existing build report — i.e. the current design
*worked*, just not as fast as it could have. Worth weighing against
that before committing to the engineering.

The two **bonus items below** (split overloaded `curl`, drop the
leaked `Vorticity` array) are unambiguously good and stand on their
own without any schema machinery. The headline proposal here is
better treated as an open design question than a ready-to-build plan.

Tentative shape, if pursued: add a lightweight output-schema mechanism
to `NodeRef` and use it for script-time validation of field references:

- Each filter wrapper declares what arrays it adds, removes, or
  renames. For most wrappers this is a one-liner: `contour` produces
  the same arrays as its input (filtered geometry); `calculator` adds
  `ResultArrayName`; `extract_component` removes/transforms a vector
  into a scalar; `curl(vector=True)` adds two arrays (`result=` plus
  the leaked `Vorticity`); `curl(vector=False)` adds one scalar.
- `NodeRef.output_arrays` is computed at construction time by composing
  the input node's schema with the wrapper's declared transform.
- Field-accepting kwargs (`field=`, `ContourBy=`, `ThresholdBy=`,
  `color_by=`, ...) get validated against the declared schema during
  `interpret()`. Mismatch raises a clear Python error before any VTK
  runs, with the available arrays listed at the relevant node.
- The schema is best-effort, not enforced for arbitrary `filter()`
  calls or unknown VTK classes — those fall back to today's
  build-phase validation. The win is in the high-traffic wrappers.

## Variant fix: split overloaded wrappers

Independently of schemas, the `vector=True/False` flag on `curl()` is
a bad pattern. Two output shapes, one name. Splitting into two
wrappers (`curl_vector`, `curl_magnitude`) makes the schema visible at
the call site even without a typed `NodeRef`. Same applies to any
other wrapper where a bool flag toggles output cardinality.

## Bonus: clean up VTK intermediate-array leaks

`curl()` should drop or rename `Vorticity` so only the user-named
array survives. This is a small, local change in the curl wrapper's
build path, and it removes an entire class of capitalization confusion
the agents have hit at least twice.

## Why this is worth doing

- **Catches typos at the right time.** Script execution returns the
  error before the pipeline runs, in the same turn the agent typed
  the typo. No multi-second VTK build, no scanning a long status
  payload to find the warning.
- **Surfaces semantic confusion.** The "magnitude vs vector" mistake
  in the example would have surfaced as "vmag's output has no array
  'vorticity', available: ['vmag', 'velocity', 'Vorticity']" — which
  is exactly the diagnostic the agent needed.
- **Doesn't replace build-phase validation.** Both layers can coexist:
  schema validation catches what it can, VTK-level errors catch the
  rest. This is purely additive.

## Scope

The schema declaration is per-wrapper and small (one line of metadata
or a tiny method). The validation hook is one helper called from
field-accepting wrappers. The split of `curl` into two wrappers is a
half-hour refactor with backwards-compatibility deletable since the
project explicitly disclaims that.

## Related

- `2026-04-25-cascade-leak-downstream-builders.md` — orthogonal: that
  one is about build-phase robustness, this one is about pre-build
  validation.
- `2026-04-25-unified-error-reporting.md` — also orthogonal: that one
  is about consolidating the two error channels at build time. With
  schema-aware wrappers, more errors land in the script-execution
  channel and never reach build time, which makes the unification
  proposal smaller.
