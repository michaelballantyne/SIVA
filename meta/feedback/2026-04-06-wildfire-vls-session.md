# Feedback: Wildfire VLS Exploration Session
**Date:** 2026-04-06
**Session log:** `~/.claude/projects/-Users-michaelballantyne-code-VisDemo/1ffa340b-5d34-41d2-9a9a-8dc1bcf28013.jsonl`
**Dataset:** `datasets/wildfire/data/output.30000.vts` (vtkStructuredGrid, 600x500x61, terrain-following)

---

## Session Summary

Longer, deeper session than the earlier wildfire feedback entry. Single user prompt ("explore and visualize the wildfire dataset"), then a directed conversation building toward understanding vorticity-driven lateral spread (VLS). The session produced 4 views: fuel density + plume + wide wind, oxygen depletion, sphere-seeded streamlines, and vertical vorticity cross-section. Approximately 20+ pipeline versions across 4 views.

The terrain-following grid detection fix (from the previous session's feedback) was already in place — `load()` correctly flagged the grid type and `extract_grid(VOI=...)` was used from pipeline v1 with no errors. This is a direct win from the prior feedback loop.

---

## 1. Tools and DSL Features

### What worked well

- **Terrain-following detection now works end-to-end.** `load()` output included the advisory and `extract_grid(VOI=[0,599,0,499,0,0])` example. Claude copied it correctly on the first attempt. No wrong-ground-surface iterations this time.

- **Proactive pre-flight queries.** Before writing any pipeline code Claude called `get_statistics("theta")` and `suggest_isosurface("theta")`. The isosurface suggestions (347K, 578K, 728K) were used directly. This is exactly the right pattern.

- **`get_spatial_extent` before sphere seed placement.** When the user asked for a sphere seed "just upwind of the fire," Claude called `get_spatial_extent(field="theta", min_value=350)` to locate the fire center (x=[26,136], y=[-58,28]) before committing to seed coordinates. Good proactive use of a query tool.

- **`get_ground_z` corrected a bad seed placement.** First attempt at wide-area seeds used z=100 (just below the terrain floor at x=-200, which was z=102). The pipeline returned a warning. Claude immediately called `get_ground_z` at the upwind position, confirmed z=102, and adjusted seeds to z=120. One iteration to fix.

- **`suggest_camera` called proactively after first ground render.** Good habit.

- **`seeds_near` limitation correctly recognized.** When the user asked for seeds across the whole domain, Claude read `get_dsl_reference("seeds_near")`, understood that it confines seeds to the feature bounding box, and correctly switched to `vtkLineSource` with manual extent. No wrong seeds_near calls first.

- **Multi-view workflow (new_view / focus) worked smoothly.** The session ended with 4 simultaneous views, all built and re-titled without confusion about which was active. `list_views()` was called once for orientation when it was needed.

- **`get_camera` + bake into pipeline.** User manually adjusted camera interactively, asked Claude to save the angle. Claude called `get_camera`, got the exact position/focal_point/up, and edited the pipeline file. `export_standalone` also worked without issues.

- **`export_standalone` worked cleanly** in one call with no iteration.

### What was missing or awkward

**`seeds_near` can only seed in the feature bounding box.** This is a documented limitation, but the user's natural ask ("wider area, so I can see what happens at the sides") immediately ran into it. There is no DSL shortcut for "seed a grid-plane at a given height" — Claude had to drop down to raw `vtkLineSource`. A helper like `seeds_plane(input, x=value, normal=(1,0,0), bounds=..., num_seeds=40, z_offset=20)` would have made this a one-liner.

**Vorticity component extraction is a three-call chain.** To get vertical vorticity as a scalar:
```python
velocity = compute_velocity(input=data, ...)
vort_vec = compute_vorticity(velocity_input=velocity, result="vorticity", vector=True)
vort_z = extract_component(input=vort_vec, field="vorticity", component=2, result_name="vort_z")
```
This is three DSL forms, each a separate pipeline node, for what conceptually is a single derived quantity. A helper like `vorticity_component(input, component=2, velocity_components=("u","v","w"))` would reduce this to one call.

**No spatial query for "where is vorticity strongest."** When iterating on VLS isosurface values, Claude got global statistics (`min=-5.1, max=5.9, std=0.22`) from `get_statistics(node="vort_z")` but had no way to ask "where in the domain are the strongest ±3 regions?" The only spatial query tool for field concentration is `get_spatial_extent`, but that requires knowing a threshold. Claude ended up doing blind threshold sweeps (±1.5, ±2.5, ±3.0) to tune out ambient noise, which took 4 iterations. A query like "what percentile threshold would give N% of the domain" or "show distribution by region" would have helped.

---

## 2. Errors and Documentation

### `KeyError: 'num_points'` appeared ~8 times during vorticity work

Every `set_pipeline` call in the vortex-topdown view returned this error in the pipeline report:
```
Pipeline error: KeyError: 'num_points'

    line += f" -> {status['num_points']} pts, {status['num_cells']} cells"
                   ~~~~~~^^^^^^^^^^^^^^
KeyError: 'num_points'
```
The pipeline nonetheless rendered successfully and returned an image. The error was in the server's status-reporting loop, not in the VTK execution. The pattern that triggers it seems to be the `extract_region` + `compute_velocity` + `compute_vorticity(vector=True)` + `extract_component` chain — one of those nodes returns a status dict without `num_points` / `num_cells` keys.

Claude correctly interpreted the situation ("it rendered despite the internal error") and kept iterating. But the error recurred in every subsequent call to that view, which meant every pipeline result was flagged as an error even when successful. This erodes confidence and makes it hard to distinguish real problems from this noise.

### `title()` actors accumulate across `set_pipeline` rebuilds

When the vortex-topdown view was rebuilt after an earlier version already had a `title()` call, the old text actor persisted and the new one was drawn on top of it, producing overlapping text. This is a renderer bug: 2D overlay actors are not cleared at the start of a pipeline rebuild the way 3D actors are.

The user noticed ("The one that already had a title seems to have overlapping titles now!"). Claude correctly diagnosed the root cause and filed a backlog item in `meta/BACKLOG.md`. The fix is tracked there. This is a good example of the session producing a clean bug report through natural use.

### Documentation for `compute_vorticity` marks the explicit chain as "legacy"

The `get_dsl_reference("compute_vorticity")` response says:
> "Legacy wrapper. For new pipelines, prefer the explicit `make_vector()` + `curl()` pattern."

But when Claude looked up `compute_vorticity`, it was precisely because it wanted the "just give me vorticity" path. Calling it a legacy wrapper sends a confusing signal — the function still works, but the docs suggest reaching for a different, lower-level API instead. If the preferred path is `make_vector + curl`, there should be a worked example showing that full sequence, ideally with `extract_component` attached.

---

## 3. Human + Agent Interaction Flow

- The user asked a scientific question ("I'm interested in vorticity-driven lateral spread") mid-session after seeing the stream twists. Claude gave a substantive domain explanation before building the visualization — this calibration was appropriate given the user's apparent expertise.
- When the user asked to save a specific camera angle they had manually set, the workflow was: user adjusts camera interactively → says "can you save a snapshot with that exact view" → Claude calls `get_camera`, edits file, calls `export_standalone`, calls `screenshot`. User then explicitly requested a `set_pipeline` to confirm the baked camera matched. This worked, but the final confirmation step suggests the user wasn't sure if Claude's edit was correct. The workflow would feel more trustworthy if `set_pipeline` were the step that saves the camera (i.e., the screenshot from `set_pipeline` is always the authoritative render).
- The user asked Claude to "pop over to ../VisLang and add a backlog item" — a natural cross-project task. Claude used an Agent subagent to locate the backlog file, then edited it directly. This worked but was slightly awkward: Claude launched an Explore subagent just to find the file path, which could have been inferred from context. The edit itself was clean and well-described.
- No direct pipeline.py edits by the user. All edits went through Claude's Write/Edit calls.

---

## 4. Efficiency — What Took Too Many Rounds

### VLS vorticity visualization: ~8 iterations to find the right presentation

The sequence was:
1. Horizontal slices at 3 heights (too noisy, whole-domain vorticity)
2. Cropped region + same slices (still noisy)
3. Top-down view (new view)
4. 3D isosurfaces at ±1.5 (ambient noise too high)
5. Same at ±2.5 (cleaner, still scattered)
6. Same at ±3.0 (sparse, weak signal)
7. YZ cross-section at x=100 + x=160 (good!)
8. Single cross-section at x=130 (final)

Steps 4–6 were threshold sweeps without data-guided guidance. The underlying issue is that `std=0.22` for a field with `max=5.9` is hard to reason about — you can't tell from those numbers whether there's a clear signal at ±2 or whether everything above ±1 is noise. A histogram or "top 1% spatial locations" query would have shortcut this.

Steps 1–3 (slices before isosurfaces) were not wrong but were less revealing for this phenomenon. The cross-section approach that succeeded (step 7) could have been suggested earlier if there were a "visualize scalar dipole" pattern in the DSL overview.

### Title propagation: 3 set_pipeline calls for a title bug

Once the overlapping title was discovered, it couldn't be easily fixed — the title() bug means that any subsequent set_pipeline call will accumulate another title. The session ended without the vortex view being cleanly re-rendered without the artifact (Claude added ground context in a late set_pipeline run, and the title stacked again). The user accepted this ("Much better, thank you") because the content was good, but the bug left a cosmetic artifact in the final state.

---

## 5. Tool Output Verbosity

- **`load()` / `describe_data()` output was good.** Terrain-following warning, field distributions with shape classification, and grid dimensions all surfaced usefully. No truncation issues.
- **`set_pipeline()` success output is slightly verbose.** Each successful run lists all nodes with their full array lists. In a 7-node pipeline this is ~15 lines of arrays that repeat across every call. After the first successful build, subsequent runs mostly confirm nothing changed — Claude rarely reads these arrays. A terse mode ("Pipeline v7 built. 7 nodes, all ok.") with verbose mode available on demand would reduce noise.
- **`get_statistics()` output is well-sized.** Single field, compact table, useful.
- **`KeyError: 'num_points'` noise** (see Errors section) made every vortex-topdown pipeline result look like an error, even when it wasn't. This made the output harder to scan.

---

## 6. Workflow and Session Patterns

The arc went:
1. **Exploration** (1–2 rounds): load, stats, isosurface suggestions
2. **Initial build** (3–4 rounds): ground + plume + wind, camera placement
3. **User-directed refinement** (3 rounds): thinner tubes, wider seeds, sphere seeds
4. **Save and archive** (2 rounds): bake camera, export standalone
5. **Domain-directed investigation** (8 rounds): VLS vorticity views, many thresholds
6. **Polish** (2 rounds): titles, context for vortex view
7. **Close**

The session was long enough that phase 5 consumed more context than earlier phases. Claude's explanations of VLS physics were substantive and apparently accurate — the user's "Yes, exactly right!" confirms the domain interpretation was on target.

Human appeared to be a researcher (or at minimum domain-literate) — they knew the term "vorticity-driven lateral spread," recognized the vortex structure from streamline twists, and asked for scientific confirmation before requesting visualization. Claude calibrated well, providing a physics explanation before building the views.

The multi-view pattern matured by the end — 4 views open simultaneously, each with its own pipeline file and camera. The `list_views()` call was used once when the user asked to add titles to all views. This confirms the pattern is working but the overhead of re-running 4 separate pipelines to add titles to each shows there's no batch-update affordance.

---

## What Worked Well (Summary)

- Terrain-following grid detection fix from prior session worked perfectly — zero ground-extraction errors
- Proactive `suggest_isosurface`, `get_statistics`, `get_spatial_extent`, `get_ground_z` calls before guessing
- `seeds_near` limitation correctly diagnosed; `vtkLineSource` fallback identified without trial-and-error
- `get_camera` + file edit + `export_standalone` workflow for saving user's interactive camera
- Multi-view composition (new_view, focus, list_views) worked without confusion
- Domain-level VLS explanation was accurate and helped ground the subsequent visualization choices
- Bug (title accumulation) was correctly diagnosed and a clean backlog item was filed mid-session
