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
- [x] `test_executor.py` — 29 additional executor/pipeline/inspect/namespace tests
- [x] Fix: `ImportError`/`ModuleNotFoundError` missing from `_SAFE_BUILTINS` (caused
      NameError when pipeline tried `except (ImportError, NameError):`)

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

- [x] `tracked_show()` / `tracked_add_mesh()` — record desired actors with hashes
      (implemented inside `execute_pipeline()` as `_tracked_show`; actors list
       stored in `ExecutionResult.actors` as `(mesh_proxy, kwargs)` tuples)
- [x] Scene reconciler — `SceneReconciler` in `reconciler.py`: diffs old vs new
      actor sets, applies minimal updates. Works in diff-only mode (plotter=None)
      for testing. `ReconcileResult` counts unchanged/updated/added/removed.
      `ActorRecord` records name, mesh_hash, params_hash per actor.
- [x] File watcher — `watcher.py`: `watch_and_reload()` + `ReloadHandler` using
      watchdog library. 100 ms debounce to suppress spurious duplicate save events.
      Errors print + continue (watcher thread doesn't crash on bad pipeline code).
- [x] `Session` / `run_session` — `runner.py`: complete execution loop combining
      DAG, optional Plotter, SceneReconciler, and watcher. Session is a context
      manager; methods: execute(), inspect(), screenshot(), stats(), start_watcher(),
      stop_watcher(). run_session() is a factory that creates everything and does
      the initial execute().
- [x] Tests: `test_reconciler.py` (25 tests) — covers initial, no-change, param
      change, mesh change, actor added, actor removed, tuple format, auto-naming.
      `test_session.py` (12 tests) — covers execute, inspect, re-execution caching,
      stats, context manager, screenshot-without-plotter, custom DAG.
- [ ] Offscreen rendering test — render to image, modify pipeline, re-render,
      verify image changed and caching worked (needs xvfb-run / display)

## Phase 3: One-off Inspection Layer

- [x] `inspect_exec(code, dag)` — run a one-off Python snippet with read-only
      access to the cached DAG state (meshes, arrays from last pipeline run).
      Agent uses this for ad-hoc data queries without modifying the pipeline.
      Restricted namespace: numpy, cached proxies by name, no plotter access.
- [x] Return captured print output as string result
- [x] Test: run pipeline, then inspect_exec to query stats on a cached mesh

## Phase 4: Whitelist and API Surface

- [x] Auto-generate whitelist from PyVista/numpy public APIs
      (`scripts/generate_whitelist.py` + `WHITELIST-COVERAGE.md`)
- [x] Expand coverage: common filters (clip, contour, slice, glyph, streamline,
      voxelize, streamlines, texture_map, etc.) — all major DataSet filters added
- [x] Expand coverage: PolyData-specific (tube, ribbon, extrude, curvature,
      geodesic, delaunay_2d, flip_normals, strip)
- [x] Expand coverage: ImageData-specific (dimensions, spacing, origin, extent,
      gaussian_smooth, fft, rfft, pad_image, resize, resample, image_threshold,
      dilate, erode, low_pass, high_pass, scalar_type/size/range)
- [x] Expand coverage: numpy (argsort, sort, all, any, prod, cumprod, dot, trace,
      diagonal, round, clip, real, imag, conj, ravel, squeeze, swapaxes, etc.)
- [ ] Expand coverage: PyVista Plotter methods for display configuration
- [x] Blacklist filesystem/network methods (save, write, export)
- [x] Blacklist in-place mutation (__setitem__, __iadd__, etc.)
- [x] Coverage: numpy operations (percentile, histogram, where, sqrt, etc.)
      via _TrackedNumpyNamespace

## Phase 5: Examples and Validation

- [x] Example: caching demo — cold/cached/changed-threshold runs with timing (examples/demo_caching.py)
- [x] Example: inspect_exec demo — field ranges, stats, filtered views (examples/demo_inspect.py)
- [x] Example: iterative refinement — 6 agent iterations, hit/miss table (examples/demo_iterative_refinement.py)
- [x] Example: GC demo — eviction of stale entries, shared read() survival (examples/demo_gc.py)
- [x] Shared test utils — examples/utils.py with create_test_dataset() and cleanup()
- [ ] Example: wildfire dataset exploration (read VTS, threshold, volume render)
- [ ] Example: CT scan with isosurface + volume composite
- [x] Document what PyVista API surface is covered vs not
      (`WHITELIST-COVERAGE.md` generated by `scripts/generate_whitelist.py`)

### Known limitation discovered during Phase 5

- [x] `np.percentile(proxy_array, q)` fails in `inspect_exec` — FIXED: added
  `__array__` to whitelist and switched inspect_exec to use tracked numpy namespace.

## Phase 6: Reconciliation Benchmarks

Motivated benchmarks showing the real-world speedup from content-addressed
caching across realistic scientific visualization editing scenarios.

- [ ] **Benchmark harness** — timing framework that runs a sequence of edits,
      measures wall-clock time for each, and reports speedup vs full rebuild.
      Should output markdown tables and optionally CSV for plotting.

- [ ] **Wildfire simulation edits** — Using the wildfire VTS dataset (~1.1 GB):
  - [ ] Threshold sweep: vary fire temperature threshold (400→500→600→700→800)
  - [ ] Isosurface refinement: adjust contour values
  - [ ] Colormap iteration: cycle through colormaps on same data
  - [ ] Add/remove filter: add a clip plane, then remove it
  - [ ] Multi-field: switch between Temperature, Velocity, Vorticity views

- [ ] **Large array numpy workflows** — Pure numpy computation benchmarks:
  - [ ] 100M+ point statistical analysis with varying percentile queries
  - [ ] Derived field computation (magnitude, gradient) with parameter sweeps
  - [ ] Boolean mask chains: threshold → clip → stats, vary each step

- [ ] **The Gamma-style edit scenarios** — Realistic edits scientists make
      during interactive data exploration (inspired by The Gamma's work on
      composable, incremental data transformations):
  - [ ] "Zoom in on a feature": progressively tighter threshold bounds
  - [ ] "Compare representations": same data, surface vs volume vs wireframe
  - [ ] "Adjust visual parameters": opacity, colormap, scalar bar — no data change
  - [ ] "Refine a multi-step pipeline": change one middle step, everything
        downstream re-executes but upstream is cached
  - [ ] "Switch datasets": same pipeline, different input file — everything
        re-executes but the pipeline structure is the same
  - [ ] "A/B comparison": alternate between two pipeline variants on same data,
        each fully cached on second viewing

- [ ] **Speedup summary table** — Aggregate results across all scenarios showing
      cold build time, cached rebuild time, and speedup factor. Target: 10-100x
      for parameter tweaks, 2-5x for filter changes, 1x for full pipeline changes.

## Future: Monty Integration

- [ ] Track Monty opaque object support (github.com/pydantic/monty)
- [ ] When available: port restricted exec to Monty for real security boundary
- [ ] File an issue or feature request on Monty for foreign/opaque objects

## Completed

- Phase 1 core complete (2026-04-10): TrackedProxy, DAG, dispatch, stable_hash,
  whitelist, tracked_read, execute_pipeline, inspect_exec, 32 tests all passing.
- Phase 2 core complete (2026-04-10): SceneReconciler, ActorRecord, ReconcileResult,
  ReloadHandler, watch_and_reload, Session, run_session. 37 new tests (25 reconciler
  + 12 session); total 98 tests, all passing.
- Phase 5 examples (2026-04-10): 4 runnable demos (caching, inspect, iterative
  refinement, GC) + utils.py. All run without errors; 98 tests still passing.
- Phase 4 whitelist expansion (2026-04-10): auto-generated coverage report
  (scripts/generate_whitelist.py, WHITELIST-COVERAGE.md), expanded whitelist
  with ~70 new methods across DataSet, PolyData, ImageData, numpy.ndarray.
  98 tests still passing.
