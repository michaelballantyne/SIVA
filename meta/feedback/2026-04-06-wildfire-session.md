# Feedback: Wildfire Dataset Visualization Session
**Date:** 2026-04-06  
**Session log:** `~/.claude/projects/-Users-michaelballantyne-code-VisDemo/17a760c7-7f3d-40e8-90b8-5e7a9d4aa66a.jsonl`  
**Dataset:** `datasets/wildfire/data/output.30000.vts` (vtkStructuredGrid, 600x500x61, terrain-following)

---

## Session Summary

A short, focused session: one user prompt ("explore and visualize the wildfire dataset"), 9 pipeline versions over ~10 minutes. The visualization goal — ground surface colored by fuel/temperature, fire plume isosurface, and wind streamlines — was achieved by v3/v4 in terms of content, but the ground surface extraction was wrong until v9. The last human message asked Claude to reflect on what the MCP/DSL could have done better; Claude produced a detailed self-critique that is incorporated and extended here.

---

## 1. Tools and DSL Features

### What worked well

- The incremental build pattern (start with ground, add isosurface, add streamlines) went smoothly once the ground layer was correct. Each `set_pipeline()` returned a screenshot, so no separate screenshot calls were needed.
- `suggest_isosurface()` was called proactively before writing any pipeline code, which was correct. It returned useful gradient-based values (347K, 577K, 728K) that Claude used directly.
- `compute_velocity` + `seeds_near` + `stream_tracer` + `tube` is a full streamline recipe that composed without errors or iteration.
- The `outline()` form was used well to add a bounding box for spatial orientation.
- `get_statistics(node="ground", field="rhof_1")` was used correctly to investigate the sparse field issue after the human flagged it.

### What was awkward or missing

**No terrain-following-grid surface extraction shortcut.** The "SURFACE COLORING" pattern in `get_dsl_overview()` (line 1738 in `server.py`) reads:
```python
surface = extract_region(input=data, bounds=[xmin, xmax, ymin, ymax, zmin, zmin])
```
This assumes a flat grid and implies that `zmin` is the ground z. For a terrain-following `vtkStructuredGrid`, the z-coordinate at k=0 varies spatially — `zmin` is just the global minimum, not the ground surface. Claude followed this pattern literally (v1 through v8), using `bounds=[-498, 700, -500, 498, 0.8, 0.8]`. The k=0 ground layer is not flat at z=0.8; it follows the terrain. The correct form is `extract_grid(VOI=[0, 599, 0, 499, 0, 0])`, which was not in the surface coloring pattern.

The fix arrived only after the human asked "Are you working with the terrain following grid?" (message queued around pipeline v8). That question unlocked the correct approach in v9.

**`get_ground_z()` result was not connected to extraction method.** Claude called `get_ground_z(node="data", x=100, y=0)` early in the session and got back a useful result: `iz=0: z=149.1`. However, this was not the global bounds minimum (0.8). The discrepancy was not flagged in the tool output as a "this grid is terrain-following" signal, so Claude did not use it to reconsider the extraction approach. It was treated as seed-placement information only — exactly as its description suggests.

---

## 2. Errors and Documentation

### No errors were hit in the VTK sense

All 9 pipelines executed successfully — no exceptions, no empty-output warnings. The problem was not an error but a silent wrong result: the ground surface was extracted at z=0.8 (global bounds minimum) rather than as the k=0 index layer. VTK's `vtkExtractGrid` with `Bounds=[xmin, xmax, ymin, ymax, 0.8, 0.8]` clips to a narrow z-plane that happens to include some terrain-following cells, so the output was non-empty and plausible-looking.

### The load() / describe_data() output contained a useful hidden signal

The `load()` output showed:
```
Avg spacing: X~2, Y~2, Z~15
```
For a terrain-following grid with dimensions 600x500x61 spanning z=[0.8, 898.6], an average z-spacing of ~15m is correct — but the ground layer has a minimum z of ~149 (per `get_ground_z`) while the global z-minimum is 0.8. That 148-unit gap between the global zmin and the actual ground is the terrain-following signature, but it was never made explicit.

### The "SURFACE COLORING" pattern is documented for flat grids only

The pattern in `get_dsl_overview()` at server.py line 1738 says `zmin, zmin` with no qualification about grid type. The `extract_grid` reference example at server.py line 2439–2443 shows the correct index-based approach, but it is a separate entry in the DSL reference, not surfaced in the "KEY PATTERNS" section that Claude followed. Claude did not call `get_dsl_reference("extract_grid")` before choosing `extract_region`.

### Statistics misleading for surface-confined fields

The `load()` output for `rhof_1` showed:
```
rhof_1 (point, float): [0, 0.6]  shape=sparse
  p1=0  p25=0  p50=0  p75=0  p99=0.471809
```
With p75=0, Claude concluded "fuel only exists in one region." In fact, fuel occupies the entire k=0 layer (600,000 points out of 18,300,000) but not the 60 air layers above. When Claude later called `get_statistics(node="ground", field="rhof_1")`, it found `min=1e-06, max=0.594, mean=0.204` — a well-distributed field. The volume-wide statistics gave a completely misleading picture of a surface-confined field.

---

## 3. Human + Agent Interaction Flow

- The user interruption at line 50 ("The black land and background is hard to see") was handled naturally — Claude focused back on the main view and began iterating on color.
- The key inflection was the queued message "Are you working with the terrain following grid?" at line 90. This was typed while Claude was executing, queued, and delivered after the pipeline run. The question unlocked the correct solution in a single iteration. Without it, the session would have ended with a subtly wrong ground surface.
- The session was short enough that there was no fatigue or context-window pressure. Claude did not need to re-read documentation mid-session.
- The human did not directly edit `pipeline.py` — all edits went through Claude's Write/Edit tool calls, so there was no handoff friction.

---

## 4. Efficiency — What Took Too Many Rounds

**Ground surface extraction: 8 wrong versions before getting it right.**

v1–v8 all used `extract_region(bounds=[..., 0.8, 0.8])`. The iterations in between (v4–v7) were spent on cosmetics — color, background, colormap — while the fundamental extraction was wrong the whole time. The terrain-following issue only surfaced when the human asked directly.

The root path to the error:
1. `get_dsl_overview()` "SURFACE COLORING" pattern uses spatial bounds extraction
2. `load()` output does not flag terrain-following character
3. `get_ground_z()` was called but its output was not recognized as a grid-structure signal
4. The wrong extraction produced a non-empty, visually plausible result, so no error triggered
5. Volume-wide statistics for `rhof_1` produced a misleading reading that Claude narrated confidently

All five of these contributed. The first two are fixable with documentation/detection. The third is fixable with better tool output language. The fourth and fifth are harder — silent wrong results are the toughest class of bug.

**Color iteration: 4 rounds for cosmetics while the substrate was wrong.**

v4–v7 iterated on ground coloring (terrain elevation, fuel density at z=0.8, O2, fueld density again) without ever getting a correct ground slice. These rounds could have been collapsed if the terrain-following issue had been caught at load time.

---

## 5. Tool Output Verbosity

- `load()` / `describe_data()` output length is appropriate. The per-field percentile table is useful and Claude used the theta p99=301.6 value to calibrate the 350K isosurface choice.
- `suggest_isosurface()` output is well-structured. Gradient-based values + percentile table made it immediately usable.
- `get_ground_z()` output lists z values at multiple layers, which is slightly verbose for its primary use (getting a seed-placement z). The listing of iz=0 through iz=7 is not necessary if you just want the ground level. However, the key missing signal is not in the output volume but in what the output implies — see design proposals below.
- `get_statistics()` on the full `data` node for `rhof_1` returned correct values but was actively misleading in context. No warning was given that the field is surface-confined.

---

## 6. Workflow and Session Patterns

The arc was: explore (1 round) → build incrementally (3 rounds) → cosmetic refinement (4 wrong rounds) → correct fix (1 round) → reflection. The session ended productively with a correct visualization.

The human appeared knowledgeable about the dataset (asked specifically about terrain-following grids) but was testing Claude rather than guiding it — the human allowed the wrong extraction to persist for 8 versions, then asked the corrective question. This is a realistic usage pattern for demos and evaluations.

Claude's self-reflection at the end of the session was accurate and detailed. It correctly identified all four root causes. That reflection is the basis for the design proposals below.

---

## Design Proposals

These address the specific failure modes observed, referencing the current source code.

### 1. Terrain-following grid detection in `describe_data()` / `load()`

**Problem:** `describe_data()` reports global bounds (including z-min of terrain = 0.8) but does not flag that the grid is terrain-following. An AI using the surface-coloring pattern will use `zmin=0.8` as the ground z.

**Proposed fix:** In `server.py`'s `describe_data()` (around line 862), after computing bounds, add a check for terrain-following geometry for `vtkStructuredGrid` datasets. Sample the z-coordinate at k=0 across several (i,j) points. If the standard deviation of ground z values exceeds some fraction of the average z-spacing, emit a warning:

```python
# After lines 852-875 in server.py (describe_data):
if data.GetClassName() == "vtkStructuredGrid":
    dims = [0, 0, 0]; data.GetDimensions(dims)
    nx, ny, nz = dims
    if nz > 1:
        ground_zs = [data.GetPoint(iy * nx + ix)[2]
                     for iy in range(0, ny, max(1, ny // 20))
                     for ix in range(0, nx, max(1, nx // 20))]
        gz_std = np.std(ground_zs)
        gz_mean = abs(np.mean(ground_zs))
        if gz_std > 1.0:  # terrain varies by more than 1 unit
            lines.append("")
            lines.append("=== Grid Structure ===")
            lines.append(f"  Terrain-following grid detected (ground z std={gz_std:.1f}).")
            lines.append(f"  Ground z ranges from {min(ground_zs):.1f} to {max(ground_zs):.1f}.")
            lines.append(f"  Use extract_grid(VOI=[0,{nx-1},0,{ny-1},0,0]) for the ground surface.")
            lines.append(f"  Do NOT use extract_region with z=bounds_min for ground extraction.")
```

This would have given Claude the correct extraction form at load time.

### 2. Fix the "SURFACE COLORING" key pattern in `get_dsl_overview()`

**Problem:** `server.py` line 1738 shows a flat-grid-only pattern without qualification. It is the first "KEY PATTERN" that Claude sees and it steered every wrong attempt.

**Proposed fix:** Replace the single surface-coloring pattern with two variants, one for flat/regular grids and one for terrain-following structured grids:

```python
# In server.py get_dsl_overview(), around line 1736:
"1. SURFACE COLORING — flat/regular grid (vtkImageData, vtkRectilinearGrid):",
"surface = extract_region(input=data, bounds=[xmin, xmax, ymin, ymax, zmin, zmin])",
"show(surface, \"ground\", color_by=\"fieldname\", scalar_range=(lo, hi), lut=\"cool_to_warm\")",
"",
"1b. SURFACE COLORING — terrain-following structured grid (vtkStructuredGrid):",
"#   Use grid index k=0, NOT spatial z bounds (ground z varies across the domain)",
"#   Check dimensions with describe_data() first",
"ground = extract_grid(input=data, VOI=[0, ni_max, 0, nj_max, 0, 0])",
"show(ground, \"ground\", color_by=\"fieldname\", scalar_range=(lo, hi), lut=\"cool_to_warm\")",
```

The key addition is the explicit note "NOT spatial z bounds" with the reason.

### 3. Add terrain-following flag to `get_ground_z()` output

**Problem:** `get_ground_z()` is described as a streamline-placement helper (server.py line 1161–1164). Its output shows z values at different layers, but says nothing about what those varying z values mean for surface extraction.

**Proposed fix:** In `queries.py`'s `get_ground_z()` (around line 960), after computing `best_pt`, check whether z varies significantly and add a note:

```python
# At the end of get_ground_z(), before returning:
# Check if this is a terrain-following grid by sampling a few more points
sample_zs = [data.GetPoint(iy * nx + ix)[2]
             for iy in range(0, ny, max(1, ny // 10))
             for ix in range(0, nx, max(1, nx // 10))]
if np.std(sample_zs) > 1.0:
    terrain_note = (
        f"\nNote: Ground z varies significantly (std={np.std(sample_zs):.1f}) — "
        "this is a terrain-following grid.\n"
        "Extract the ground layer by grid index, not by spatial z bound:\n"
        f"  extract_grid(input=data, VOI=[0, {nx-1}, 0, {ny-1}, 0, 0])"
    )
else:
    terrain_note = ""
```

This would have let Claude connect the z-variation it observed to the correct extraction method.

### 4. Flag surface-confined sparse fields in statistics output

**Problem:** `rhof_1` and fields like it are zero in the 60 air layers but well-distributed at the ground. Volume-wide statistics showed p25=p50=p75=0, leading Claude to misinterpret the field distribution.

**Proposed fix:** In `queries.py`'s `get_rich_field_stats()` or `format_rich_field_stats()`, when a field is classified as `shape="sparse"` on a 3D structured grid, add a note about potential layer-confinement. This could be a heuristic: if the field is sparse overall but the fraction of non-zero values is close to 1/nz, suggest it may be surface-confined.

```python
# In format_rich_field_stats or get_statistics, when shape == "sparse"
# and data is vtkStructuredGrid with nz > 1:
nz = dims[2]
nonzero_fraction = np.mean(sample != 0)
expected_surface_fraction = 1.0 / nz
if nonzero_fraction < 3 * expected_surface_fraction:
    note = (f"  [Sparse: may be surface-confined — "
            f"non-zero fraction {nonzero_fraction:.1%} ~ 1/{nz} layers. "
            f"Check get_statistics(node='ground_node', field='{name}') for ground-layer stats.]")
```

This would have preempted the "fuel only exists in one region" misreading.

### 5. Teach `quick_start()` about terrain-following grids

**Problem:** `quick_start()` at server.py line 654–676 generates a surface-coloring snippet using `show(data, ...)` directly for non-image data, with no ground extraction at all for structured grids. It does not produce a terrain-following aware suggestion.

**Proposed fix:** When `quick_start()` detects a `vtkStructuredGrid` with nz > 1 and variable ground z, generate `extract_grid(VOI=[0, nx-1, 0, ny-1, 0, 0])` as the ground extraction node rather than showing the full 3D data directly. The check could reuse the same terrain-following detection logic as proposal 1.

---

## What Worked Well (Summary)

- Incremental pipeline building: adding one layer per `set_pipeline()` call was the right pattern and Claude followed it correctly.
- Proactive use of `suggest_isosurface()` before choosing contour values — this was correct and saved guessing.
- `compute_velocity` + `seeds_near` + `stream_tracer` + `tube` composed cleanly with no errors or retries.
- The multi-view interrupt (`new_view("oxygen")`) was used appropriately, though the human interrupted before it was set up.
- `get_statistics` on a specific named node (`node="ground"`) to investigate the sparse field issue — this is exactly the right pattern and Claude used it correctly once prompted.
- Claude's self-reflection at the end was accurate, complete, and well-structured. It correctly identified all four root causes and proposed sensible remedies.
