# Diagnostic-spine cluster: simplicity / DRY review

Reviewing commits `5cabe99..6d8709d` (cascade-skip, wrapper validation,
property-typo, empty-output hints) for duplication, parallel concepts,
and abstractions that emerged but aren't crystallized.

---

## 1. Field-range computation — partial duplication [ACT]

`queries.py` already has the pattern "look up field in point data, fall
back to cell data, return `arr.GetRange()`": see `_get_scalar_array`
(`queries.py:978`, returns `(np_array, location, arr)`) and the
near-identical lookups at `queries.py:253`, `:307`, `:358`, `:593`, `:985`.

Pillar 4's `_format_field_range_hint` (`filters.py:580-624`) reimplements
the same point-data-then-cell-data lookup at lines 596-599. Pillars 1/2/3
also do it inline (`filters.py:285-298` in `extract_component`,
`:840-862` in the contour/threshold branches).

**Concrete diff sketch:** add a small helper to `queries.py` (or a new
`vislang/_vtk_introspect.py`):

```python
def find_field_array(data, field_name):
    """Return (vtk_array, location) where location is "point" or "cell", or (None, None)."""
    arr = data.GetPointData().GetArray(field_name)
    if arr is not None: return arr, "point"
    arr = data.GetCellData().GetArray(field_name)
    if arr is not None: return arr, "cell"
    return None, None
```

Then `_format_field_range_hint:596-599`, `extract_component:285-289`,
the threshold branch at `filters.py:860-862`, and `_get_scalar_array`
all delegate. This is the single highest-ROI consolidation in the cluster.

## 2. VTK output extraction — inconsistent [ACT]

Pillar 4 introduced `_get_algorithm_output` (`filters.py:626-636`) to
handle both `GetOutput()` and `GetOutputDataObject(0)`. Good. But:

- `_volume_prepare_data` (`filters.py:1162-1168`) duplicates the same
  pattern inline rather than calling the new helper.
- `_validate_field_names` calls `_get_output_array_names` which itself
  calls `Update()` + `GetOutput()` (`filters.py:482-502`) — a third path.
- `_create_volume`'s upstream-hint code (`filters.py:1185-1188`) reaches
  for `GetInput()` which is yet another shape.

**Diff:** route all three through `_get_algorithm_output` and add a sister
`_get_algorithm_input(alg)` that returns the upstream's output dataset.
Keeps the GetOutput/GetOutputDataObject quirk in exactly one place.

## 3. Error message style — drift across pillars [MAYBE]

Comparing the error/warning strings:

- Pillar 2 (wrapper): `"extract_region: missing required 'bounds' argument; expected [...]"` — **lowercase verb, semicolon, expected form**.
- Pillar 3 (typo): `"unknown property 'X' on vtkContourFilter\nsimilar: ...\nvalid: [...]"` — **multi-line, no class-name colon prefix**.
- Pillar 4 (empty): `"Filter produced empty output. 'temp' range is [0, 100] but your ClipValue was 200 (outside range)"` — **sentence-style, period-joined fragments**.
- Pillar 1 (cascade): no message; structured `{"status": "skipped", "upstream": N}`.

These are reasonable in isolation but don't share a convention.
Programmatic consumers (an LLM agent reading `run_pipeline` output) would
benefit from a shared prefix like `"<wrapper_name>: <reason>"`. Worth a
short style note in a doc, not necessarily a refactor.

## 4. Status dict shape — schema drift [ACT]

Status entries today:

- Success: `{"class": ..., "num_points": ..., ...}`
- Failure: `{"error": str}`  (pillars 2/3)
- Cascade: `{"status": "skipped", "upstream": N, "class": ...}`  (pillar 1)
- Empty:   success dict + `"warning": str`  (pillar 4)

Notable: failure has no `"status"` key, but cascade does — `"error" in s`
vs `s.get("status") == "skipped"` are checked at different sites
(`dsl.py:2104`, `test_cascade_skip.py` lines 49-52). Also, the `"error"`
and `"warning"` strings are unstructured prose; agents must regex them.

**Diff sketch:** unify under a `"status"` key with values
`"ok" | "error" | "skipped" | "warning"` and put structured data
alongside (e.g. `{"status": "warning", "kind": "field_out_of_range",
"field": "T", "range": [0, 100], "value": 200, "message": "..."}`). The
prose stays for humans; the kind/field/range/value enables clean
programmatic chaining (e.g. an agent automatically retrying with a
clamped value). High value, but a real refactor — propose as a single
backlog item.

## 5. VTK introspection helpers — scattered [MAYBE]

Pillar 3's `_get_vtk_valid_setters` (`filters.py:32-50`) caches setter
names per class. Pillar 4's `_get_algorithm_output` does runtime
`hasattr(GetOutput)` checks. `_get_output_array_names` does its own
introspection. None are unified. If a `vislang/_vtk_introspect.py` is
spun out for finding 1, 2 also lands there — natural home. Don't create
the module preemptively, but bundle it with finding 1.

## 6. Test fixture pattern — duplication [ACT, easy]

Three of the four test files (`test_cascade_skip.py`,
`test_wrapper_validation.py`, `test_property_typo.py`) reimplement the
same `SYNTHETIC_VTI` constant + `_ensure_synthetic()` skip helper at
the top of each file. `tests/conftest.py:83` already has
`_ensure_synthetic_data()` (auto-generates the file). Two parallel
mechanisms.

**Diff:** add to `conftest.py`:

```python
@pytest.fixture
def synthetic_vti_path():
    return os.path.join(REPO_ROOT, "datasets/synthetic/data/output.vti")
```

Replace 50+ `_ensure_synthetic(); ... FileName=SYNTHETIC_VTI` pairs with
a fixture parameter. Mechanical change, removes ~30 lines of boilerplate.

`test_empty_output_hints.py` uses a different pattern entirely
(`_make_image_data` + `_make_algorithm`, `filters.py`-only inline data).
That's actually a **better** pattern for the unit-test scope it covers —
no synthetic file dependency. Worth promoting `_make_image_data` to
`conftest.py` for reuse by future tests that don't need the full
synthetic dataset.

---

## Closing — triage summary

| # | Area | Label | Effort |
|---|---|---|---|
| 1 | `find_field_array` helper | ACT | S |
| 2 | Unify GetOutput/GetOutputDataObject extraction | ACT | S |
| 3 | Error message style guide | MAYBE | XS (doc) |
| 4 | Structured status schema | ACT | M (real refactor) |
| 5 | `vislang/_vtk_introspect.py` module | MAYBE | bundle with #1/#2 |
| 6 | Pytest fixture for synthetic data | ACT | XS |

**Top pick:** finding 1 + 6 are quick wins under an hour each. Finding 4
is the most strategically valuable — the diagnostic spine is becoming
the LLM agent's primary feedback channel, and unstructured prose strings
limit how programmatically the agent can recover from failures.
