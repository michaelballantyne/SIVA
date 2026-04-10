# Tracked Execution — Backlog

## Context

Building a library for secure, incrementally-reconciled scientific visualization.
Pipeline scripts use PyVista syntax. A tracked dispatch layer intercepts operations
to provide content-addressed caching (hash consing) and incremental scene updates.

See `../../TRACKED-EXECUTION-DESIGN.md` for the full design.

### Key finding: Monty can't handle arbitrary Python objects

Pydantic Monty (v0.0.10) only accepts dataclasses and basic Python types as
external function return values. Numpy arrays and PyVista meshes fail with
`TypeError: Cannot convert <type> to Monty value`. Until Monty adds opaque/foreign
object support, we build on CPython with restricted exec. Monty is a future
upgrade path for security.

## Phase 1: Core Infrastructure

- [x] Project scaffold — pyproject.toml, package structure, dev dependencies
- [x] `TrackedProxy` — wraps real objects with content hash and DAG reference
- [x] `DAG` — stores cache (hash → object), tracks current run, implements GC
- [x] `dispatch()` — generic method interception: whitelist check, hash, cache
      lookup, execute, record
- [x] `stable_hash()` — deterministic hashing for operations, scalars, tuples
      (handles numpy scalars via .item() conversion)
- [x] `tracked_read()` — file reader entry point with mtime-based identity
- [x] Restricted exec namespace — provide entry points, block builtins/imports
- [x] `execute_pipeline()` — runs pipeline code with tracked entry points,
      captures print output, records show/add_mesh actors, returns stats
- [x] `inspect_exec()` — read-only inspection against cached DAG state
- [x] `__init__.py` — package exports
- [x] Basic test: read → threshold → verify cache hit on re-run with same params
- [x] Basic test: change threshold value → verify only threshold re-executes
- [x] Full test suite: 32 tests covering hash, proxy, caching, GC, whitelist,
      scalar escape, numpy operators, full pipeline, inspect_exec, tracked_read

### Notable implementation fixes during Phase 1

- `pv.core.dataset_attributes.DataSetAttributes` path invalid in pyvista 0.47.2;
  correct path is `pv.core.DataSetAttributes`
- `dispatch()` must handle properties (non-callable attributes) separately from
  methods: check `callable(attr_val)` before invoking
- `TrackedProxy.__setitem__` must be defined explicitly to intercept `proxy[k] = v`
  (Python's item assignment bypasses `__getattr__`)
- `NameError` must be in `_SAFE_BUILTINS` for restricted exec (try/except NameError
  in pipeline code)

## Phase 2: Rendering and Reconciliation

- [ ] `tracked_show()` / `tracked_add_mesh()` — record desired actors with hashes
- [ ] Scene reconciler — diff old vs new actor sets, apply minimal updates to Plotter
- [ ] Offscreen rendering test — render to image, modify pipeline, re-render,
      verify image changed and caching worked
- [ ] File watcher — watchdog-based hot reload on pipeline file save

## Phase 3: One-off Inspection Layer

- [x] `inspect_exec(code, dag)` — run a one-off Python snippet with read-only
      access to the cached DAG state (meshes, arrays from last pipeline run).
      Agent uses this for ad-hoc data queries without modifying the pipeline.
      Restricted namespace: numpy, cached proxies by name, no plotter access.
- [x] Return captured print output as string result
- [x] Test: run pipeline, then inspect_exec to query stats on a cached mesh

## Phase 4: Whitelist and API Surface

- [ ] Auto-generate whitelist from PyVista/numpy public APIs
- [ ] Expand coverage: common filters (clip, contour, slice, glyph, streamline)
      (many already covered in curated whitelist)
- [ ] Expand coverage: PyVista Plotter methods for display configuration
- [x] Blacklist filesystem/network methods (save, write, export)
- [x] Blacklist in-place mutation (__setitem__, __iadd__, etc.)
- [x] Coverage: numpy operations (percentile, histogram, where, sqrt, etc.)
      via _TrackedNumpyNamespace

## Phase 5: Examples and Validation

- [ ] Example: wildfire dataset exploration (read VTS, threshold, volume render)
- [ ] Example: CT scan with isosurface + volume composite
- [ ] Example: iterative refinement loop (simulate agent editing pipeline 10x)
- [ ] Performance benchmark: cached re-execution vs full rebuild
- [ ] Document what PyVista API surface is covered vs not

## Future: Monty Integration

- [ ] Track Monty opaque object support (github.com/pydantic/monty)
- [ ] When available: port restricted exec to Monty for real security boundary
- [ ] File an issue or feature request on Monty for foreign/opaque objects

## Completed

- Phase 1 core complete (2026-04-10): TrackedProxy, DAG, dispatch, stable_hash,
  whitelist, tracked_read, execute_pipeline, inspect_exec, 32 tests all passing.
