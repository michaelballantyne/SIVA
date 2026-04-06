# VisLang Backlog

## High Priority

- [ ]  Consolidate DSL discovery into a single entry point: Rename get_examples
to get_dsl_overview and merge list_capabilities into it (workflow patterns on
top, full form index with one-line descriptions below). Add "see X instead"
cross-references in get_dsl_reference for easily confused forms (e.g.
extract_region → slice). Update the MCP server WORKFLOW instructions to call
get_dsl_overview() as step 1. Goal: the LLM sees the complete toolkit before
writing its first pipeline, not just the subset the examples happen to use.
- [ ] `start_session` tool, `file_source` DSL form, and session/data path separation — Add a `file_source(filename)` DSL form that infers the VTK reader class from the file extension (moving the detection logic currently in `load()` into the DSL). Replace `load` with a `start_session(data_file, session_dir=None)` tool that (1) creates and sets the session directory for all artifacts (logs, version history, `view-*.py` pipeline files, screenshot temps), (2) writes an initial `view-main.py` containing a `file_source(...)` call and executes it, and (3) returns the `describe_data` overview. The agent gets data immediately and a pipeline file to build on. `session_dir` defaults to cwd when omitted. All cwd-relative `Path(...)` and `glob.glob(...)` calls in `server.py` need to resolve against the session directory instead. Rename `.vislang/` to use visible (non-dotfile) paths so session contents are easy to browse — e.g. `history/`, `server.log`, etc. Add a `path` parameter to `list_data_files` so agents can search a specific directory for data (e.g. `list_data_files(path="datasets/wildfire/data")`). Together these let an agent launch the server from any directory and control where session state vs. data live without `cd` tricks or CLI args. Nothing should be hardcoded to the VisLang source tree — both paths are agent-supplied.
- [ ] Remove auto-screenshots from state-changing tools to fix context bloat — Currently every mutation tool (set_pipeline, set_camera, set_colormap, toggle_visibility, etc.) auto-returns a base64 screenshot via `_with_screenshot()`. In long sessions this accumulates tens of MB of image data in Claude's context, eventually hitting the 20MB API request limit. Fix: (1) Remove `_with_screenshot()` from all mutation tools so they return text only. (2) Add `resolution` parameter to `screenshot()` with "low" (e.g. 640x480) and "high" (1920x1080) options, defaulting to low. (3) Update server instructions to guide Claude to call screenshot() in the same turn as a state-changing tool when it wants to see the result (parallel tool calls), start with low-res, and use high-res only when detail is needed. (4) `camera_orbit` and `render_chart` also need adjustment — return file paths or reduce resolution.
- [ ] Merge `get_statistics` into `describe_data` — Add an optional `field` parameter to `describe_data` so `describe_data(field="ImageFile")` returns rich stats (percentiles, distribution shape) for just that field. Then remove `get_statistics` as a separate tool — it's strictly less informative than `describe_data`'s per-field output. `describe_data` already accepts a `node` parameter, so per-node stats are already supported.
- [ ] Remove mutation tools in favor of pipeline file edits — Remove `set_colormap`, `set_opacity`, `toggle_visibility`, `set_background`, `annotate`, `clear_annotations` — Claude edits the `show()` / `background()` lines in the pipeline file and calls `set_pipeline` (or hot reload picks it up if available). This eliminates the divergence where mutation tools change VTK state without updating the pipeline file (making the file lie about what's rendered). `set_camera` is the exception — camera is interactive (human rotates with mouse) and shouldn't live in the file unless explicitly frozen.
- [ ] File-watching hot reload with status file — Watch pipeline files for changes and auto-rebuild on save. Write build output (success summary or error) to a status file next to the pipeline file with a matching name (e.g. `view-main.py` -> `view-main.status.txt`) so the human can see it in a split view. Must work with multiple views, each getting its own status file. Replaces `set_pipeline` as the primary build trigger — Claude just writes the file and reads the status file afterward; the human just saves and checks status. `screenshot()` should also include the latest build status in its text result. Update server instructions to guide Claude to read the status file after writing a pipeline file to check for errors before taking a screenshot — avoids wasting an image on a broken build.
- [ ] Reconciler-based pipeline updates — Instead of rebuilding the entire VTK pipeline on every `set_pipeline`, diff the old and new pipeline specs and apply only the changes (add/remove/update nodes). This avoids re-reading data and re-executing unchanged filters, making incremental edits fast. Similar in spirit to a React reconciler — the pipeline spec is the declarative target, the reconciler figures out the minimal mutations to VTK state.

## Medium Priority

- [ ] Separate 2D overlay actors from 3D scene actors in Renderer — `_actors` dict currently mixes vtkActors (3D geometry) with vtkScalarBarActors (2D overlays). Any code that iterates `_actors` and calls `GetBounds()` must guard against `None` returns, and future bounds/picking/iteration logic will hit the same issue. Store scalar bars (and any other 2D annotations) in a separate `_overlays` dict so 3D-only operations like `suggest_camera`, bounds computation, and actor enumeration don't need per-item type checks.
- [ ] Interactive-mode test harness — Launch server under xvfb without `--offscreen`, exercise multi-view and concurrency via JSON-RPC over stdio, verify windowing with xvfb framebuffer captures (`import -window root`), and optionally simulate mouse input with xdotool. Key scenarios: multi-view create/switch/pipeline, concurrent mouse interaction during pipeline rebuild. See `meta/TESTING.md` "Testing interactive mode headlessly" for details.
- [ ] Add MCP protocol-level tests — Call every `@mcp.tool` function through the actual MCP protocol with minimal valid inputs. Verify responses serialize without errors and match declared return types. Would have caught the bonsai session's Pydantic validation bug. See `meta/TESTING.md` level 3.
- [ ] Add stateful integration tests — Test sequences of operations that exercise server state: multi-view create/switch/verify, version history set/modify/restore, combined load/query/pipeline/query-filtered-node workflows. Call Python functions directly (no MCP protocol needed). See `meta/TESTING.md` level 2.
- [ ] Move validation before pipeline execution — Currently field name checks and empty output detection happen after `Update()` (the expensive data push). The pipeline graph build itself is cheap, so we can validate between build and execute: check field names against source metadata, verify data type compatibility, warn on out-of-range parameters. For small data this barely matters, but for large data it avoids waiting 30s only to discover a typo. Start with field name validation since that's the most common error.
- [ ] Remove alias DSL operations — Audit all DSL forms and remove any that are simple aliases of other operations (i.e. thin wrappers that add no logic beyond renaming). The DSL should expose a minimal basis of orthogonal operations. Identify which forms are aliases, document what they map to, then remove them and update examples/docs to use the underlying form directly.
- [ ] Isosurface + volume rendering composite — A natural CT visualization pattern ("leaves via direct volume rendering, trunk/branches via isosurface") had no clear DSL idiom. The DSL supports both representations but there's no documented pattern for layering them on the same data. Add an example to `get_dsl_reference` and `get_examples` showing multi-representation overlays, and verify the pipeline actually works for this case.
- [ ] `server.py` module split — at ~1,700 lines, `server.py` mixes MCP setup, global state, 35 tool handlers, pipeline execution, and examples. Suggested split: `server_state.py` (globals, `_get_data`, helpers), `tools_query.py`, `tools_mutate.py`, `tools_meta.py`, with `server.py` as a thin entry point. Previously attempted but stalled; mark `[~]` until resumed.
- [ ] Multi-panel layouts — Side-by-side views showing different fields or representations on the same geometry. Named views (`new_view`, `focus`) are implemented, but there's no layout tool for compositing multiple renders into a single image. Useful for comparison workflows; mentioned independently in sessions 1, 2, and bonsai.
- [ ] View title overlay — Add a `title("text")` DSL form (or parameter on `new_view`) that renders a text label inside the render window at the top, using a 2D text actor (e.g. `vtkTextActor`). macOS window titles are barely visible, so an in-window overlay is the primary mechanism. Also set the OS window title (`SetWindowName`) to include the view name for taskbar/alt-tab identification.
- [ ] Expand VTK class whitelist — The `source()` and `filter()` DSL forms restrict which VTK classes can be instantiated. The current list is conservative and missing common sources/filters (e.g. `vtkConeSource`, `vtkCylinderSource`, `vtkAppendPolyData`, `vtkCleanPolyData`). Audit all VTK source/filter classes, add anything useful that doesn't have dangerous side effects (file I/O, system access). The whitelist exists as a security boundary since pipeline code is `exec()`'d.
- [ ] In-plane vector glyphs on slices — `show_vectors()` for flow visualization on cross-sections. Useful for any vector field dataset (velocity, vorticity); not yet implemented.

## Low Priority / Ideas

- [ ] Multi-timestep support — Discover sibling timesteps, animate, compare across time. The wildfire dataset is single-timestep; this remains unvalidated.
- [ ] Jupyter notebook with trame interactive views — After exploration, Claude assembles a summative notebook where each cell runs a VisLang pipeline and displays an interactive 3D trame view. Requires a small `vislang.notebook.show(code)` API. Lower priority than active session capabilities.
- [ ] Autonomous feedback loop via Anthropic Agents SDK — Build a harness using the Agents SDK where an outer Claude launches a subagent that connects to the VisLang MCP server, explores a visualization task end-to-end, then the outer Claude reflects on the subagent's session to identify design improvements. The Agents SDK enables this to run on cloud infrastructure without a human restarting the MCP server on each code edit or bug fix — the harness can manage the server lifecycle programmatically. This would make the current gather-feedback / reflect-design cycle fully autonomous and continuous.

## Completed

- [x] Fix multi-view crash in interactive mode — shared work queue across renderers, fixed `suggest_camera` crash on scalar bar actors, fixed `focus()` return type validation. Also split pipeline compute (MCP thread) from scene update (main thread) so interaction stays responsive during rebuilds.
- [x] `RenderMode` enum and `--headless-interactive` — replaced boolean flags with `OFFSCREEN`/`INTERACTIVE`/`HEADLESS_INTERACTIVE` enum. Headless interactive mode exercises the real threading path without a display, for automated testing.
- [x] Multiple named views — Implemented `new_view(name)`, `focus(name)`, `close_view(name)`, `list_views()`. `ViewContext` class bundles per-view state. All tools use `_current_ctx()` helper.
- [x] Per-view pipeline files — Each view has its own `view-<name>.py` file.
- [x] Replace Python loops with numpy in `queries.py` — `get_statistics()`, `get_histogram()`, `get_spatial_extent()` converted to numpy path for 10-100x speedup.
- [x] Fix `get_statistics()` missing cell-data arrays in error messages — Error hint now lists arrays from both point and cell data.
- [x] Consolidate duplicated constants — `_scalar_type_map`, `_component_name_map`, and reader extension maps unified.
- [x] Extract node-lookup boilerplate into a helper — `@requires_data` decorator / `_get_data_or_error()` helper eliminates ~64 lines of duplication.
- [x] Standardize API parameter naming — consistent parameter names across tools.
- [x] Reduce tool count by merging redundant tools — removed `sample_point`, `set_color_range`, `benchmark_pipeline` as MCP tools. Tool count reduced from 38 to 35.
- [x] Generalize or gate `get_ground_z` — generalized for any structured grid.
- [x] Automated documentation extraction — `scripts/gen_docs.py` writes `docs/` files from docstrings.
- [x] Restructure docs to clarify DSL vs MCP layers — `dsl-reference.md`, `mcp-reference.md`, `getting-started.md`.
- [x] Comprehensive docstrings for all DSL forms and MCP tools.
- [x] Add server-layer test coverage — 29 tests in `tests/test_server_tools.py`.
- [x] Remove MCP tool versions of `extract_component`, `make_vector`, `curl` — DSL versions preferred.
- [x] Decompose `_create_volume()` and `PipelineBuilder.build()` — broken into smaller functions.
- [x] Defer module-level side effects in `server.py` — `_parse_args()` and `Renderer` creation moved into `main()`.
- [x] Standardize error return conventions in `queries.py` — all errors prefixed with "Error: ".
- [x] Scene annotations — `annotate()` and `clear_annotations()` MCP tools using `vtkBillboardTextActor3D`.
- [x] Add a second dataset (bonsai CT scan) — 256³ uint8 volume; 19 verification tests.
- [x] 2D chart rendering — `render_chart()` supports "histogram" and "line" charts via matplotlib.
- [x] Camera orbit / turntable — `camera_orbit()` returns multi-frame azimuth views.
- [x] Auto-screenshot from state-changing tools — `_with_screenshot()` wrapper (NOTE: being reverted due to context bloat and return-type bugs; see high-priority item above).
- [x] Rich `describe_data` with percentiles — p1/p25/p50/p75/p99, distribution shape, terrain detection.
- [x] `extract_component` helper and `compute_vorticity(vector=True)`.
- [x] `sample_line` / line probe — `profile` MCP tool, `line_probe` DSL method.
- [x] Conditional / subregion statistics — `query_stats(node, field, condition)`.
- [x] Batch point probing — `sample_points(node, points, fields)`.
- [x] Implement the missing `load()` MCP tool.
- [x] Fix `restore_version()` bug.

## Dataset Sources

- [Open SciVis Datasets](https://klacansky.com/open-scivis-datasets/) — curated collection of volumetric datasets (CT scans, simulations) in raw format. Includes bonsai, hydrogen atom, nucleon, skull, foot, and ~30 others ranging from 67 KB to multi-GB.
