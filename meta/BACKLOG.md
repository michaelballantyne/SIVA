# VisLang Backlog

## Do Now (Independent)

Bug fixes, cleanup, and mechanical refactoring that don't need design input.

### High

- [x] Add `describe_file` MCP tool and simplify `tracked-execution` — Added
  `describe_file(data_file)` tool to `experiments/tracked-execution/mcp_server/server.py`
  (was already present). Added 8 tests in `test_describe_file.py`. Removed `run_session`
  factory from `runner.py` and `__init__.py` (it was a thin wrapper around
  `Session(...); session.execute()` with no external test or example usage). Updated
  README.md references. Updated INSTRUCTIONS string to add `describe_file` to the workflow,
  note that view name = basename without extension, list common colormaps, and note that
  colormap/opacity changes are essentially free. All 273 tests pass.

- [x] Remove phantom tools and fix instructions string — Removed `make_vector`
  and `curl` from `MUTATION_TOOLS` (they have no `@mcp.tool()` implementations).
  The `_ALL_TOOLS` list and instructions string auto-update from `MUTATION_TOOLS`,
  so they now reflect only actual callable tools. Regenerated docs.

- [x] Fix `KeyError: 'num_points'` in pipeline status reporting — Fixed `.get()`
  fallback in the main reporting loop and in the `export_standalone` template.


- [x] Remove dead code in `server.py` — Deleted undecorated `sample_point()`,
  `set_color_range()`, and `benchmark_pipeline()` from `server.py`. Also updated
  `test_integration.py` to use `sample_points()` (the live batch API).

- [x] Remove vestigial `node` parameter from `camera_orbit` — Documented as
  "Unused — kept for API consistency. Leave empty." An unused parameter is wasted
  cognitive load; remove it entirely.

- [x] Remove DSL aliases — Removed `isosurface()` (alias for `contour()`),
  `compute_velocity()` (alias for `make_vector()`), and `compute_vorticity()`
  (thin wrapper around `curl()`). Updated all examples, tests, demos, and
  `get_dsl_overview`/`get_dsl_reference` to use canonical forms only.

- [x] Fix `title()` actors not cleared on `set_pipeline` rebuild — Previous
  `title()` text actors persist and overlap with new ones. Fixed by routing
  `title()` through `renderer.add_overlay_actor()` so they are tracked in
  `_overlay_actors` and removed during `renderer.clear()`. Tests in
  `tests/test_title_clear.py` verify: actor tracked, text set, cleared on
  `clear()`, no accumulation on rebuild, and title removed when new pipeline
  omits `title()`.

- [x] Remove legacy global state and `_LegacyCtx` shim — Deleted `_renderer`,
  `_vtk_objects`, `_current_code`, `_annotations` module-level globals and the
  `_LegacyCtx` shim. Added `_init_for_test(renderer=None)` helper that creates a
  real `ViewContext` and installs it in `_views`. Updated all 5 test files
  (`test_annotations.py`, `test_server_tools.py`, `test_camera_orbit.py`,
  `test_chart_rendering.py`, `test_named_views.py`) to use `_init_for_test()`.
  Removed `TestLegacyShim` class. All 448 tests pass.

- [x] Improve `get_ground_z` output — Response now leads with "Ground z = X.X";
  added `layers=False` parameter to return only the ground value. Tests added.

- [x] Minor server.py cleanups — Move `_parse_color` from annotate() closure to
  module level; deduplicate `GetDimensions()` calls in `describe_data()`; replace
  7 inline `import vtk` statements with a single top-level import.

### Medium

- [x] Auto-populate DSL namespace from `PipelineBuilder` methods — `_make_namespace()`
  manually maps 40+ builder methods by name. Adding a new DSL form requires
  updating both the class and this mapping; if missed, the form silently fails
  with a `NameError`. Replace with `inspect.getmembers()` over public methods.

- [x] Unify histogram-guided opacity logic — `filters._auto_opacity()` and
  `queries.suggest_opacity_function()` implement the same algorithm. Consolidated
  into one function with `format` and `n_bins` parameters. Also converted
  remaining Python loops in `suggest_scalar_range()`, `suggest_opacity_function()`,
  and `suggest_isosurface()` to numpy (`vtk_to_numpy` + `np.histogram` +
  `np.percentile`).

- [x] Extract shared scalar bar builder — The 11-line scalar bar construction
  sequence was duplicated. Extracted into `_build_scalar_bar(lut, title)` helper
  at `filters.py:900`; both call sites updated to use it.

- [x] Separate 2D overlay actors from 3D scene actors in Renderer — Added
  `_overlays` dict and `add_overlay(name, actor2d)` to Renderer; scalar bar
  actors moved from `_actors` to `_overlays` via `add_overlay()`; `clear()`
  and `list_actors` updated accordingly; `GetBounds()` iteration no longer
  needs guards for 2D actors.

- [x] Add MCP protocol-level tests — Call every `@mcp.tool` function through
  the actual MCP protocol with minimal valid inputs. Verify responses serialize
  without errors and match declared return types. Would have caught the bonsai
  Pydantic validation bug and would catch phantom tool entries. Added
  `tests/test_mcp_protocol.py` with 105 tests covering all QUERY_TOOLS,
  MUTATION_TOOLS, and META_TOOLS, plus a return-type invariant suite.

- [x] Add stateful integration tests — Test sequences of operations: multi-view
  create/switch/verify, version history set/modify/restore, combined
  load/query/pipeline/query-filtered-node workflows. Call Python functions
  directly (no MCP protocol needed). See `meta/TESTING.md` level 2. Added 24
  tests in `tests/test_stateful_integration.py` covering all three workflows with
  a `_FakeRenderer` stub that supports the full renderer interface needed by
  `server.py` and `dsl.py`.

- [x] Move validation before pipeline execution — Added `_get_output_array_names`,
  `_FIELD_NAME_PROPERTIES`, and `_validate_field_names` to `filters.py`. The
  validation runs before `vtk_obj.Update()` in `create_vtk_filter`, checking
  `ContourBy`, `ThresholdBy`, `AddScalarArrayName`, `AddVectorArrayName`,
  `GradientField`, `ScaleArray`, `OrientationArray`, `Vectors` against upstream
  arrays. Typos raise `ValueError` with available field names listed. Tests in
  `tests/test_field_validation.py` (28 tests).

- [x] Detect user-closed windows and surface status to agents — Added
  `Renderer.is_window_closed()` using `vtkRenderWindow.GetMapped()`;
  `list_views()` now shows `[window closed]` flag. Only active in
  INTERACTIVE mode; offscreen always returns False. Tests in
  `test_renderer_window_closed.py` (8 tests) and 4 new tests in
  `test_named_views.py` covering the flag in `list_views()`.

- [x] Pandas domain on tracked_core — proves generalization — Built
  `experiments/tracked-execution/tracked_data/` with whitelist.py (DataFrame,
  Series, GroupBy), dispatch.py (thin wrapper around tracked_core.dispatch),
  executor.py (tracked_read_csv, execute_data_pipeline, inspect_data), and
  __init__.py. 8 tests in tracked_data/tests/test_pandas_core.py: read_csv caching,
  query caching, groupby+agg, partial-miss on filter change, full pipeline, inspect,
  blacklist enforcement, describe. Added tracked_data* to pyproject.toml include list.
  All 8 pandas tests + 229 viz tests pass.

- [~] Split `server.py` into modules — Phase 1: dsl_docs.py extraction in progress.
  At 3,048 lines it contains MCP setup, 45 tool handlers, pipeline execution, DSL docs,
  and session state. Split into `tools_query.py`, `tools_mutate.py`, `tools_meta.py`,
  `dsl_docs.py` (static data for `get_dsl_reference` / `get_dsl_overview`), with
  `server.py` as a thin entry point targeting under 400 lines. Also, split this
  splitting-up task into multiple working intermediate states and commits and even
  agent runs---this is much too big to do all at once. Make some progress, update
  the backlog, and let the manager push it and run another task.

## Do With Human

Items requiring design decisions, new feature design, or human review.

### High

- [ ] Merge overlapping query tools into `describe_data` — `get_array_info`,
  `get_node_info`, and `get_bounds` all return subsets of what `describe_data`
  already returns. Remove all three. Also add a `field` parameter to
  `describe_data` so `describe_data(field="Temperature")` returns rich single-
  field stats (percentiles, histogram shape, opacity suggestion), then remove
  `get_statistics` and `get_field_summary` as separate tools. `query_stats`
  stays (conditional filtering is distinct). Target: 5 fewer tools.

- [ ] Remove mutation tools in favor of pipeline file edits — Remove
  `set_colormap`, `set_opacity`, `toggle_visibility`, `set_background`,
  `annotate`, `clear_annotations`. Claude edits the pipeline file and calls
  `set_pipeline` (or hot reload picks it up). This eliminates the divergence
  where mutation tools change VTK state without updating the pipeline file.
  `set_camera` is the exception — camera is interactive and shouldn't live in
  the file unless explicitly frozen.

- [ ] Fix `quick_start` tool — Currently generates pipeline code as a string
  that the LLM must paste and then call `set_pipeline()`. Should write the file
  and execute it directly, or fold into `load()` as optional auto-pipeline.
  The two-step dance is unnecessary friction.

- [ ] Reduce `set_pipeline` output verbosity — After the first successful build,
  subsequent calls repeat full array lists for all nodes. This is unnecessary
  noise during refinement. Add a terse mode that reports only what changed:
  "Pipeline v7 built. 7 nodes, all ok. Changes: updated threshold on 'fire'."
  Verbose output on demand. Connects naturally to the reconciler item.

### Medium

- [ ] File-watching hot reload with status file — Watch pipeline files for
  changes and auto-rebuild on save. Write build output to a status file next to
  the pipeline file (`view-main.py` -> `view-main.status.txt`). Replaces
  `set_pipeline` as the primary build trigger for humans; Claude writes the
  file and reads the status file. Must handle multiple views with per-view
  status files. Foundation for the LSP vision.

- [~] Reconciler-based pipeline updates — Diff old and new pipeline specs and
  apply only the changes instead of full rebuild. Avoids re-reading data and
  re-executing unchanged filters. Also enables terse set_pipeline output:
  report only what changed rather than full array lists for all nodes.
  In-place opacity updates done: `SceneReconciler` detects opacity-only param
  changes and calls `actor.GetProperty().SetOpacity()` without remove/re-add.
  `updated_property` counter in `ReconcileResult` tracks these. Two offscreen
  rendering tests added in `test_offscreen.py` (`test_opacity_change_in_place`,
  `test_colormap_change_full_update`). Remaining: higher-level pipeline diff
  (mesh hashes, filter tree diffing) for `set_pipeline` integration.

- [ ] Multi-panel layouts — No layout tool for compositing multiple renders
  into a single image. Useful for comparison workflows; mentioned in sessions
  1, 2, and bonsai independently.

- [ ] In-plane vector glyphs on slices — `show_vectors()` for flow
  visualization on cross-sections. Useful for any vector field dataset.

- [x] Expand VTK class whitelist — Added 46 new classes (3 readers, 3 sources,
  30 geometry/topology filters, 4 point cloud/sampling filters, 9 image
  processing filters). Total whitelist now 119 classes.

- [x] Fix `active_scalars_name` hidden state in `tracked-execution` dispatch —
  When `scalars=` is omitted from `threshold()`, `contour()`, and similar
  scalar-sensitive filters, `active_scalars_name` is now included in the hash
  so different active scalars produce distinct cache keys. `set_active_scalars`
  moved from whitelist to blacklist with a clear error message. The xfail test
  was promoted to a regular passing test. 4 new tests in `TestActiveScalarsHashCorrectness`.

- [x] Enforce `scalars=` in scalar-sensitive methods — Changed behavior from
  UserWarning to ValueError when `threshold()`, `contour()`, `clip_scalar()`,
  `warp_by_scalar()`, etc. are called without `scalars=`. Updated all tests in
  `test_error_messages.py` (TestScalarSensitiveWarnings → raises ValueError),
  `test_purity.py` (TestCacheSabotage uses scalars="T"; TestActiveScalarsHiddenState
  and TestActiveScalarsHashCorrectness now verify ValueError is raised instead of
  testing the old warning/hash behavior). All 209 tests pass (+ 1 xfailed).

- [ ] Investigate VTK passthrough optimization in `tracked-execution` — When
  a threshold filter passes ALL points, VTK reuses the source VTK array object
  directly. The filter result's data array is a view of the source's array.
  Mutating the source after caching the filter result corrupts the cached data.
  Possible mitigations: detect sharing via VTK object identity, or copy-on-
  store. See PURITY-ANALYSIS.md Hazard 3 for details.

- [ ] Window-closed detection doesn't work in interactive mode — `list_views`
  never shows `[window closed]` after the user closes OS windows. `focus()`
  and `screenshot()` silently render into the dead window's buffer, so the
  agent has no idea the user can't see anything. `GetMapped()` may not behave
  as assumed on macOS/Cocoa. Needs investigation and a reliable detection
  mechanism.

- [ ] Fix UI freeze during `set_pipeline` on other views — Interactivity in
  already-open windows freezes while pipeline builds in another view. Likely
  GIL contention during pipeline execution blocking the event loop.

- [ ] Update VISION.md to reflect current reality — Part 1 cites ~35 tools
  (actual: 45), lists `get_examples()`/`list_capabilities()` which no longer
  exist, and describes `spatial-region statistics` as future work when it
  shipped as `query_stats`. Part 2 "Next Steps" still lists three items from
  April 4 with one already done. The mutation-tools removal decision and the
  `start_session` architectural shift are absent. Update to match the current
  codebase and current priorities.

### Low / Ideas

- [ ] `start_session` tool, `file_source` DSL form, and session/data path
  separation — Add a `file_source(filename)` DSL form (file extension-driven
  reader inference). Replace `load` with a `start_session(data_file,
  session_dir=None)` tool that creates the session directory, writes an
  initial `view-main.py` with `file_source(...)`, executes it, and returns
  `describe_data` output. All `Path(...)` / `glob.glob(...)` calls resolve
  against the session directory. Add a `path` parameter to `list_data_files`.
  Rename `.vislang/` to non-dotfile paths (`history/`, `server.log`). This
  also fixes the chicken-and-egg problem where query tools fail on fresh views
  because no pipeline is active yet — data becomes a session-level resource.

- [ ] Remove auto-screenshots from state-changing tools — Every mutation tool
  currently returns a base64 screenshot via `_with_screenshot()`. In long
  sessions this accumulates tens of MB in Claude's context. Fix: remove
  `_with_screenshot()` from all mutation tools so they return text only; add
  `resolution` parameter to `screenshot()` ("low" 640x480 default, "high"
  1920x1080); update server instructions to guide parallel tool calls when a
  screenshot is wanted. `camera_orbit` also needs adjustment.

- [ ] Isosurface + volume rendering composite example — A natural CT pattern
  with no clear DSL idiom. Add a documented multi-representation overlay
  example to `get_dsl_reference` and verify the pipeline actually works.

- [ ] Data-guided parameter suggestions during refinement — `suggest_*` tools
  work well for initial build but don't help during threshold sweeping. A
  capability like "suggest threshold that isolates top N% of field" or "suggest
  scalar range maximizing contrast in region X" would reduce guessing iterations.

- [ ] LSP for the pipeline DSL — Autocomplete for field names from the loaded
  dataset, hover info showing field ranges, inline diagnostics ("threshold 500
  selects 0 points"). Backend query layer already exists; LSP is another
  delivery channel. Depends on hot reload infrastructure.

- [ ] Multi-timestep support — Discover sibling timesteps, animate, compare
  across time. Wildfire dataset is single-timestep; unvalidated.

- [x] Trame viewer prototype for browser-based visualization (tracked-execution)
  — Created `experiments/tracked-execution/mcp_server/trame_viewer.py` with
  `TrameViewer` class: manages a Trame server with tabbed views (one per
  pipeline file), uses `VtkRemoteView.replace_view()` for tab switching,
  supports background-thread startup so it can coexist with the MCP server's
  asyncio loop. `update_view(name)` pushes a fresh image to clients after
  reconciler changes actors. Screenshots captured directly from PyVista
  plotter (not via browser). Standalone test in `test_trame_standalone.py`
  verifies: server starts, views register, tab-switch via `update_view`,
  `remove_view` cleanup. All existing tests still pass.

- [ ] Jupyter notebook with trame interactive views — After exploration, Claude
  assembles a notebook where each cell runs a VisLang pipeline and displays
  an interactive 3D trame view. Requires a small `vislang.notebook.show(code)`
  API.

- [ ] Autonomous feedback loop via Anthropic Agents SDK — Outer Claude launches
  a subagent that connects to the VisLang MCP server, explores a task end-to-
  end, then reflects to identify improvements. Makes the gather-feedback /
  reflect-design cycle fully autonomous.

## Completed

- Fix numpy proxy interop — `__array__` and `__array_wrap__` added to TrackedProxy.
  `TrackedProxy.__array__(dtype, copy)` accepts the numpy 2.x `copy` keyword argument.
  `TrackedProxy.__array_wrap__(array, context, return_scalar)` returns the plain ndarray
  after ufunc calls, bypassing dispatch so `np.sqrt(proxy)` / `np.abs(proxy)` / etc.
  work transparently without whitelist errors. 15 tests in
  `experiments/tracked-execution/tests/test_numpy_proxy_interop.py` cover: `__array__`
  protocol correctness, ufunc interop (sqrt, abs, log), mesh-field assignment via
  `vtk_escape`, and the full complex workflow (`np.sqrt(arr)` result assigned to
  `mesh_copy["Derived"]`) via `execute_pipeline`. All 221 tests pass.

- Complex agent workflow tests for tracked-execution MCP server — Added
  `test_complex_workflow.py` with 5 tests: multi-view iteration with shared read
  cache verification, error recovery via watcher (write bad code / fix / verify
  recovery), vtk_escape inside a pipeline file (with documented constraint: use
  ``** 0.5`` instead of ``np.sqrt`` since the pipeline ``np`` is
  _TrackedNumpyNamespace), inspect-driven 3-round pipeline refinement verifying
  cache hit accumulation, and cross-view inspect (wildfire, skipif absent) that
  reads T range from one view and uses it to configure a threshold in a second.
  All 5 tests pass in ~7 s on synthetic data.

- Write comprehensive user-facing documentation for tracked execution system —
  Added `experiments/tracked-execution/docs/getting-started.md` (quick start
  walkthrough, prerequisites, installation, MCP config, synthetic dataset demo),
  `docs/mcp-reference.md` (all 7 tools with parameters, returns, examples,
  error cases), `docs/pipeline-reference.md` (namespace, filters, numpy ops,
  vtk_escape patterns, whitelist categories, scalars= rule, caching mechanics),
  and `docs/architecture.md` (ASCII diagram, content-addressed caching,
  TrackedProxy/dispatch/DAG flow, file watching, threading model, security model).

- Simplification pass on tracked-execution — removed `add_mesh` alias from all docs
  (AGENT-GUIDE.md, README.md, executor.py docstrings): `add_mesh` was never in the
  execute_pipeline namespace, docs falsely claimed it was an alias for `show`. Fixed
  `_shared_tracked_read` to avoid direct `dag.cache` manipulation: the execute function
  now checks `_shared_read_cache` before hitting disk, so `_dag_call` manages the full
  cache lifecycle. Kept `pipeline_status` (unique info: watcher alive, evictions, print
  output not in `list_views`). All 266 tests pass.

- Add interactive VTK window support to tracked-execution MCP server — Added
  `_offscreen` flag, `_work_queue`, `_main_thread_id`, `run_on_main_thread()`, and
  `run_event_loop()` to `experiments/tracked-execution/mcp_server/server.py`. All VTK
  operations in `create_view` (plotter creation, reconcile, render), `screenshot`
  (render + capture), `close_view` (plotter.close), and the watcher `on_reload` callback
  (reconcile) are routed through `run_on_main_thread()`. `create_view` passes
  `off_screen=_offscreen` so real windows appear in interactive mode. Updated `run.py`
  with `--offscreen` / `--interactive` argparse: offscreen mode runs MCP directly, interactive
  mode starts MCP on a daemon thread and runs the event loop on the main thread. All 266
  existing tests pass in offscreen mode.

- Remove watcher-stop workaround from e2e tests — The `test_sequential_inspect_calls`
  test in `test_bonsai_e2e.py` called `_stop_watcher(srv, "seq")` which was never
  defined, causing a NameError. Removed the undefined call and the now-unused
  `import mcp_server.server as srv`. The locking in `server.py` (all DAG/plotter
  access paths use `vs.lock`, including the watcher `on_reload` callback) is
  sufficient; no watcher-stop workaround is needed. All 8 e2e tests pass.

- `list_views`, `close_view`, and `pipeline_status` MCP tools — Added three tools to
  `experiments/tracked-execution/mcp_server/server.py`. `list_views()` shows all active
  views with cache stats and error status. `close_view(pipeline_file)` stops the watcher,
  closes the plotter, and removes the view. `pipeline_status(pipeline_file)` reports cache
  stats, pipeline output, variable names, last error, and watcher state — letting the agent
  check whether file edits were picked up by the watcher. Added INSTRUCTIONS entries for all
  three tools. 13 tests in `mcp_server/tests/test_list_close.py`; all 40 MCP server tests pass.

- Bonsai CT end-to-end agent test — Added `test_bonsai_e2e.py` with 4 tests
  covering the full CT workflow: load → explore density → threshold (wood region)
  → isosurface contour → multiple simultaneous views. Also added `bonsai_session_log.md`
  documenting the workflow, key lessons (thread safety, explicit scalars=), and
  test coverage. All 4 bonsai tests pass in ~7.6 s with `xvfb-run -a`.

- `inspect` and `screenshot` MCP tools — Added `inspect(pipeline_file, code)` and
  `screenshot(pipeline_file)` to `experiments/tracked-execution/mcp_server/server.py`.
  `inspect` runs read-only code against the cached DAG state via `inspect_pipeline()`
  and returns captured print output (with a hint if no output is produced).
  `screenshot` renders the view and returns a native FastMCP `Image` object (PNG bytes)
  that Claude can display directly. `screenshot` raises `ValueError` for missing views
  (to surface a clean error to the MCP layer) while `inspect` returns an error string.
  8 tests in `mcp_server/tests/test_inspect_screenshot.py`; all 20 MCP server tests pass.

- `create_view` MCP tool with file watching — Added `create_view(pipeline_file)`
  to `experiments/tracked-execution/mcp_server/server.py`. Creates an offscreen
  PyVista Plotter, executes the pipeline through `execute_pipeline`, reconciles
  actors via `SceneReconciler`, and starts a `watchdog` file watcher that
  re-executes and re-reconciles on every save. Syntax errors prevent view creation
  and return an error; runtime errors create the view so the watcher can retry when
  the file is fixed. Added `_get_view()` and `_resolve_view_name()` helpers and
  `lock: threading.Lock` on `ViewState`. 9 tests in
  `mcp_server/tests/test_create_view.py`; all 12 tests pass.

- MCP server skeleton with set_working_directory — Created
  `experiments/tracked-execution/mcp_server/` with `server.py` (FastMCP instance,
  INSTRUCTIONS string, `set_working_directory` tool), `run.py` (stdio entry point),
  and `tests/test_server_basic.py` (3 tests: valid path, invalid path, error when
  views exist). All tests pass.

- vtk_escape pattern for raw VTK within tracked pipelines — Implemented
  `vtk_escape(input_proxy, func, *, key=None)` and `vtk_escape_multi()` in
  `experiments/tracked-execution/tracked_execution/vtk_escape.py`. Function
  hashing: explicit key > inspect.getsource > bytecode fallback. Both available
  in execute_pipeline namespace. 24 tests in `tests/test_vtk_escape.py`.
  Design doc in `VTK-ESCAPE-PATTERN.md`. Four runnable demos in
  `experiments/tracked-execution/examples/`: demo_vtk_escape_basic.py (windowed
  sinc smoother, 200x+ cached speedup), demo_vtk_escape_caching.py (hit/miss
  across threshold changes), demo_vtk_escape_derived.py (velocity magnitude
  derived field cached across iterations), demo_vtk_escape_multi.py (merge
  two tracked meshes, cache propagation from each input). Note: filter functions
  must be defined at module scope (not inside execute_pipeline code strings) so
  that normal Python import works; demos use the direct tracked_read/vtk_escape
  API rather than execute_pipeline strings.

- Purity analysis: VTK/PyVista statefulness limits for caching correctness —
  Wrote `experiments/tracked-execution/tests/test_purity.py` (25 tests, 23 pass
  2 xfail) and `experiments/tracked-execution/PURITY-ANALYSIS.md`. Discovered
  three genuine caching hazards: (1) `set_active_scalars` hidden state not
  captured in content hash causes wrong cache hits — CRITICAL; (2) VTK
  passthrough optimization shares source VTK array when all points pass a
  threshold filter; (3) cache stores live references so direct mutation
  corrupts all future hits. Confirmed safe behaviors: eager execution, partial-
  pass filter output is an independent copy, contour is deterministic, chained
  filter outputs are isolated from intermediate mutations.
  New items added: see "Fix active_scalars hidden state in dispatch()" below.

- Simplification round 2 for `tracked-execution` — deleted `inspect.py` compat shim and updated all imports to `executor.py` directly; extracted `_make_print_buffer()` and `_base_namespace()` helpers to eliminate namespace-building duplication between `execute_pipeline` and `inspect_exec`; auto-generated 15 single-arg numpy wrapper methods via loop instead of 30-line boilerplate; simplified `_should_wrap()` to a single-line condition; added `Any` type annotation to `dispatch()` signature; added `DAG` and `Any` type annotations to `TrackedProxy.__init__`; imported `_unwrap` from dispatch into reconciler to eliminate duplicated proxy-unwrap code; moved `import numpy as np` to module level in executor.py; fixed `dag.misses += 1` ordering in `vtk_escape.py` to match `dispatch.py` convention. All 145 tests pass (+ 2 xfail).

- API/UX review for `tracked-execution` external experience — renamed `inspect_exec`
  to `inspect_pipeline` (kept `inspect_exec` as backward-compatible alias); added
  `__repr__` to `ExecutionResult` and `InspectResult` (shows hits/misses/output
  preview at a glance); added `ExecutionResult.ok = True` sentinel (quick success
  check without exceptions); fixed `pv` documentation inconsistency (AGENT-GUIDE
  said `pv` was not available; it IS in the namespace and is now documented correctly);
  improved `execute_pipeline` docstring for `show_callback` and listed all
  namespace members; updated all examples and docs to use `inspect_pipeline`.
  9 new tests in `TestResultErgonomics`. All 204 tests pass (+ 2 xfail).

- Simplification round 4 for `tracked-execution` (deeper structural review) —
  Merged DAG class from `core.py` into `dispatch.py` (DAG and dispatch belong
  together; core.py kept as a 9-line re-export shim for backward compat).
  Extracted `_dag_call()` helper into `dispatch.py` to eliminate the repeated
  cache-check/execute/store pattern that was duplicated across `dispatch()`,
  `_TrackedNumpyNamespace._call()`, `tracked_read()`, `vtk_escape()`, and
  `vtk_escape_multi()`. All five call sites now delegate to `_dag_call`.
  Concluded: watcher.py/runner.py split is correct (watcher is watchdog-specific
  boilerplate that would add visual noise to runner); _should_wrap heuristic is
  complete; _SAFE_BUILTINS in executor.py is the right location (sandbox policy,
  not whitelist policy); vtk_escape's function-hashing approach is intentionally
  different from dispatch's method-based approach and should stay separate.
  1841 total lines (was 1853). All 145 tests pass (+ 2 xfail).
  Line counts: __init__=38, core=9, dispatch=259, executor=384, proxy=172,
  reconciler=179, runner=231, vtk_escape=187, watcher=143, whitelist=239.

- Simplification round 3 for `tracked-execution` (final polish) — wrote README.md
  covering architecture, quick start, pipeline execution model, inspect_exec,
  vtk_escape, purity contract, known hazards, and running tests/benchmarks/examples;
  added `ExecutionResult` to `__init__.py` exports (was missing); removed duplicate
  `"diagonal"` entry from whitelist; tightened docstrings across all modules
  (removed restatements, clarified _TrackedNumpyNamespace and _base_namespace);
  cleaned up a stale comment in stable_hash. All 145 tests pass (+ 2 xfail).
  Line counts: __init__=38, core=60, dispatch=169, executor=411, proxy=173,
  reconciler=179, runner=231, vtk_escape=210, watcher=143, whitelist=239.

- Simplification round 1 for `tracked-execution` — fixed `record_hit`/`record_miss` bug (37 tests were failing); merged `inspect_exec` + `InspectResult` into `executor.py` (inspect.py is now a compatibility shim); moved `_SAFE_BUILTINS` before the code that uses it; eliminated redundant `arg_hash` local function and stale imports in `_TrackedNumpyNamespace._call`; removed dead `_actor_name` function from `reconciler.py`; removed bogus `pi()` method shadowing `__getattr__` fallback; removed unused `TYPE_CHECKING` import from `inspect.py`. All 98 tests pass.

- Consolidate DSL discovery into `get_dsl_overview()` — merged `list_capabilities` and renamed `get_examples`; workflow patterns + full form index + "see also" cross-references
- Fix multi-view crash in interactive mode — shared work queue, `suggest_camera` crash fix, `focus()` return type, pipeline compute/scene-update thread split
- `RenderMode` enum and `--headless-interactive` mode
- Multiple named views — `new_view`, `focus`, `close_view`, `list_views`, `ViewContext`
- Per-view pipeline files
- Replace Python loops with numpy in `queries.py` — `get_statistics`, `get_histogram`, `get_spatial_extent`
- Consolidate duplicated constants (`_scalar_type_map`, `_component_name_map`, reader extension maps)
- Extract node-lookup boilerplate into `@requires_data` decorator
- Standardize API parameter naming
- Reduce tool count from 38 to 35 — removed `sample_point`, `set_color_range`, `benchmark_pipeline` as MCP tools
- Generalize `get_ground_z` for any structured grid
- Automated documentation extraction via `scripts/gen_docs.py`
- Comprehensive docstrings for all DSL forms and MCP tools
- Add server-layer test coverage (29 tests in `test_server_tools.py`)
- Remove MCP tool versions of `extract_component`, `make_vector`, `curl`
- Decompose `_create_volume()` and `PipelineBuilder.build()`
- Defer module-level side effects in `server.py`
- Standardize error return conventions in `queries.py`
- Scene annotations — `annotate()` and `clear_annotations()` with `vtkBillboardTextActor3D`
- Add bonsai CT dataset — 256³ uint8 volume; 19 verification tests
- 2D chart rendering — `render_chart()` with histogram and line charts
- Camera orbit / turntable — `camera_orbit()` multi-frame azimuth views
- Rich `describe_data` with percentiles, distribution shape, terrain detection
- `extract_component` helper and `compute_vorticity(vector=True)`
- `sample_line` / line probe — `profile` MCP tool, `line_probe` DSL method
- Conditional / subregion statistics — `query_stats(node, field, condition)`
- Batch point probing — `sample_points(node, points, fields)`
- Implement the missing `load()` MCP tool
- Fix `restore_version()` bug
- Restructure docs — `dsl-reference.md`, `mcp-reference.md`, `getting-started.md`
- Terrain-following grid detection in `describe_data` and `get_ground_z`
- Fix `run.py` return-value unpacking — corrected `(vtk_objects, node_names, node_statuses, show_statuses)` to `(vtk_objects_by_name, node_statuses, show_statuses, builder)`

## Dataset Sources

- [Open SciVis Datasets](https://klacansky.com/open-scivis-datasets/) — curated collection of volumetric datasets (CT scans, simulations) in raw format. Includes bonsai, hydrogen atom, nucleon, skull, foot, and ~30 others ranging from 67 KB to multi-GB.
