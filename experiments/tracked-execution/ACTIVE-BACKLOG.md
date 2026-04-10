# Tracked Execution — Active Backlog

Items for the current independent work session (2026-04-10).

## Do Now

### 1. Offscreen rendering validation
- [ ] Test SceneReconciler with a real `pv.Plotter(off_screen=True)`
- [ ] Pipeline → render → screenshot → modify pipeline → re-render → verify image changed
- [ ] Verify reconciler correctly adds/removes/updates actors on a real plotter
- [ ] Test with xvfb-run wrapper for headless environments
- [ ] Write `examples/demo_offscreen_render.py` showing the full loop

### 2. Wildfire dataset end-to-end
- [ ] Download wildfire data (`datasets/wildfire/download.sh`)
- [ ] Write a pipeline that loads the VTS, thresholds fire region, volume renders
- [ ] Run through tracked execution with offscreen rendering
- [ ] Use inspect_pipeline for data exploration
- [ ] Verify caching works (re-run with threshold change, read cached)
- [ ] Screenshot comparison across runs

### 3. MCP server (`experiments/tracked-execution/mcp_server/`)
Build a standalone MCP server wrapping tracked execution. Separate from
the outer VisLang MCP. Architecture:

**MCP tools:**
- `set_working_directory(path)` — set the working dir for all file operations.
  Error if called after any view has been created.
- `create_view(pipeline_file)` — create a named view (name = filename) with
  a native VTK offscreen plotter. Starts file watching on the pipeline file.
  Writes to the file trigger execute_pipeline + reconcile + render.
  Returns describe_data-style info about what loaded.
- `inspect(pipeline_file, code)` — run inspect_pipeline against the DAG
  for the named view. Returns captured print output.
- `screenshot(pipeline_file)` — capture the current render of the named view.
  Returns base64 PNG.

**Server instructions (initial MCP description):**
- Overview of the tool and its purpose
- How to create and iterate on visualizations
- The caching model (why edits are fast)
- Always specify `scalars=` in threshold/contour calls
- How to use inspect for data exploration
- How to use vtk_escape for custom VTK filters

**Multi-view architecture:**
- Each view has its own: DAG, Plotter, SceneReconciler, file watcher
- Views are keyed by pipeline filename (basename)
- File writes by the agent trigger re-execution automatically
- Multiple views share nothing (separate DAGs — cache isn't shared across views yet)

**Implementation plan:**
- [ ] MCP server skeleton using `mcp` Python package (FastMCP)
- [ ] `set_working_directory` tool
- [ ] `create_view` tool — creates Plotter, DAG, watcher, runs initial execute
- [ ] `inspect` tool — runs inspect_pipeline on the view's DAG
- [ ] `screenshot` tool — captures and returns base64 PNG
- [ ] Server instructions string with agent guidance
- [ ] File watching integration — watcher triggers re-execute on save
- [ ] Error handling — pipeline errors return friendly messages, don't crash server
- [ ] Multi-view state management (dict of ViewState objects)
- [ ] Test with simulated JSON-RPC calls

### 4. Multi-view caching updates
- [ ] Currently each view has its own DAG. If two views read the same file,
      the data is loaded twice. Consider a shared read cache across views
      (keyed by absolute path + mtime).
- [ ] The GC currently evicts everything not in the current run. With multi-view,
      a shared cache needs to track which views reference which entries.

### 5. End-to-end agent test — wildfire
- [ ] Subagent connects to MCP server (simulated JSON-RPC)
- [ ] Downloads wildfire data
- [ ] Creates a view, writes initial pipeline
- [ ] Explores data with inspect
- [ ] Iterates on threshold/colormap 3-4 times
- [ ] Takes screenshots
- [ ] Log the full interaction
- [ ] Reflect on what worked and what didn't → new backlog items

### 6. End-to-end agent test — CT data
- [ ] Use bonsai or foot dataset
- [ ] Same flow: create view, explore, iterate, screenshot
- [ ] Different visualization patterns (isosurface, volume rendering)
- [ ] Log and reflect

### 7. Cleanup and documentation rounds
- [ ] After each major feature: simplification pass
- [ ] After each agent test: reflect, create improvement items
- [ ] Keep AGENT-GUIDE.md and README.md current
- [ ] Ensure coherent working commits throughout

## Later

- [ ] Shared read cache across views (multi-view optimization)
- [ ] Trame viewer integration (browser-based alternative to native VTK)
- [ ] Fix active_scalars_name hashing for scalar-sensitive methods
- [ ] Defensive copy option for cached filter outputs (VTK passthrough hazard)
- [ ] Pydantic Monty integration when opaque objects are supported
- [ ] Mini LSP for pipeline files (data-aware autocomplete)
