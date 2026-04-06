# Feedback: Wildfire VLS Exploration Session (0bc6d7d3)
**Date:** 2026-04-06
**Session log:** `~/.claude/projects/-Users-michaelballantyne-code-VisDemo/0bc6d7d3-da02-42e1-88ce-9e7de5775c6f.jsonl`
**Dataset:** `datasets/wildfire/data/output.30000.vts` (vtkStructuredGrid, 600x500x61, terrain-following)
**Working directory:** `../VisDemo` (separate project, VisLang used as MCP server)

---

## Session Summary

User asked Claude to read two reference PDFs (a dataset description and the 2022 IEEE SciVis Contest winner paper), then explore and visualize vorticity-driven lateral spread (VLS) using VisLang. The session produced 6 views: main overview, streamlines, vorticity, topdown, sideview, and combined. All pipelines built successfully on first attempt (one recurring server-side reporting error, not a VTK error). Session lasted approximately 30 turns.

This session appears substantially smoother than the one already documented (1ffa340b) — fewer iterations, more confident choices. The terrain-following grid detection guidance worked correctly throughout.

---

## 1. Tools and DSL Features

### What worked well

- **Proactive pre-flight queries.** Claude called `suggest_isosurface("theta")` and `suggest_opacity("theta")` before writing any pipeline code. It used the histogram-guided suggestions directly (347K, 578K, 728K transition points; opacity function clipping below 302K).

- **`get_dsl_reference` and `get_dsl_overview` used upfront.** Called before the first `set_pipeline` — Claude read the overview to understand available patterns before committing to a structure. This paid off: `extract_grid(VOI=...)` was used correctly from the start with no wrong-ground-surface iterations.

- **`get_statistics` used reactively and correctly.** When the first vorticity volume rendering was "barely visible," Claude immediately called `get_statistics(node="low_atm", field="vorticity")` rather than guessing new values. It saw std=1.07 for Y-vorticity and adjusted the opacity function to a tighter range. One iteration to fix.

- **`get_ground_z` used to understand terrain before placing seeds.** Claude called `get_ground_z` at x=0, x=100, x=200 to understand the terrain profile before writing streamline seeds. This correctly identified the ridge position and informed sensible seed z-values.

- **Multiple views created cleanly.** 6 views created (`new_view` / `focus` pattern), each with its own pipeline file. No confusion about which view was active after switching.

- **`compute_vorticity` used correctly.** The vorticity computation pipeline used the legacy wrapper (via `get_dsl_reference`), applied it correctly, and produced meaningful results without field-name errors.

### What was missing or awkward

**`get_ground_z` silently fails after `new_view` before `set_pipeline`.**

After creating the "streamlines" view with `new_view`, Claude immediately tried:
```
get_ground_z(node="data", x=-200, y=0)
```
and received:
```
Node 'data' not found. No pipeline is active. Call set_pipeline() first to load data.
```
Claude had the right instinct — query terrain height before writing seed positions — but `get_ground_z` requires an active pipeline in the current view. After the failed calls, Claude wrote the pipeline with guessed z-values anyway (they turned out to be reasonable). The workflow gap here is real: there's no way to query ground z in a fresh view without first setting a pipeline, which creates a chicken-and-egg problem for seed placement.

**Vorticity isosurfaces caused a `KeyError: 'num_points'` server error (recurring).**

When the vorticity view used a contour filter:
```
Pipeline error: KeyError: 'num_points'
    line += f" -> {status['num_points']} pts, {status['num_cells']} cells"
                   ~~~~~~^^^^^^^^^^^^^^
KeyError: 'num_points'
```
This error appeared twice in the vorticity pipeline. The rendering succeeded despite it — Claude's comment: "Despite the minor error, the rendering worked." The error is in the server's pipeline status-reporting loop, not in VTK execution. A contour or cell-derivative node returns a status dict that doesn't include `num_points`/`num_cells` keys, and the reporting code crashes without a fallback. This is the same bug already documented in the 1ffa340b feedback session and still unfixed.

**No dedicated tool for cross-stream seed planes.**

The streamlines view required manually specifying seed positions as a list of (x, y, z) tuples spread across the domain. For structured grids, a common pattern is "seed on a vertical cross-section at a given x" but there's no helper for this. Claude inferred seed positions from `get_ground_z` readings and rough domain knowledge, which worked but required several lines of manual coordinate arithmetic.

---

## 2. Errors and Documentation

### PDF path error (minor)
Claude initially tried to read the contest winner PDF from the wrong path — `datasets/wildfire/2022_IEEE...pdf` rather than `datasets/wildfire/data/2022_IEEE...pdf`. This was discovered immediately when Bash `ls` showed the file is in the `data/` subdirectory. A minor wasted read but not a workflow blocker.

### "PDF" was actually an HTML file
The first attempt to read the contest winner PDF via the MCP's Read tool failed with pdftoppm errors. Claude ran `file` on the path and discovered it was "HTML document text, ASCII text" — a download-redirect artifact rather than an actual PDF. Claude correctly moved on to look for the file at the `data/` path (a different copy that was a valid PDF). This was handled cleanly but the error messages from pdftoppm were cryptic:
```
Syntax Error (2): Illegal character <68> in hex string
```
These don't clearly communicate "this file is not a PDF." A better error would be "file does not appear to be a valid PDF (detected: HTML)."

### `compute_vorticity` documented as "legacy" — confusing signal
The `get_dsl_reference("compute_vorticity")` response says:
> "Legacy wrapper. For new pipelines, prefer the explicit `make_vector()` + `curl()` pattern."

Claude read this reference precisely because it wanted to compute vorticity, and the "legacy" label made it hesitate before using it. It nonetheless used `compute_vorticity` because it was shorter. The docs don't show a complete worked example of the preferred `make_vector + curl + extract_component` chain — just a pointer. If the preferred pattern is genuinely better, a worked example would help. If `compute_vorticity` is the right choice for most uses, calling it "legacy" is confusing.

---

## 3. Human + Agent Interaction Flow

Overall the interaction was directed and smooth. The user gave a high-level scientific goal and let Claude work autonomously. There were no requests to edit pipeline files directly.

**One friction point at the end:**

After completing all views, Claude said:
> "You can switch between views using `focus("viewname")`."

The user responded:
> "Why are you telling me this? That's a command for you to use, I can see and interact with all the windows."

This is a real conceptual confusion: Claude offered guidance about a VisLang MCP tool as if it were a UI affordance for the human. The user has live VTK windows open and doesn't need to issue `focus()` — they can just click on a window. Claude mixing up "what the agent does" with "what the human does" is a recurring risk in multi-window sessions where both parties are observing the output.

**No human-initiated pipeline edits.** All pipeline construction was via Claude's tool calls. The user's interaction was asking for new views, reacting to screenshots Claude showed.

**Scientific calibration was appropriate.** Claude provided substantive VLS physics explanations alongside each view ("Fast red wind over the ridge decelerates dramatically...this closely matches Figure 6/10 from the winner's paper"). The user's implicit approval (no corrections, continuing the session) suggests the domain interpretation was reasonable.

---

## 4. Efficiency — What Took Too Many Rounds

**This session was notably efficient.** 6 views produced without a single "0 points output" error or wrong-field-name failure. The main sources of iteration were:

- One vorticity opacity pass (too faint → get_statistics → tighter range): 2 iterations, appropriate.
- One vorticity isosurface pass (volume → contours): 2 iterations, reasonable creative choice.
- Camera adjustments after initial render: 1-2 per view, normal.

**The `get_ground_z` after `new_view` misfire** (2 failed calls) was the clearest unnecessary friction — Claude had the right strategy but hit a tool availability wall.

No cases of blind threshold sweeping or guessing field names. The pre-flight query pattern (stats → suggest → pipeline) worked as intended.

---

## 5. Tool Output Verbosity

- **`set_pipeline` success output** is verbose. Each call lists all node names with full array lists. In a 5-node pipeline, that's ~10 lines of repeated array names per call. After the first successful build, Claude rarely reads these arrays — it's noise on subsequent refinement calls.

- **`load()` / `describe_data()` output was well-sized.** The terrain-following detection advisory was present and Claude copied the `extract_grid(VOI=...)` example directly from it. The field percentile summaries surfaced the theta ambient (~300K) and max (~1184K) cleanly.

- **`get_statistics()` output is well-sized.** Compact, useful for making opacity decisions.

- **`suggest_isosurface()` output is actionable.** Claude pasted the suggested values directly into its pipeline code. Good format.

- **`get_ground_z()` output returns more detail than necessary.** The response includes z-values for iz=0 through iz=9:
  ```
  iz=0: z=149.1
  iz=1: z=150.4
  ...
  iz=9: z=163.0
  ```
  Claude only needs iz=0 (the ground level) for seed placement. The full layer listing may help in other contexts but adds noise in the common case. At minimum the response could lead with "Ground z = 149.1" before the layer detail.

---

## 6. Workflow and Session Patterns

The arc went:

1. **Reference study** (PDF reading, domain context): 4–5 rounds
2. **Data exploration** (load, get_dsl_overview, suggest_isosurface, suggest_opacity): 2 rounds
3. **Main view** (terrain + fire/smoke): 3 rounds
4. **Streamlines view** (new_view, write, set_pipeline, set_camera): 3 rounds
5. **Vorticity view** (new_view, write, 2 iterations for opacity/style, set_camera): 4 rounds
6. **Topdown view** (new_view, write, set_pipeline): 2 rounds
7. **Sideview** (new_view, write, set_pipeline, camera tweak): 3 rounds
8. **Combined view** (new_view, write, set_pipeline, camera tweak): 3 rounds
9. **Summary and correction**: 1 round

The progression was natural: start with overview, add derived-quantity views, finish with combined multifield view. No backtracking between views (unlike the 1ffa340b session which revisited earlier views after later discoveries).

The user appeared domain-literate — they asked about a specific named phenomenon (VLS), referenced a specific contest paper, and said "Note we only have one timestep for one terrain variant" demonstrating prior familiarity with the dataset. Claude calibrated well, providing physics framing and explicitly connecting each view to figures from the reference paper.

---

## What Worked Well (Summary)

- Proactive `suggest_isosurface` + `suggest_opacity` pattern before any pipeline writing
- `get_dsl_overview` + `get_dsl_reference` used upfront to understand available tools
- `get_statistics` used reactively when volume rendering was too faint — one iteration fix
- `get_ground_z` used to understand terrain profile before placing seeds
- Terrain-following grid detection advisory correctly applied (`extract_grid(VOI=...)`) from pipeline v1
- 6 views created with no empty-output errors, no wrong-field-name errors
- `compute_vorticity` applied correctly without needing multiple attempts
- Scientific domain interpretation (VLS physics) was substantive and apparently accurate

## What Could Be Better (Summary)

- `get_ground_z` unavailable in fresh views before `set_pipeline` — seed placement chicken-and-egg
- `KeyError: 'num_points'` server reporting bug: contour nodes produce status dicts without expected keys; renders succeed but every call looks like an error
- pdftoppm error messages for non-PDF files are cryptic; could say "file is not a valid PDF"
- `compute_vorticity` labeled "legacy" without a complete worked example of the preferred alternative
- `get_ground_z` output includes 10 vertical layer z-values when only iz=0 is typically needed
- Claude incorrectly offered `focus("viewname")` as user guidance rather than recognizing it as an agent-only tool
- `set_pipeline` success output repeats full array lists on every call; terse confirmation would reduce noise

---

## 7. Visual Quality of Final Outputs (from screenshots)

Looking at the actual rendered images, the quality is mixed across views:

**Streamlines view** — the strongest output. Fast red/pink tubes flowing over the ridge decelerate dramatically to slow blue turbulent eddies on the leeward side. The fire is visible in orange, and the VLS mechanism (cross-stream vortices flanking the fire) is legible. This closely matches the contest winner's Figure 6/10 aesthetically.

**Sideview** — very good. Temperature-colored volume rendering with the hot orange/white core and pale gray smoke tail is visually polished. The terrain profile is clear and the colorbar is readable. Most aesthetically complete view of the set.

**Vorticity view** — scientifically informative (the pink vorticity sheet blanketing the leeward slope is clearly visible with green patches embedded), but has a barely legible label text line at the bottom-left corner, likely the `KeyError: 'num_points'` error rendered as a text actor artifact or the overlap-title bug. Background is pure black which gives good contrast for the surfaces.

**Combined view** — shows red/blue lateral velocity on streamlines flanking the fire. The cross-stream vortex structure is visible. However, there is a large black band at the top of the image where streamlines extend far above the terrain into empty space — the seed placement went too high or the integration ran too far upward. This partially obscures the ridge/windward-flow context. A `max_steps` or height-clipping parameter on streamlines would help.

**Main overview** — the weakest view. The terrain is shown nearly edge-on from the default camera, the fire is tiny relative to the frame, and the burned scar is barely visible. The camera placement didn't produce a representative overview. This view was built first, before Claude had a feel for the domain layout, and was never revisited after the other views made it redundant.

**Topdown view** — small and somewhat dark overall. The burned scar (brown) and O2 contour (orange loop) are the key elements and are visible, but the view is understated. The fuel density colormap makes most of the terrain appear a uniform dark green, offering little depth cue. Adding terrain shading (hill-shading or elevation-colored surface) would significantly improve readability.

### Cross-cutting visual issues

- **No axis or scale bar in any view.** Domain dimensions (1.2 km × 1 km × 900 m) aren't communicated. The contestant winners all included distance labels.
- **Camera angles are inconsistent across views.** Some are bird's-eye, some are oblique, and the main view is nearly horizontal. A consistent convention (e.g., 30° elevation viewing from windward) would make the set read as a coherent suite.
- **Black background in vorticity view vs. dark-grey in main/streamlines vs. off-white in sideview.** Background color wasn't standardized, which makes the views feel less like a coordinated set.
