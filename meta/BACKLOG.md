# VisLang Backlog

## High Priority

- [x] Fix cascade-leak in downstream builders — uniform skip-descendants-of-failed-nodes contract at `_build_pipeline` level; `{"status":"skipped","upstream":<id>}` on all descendants; both extract_region and extract_component audited (their `except Exception` blocks and upstream None-checks are intact); 15 new tests in `tests/test_cascade_skip.py`; existing `test_error_paths` updated to match new contract.

- [x] Unify wrapper validation into build-phase status channel — `extract_region`,
  `extract_component`, and `line_probe` validation errors (missing `bounds`,
  bad field/component, missing endpoints) now surface as `{"error": ...}` in
  `node_statuses` instead of raising `ValueError`. Fixed missing `_line_probe`
  dispatch in `_build_pipeline` (the method existed but was never called).
  Improved error messages in `extract_component` to include wrapper name,
  missing arg, and expected form. 18 new tests in `tests/test_wrapper_validation.py`;
  updated `test_coordinate_extract.py` and `test_line_probe.py` to match new contract.
  Full suite: 635 passed.

- [x] Property-typo checking in `create_vtk_filter` — typo'd VTK kwargs (e.g.
  `ScalarArrays`) now produce a structured error: "unknown property 'ScalarArrays'
  on vtkContourFilter\nsimilar: ScalarTree\nvalid: [...]". Implemented via
  `_validate_vtk_kwargs` + `_get_vtk_valid_setters` (lazy per-class cache) in
  `vislang/filters.py`. Special-case keys exempt. Error surfaces through
  `_build_generic_node` as `{"error": ...}` node status; cascade-skip propagates.
  22 tests in `tests/test_property_typo.py`. Note: `InputScalarsSelection` does
  not exist in this VTK version — similar match is `ScalarTree`. Third pillar done.

- [x] Inline field range in empty-output warnings — `_format_field_range_hint`
  and `_get_active_scalar_hint` helpers added to `filters.py`. Improved paths:
  `vtkClipDataSet` (already had it; now uses `_get_algorithm_output` for
  vtkTrivialProducer compat), `vtkThreshold` (now includes range even when
  ThresholdRange overlaps), `vtkProbeFilter` (new: active scalar range + spatial
  overlap check), volume rendering (raises ValueError when opacity_function
  control points are all-zero or entirely outside scalar_range with field hint),
  generic fallback for all other filter types (active scalar range or
  `describe_data()` fallback). 23 tests in `tests/test_empty_output_hints.py`.
  Full suite: 680 passed.

- [x] Structured per-node status schema — every node status dict now has
  "status" (ok/error/skipped/warning), "class", "kind", and "message" keys via
  helpers in `vislang/diagnostics.py`. Migrated all emitter sites (dsl.py
  extract_region/extract_component/line_probe/generic nodes + skipped cascade,
  filters.py create_vtk_filter ok/warning paths, extract_component status).
  Unknown-property errors now carry structured fields (property, vtk_class,
  similar, valid). Consumers in server.py updated to check status["status"]
  instead of "error" in s. 27 new tests in test_diagnostics_schema.py; all
  existing tests updated to use new schema. Full suite: 707 passed.

- [ ] Lazy view creation — server should not create `main` / open a window / start a watcher until `load()` or `new_view()` is called. See `meta/feedback/2026-04-27-headsq-hu-and-lazy-view-creation.md` (Part 2) for motivation, mapped lifecycle, and redesign sketch.

- [ ] Syntax errors should report column as well as line — `exec(code, namespace)`
  in `dsl.py` `interpret` / `interpret_build` raises `SyntaxError` with `.lineno`,
  `.offset`, `.text`, but the catch in `hot_reload.py:393` only formats
  `f"{type(exc).__name__}: {exc}"`. Plan: pass `ctx.pipeline_file` through as a
  `filename` arg, replace `exec(code, ns)` with `exec(compile(code, filename,
  "exec"), ns)`, and special-case `SyntaxError` in the catch to format
  `SyntaxError at {filename}:{lineno}:{offset}: {msg}` with an optional caret
  line from `exc.text`. Stretch: use `traceback.extract_tb` to surface
  user-frame line numbers for runtime errors (NameError/TypeError) too.

- [ ] Trim noisy `valid:` list in unknown-property errors — `_validate_vtk_kwargs_structured`
  in `filters.py` currently lists every `Set*` method on the class hierarchy,
  which for filters like `vtkStreamTracer` floods the user with generic
  `vtkObject`/`vtkAlgorithm` plumbing (`AbortExecute`, `Debug`,
  `GlobalWarningDisplay`, `Executive`, `InputArrayToProcess`, `InputConnection`,
  `InputData`, ...) and `*To*` enum-shortcut methods
  (`IntegrationDirectionToBackward`, `IntegratorTypeToRungeKutta4`, ...). Plan:
  subtract setters defined on `vtkAlgorithm` and `vtkObject`; drop names
  matching `*To<CapWord>` enum-toggle pattern; prepend the DSL framework keys
  from `filters.py:29` (`input`, `source`, `result`, ...) so misspellings of
  `input` land near the top of `similar:`. Example: misspelling `input` as
  `iput` on `stream_tracer` should suggest `input` first instead of nothing.

## Medium Priority

- [ ] Investigate and fix slow tests — full pytest run takes minutes when most
  individual files complete in <2s. `test_mcp_protocol.py` alone took ~37s in
  one chunked run. Profile with `pytest --durations=20` to identify outliers,
  then either parallelize (pytest-xdist), trim per-test renderer setup, or
  share fixtures across cases that currently rebuild VTK state from scratch.

- [ ] Fix `tests/test_stateful_integration.py` segfault on macOS — segfaults in
  `vtkCocoaRenderWindow::CreateAWindow` → `[NSWindow initWithContentRect:...]`
  when run under pytest. NSWindow initialization must happen on the main
  thread on macOS; the test appears to construct a real `Renderer` (or
  triggers code that does) off the main thread. Failing locally on macOS
  (Darwin 23.6, Python 3.14, VTK from venv). Likely fix: the test should use
  `_FakeRenderer` end-to-end and never instantiate `vislang.renderer.Renderer`,
  or the renderer init path should refuse to run off-main-thread instead of
  hard-segfaulting.

- [ ] DSL form vibecode pass — the DSL surface has accumulated inconsistencies
  the tool-count reduction didn't touch: (1) snake_case wrapper args mixed with
  CamelCase VTK passthrough in the same call; (2) `curl`'s positional
  `vector_field` + bool `vector=` flag (every other filter uses `input=`);
  (3) `clip` vs `clip_sphere`/`clip_box` `inside_out` polarity mismatch;
  (4) `extract_grid` and `extract_region` overlap; (5) three data-loading entry
  points (`source`, `load`, `raw_source`). Mirror of the MCP-tool style pass
  already done on the tool surface.

- [x] Split `curl` into two wrappers; clean up Vorticity array leak —
  `curl_vector(...)` and `curl_magnitude(...)` replace the old `curl(vector=True/False)`
  API. The VTK-internal capital-V `Vorticity` array is now renamed by a
  `vtkArrayCalculator` pass before it reaches user code; output names are
  snake_case (`vorticity`, `vorticity_magnitude`). Old `curl` fully removed —
  no shim. All tests, demos, and docs updated. Full suite: 719 passed.

- [ ] Surface camera staleness to the model on its next turn — when the human
  moves the camera in the live window, the model's last screenshot no longer
  matches what the human sees, and there's currently no signal short of the
  model defensively re-screenshotting (which the MCP instructions already
  nudge it to do). Don't push a `<channel>` event on every camera change —
  that would force a turn for idle fiddling. Instead, latch the latest
  camera state (cheap monotonic version counter bumped by the VTK
  interaction callback) and lace it into the next tool result the model
  receives: e.g. every state-changing tool's response and `screenshot()`
  itself include `camera_version: N` and, when N differs from the version
  at the model's last image, a one-line "human moved the camera since your
  last screenshot" hint. Model decides whether to re-screenshot. Sidesteps
  the "interrupt vs. silence" tradeoff. If we later want push-on-change
  for non-tool-call moments (e.g. after a long idle), the Claude Code
  channels protocol (`claude/channel` capability + `notifications/claude/channel`)
  is the right primitive — see `docs/en/channels-reference` upstream.
  Channels are research-preview / allowlist-gated as of v2.1.80, so the
  latch-and-piggyback approach ships first.

- [ ] Auto-include overview thumbnail in build responses — `camera_orbit`
  exists but is under-used because its trigger is metacognitive ("I am
  uncertain about 3D structure") rather than propositional. Agents don't
  reliably recognize when they need it. Fix: auto-include a small low-res
  overview-framed thumbnail alongside the main screenshot in every build
  response. Sidesteps the trigger problem entirely; low context cost; easy to
  evaluate over a few sessions.
  **Implementation constraint**: the thumbnail MUST be captured via a
  separate offscreen `vtkRenderWindow` — not by mutating the live interactive
  window's camera/size and restoring. A previous attempt (commit `9aefe70`,
  rolled back) resized 640→256→640 and moved the camera on the live window,
  causing a visible flash on every re-render. The offscreen window can share
  the renderer's actor list (or copy on demand); the user-facing window must
  never be touched. Consider also skipping the thumbnail entirely in
  INTERACTIVE / HEADLESS_INTERACTIVE modes if the offscreen-window approach
  proves complex — agents only need the thumbnail in the MCP response, and
  humans watching the live window don't.

- [x] Vega-lite-style display-property inference — auto scalar_bar when
  `color_by` is set; diverging colormap + symmetric range for signed fields;
  auto scalar_bar title from field name. `_infer_display_defaults` in
  `filters.py:1501`; `_humanize_field_name` helper; `create_show` calls
  inference at build time; `show()` docstring updated; 25 new tests in
  `tests/test_display_defaults.py`. Diverging preset: `cool_to_warm`.
  Full suite: 768 passed.

- [x] Reduce `run_pipeline` output verbosity — terse mode (default) for
  subsequent builds reports only what changed: "Pipeline v7 ok. 3 nodes.
  rebuilt 'thresh', rebuilt 'surf'. Cache: 1 hits, 2 misses. Took 15 ms."
  `run_pipeline(verbose=True)` returns the full per-node listing.
  Errors/warnings always emit verbose path. Diff detection uses `cached: True`
  flag from BuildCache to identify hits vs misses. 24 new tests in
  `tests/test_terse_report.py`. Verbose report stored as `record.verbose_report`.
  Tests in `test_headless_interactive.py` and `test_stateful_integration.py`
  updated for new format. Full suite: 743 passed.

- [x] File-watching hot reload with status file — complete. `vislang/hot_reload.py`
  implements `BuildCoordinator` + `PipelineWatcher`. Watcher detects file saves
  (including atomic renames); coordinator runs builds on a single worker thread,
  marshals renderer ops to main thread via `run_on_main_thread()`, writes
  `view-{name}.status.json` after every build. `run_pipeline()` MCP tool delegates to
  `coordinator.wait_for_current()`. New `pipeline_status()` tool for non-blocking peeks.
  Cold build ~41ms; warm (same content) ~0.1ms; visual param change ~1ms;
  partial-cache rebuild ~13ms. 17 tests pass in `tests/test_hot_reload.py`.

- [x] Hot reload simplification pass — unified `_cv` Condition (replaces `_lock` +
  `_work_event` + per-record `_done_event`); `_pending: Optional[BuildRecord]` replaces
  triplet; `BuildRecord.code`/`.wait()`/`._finish()` removed; `applied_hash` on
  `ViewContext` tracks renderer state; `save_version` moved to `ViewContext` method;
  `_take_screenshot` inlined; `_save_version_for` moved to `ViewContext.save_version`;
  `start_hot_reload()` explicit method (no thread spawn in `__init__`); `_read_file`
  retries on `FileNotFoundError` (atomic-rename saves); cancelled-record semantics for
  displaced pending builds; `pipeline_status` returns real JSON from status file;
  `run_pipeline`/`pipeline_status`/`restore_version` docstrings rewritten; HOT RELOAD
  section added to `FastMCP(instructions=...)`; cache stats surfaced in build report;
  23 tests pass.

- [x] Gamma-inspired test coverage + hash determinacy + what's-fast docs — 25 new tests
  covering let-intro-var, reorder, whitespace-only, append-tail, partial-edit cache hits,
  file-mtime invalidation, dict-key-order invariance, int/float distinctness, numpy scalar
  coercion, array repeatability, and unhashable fallback. WHAT'S INCREMENTAL table in
  server instructions; `pipeline_status` docstring updated; `dsl.py` module docstring
  notes incremental/content-addressed behavior. Docs regenerated. All 48 target tests pass.

- [ ] VISION.md refresh — Part 1 says "~35 tools" (actual: 25); lists
  `get_examples()`/`list_capabilities()` (both gone, folded into
  `get_dsl_overview`/`get_dsl_reference`); still describes `suggest_opacity()`
  as a query tool (removed); architecture diagram still shows removed mutation
  tools; named views not mentioned as a foundational feature. Human-reviewed
  edit, not autonomous. Worth doing soon to avoid further orientation drift.

- [ ] Reconciler-based pipeline updates — diff old vs new actor sets and
  apply only the minimal changes (opacity, color, visibility) without a full
  rebuild. A `SceneReconciler` prototype lives in `experiments/tracked-execution/`
  but has NOT been integrated into `vislang/`. Needed primarily when the terse
  output mode requires knowing what actually changed. Low urgency until terse
  mode lands.

- [ ] Split `server.py` into modules — ~2,260 lines, 25 tool handlers + pipeline
  execution + DSL doc strings + session state. Deliberately deferred until the
  tool surface stabilizes. No split started yet.
  Don't split until after the diagnostic spine and DSL surface cleanup land —
  otherwise we move the same code twice.

- [ ] Window-closed detection doesn't work in interactive mode — `list_views`
  never shows `[window closed]` after user closes OS windows; `focus()` and
  `screenshot()` silently render into dead window buffers. `GetMapped()` may
  not behave as assumed on macOS/Cocoa. Needs investigation and a reliable
  detection mechanism.

- [ ] Fix UI freeze during `set_pipeline` on other views — interactivity in
  already-open windows freezes while a pipeline builds in another view. Likely
  GIL contention during pipeline execution blocking the VTK event loop.

- [ ] Follow-ups from the 2026-04-26 hot-reload + diagnostic-spine session —
  cluster of questions raised but not chased during that session. Each is
  small-to-medium effort. Worth a single design pass to triage before
  implementing.
  - **Terse mode doesn't diff params.** The terse report says "rebuilt 'thresh'"
    but doesn't actually compare `min_value=200→500` between successive
    versions. The original brief asked for that; the implementation shipped a
    coarser hits-vs-misses summary keyed on `cached: True`. Add real per-node
    param diffs (the structural hash already lets us identify which params
    changed; just needs to be surfaced).
  - **`bar.GetTitle() == ''` mystery in display defaults.** `_style_scalar_bar`
    explicitly calls `bar.SetTitle("")` because the title is rendered via a
    separate `vtkTextActor` overlay. Tests had to be changed to not assert on
    `bar.GetTitle()`. Investigate why the design uses a separate text actor —
    is it for layout flexibility, or a workaround for a VTK quirk? Either fold
    the title back into the bar, or document why the split exists.
  - **Coarse warning `kind` taxonomy.** The structured-status migration
    collapsed all warnings to `KIND_EMPTY_OUTPUT`. Specific cases like
    "field range doesn't include user's threshold value" or "spatial
    non-overlap on probe filter" carry rich structured fields but share one
    kind. Adding `KIND_FIELD_OUT_OF_RANGE`, `KIND_SPATIAL_NO_OVERLAP`, etc.
    would let agents pattern-match for self-recovery.
  - **Audit for other dead `_build_*_node` dispatch branches.** Pillar-2's
    `_build_line_probe_node` existed but was never reached because
    `_build_pipeline` didn't dispatch to it. Grep for similarly-orphaned
    methods.
  - **Hot-reload shutdown from main render thread.** Currently documented as
    an invariant ("don't call shutdown() from the renderer's main thread
    while a build is mid-render-phase"). Could be made bulletproof by having
    `shutdown()` drain the main-thread work queue while waiting for the
    worker — instead of just timing out.
  - **Flaky `test_valid_pipeline_after_error_succeeds` under xvfb
    contention.** Passes in isolation; fails sporadically when full suite
    runs concurrently with other GLX work. Likely a test-infrastructure
    issue (xvfb display sharing) rather than a real bug. Either isolate or
    skip-with-reason.

- [x] `"hot"` colormap preset referenced in DSL docstrings and examples (e.g. `show(..., lut="hot")`)
  but not defined in `colormaps.PRESETS`. Will silently fail at runtime with a `ValueError`.
  Fixed: replaced all 8 occurrences with valid presets ("fire" for flame/threshold contexts,
  "heat" for surface temperature display). Also fixed stale `set_colormap` mentions in
  server.py instructions and the removed-tools sentence in the show() docstring.

- [x] Consolidated VTK introspection helpers + synthetic-data test fixture —
  `vislang/_vtk_introspect.py` centralizes `find_field_array`, `get_algorithm_output`,
  `get_algorithm_input`, `vtk_setter_names`; all call sites in `filters.py` and
  `queries.py` updated; `synthetic_vti_path` pytest fixture in `conftest.py`;
  `make_image_data` helper promoted to `conftest.py`; three test files use fixture.
  Net: -85/+32 lines in `filters.py`+`queries.py`.

- [ ] Empty-output diagnostics registry pattern — `filters.py:629–703` is a
  fragile `if/elif` chain. Refactor into `filter_class → diagnostic_fn`
  registry. Adding "why is your `vtkProbeFilter` empty" becomes one new
  function, not a new branch. Unblocks broader coverage and is the right
  precursor to any exec-style inspection tool.

- [ ] NodeRef output schema (scoped) — declare per-wrapper what arrays are
  added/removed; validate field-accepting kwargs at `interpret()` time for
  high-traffic wrappers (`color_by`, `ContourBy`, `ThresholdBy`,
  `extract_component`, `field=`). Catches field-name typos before VTK runs.
  Design question flagged in reflection: the current build-phase feedback
  already works; weigh ROI carefully. Don't try to make it total — fall back
  to build-phase validation for `filter()` and unknown VTK classes.

- [ ] `start_session` tool and `file_source` DSL form — replace `load` with a
  `start_session(data_file)` tool that creates the session directory, writes
  an initial pipeline, executes it, and returns `describe_data` output. Fixes
  the chicken-and-egg problem where query tools fail on fresh views because no
  pipeline is active yet.

- [ ] Remove auto-screenshots from state-changing tools — mutation tools
  currently return base64 screenshots via `_with_screenshot()`. In long
  sessions this accumulates tens of MB in Claude's context. Remove from
  mutation tools; add `resolution` parameter to `screenshot()`; update
  server instructions to guide parallel tool calls.

- [ ] Multi-panel layouts — no layout tool for compositing multiple renders
  into a single image. Useful for comparison workflows; mentioned independently
  in three separate sessions.

- [ ] LSP for the pipeline DSL — autocomplete for field names from the loaded
  dataset, hover info showing field ranges, inline diagnostics. Backend query
  layer already exists; LSP is another delivery channel. Depends on hot reload
  infrastructure. Longer-term; log it to avoid losing the design.

- [ ] Jupyter notebook with trame interactive views — after exploration, Claude
  assembles a notebook where each cell runs a VisLang pipeline and displays an
  interactive 3D trame view. Requires a small `vislang.notebook.show(code)` API.

- [ ] Autonomous feedback loop via Anthropic Agents SDK — outer Claude launches
  a subagent that connects to the VisLang MCP server, explores a task end-to-
  end, then reflects to identify improvements. Makes the gather-feedback /
  reflect-design cycle fully autonomous.

## Completed

- Dead-code sweep (2026-04-25): removed unused imports (`vtk`, `interpret`, `COMPONENT_NAME_MAP`,
  `math`, inline `typing`) and dead `Renderer` methods (`remove_actor`, `add_overlay`).

- Annotations redesign — replaced `annotate()`/`clear_annotations()` MCP tools with a
  declarative `annotate(x, y, z, text, color, font_size)` DSL method; fixed bounds
  distortion with `UseBoundsOff()`; added to DSL overview and docs; removed dead
  `_parse_color`; hardened `_coerce_color`.
- Remove `list_actors`, `get_actor_info`, `render_chart`, `quick_start` features
- Remove `suggest_opacity` tool; replace with CDF-rarity algorithm in `suggest_isosurface`
- Redesign scalar bars: horizontal, bottom-right, pixel-anchored
- Remove redundant mutation tools (`set_colormap`, `set_opacity`, `toggle_visibility`)
- Fix `KeyError` when property-referenced node fails to build
- `axes()` DSL form — labeled X/Y/Z axes with tick marks
- `gen_docs.py` errors on stale DSL form names; catches doc/code drift
- Auto-populate DSL namespace from `PipelineBuilder` methods via `inspect.getmembers()`
- Add percentiles to `describe_data` and introduce `describe_data(field=)` mode
- Move field-name validation before `Update()` — typos caught pre-execution with "did you mean?" list
- Add MCP protocol-level tests (105 tests); add stateful integration tests (24 tests)
- Separate 2D overlay actors from 3D scene actors in Renderer
- Remove legacy global state and `_LegacyCtx` shim; add `_init_for_test()` helper
- Fix `title()` actors not cleared on `set_pipeline` rebuild
- Remove DSL aliases (`isosurface`, `compute_velocity`, `compute_vorticity`)
- Remove vestigial `node` parameter from `camera_orbit`
- Remove dead code in `server.py` (undecorated `sample_point`, `set_color_range`, `benchmark_pipeline`)
- Fix `quick_start` tool — removed entirely (the two-step dance was unnecessary friction)
- Detect user-closed windows in offscreen mode via `Renderer.is_window_closed()`
- Expand VTK class whitelist to 119 classes
- Unify histogram-guided opacity logic into `_auto_opacity()`; extract `_build_scalar_bar()` helper
- Fix `get_ground_z` output; add `layers=False` parameter
- Minor `server.py` cleanups — `_parse_color` to module level, dedup `GetDimensions()`, single top-level `import vtk`
- Consolidate DSL discovery into `get_dsl_overview()`; merge `list_capabilities` / rename `get_examples`
- Multiple named views — `new_view`, `focus`, `close_view`, `list_views`, `ViewContext`
- Per-view pipeline files; version history with rollback
- Conditional / subregion statistics — `query_stats(node, field, condition)`
- Rich `describe_data` with percentiles, distribution shape, terrain detection
- Reconciler prototype (`SceneReconciler`) built in `experiments/tracked-execution/` but not integrated into main vislang (moved to active backlog)
- Scene annotations, camera orbit, 2D chart rendering, `sample_line` / line probe
- Batch point probing — `sample_points(node, points, fields)`
- Automated documentation extraction via `scripts/gen_docs.py`
- Restructured docs — `dsl-reference.md`, `mcp-reference.md`, `getting-started.md`
- `extract_component` helper, `make_vector`, `curl`, `gradient` DSL forms
- Implement the missing `load()` MCP tool; fix `restore_version()` bug
- Replace Python loops with numpy in `queries.py`
- Consolidate duplicated constants; extract node-lookup boilerplate into `@requires_data` decorator
- Standardize API parameter naming across tools
- Add bonsai CT dataset (256³ uint8 volume; 19 verification tests)
- Terrain-following grid detection in `describe_data` and `get_ground_z`
- Remove dead `get_statistics` function; fix stale references to use `describe_data`
