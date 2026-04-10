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

- [ ] Project scaffold — pyproject.toml, package structure, dev dependencies
- [ ] `TrackedProxy` — wraps real objects with content hash and DAG reference
- [ ] `DAG` — stores cache (hash → object), tracks current run, implements GC
- [ ] `dispatch()` — generic method interception: whitelist check, hash, cache
      lookup, execute, record
- [ ] `stable_hash()` — deterministic hashing for operations, scalars, tuples
- [ ] `tracked_read()` — file reader entry point with mtime-based identity
- [ ] Restricted exec namespace — provide entry points, block builtins/imports
- [ ] Basic test: read → threshold → verify cache hit on re-run with same params
- [ ] Basic test: change threshold value → verify only threshold re-executes

## Phase 2: Rendering and Reconciliation

- [ ] `tracked_show()` / `tracked_add_mesh()` — record desired actors with hashes
- [ ] Scene reconciler — diff old vs new actor sets, apply minimal updates to Plotter
- [ ] Offscreen rendering test — render to image, modify pipeline, re-render,
      verify image changed and caching worked
- [ ] File watcher — watchdog-based hot reload on pipeline file save

## Phase 3: One-off Inspection Layer

- [ ] `inspect_exec(code, dag)` — run a one-off Python snippet with read-only
      access to the cached DAG state (meshes, arrays from last pipeline run).
      Agent uses this for ad-hoc data queries without modifying the pipeline.
      Restricted namespace: numpy, cached proxies by name, no plotter access.
- [ ] Return captured print output as string result
- [ ] Test: run pipeline, then inspect_exec to query stats on a cached mesh

## Phase 4: Whitelist and API Surface

- [ ] Auto-generate whitelist from PyVista/numpy public APIs
- [ ] Blacklist filesystem/network methods (save, write, export)
- [ ] Blacklist in-place mutation (__setitem__, __iadd__, etc.)
- [ ] Expand coverage: common filters (clip, contour, slice, glyph, streamline)
- [ ] Expand coverage: numpy operations (percentile, histogram, where, sqrt)
- [ ] Expand coverage: PyVista Plotter methods for display configuration

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

(nothing yet)
