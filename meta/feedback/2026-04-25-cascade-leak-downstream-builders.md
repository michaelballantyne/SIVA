# Cascade leak: downstream builders crash on failed upstream nodes

## Observation

When a build-phase error occurs at one node, the documented contract is
that descendants get marked `"Input node not built"` and skipped cleanly.
In practice this contract is only enforced in a narrow case, and most
real failures leak through downstream builders as Python `AttributeError`
or as silently-empty outputs that *look* like independent problems.

Concrete example from a real session
(`-Users-michaelballantyne-code-VisDemo-mcp-paper/55198298-...jsonl`):

The agent wrote:

```python
qcrit     = filter("vtkGradientFilter", input=velocity,
                   ComputeQCriterion=1,
                   ScalarArrays=[(0,0,0,0,"velocity")], ...)   # bad property
qpt       = cell_to_point(input=qcrit)
near_fire = extract_region(input=qpt, bounds=[...])
velocity2 = make_vector(input=near_fire, ...)
vort2     = curl(vector_field=velocity2, ...)
wz_near   = extract_component(input=vort2, ...)
cores     = contour(input=wz_near, ...)
```

The build report came back as:

```
node_3:    ERROR - VTK class 'vtkGradientFilter' has no method 'SetScalarArrays'
qpt:       vtkCellDataToPointData -> 0 pts, 0 cells WARNING: Filter produced empty output
node_5:    ERROR - 'NoneType' object has no attribute 'GetClassName'
velocity2: vtkArrayCalculator -> 0 pts, 0 cells WARNING: Filter produced empty output
node_8:    ERROR - 'NoneType' object has no attribute 'GetPointData'
node_9:    vtkCellDataToPointData -> 0 pts, 0 cells WARNING: Filter produced empty output
vort2:     vtkArrayCalculator -> 0 pts, 0 cells WARNING: Filter produced empty output
node_11:   ERROR - Input has no output data.
cores:     vtkContourFilter -> 0 pts, 0 cells WARNING: Filter produced empty output
```

One root failure (`node_3`'s bad property) surfaces as **four different
error idioms** down the chain:

1. `'NoneType' object has no attribute 'GetClassName'` — leaks from
   `_build_extract_region_node` (dsl.py:1652) inspecting the input
   dataset class without a None-check.
2. `'NoneType' object has no attribute 'GetPointData'` — same shape,
   different builder path.
3. `Input has no output data.` — VTK's own message, surfacing from
   somewhere that did get past the None-check but had an unconfigured
   filter.
4. Spurious `"Filter produced empty output"` warnings on intermediate
   nodes (`qpt`, `velocity2`, `vort2`, `cores`) that ran successfully on
   empty inputs and produced empty outputs. These look like independent
   failures with their own diagnostic value but the actual cause is
   purely upstream.

## Why this matters

For an LLM caller, four different error idioms for one structural
problem is much harder than one. The agent has to triage — is `qpt`'s
empty output the same problem as `node_3`, or did `qpt` independently
fail? Today there's no way to tell from the report, so the agent either
chases multiple ghosts or correctly guesses the root cause from the
position in the chain. Either way, the build-phase aggregation that
makes the design good (parallel branches accumulate) is undermined by
the chain not being modeled.

## What's actually happening in the code

`_build_generic_node` (dsl.py:1705) does the right thing: try/except,
record `{"error": str(e)}`, leave `vtk_objects[node_id]` unset. But:

- Downstream builders look up `vtk_objects[input_id]` and don't check
  whether the input's status carries an error before proceeding.
  `_build_extract_region_node` is the clearest case (dsl.py:1652) —
  it uses `input_alg` to dispatch to `vtkExtractVOI` vs `vtkExtractGrid`
  by calling `input_alg.GetOutput().GetClassName()`. When `input_alg` is
  not None but its output is, this AttributeErrors.
- For `vtkCellDataToPointData` and `vtkArrayCalculator`, generic build
  succeeds because VTK accepts an empty input and produces an empty
  output. So the per-node empty-output diagnostic fires (correctly, for
  that node viewed in isolation) but it's a false signal: the cause is
  upstream, not local.

## Proposal

Make the cascade contract uniform and enforce it at the per-node entry
point in `build_pipeline`:

1. Before dispatching to any node builder, check the input node's
   status. If it carries `"error"`, set this node's status to
   `{"error": "Input node not built", "upstream": <input_node_id>}`
   and skip the builder entirely. The `upstream` field lets the agent
   trace the chain in one read.
2. Also short-circuit if the input built successfully but produced
   `num_points == 0` *and* the failure is structural (i.e. the upstream
   carries a `"warning"` like "Filter produced empty output. Field 'X'
   not found"). For pure-data empties (range mismatches), let the
   downstream filter run as today — empty-on-empty propagation is the
   semantically correct behavior.
3. Audit the specialized builders (`_build_extract_region_node`,
   `_build_extract_component_node`) for None-checks on input. Their
   custom dispatch needs the same try/except wrapping the generic path
   already has.

## Why this is safe

- No new VTK work runs. We're only changing which nodes the loop
  decides to skip and how cleanly the skip is recorded.
- The parallel-branch accumulation property is preserved: independent
  branches (no shared upstream) still build and report independently.
- The structured `"upstream": ...` field is additive — existing
  consumers that just look for `"error"` keep working.

## Test cases that should turn green

Drawing from the session above, after the fix the same script should
produce:

```
node_3:    ERROR - VTK class 'vtkGradientFilter' has no method 'SetScalarArrays'
qpt:       SKIPPED - upstream node_3 failed
node_5:    SKIPPED - upstream node_3 failed
velocity2: SKIPPED - upstream node_3 failed
vort2:     SKIPPED - upstream node_3 failed
cores:     SKIPPED - upstream node_3 failed
```

One root error, every descendant clearly attributed to it, no Python
attribute errors leaking, no false-positive "empty output" warnings
that send the agent chasing the wrong cause.

## Related

- See `2026-04-25-unified-error-reporting.md` — that proposal is about
  collapsing the wrapper-vs-build-phase split. This one is orthogonal:
  it's about strengthening the existing build-phase channel so its
  contract holds end-to-end. Both can land independently.
