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

- [ ] Property-typo checking in `create_vtk_filter` — typo'd VTK kwargs (e.g.
  `ScalarArrays` instead of `InputScalarsSelection`) silently no-op or crash
  opaquely. Add `hasattr(vtk_class, "Set" + key)` check with a structured
  "unknown property on `vtkContourFilter`; valid: [...]" error. Half-day of
  work; directly caused the cascade-leak session example. Third pillar of the
  diagnostic spine.

- [ ] Inline field range in empty-output warnings — when a filter produces
  empty output, the build report says "Use describe_data() to verify field
  ranges." Instead, look up the relevant field's actual range and include it
  inline: "theta range is [298, 812] but your ThresholdRange was [1000, 2000]."
  Saves the agent a round-trip tool call and enables self-correction in one
  step. (Note: `vtkContourFilter` and `vtkThreshold` already include range info
  when out-of-range; this improvement is for other filter types and the volume
  rendering empty-output path.)

## Medium Priority

- [ ] DSL form vibecode pass — the DSL surface has accumulated inconsistencies
  the tool-count reduction didn't touch: (1) snake_case wrapper args mixed with
  CamelCase VTK passthrough in the same call; (2) `curl`'s positional
  `vector_field` + bool `vector=` flag (every other filter uses `input=`);
  (3) `clip` vs `clip_sphere`/`clip_box` `inside_out` polarity mismatch;
  (4) `extract_grid` and `extract_region` overlap; (5) three data-loading entry
  points (`source`, `load`, `raw_source`). Mirror of the MCP-tool style pass
  already done on the tool surface.

- [ ] Split `curl` into two wrappers; clean up Vorticity array leak —
  `curl(vector=True/False)` is one call with two completely different output
  schemas (3-vector vs scalar). Split into `curl_vector(...)` / `curl_magnitude(...)`
  so the output schema is visible at the call site. Also, drop or rename the
  leaked `Vorticity` (capital V) intermediate array that `curl(vector=True)`
  currently passes through — agents have been tripped up by the capitalization
  at least twice. Standalone fix from the broader NodeRef schema proposal;
  unambiguously worth doing regardless.

- [ ] Auto-include overview thumbnail in build responses — `camera_orbit`
  exists but is under-used because its trigger is metacognitive ("I am
  uncertain about 3D structure") rather than propositional. Agents don't
  reliably recognize when they need it. Fix: auto-include a small low-res
  overview-framed thumbnail alongside the main screenshot in every build
  response. Sidesteps the trigger problem entirely; low context cost; easy to
  evaluate over a few sessions.

- [ ] Vega-lite-style display-property inference — auto scalar_bar when
  `color_by` is set; diverging colormap + symmetric range for signed fields;
  auto scalar_bar title from field name. Enrich defaults rather than rely on
  the agent to know to ask. Same theme as the overview thumbnail: the right
  default behavior shouldn't require the agent to trigger it. Existing
  `describe_data` already detects signed fields.

- [ ] Reduce `run_pipeline` output verbosity — subsequent builds repeat full
  array lists for all nodes even when nothing changed. Add a terse mode
  reporting only what changed: "Pipeline v7 built. 7 nodes, all ok. Changes:
  updated threshold on 'fire'." Verbose on demand. Reduces per-turn context
  cost; pairs naturally with the reconciler diff work.

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

## Low Priority / Ideas

- [x] `"hot"` colormap preset referenced in DSL docstrings and examples (e.g. `show(..., lut="hot")`)
  but not defined in `colormaps.PRESETS`. Will silently fail at runtime with a `ValueError`.
  Fixed: replaced all 8 occurrences with valid presets ("fire" for flame/threshold contexts,
  "heat" for surface temperature display). Also fixed stale `set_colormap` mentions in
  server.py instructions and the removed-tools sentence in the show() docstring.

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
