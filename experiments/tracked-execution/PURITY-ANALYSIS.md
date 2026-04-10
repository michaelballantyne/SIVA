# Purity Analysis: VTK/PyVista Statefulness Limits for Caching Correctness

**Date**: 2026-04-10  
**PyVista version**: 0.47.2  
**VTK version**: underlying VTK as shipped with PyVista 0.47.2  
**Python version**: 3.11  
**Tests**: `experiments/tracked-execution/tests/test_purity.py`

---

## Summary

PyVista is **mostly safe for content-addressed caching**, but has **three genuine
hazards** that the tracked-execution library must account for:

1. **`set_active_scalars` hidden state** — CRITICAL. Filters that don't receive
   an explicit `scalars=` argument use the mesh's `active_scalars_name` metadata.
   This is not captured in the content hash. Two pipeline runs with different
   active scalars produce different results from the same hash.

2. **VTK passthrough optimization** — MODERATE. When a threshold filter passes
   ALL points (zero points are removed), VTK skips allocation and reuses the
   source VTK array object. The filter output's data array shares memory with
   the source. Mutating the source after caching corrupts the cached result.

3. **Cache stores live references** — OPERATIONAL. The DAG cache stores the
   actual Python/VTK object, not a snapshot. Any code that bypasses the proxy
   layer (e.g., `object.__getattribute__(proxy, '_real')`) and mutates the
   object will corrupt all future cache hits for that entry.

The remaining behaviors investigated (pipeline laziness, filter output
independence, contour determinism, chained filter isolation) are **safe**.

---

## Per-Test Findings

### 1. Source mutation after filter (Tests 1–2)

**Behavior**: Calling `mesh.threshold(value)` is **eager**. The VTK pipeline
runs `Update()` immediately, and the output `UnstructuredGrid` is fully
materialized before the call returns. Subsequent mutation of `mesh["T"]` or
`mesh["T"][:] = new_array` does not affect the returned filtered object's
topology (n_points).

**Classification**: SAFE

**Why it matters**: The core caching assumption — that a stored filter result
is stable after computation — holds for the normal case.

**Caveat**: See Hazard 2 (passthrough) for the exception when ALL points pass.

---

### 2. VTK pipeline laziness (Tests 3–4)

**Behavior**: `mesh.threshold()` does NOT return a lazy pipeline view. The
result type is a concrete `UnstructuredGrid` (not a VTK pipeline object).
Changing `mesh["T"]` after calling `threshold()` does not change the result's
`n_points`. VTK's lazy pipeline model is hidden behind PyVista's filter methods.

**Classification**: SAFE

---

### 3. Filter output: copy vs view (Tests 5–7)

**Behavior**: This is **case-dependent**:

- **Partial pass** (subset of points satisfy the threshold): VTK allocates a
  new array for the filtered subset. `filtered["T"]` does NOT share memory
  with `mesh["T"]`. Mutation of the source does not affect the cached result.
  **SAFE**.

- **Full passthrough** (ALL points satisfy the threshold): VTK skips allocation
  and reuses the source VTK array object directly. `filtered["T"]` IS a view
  of `mesh["T"]` at the VTK level. Mutating `mesh["T"][:] = 0` after caching
  the threshold result will retroactively zero out `filtered["T"]`, corrupting
  the cached object.

**Classification**: HAZARDOUS (passthrough case), SAFE (partial case)

**Severity**: Moderate. The passthrough case (threshold that passes everything)
is unusual in practice but can occur when threshold values are outside the
data range or when debugging pipelines with permissive filters.

**Mitigation option**: Call `filtered.copy(deep=True)` before storing in the
cache (expensive — allocates full copy for all results).

---

### 4. Array access: view into VTK storage (Tests 8–10)

**Behavior**: `mesh["T"]` returns a `pyvista_ndarray` that is a **view** into
VTK's internal memory buffer (`flags.owndata=False`, `base is not None`). This
means:

- Mutating `arr = mesh["T"]; arr[0] = X` also mutates `mesh["T"][0]`.
- Two calls to `mesh["T"]` return different Python objects that share the same
  underlying VTK buffer.
- `mesh.points` is similarly a view.

**Classification**: HAZARDOUS (for cache safety), expected VTK behavior

**Why it matters**: Any code that:
1. Gets a raw array via `object.__getattribute__(proxy, '_real')["T"]`
2. Writes to it

will silently corrupt the cached mesh object. Subsequent cache hits will see
the corrupted data.

The proxy layer prevents `proxy["T"][:] = X` (blocks `__setitem__` on the
array-proxy), but the underlying raw view can still be accessed and mutated
by code that bypasses the proxy system.

**Mitigation**: Document as a known limitation. The proxy cannot fully protect
against callers that obtain the raw `_real` reference.

---

### 5. Multiple filter calls — independent outputs (Tests 11–13)

**Behavior**: Two calls to `mesh.threshold(value=300)` with identical arguments
return **different Python objects** with **independent data**. Mutating `t1`
does not affect `t2`. The VTK filter creates a fresh output object for each
`Update()` call.

**Classification**: SAFE

**Why it matters**: The cache can safely store results from multiple filter
calls as distinct entries with different hash keys.

---

### 6. Contour/isosurface determinism (Tests 14–15)

**Behavior**: Two successive calls to `mesh.contour(isosurfaces=[500],
scalars="T")` with the same input produce **identical point counts** and
**identical point coordinates** (`np.allclose` passes). The VTK marching cubes
algorithm is deterministic for a given input.

**Classification**: SAFE

**Why it matters**: Cache hits will serve geometrically identical results to
what a fresh computation would produce.

---

### 7. Chained filters and intermediate mutation (Tests 16–17)

**Behavior**: In the chain `mesh -> threshold -> extract_surface`:

- Zeroing `threshed.points` after computing `surfaced` does NOT change
  `surfaced.n_points` or `surfaced.points`.
- Mutating `threshed["T"]` does NOT affect `surfaced["T"]`.

Each filter stage produces an independent output dataset. Downstream results
are isolated from mutations to their upstream intermediate.

**Classification**: SAFE

**Why it matters**: The cache can safely evict intermediate results (like
`threshed`) without corrupting already-computed downstream results (like
`surfaced`). This validates the GC/eviction strategy in the DAG.

---

### 8. Cache sabotage via live references (Tests 18–19)

**Behavior**: The DAG `cache` dict stores live Python/VTK object references.
Obtaining the raw cached object (e.g., `dag.cache[filtered_hash]`) and
mutating its arrays (`raw["T"][:] = 99999`) permanently corrupts the cached
entry. Subsequent cache hits serve the mutated data (`T[0] == 99999` instead
of the original value).

`n_points` is structural/topological and is NOT affected by array value
corruption — only data arrays can be sabotaged this way.

**Classification**: HAZARDOUS

**Severity**: Operational. This hazard requires code outside the proxy system
to deliberately (or accidentally) obtain a raw reference and mutate it. In
normal pipeline execution via `execute_pipeline()`, this does not happen because
user code only sees `TrackedProxy` objects. But:

- VTK callbacks that receive the raw object could mutate it
- `inspect_exec` users who unwrap proxies could mutate data
- numpy ufuncs called on unwrapped arrays can write back to the buffer

**Mitigation options** (in order of cost):
1. **Document as limitation**: Callers who bypass the proxy system void the
   caching guarantee.
2. **Copy on store**: `dag.cache[op_hash] = result.copy()` — expensive but
   fully safe. Not recommended for large meshes.
3. **Read-only memoryview**: Set VTK arrays to read-only after caching.
   PyVista does not expose a clean API for this.

---

### 9. `set_active_scalars` hidden state (Tests 20–22)

**Behavior**: When `scalars=` is omitted from `mesh.threshold(value)`, PyVista
uses `mesh.active_scalars_name` to select which field to threshold on. This
is **mesh metadata**, not part of the explicit call arguments.

The content hash in `dispatch()` is computed from:
- the proxy's hash (which identifies the cached mesh object)
- the method name (`"threshold"`)
- explicit positional args (`value=500`)
- explicit keyword args (none if `scalars=` is omitted)

The `active_scalars_name` is NOT included. Therefore:

- Run 1: `mesh.set_active_scalars("T")`, then `proxy.threshold(value=500)`
  → computes 600 points (T values 500–999 pass), hash = H1
- Run 2: `mesh.set_active_scalars("P")` (P is all 999), same call
  → cache hit on H1 → returns 600 points
  → WRONG: with P active, all 1000 points should pass

The cache serves the Run 1 result for the Run 2 query, even though the hidden
state changed. This is a **silent correctness bug**.

**Classification**: HAZARDOUS — silent wrong results

**Severity**: CRITICAL. This is the only hazard that can cause the cache to
silently return wrong results without any visible error. The passthrough hazard
(#3) corrupts data values, but this hazard corrupts topology (wrong n_points).
It is also difficult to detect because the cache hit looks legitimate.

**Mitigation options**:
1. **Document as user contract**: "Always pass explicit `scalars=` to
   `threshold()`, `contour()`, `clip_scalar()`, and similar filters."
   Add a note to the whitelist or tool description.
2. **Include `active_scalars_name` in the hash**: Before computing the op hash
   in `dispatch()`, if the method is in a known set of "scalar-sensitive"
   methods, append `real_obj.active_scalars_name` to the hash key. This
   captures the hidden state.
3. **Block omission in the whitelist**: For `threshold()`, require that the
   `scalars` kwarg is present; raise `AttributeError` if it is absent.
   This is safe but breaks pipelines that legitimately omit `scalars=`.

**Recommendation**: Option 2 (include `active_scalars_name` in hash for
scalar-sensitive methods) with Option 1 (documentation) as belt-and-suspenders.
Option 3 is too restrictive.

---

## Hazard Classification Summary

| # | Hazard | Classification | Severity |
|---|--------|---------------|----------|
| 3a | VTK passthrough: filter output shares VTK array with source (all-pass case) | HAZARDOUS | Moderate |
| 4 | `mesh["T"]` returns a mutable view into VTK storage | HAZARDOUS | Low–Moderate |
| 8 | Cache stores live references; direct mutation corrupts all future hits | HAZARDOUS | Operational |
| 9 | `set_active_scalars` hidden state not captured in content hash | HAZARDOUS | CRITICAL |

| # | Behavior | Classification |
|---|----------|---------------|
| 1 | Source mutation after filter does not affect n_points | SAFE |
| 2 | Filters are eager (not lazy VTK pipeline views) | SAFE |
| 3b | Partial-pass filter output is an independent copy | SAFE |
| 5 | Two calls with same args return independent objects | SAFE |
| 6 | Contour is deterministic | SAFE |
| 7 | Chained filter outputs are isolated from intermediate mutation | SAFE |

---

## Recommendations

### Immediate (high impact, low cost)

**1. Add `active_scalars_name` to the hash for scalar-sensitive methods.**

In `dispatch()`, before computing `op_hash`, check whether the method is in a
set of "active-scalars-sensitive" methods (e.g., `threshold`, `contour`,
`clip_scalar`, `threshold_percent`, `extract_values`, `warp_by_scalar`). If so,
append `getattr(real_obj, 'active_scalars_name', None)` to the hash tuple.

This eliminates the most critical hazard with minimal performance cost.

**2. Document the proxy bypass limitation.**

Add a note to the module docstring and the `DAG` class docstring: "The cache
stores live object references. Bypassing the TrackedProxy layer and mutating
a raw object will corrupt future cache hits."

### Medium-term (mitigable hazards)

**3. Handle VTK passthrough arrays.**

The passthrough case (all points pass, VTK reuses source array) is hard to
detect without inspecting the VTK object graph. The simplest mitigation is to
call `result.copy(deep=False)` before caching — this forces PyVista to create
a new mesh object, though VTK may still share individual array buffers. A
fully safe version requires `result.copy(deep=True)`, which is expensive.

Consider a lightweight check: after computing the filter, compare
`id(result.GetPointData().GetArray(name))` against the source to detect sharing,
and deep-copy only if sharing is detected.

**4. Consider making proxied arrays read-only.**

When `dispatch()` returns a TrackedProxy wrapping a numpy array, the underlying
`_real` pyvista_ndarray is mutable. Setting `arr.flags.writeable = False` before
caching would prevent mutation through any reference, including raw references
obtained via `object.__getattribute__`. This has no performance cost and
eliminates Hazards 4 and 8 for the data-array case (not topology/points).
Note: VTK may reset writability flags on its arrays; this needs testing.

### Long-term (documentation and testing)

**5. Add a "safe filter call" linting pass.**

Before executing pipeline code, check that calls to scalar-sensitive methods
always include an explicit `scalars=` kwarg. This could be done as a static
analysis step over the AST of the pipeline code, or as a runtime check in
`dispatch()`.

**6. Expand purity tests to other filters.**

The current tests focus on `threshold()`, `contour()`, and `extract_surface()`.
Similar analysis should be applied to `clip_scalar()`, `warp_by_scalar()`,
`compute_gradient()`, and array-returning operations like `cell_centers()`.
