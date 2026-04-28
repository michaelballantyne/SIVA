# headsq HU calibration exercise + lazy view creation redesign

Two threads from a single session:

1. Loading the ParaView headsq.vti and trying to recover Hounsfield-unit
   calibration from data alone — what worked, what hit walls.
2. The "four windows from four idle sessions" surprise, and a redesign
   sketch where no view exists until `load()` or `new_view()` is called.

Tabling both for now; this entry preserves the context for a future pass.

---

## Part 1 — headsq.vti and Hounsfield units

### Origin

User asked whether the MCP could "see the headsq dataset." It lives at
`datasets/paraview-examples/data/headsq.vti` (8.8 MB, vtkImageData, 256×256×94,
unsigned-short scalars 0–4095). It's the classic VTK "quarter" Chapel Hill
CT head, assembled from the 93 `quarter.N` raw slice files used in VTK's
Medical1 demo.

Initial workflow was straightforward: `load()`, write a `show()` with
`representation="Volume"`, hit save. The first attempt with
`opacity_function="ct_bone"` rendered nearly black — preset is tuned for
HU-space data, not the raw 0–4095 range. The natural next move was "convert
to HU space."

### What worked

- **`vtkImageShiftScale` filter is the right tool** for adjusting
  per-voxel scalar values while preserving `vtkImageData` structure (so
  volume rendering still works). `calculator()` was tried first and
  silently produced a black render — likely because the derived array
  isn't the active scalar in the way the volume mapper wants, or it
  changes the dataset type. `vtkImageShiftScale` with
  `Shift=-1024, Scale=1.0, OutputScalarType=4` (VTK_SHORT) gave a
  visible, conventional-looking volume immediately.

- **Histogram-driven sanity-check of HU calibration.**
  After the −1024 shift, `get_histogram()` with 25 bins showed:
  - 58% of voxels at −1024 to −860 (air, expected ~−1000) ✓
  - 24% at −41 to +123 (soft tissue / brain / muscle) ✓
  - Long tail to ~1700 (bone) ✓
  - Spike at 2400–3071 (metal — dental fillings) ✓

  The three-population structure of a CT scan is recognizable. This is
  a useful general technique: even without ground-truth metadata, the
  *shape* of the histogram tells you whether your calibration assumption
  is at least in the right ballpark.

- **One-point refinement with a finer histogram.**
  100-bin histogram showed:
  - Air mode at ~−922 (expected −1000) → off by +78 HU
  - Soft-tissue mode at ~+61 (expected ~+40 for muscle / brain) →
    off by ~+20 HU under that interpretation
  - Both estimates roughly agree on "data is shifted ~70-80 HU too high."
    Applied `Shift=-1102` for a one-point correction.

### What hit walls

- **No external HU calibration exists for headsq.vti.**
  Searched: VTK Medical1 source (only documents scalar=500 → skin, no
  HU), VTK discourse (general advice: "use DICOM rescale slope/intercept"
  — but the .vti has none), Kitware ParaView repo (just the SHA512
  files), tutorials. No paper, README, or source comment ties this
  dataset to HU. The .vti file lost the original DICOM metadata in
  conversion. So we're stuck with histogram inference; ground truth
  isn't recoverable from public sources.

- **Air peak is clipped.** The bottom histogram bin (`[-1024, -983)`
  at 16.6%) is saturated — true-air values that fell below −1000 in
  the original got clipped to the data type's floor. That makes the
  visible "air mode" at −922 a biased estimate, not the true mode.
  A proper two-point fit would need the unsaturated air tail.

- **Soft-tissue peak is ambiguous.** A peak at +61 could be muscle
  (~+40, so off by +21) or water/CSF (0, so off by +61). The two
  interpretations imply different corrections. We picked the
  air-anchored estimate (off by +78) for the final shift; the
  soft-tissue peak became consistency-check rather than anchor.

- **Bone is a plateau, not a peak.** Bins from ~286 to ~1597 HU are
  flat at 40–65k voxels each — bone HU varies continuously across
  density, so it doesn't concentrate. Can't anchor a calibration on
  bone in CT data.

### Generalizations worth keeping

- **For CT-like volumes, expect a histogram with three landmarks:**
  air mode (sharp, near data floor), tissue mode (sharp, mid-range),
  bone plateau (broad tail). If two of those three are recognizable,
  you can do an approximate one-point HU calibration. A full two-point
  calibration needs the unsaturated air mode *and* a known tissue mode.

- **`vtkImageShiftScale` belongs in the DSL toolkit conceptually**
  even if it's already accessible via `filter()`. Linear rescaling of
  scalar values shows up enough (HU normalization, unit conversion,
  centering) that a named convenience form (`rescale`?) might be
  worth it. Not urgent.

- **`calculator()` quietly breaks volume rendering on `vtkImageData`.**
  We didn't fully diagnose this — possibly the derived-array isn't
  promoted to active scalars, or the output isn't `vtkImageData` for
  the volume mapper's purposes. Worth confirming and either fixing or
  documenting. If `calculator()` is the "first thing you reach for"
  to derive a scalar field but it doesn't compose with volume rendering
  on image data, that's a sharp edge.

- **Text histograms are surprisingly useful for visualization
  decisions** — they give an order-of-magnitude sense of where mass
  lives without needing a separate plotting tool. Worth preserving
  this affordance and possibly extending (e.g., log-scale option for
  long-tailed distributions).

---

## Part 2 — sessions, views, windows: the four-window surprise

### What happened

User noticed that hitting "save" on `view-main.py` triggered builds in
*four* live VTK windows. Diagnosis: four Claude Code sessions were open
in the VisLang folder, each running `python -m vislang.server`, each
spawning its own MCP server, each opening its own VTK window watching
`view-main.py`. The current server eagerly creates a "main" view at
startup with renderer + watcher + window, regardless of whether the
session ever uses it.

### The proposed redesign

> Nothing VTK-ish should happen until `load()` or `new_view()` is
> called. No views exist until then; `list_views()` returns empty.
> `load(filename)` creates a "main" view (or whatever the user names
> it). `new_view(name)` creates a named view. "main" is not special —
> it's just the default name `load()` picks. If you only ever use
> `new_view()`, you have no main view at all.

The win is structural: idle sessions don't open windows or watch
files. The four-windows-from-four-idle-sessions case goes away
because three of those sessions never called `load()`.

This *doesn't* fix the case where multiple sessions all do call
`load()` — they'd all watch the same `view-main.py` and collide.
Real fix for that is per-session pipeline file namespacing
(e.g., `.vislang/<server-pid>/view-main.py`), which is a separate
problem that may not even need fixing — agents intentionally
working on the same data probably want to see each other's edits.

### Current lifecycle (mapped)

From an Explore-agent pass over `vislang/server.py`,
`vislang/renderer.py`, `vislang/hot_reload.py`:

1. **Server startup** (`server.py:2241-2270`):
   - Creates `Renderer(mode=…, view_name="main")` immediately
   - Wraps in `ViewContext("main", _main_renderer)` and adds to
     `_views["main"]`
   - Calls `main_ctx.start_hot_reload()` — watcher attached to
     `view-main.py`
   - In interactive mode, `run_event_loop()` runs against this
     renderer
   - Does **not** write `view-main.py` (only `load()` does that)

2. **View registry** (`server.py:308`):
   - `_views: dict[str, ViewContext]` — module-level
   - `_current_view: str = "main"` — module-level default

3. **`load()`** (`server.py:449-504`):
   - Operates on the **current view** (`_current_ctx()`)
   - Doesn't create a view; assumes one exists
   - Writes `view-<name>.py` if missing
   - Doesn't open a window

4. **`new_view(name)`** (`server.py:1447-1492`):
   - Creates renderer + ViewContext on the main thread
     (`run_on_main_thread(lambda: Renderer(...))`)
   - Adds to `_views[name]`, sets as current
   - Calls `ctx.start_hot_reload()` — starts watcher
   - Requires `view-<name>.py` to exist already; fails otherwise

5. **Watcher** (`hot_reload.py`):
   - One `PipelineWatcher` per view, watching the parent dir of the
     pipeline file with watchdog
   - Debounces ~100ms, calls `coordinator.request_build()`

6. **Renderer** (`renderer.py:57-107`):
   - One per view; one VTK window per view
   - In `OFFSCREEN` and `HEADLESS_INTERACTIVE` modes,
     `_ensure_initialized()` runs in `__init__`
   - In `INTERACTIVE` mode, init is deferred to first
     `run_on_main_thread()` or render

### The redesign sketch

1. **Server startup stops creating `main`.** Drop the renderer +
   ViewContext + start_hot_reload block at `server.py:2241-2270`.
   Initialize `_views = {}`, leave `_current_view = None`.

2. **`_current_ctx()` raises a friendly error** when no view exists:
   "No view exists — call `load()` or `new_view(name)` first."

3. **`load()` becomes view-creating.** Accept an optional
   `view_name="main"` arg. If `_views[view_name]` doesn't exist,
   create renderer + ViewContext + start watcher, set current, then
   proceed with the existing data-load logic.

4. **`new_view()`** is already lazy — fine as-is.

5. **`_init_for_test()`** (`server.py:367`) still hardwires
   `main`. Either keep the helper as a test-only convenience or
   update each test to call `load()`/`new_view()` explicitly.
   Probably keep the helper, just stop calling its
   moral-equivalent from server startup.

6. **Delete the committed `view-main.py`** at the repo root —
   it's a leftover from a prior session.

### The wrinkle: interactive-mode main thread

Interactive mode runs a Cocoa/Qt event loop on the main thread,
currently driven by `_main_renderer` at `server.py:2270`. If no
renderer exists at startup, the main thread has nothing to drive,
but it still has to be available for the first `load()` /
`new_view()` to schedule renderer creation onto it via
`run_on_main_thread`.

Two options:

- **(a) Renderer-independent main-thread dispatcher.** Factor the
  main-thread event loop and dispatch queue out of `Renderer` so
  it can run with zero renderers. First `load()` schedules a
  renderer creation onto the loop. Cleaner separation of "main
  thread / event loop / dispatcher" from "renderer." Touches
  `renderer.py` non-trivially.

- **(b) First `load()` blocks until main thread is freed**, then
  bootstraps both renderer and event loop. Simpler but means a
  brief lag on first call.

Lean toward (a), but (b) gets you to the win faster.

### Risks / unknowns

- **Hot-reload teardown on `close_view()`** must actually stop the
  watcher cleanly so removing the last view = silent process again.
  Already exists per the Explore pass, but worth verifying.

- **VTK on macOS** sometimes complains about creating a window
  after the app has been "running" for a while without windows.
  Need real-hardware testing on the lazy interactive path.

- **Tests assume `main` exists.** Several tests
  (`test_named_views.py`, `test_camera_orbit.py`) directly access
  `srv._views["main"]`. They'd need updating or `_init_for_test()`
  would need to keep its current behavior as a deliberate test-only
  bootstrap.

### Implementation order (when picked up)

1. Get offscreen mode working with lazy views — smaller blast
   radius, easier to test, doesn't hit the interactive event-loop
   wrinkle.
2. Refactor main-thread dispatch into a renderer-independent loop.
3. Wire interactive mode through it.
4. Update docs / instructions.

---

## Other items worth noting

- **`load()` won't overwrite an existing pipeline file.** Required
  `rm view-main.py` before reloading a different dataset. Behavior
  is documented in the tool description but feels papercut-y when
  you're switching datasets in the same session. Could surface a
  `force=True` flag, or be smarter about detecting "same source as
  before."

- **Camera doesn't always reset on dataset change.** After deleting
  `view-main.py` and loading headsq, the previous camera (zoomed
  inside the wildfire dataset) was preserved, producing an empty
  black render until `set_suggested_camera("overview")`. Maybe
  loading a new file should reset the camera, or at least flag
  "your camera is outside the new dataset's bounds."

- **`background()` form works fine.** No issues; "black" gave the
  classic CT-volume aesthetic.
