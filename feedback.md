# Session 1
  Data exploration gaps:

  1. Line probes / profiles with plots. I could sample a single point, but I repeatedly wanted to extract a 1D profile - e.g., temperature vs. height
  through the plume center, or wind speed along a horizontal transect. A probe_line(start, end, field) -> plot tool would have been the most useful
  addition for interpretation. Right now I can describe that "theta reaches 1184K" but I can't show where in the vertical column that peak is or how
  sharp the gradient is.
  2. Conditional / subregion statistics. I could get global stats, but I wanted things like "mean updraft velocity where theta > 400K" or "total
  volume where O2 < 0.20." Being able to query statistics on a filtered subset without building a full pipeline would help with quantitative
  interpretation.
  3. Multi-timestep awareness. The filename output.30000.vts implies a time series. I had no way to discover sibling timesteps, load a sequence, or
  show how the fire evolved. Even just listing related files by pattern would help.

  Presentation gaps:

  4. Text annotations on the scene. I couldn't label "fire head," "ridge," "burn scar" directly on the image. I had to explain everything in separate
  text, which disconnects the explanation from the visual. A annotate(position, label) or even annotate_2d(x, y, text) would make screenshots
  self-explanatory.
  5. Multi-panel / side-by-side layouts. I had to show fields one at a time. A 2x2 grid comparing theta, O2, w, and fuel on the same cross-section
  would have been far more effective for a human than four sequential screenshots.
  6. 2D chart rendering. Histograms and statistics come back as text. An actual rendered line plot or histogram image (e.g., from the profile probe
  above) would communicate distributions and gradients much more effectively than numbers.
  7. Camera orbit / turntable. A short animated rotation around the fire plume would help a human grasp the 3D structure far better than any single
  viewpoint I can choose. Even 4-6 frames from different angles returned as a strip would help.

  If I had to pick just two, it would be line probes with plots and scene annotations - those are where I felt the biggest gap between what I
  understood about the data and what I could actually communicate visually.



# Session 2

# VisLang MCP Server Feedback

Feedback from a session exploring vorticity structure in a 3D atmospheric simulation (`output.30000.vts`, 18.3M points, 600x500x61 structured grid with 9 scalar/vector fields).

---

## 1. Vector Component Extraction is Broken/Missing

**This was the single biggest obstacle in the session.** I needed to extract the x-component of a computed vorticity vector to visualize signed vorticity on cross-sections. This should be trivial but I could not make it work.

### What I tried

The `gradient()` helper correctly computed a `vorticity_vec` (3-component vector). But extracting a single component failed in every way I attempted:

```python
# Attempt 1: dot product with iHat (VTK calculator syntax)
omega_x = filter("vtkArrayCalculator", input=vort,
    ResultArrayName="omega_x",
    Function="vorticity_vec . iHat")
# Result: omega_x not present in output arrays

# Attempt 2: component suffix syntax
omega_x = calculator(input=vort,
    Function="vorticity_vec_X",
    ResultArrayName="omega_x",
    AddScalarArrayName=["vorticity_vec"])
# Result: omega_x not present in output arrays

# Attempt 3: using the raw vtkArrayCalculator filter
omega_x = filter("vtkArrayCalculator", input=vort,
    ResultArrayName="omega_x",
    Function="vorticity_vec . iHat")
# Same failure
```

In all cases, the pipeline reported "ok" for `show()` directives referencing `omega_x`, but the field didn't actually exist — the slices silently fell back to coloring by something else.

### What I wish I could have written

```python
# Option A: Built-in component extraction
omega_x = extract_component(input=vort, field="vorticity_vec", component=0, result="omega_x")

# Option B: compute_vorticity returning the vector, not just magnitude
vort_vec = compute_vorticity(input=vel, result="vorticity", vector=True)
# Then component extraction works

# Option C: show() supporting component selection for vector fields
show(cut, "slice", color_by="vorticity_vec", component=0, scalar_range=(-3, 3))
```

**Option C would be the most impactful** — VTK's mapper already supports `SetArrayComponent()`, so this should be straightforward to expose. ParaView does this in its UI; it's a critical feature for any vector field analysis.

### Recommendation

1. Add a `component` parameter to `show()` for coloring by a single component of a vector field
2. Make `compute_vorticity()` return the vector (with an option for magnitude)
3. Fix or document the `calculator()` function — it silently produces no output without error messages, which is very confusing
4. Add a dedicated `extract_component()` helper

---

## 2. Silent Failures in the Calculator

The `calculator()` / `vtkArrayCalculator` filter silently failed to produce output fields in multiple attempts. The pipeline reported success, `show()` directives said "ok", but the resulting field didn't exist. This led to slices colored by fallback values with no indication anything was wrong.

**What should happen:** If a calculator function fails to evaluate or produce the named result array, the pipeline report should say so explicitly. Something like:

```
omega_x: vtkArrayCalculator -> WARNING: ResultArrayName 'omega_x' not found in output.
  Function "vorticity_vec . iHat" may have failed to evaluate.
  Available arrays: [...]
```

The current behavior — silently succeeding while producing no useful output — cost me 3-4 round trips of debugging.

---

## 3. VOI Index vs. Coordinate Confusion

`vtkExtractGrid` uses grid indices for `VOI`, but the data is in physical coordinates. I wasted two iterations extracting the wrong region because I guessed indices incorrectly. The grid mapping (e.g. 600x500x61 grid spanning X=[-498,700], Y=[-500,498]) isn't obvious.

### What I wish existed

```python
# Extract by physical coordinate bounds instead of grid indices
near_ground = extract_region(input=data, bounds=(-50, 250, -100, 70), k_range=(0, 2))

# Or at minimum, a helper that converts coordinates to indices
# ix, iy, iz = grid_indices(node="data", x=80, y=-15, z=165)
```

Alternatively, `extract_grid()` could accept either `VOI` (indices) or `bounds` (coordinates) and do the conversion internally. The `clip_box()` function works on coordinates and I ended up switching to that, but it's much slower (converts structured to unstructured) and doesn't preserve the grid topology needed for `extract_grid`-style operations.

---

## 4. Too Many Round Trips for Basic Exploration

### 4a. The opening sequence is always 3-4 calls deep before I can think

Every session starts the same way:

```
Call 1: list_data_files()              → "output.30000.vts (1.1 GB)"
Call 2: quick_start("output.30000.vts")  → suggested pipeline + loads data
Call 3: describe_data()                → dimensions, bounds, field names+ranges
Call 4: get_field_summary("data", "fieldA")  → percentiles, distribution shape
Call 5: get_field_summary("data", "fieldB")  → percentiles, distribution shape
Call 6: get_field_summary("data", "fieldC")  → percentiles, distribution shape
```

That's 6 calls before I have enough context to make my first real decision. And calls 4-6 are parallelizable but still separate because `describe_data()` only returns min/max, which is actively misleading for skewed fields. A field might report a range of [299, 1184] but 99% of values are in [300, 301] — I can't plan a colormap or isosurface without knowing that distribution shape.

**Ideal: one call gives me everything I need to plan**

```
Call 1: describe_data("output.30000.vts")
```

Returns:
- Grid type, dimensions, bounds, coordinate-to-index mapping
- For EACH field: min, max, mean, std, percentiles (p1, p5, p25, p50, p75, p95, p99)
- Distribution shape flag: "uniform", "skewed", "bimodal", "sparse"
- For structured grids: whether the grid is terrain-following (z varies at iz=0), ground z range
- Suggested quick_start pipeline (already included)

With that single response, I'd see immediately which fields are skewed, which are sparse, where the interesting features are concentrated in space, and I'd plan my visualization without any follow-up field-by-field queries.

### 4b. Describing data requires loading it first

```python
# This fails:
describe_data()  # "No pipeline is active"

# Must do this first:
quick_start("output.30000.vts")  # loads data
describe_data()  # now works
```

I'd prefer `describe_data("output.30000.vts")` to work directly without requiring a pipeline. Or have `quick_start()` return the description alongside the suggested pipeline.

### 4c. Field statistics require separate calls per field

With N fields in the dataset, understanding distributions requires N separate `get_field_summary()` calls. `describe_data()` returns field ranges but not percentiles or distribution info.

**Wish:** `describe_data()` should include percentile summaries (p1, p25, p50, p75, p99) for each field. The full range alone is often misleading for real-world scientific data, which is frequently skewed. Having percentiles upfront would eliminate most follow-up `get_field_summary()` calls.

### 4d. Spatial extent + ground_z + statistics = 3 tools to position one seed line

To place streamline seeds correctly, I needed:
1. `get_spatial_extent()` to find where interesting features are
2. `get_ground_z()` to find valid z-coordinates (terrain-following grid)
3. `get_statistics()` to understand the velocity field range

These three are always needed together when setting up streamlines on terrain-following grids. A combined response or smarter `seeds_near()` would help:

```python
# This exists but unclear if it handles terrain-following grids:
seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200, num_seeds=20, offset_z=10)

# Even more useful: seed around an isosurface with an offset
seeds = seeds_ring(input=data, field="fieldA", isoval=350, offset=20, num_seeds=30)
# Places seeds in a ring around the isosurface, offset outward by some distance
```

### 4e. `set_pipeline` returns useful info — but I have to screenshot separately

Every `set_pipeline` call is followed immediately by `screenshot()`. That's 2 calls that are always sequential and never decoupled. The pipeline result already says "screenshot 0.08s" in the timing — it's already rendering one internally.

**Wish:** `set_pipeline` should return the screenshot image by default (with an opt-out flag for batch/scripting use). This would halve the number of round trips during iterative visualization development, which is the primary workflow.

Similarly, `set_camera`, `set_colormap`, `set_opacity`, `toggle_visibility`, and `set_background` should all return a screenshot. Any tool that changes the visual output should show me what it looks like now. I should never need to call `screenshot()` as a separate step.

### 4f. `get_examples()` is a wall of text I have to parse to find patterns

`get_examples()` returns ~100 lines of generic patterns covering every visualization type. When I've already loaded a structured grid with known fields, I don't need to see raw binary volume loading or image data patterns.

**Wish:** Context-aware examples that filter based on what's loaded:

```python
get_examples(node="data")
# Sees the grid type and available arrays, returns only relevant patterns
# (e.g. streamlines, slices, contours for structured grids with vector-like fields)
# with actual field names and data-derived value ranges substituted in
```

This would also eliminate the pattern where I call `get_examples()`, read through it, then still need `get_statistics()` to fill in the actual value ranges. The examples could come pre-populated with real values from the loaded data.

### 4g. `sample_point()` one point at a time

To map a flow structure, I needed to probe ~12 points across a cross-section. That was 12 separate tool calls (in batches of 5-6 due to parallelism limits). Each returned all 9 fields when I only cared about 3.

**Wish:** Batch probing with field selection:

```python
sample_points(node="data", 
    points=[(80,-60,z) for z in [165, 200, 230]],
    fields=["v", "w", "theta"])
# Returns a compact table:
#   (80,-60,165): v=+0.62, w=-0.29, theta=300.3
#   (80,-60,200): v=-0.82, w=-4.50, theta=339.9
#   (80,-60,230): v=-2.12, w=+0.18, theta=300.1

# Even better: probe along a line with automatic sampling
sample_line(node="data",
    start=(80, -80, 165), end=(80, 60, 165), num_points=20,
    fields=["v", "w", "theta"])
# Returns a 1D profile that directly shows field variation along a transect
```

This would replace 12 calls with 2-3, and the line profile format would directly show spatial patterns without manually assembling a table from individual point samples.

### 4h. Camera positioning is trial-and-error

I spent several iterations getting the camera right. `suggest_camera("overview")` gives one fixed suggestion; there's no way to say "zoom into this region" or "look at this from this direction." I ended up manually specifying camera positions and focal points, which requires mental 3D geometry.

**Wish:**

```python
# Focus on a specific actor or named feature
suggest_camera(focus_on="my_actor_name", style="overview", distance=500)

# Or: aim the camera based on a coordinate box
suggest_camera(look_at_bounds=(20, 140, -70, 40, 155, 280), direction="southeast")
```

---

## 5. Missing Visualization Primitives

### Vector glyphs on slices

I wanted to show velocity vectors on a Y-Z cross-section to directly illustrate a circulation pattern. This would have been far more intuitive than coloring by individual components separately. VTK supports `vtkGlyph3D` with arrow sources, but building in-plane vector glyphs from scalar components on a slice is very awkward in the DSL:

```python
# What I wish I could write:
cut = slice(input=data, origin=(80, -15, 200), normal=(1, 0, 0))
show_vectors(cut, "flow_arrows",
    components=("v", "w"),        # project velocity into the slice plane
    scale=5.0, every_nth=3,       # subsample for clarity
    color_by="speed",
    scalar_range=(0, 10))
```

This single visualization would have replaced two separate scalar-colored cross-section images and been much easier for the user to interpret.

### Annotations and markers

I probed specific points to build a velocity table in my analysis. It would have been powerful to mark these sample locations on the visualization:

```python
# Mark probed points with labels
mark_point(x=80, y=30, z=165, label="v=-0.8, w=-0.5", color="white")
mark_point(x=80, y=-60, z=165, label="v=+0.6, w=-0.3", color="white")

# Draw arrows to annotate flow directions
annotate_arrow(start=(80, 30, 165), end=(80, -15, 165), label="inflow", color="cyan")
```

### Schematic overlays

For explaining structures like vortex pairs, I resorted to ASCII art in my text response. Being able to overlay schematic elements (circles, arrows, labels at specified positions) on the rendered image would have been far more effective:

```python
# Overlay annotation arrows and labels on the rendered image
overlay_arrow(from_yz=(-70, 170), to_yz=(-15, 170), color="red", label="inflow")
overlay_arrow(from_yz=(40, 170), to_yz=(-15, 170), color="red", label="inflow")
overlay_arrow(from_yz=(-15, 240), to_yz=(-70, 240), color="orange", label="outflow")
overlay_text(position_yz=(-15, 200), text="updraft ↑", color="white")
```

---

## 6. Colormap and Color Bar Issues

### `cool_to_warm` for diverging data is good, but the white midpoint is hard to see on a light background

When I switched to `scene_preset("dark")`, the white midpoint of `cool_to_warm` became very visible against the dark background — good. But on the default light background, zero-values are invisible. A diverging colormap with a dark midpoint (like ParaView's "Blue-Orange Diverging") would be useful.

### Color bars overlap when multiple are shown

With 3 scalar bars (from different actors), they stacked up on the right side and overlapped. There's no way to control their placement or reduce their size:

```python
# Wish: control color bar position and size
show(data, "field", scalar_bar="Label",
    scalar_bar_position=(0.85, 0.1), scalar_bar_height=0.3)
```

---

## 7. Report Generation and Image Export

If I wanted to create a final PDF/HTML report with all the visualizations and analysis, the current workflow is painful: screenshots are returned inline in the conversation but there's no way to save them to files with names, organize them, or reference them later.

### What would help

```python
# Save named screenshots to disk
screenshot(save_as="fig1_overview.png", resolution=(1920, 1080))
screenshot(save_as="fig2_cross_section.png", resolution=(1920, 1080))

# Or batch export of the current view at multiple angles
export_turntable(prefix="overview", frames=8, resolution=(1920, 1080))
```

### Image quality

The default screenshot resolution seems adequate but there's no way to control it. For publication-quality figures, I'd want:

```python
set_window_size(1920, 1080)  # This exists, good
screenshot(magnification=2)   # 2x supersampling for anti-aliasing
screenshot(transparent_background=True)  # For compositing in papers
```

---

## 8. Interactive Features I Wanted

### Linked brushing / probing

Rather than calling `sample_point()` many times with manually chosen coordinates, I'd want:

```python
# Click-to-probe mode: user clicks on the visualization and gets field values
enable_probe_mode()  # Returns coordinates and field values for each click
```

### Animation / time series

When multiple timesteps are available, understanding temporal evolution is important:

```python
# Load a time series and animate
data = source("vtkXMLStructuredGridReader", FileName="output.*.vts")
animate(data, field="fieldA", timesteps=range(25000, 35000, 1000))

# Or compare two timesteps side-by-side
compare_timesteps("output.25000.vts", "output.30000.vts", field="fieldA",
    title_left="t=25000", title_right="t=30000")
```

### Linked multi-view

The most effective way to compare multiple fields on the same geometry would be synchronized views:

```python
# Four linked panels showing the same region colored by different fields
layout = multi_view(rows=2, cols=2)
layout[0,0].show(cut, color_by="v", scalar_range=(-8,8), lut="cool_to_warm")
layout[0,1].show(cut, color_by="w", scalar_range=(-8,8), lut="cool_to_warm")
layout[1,0].show(cut, color_by="theta", scalar_range=(298,400), lut="fire")
layout[1,1].show(cut, color_by="speed", scalar_range=(0,15), lut="wind")
# All four panels share the same camera, zoom, etc.
```

This would replace the sequential "now let me show the same slice colored by w" workflow and produce a single image showing all four fields simultaneously.

### Adjustable slice position

For exploring where structures are strongest along an axis:

```python
# Interactive slider to move a slice through the domain
interactive_slice(input=data, normal=(1,0,0), range=(0, 200),
    color_by="v", scalar_range=(-8,8))
# User can drag the slice position and see structure evolve spatially
```

---

## 9. Compute Pipeline Suggestions

### Vorticity should return a vector, not just magnitude

The `compute_vorticity()` helper internally uses `vtkCellDerivatives` + `vtkCellDataToPointData` and returns only the magnitude. The `gradient()` helper uses `vtkGradientFilter` which can compute vorticity as a vector. These two paths should be unified:

```python
vort = compute_vorticity(input=vel, result="vorticity")
# Returns vector field "vorticity" AND scalar "vorticity_magnitude"
```

Combined with `show(..., component=0)` from Section 1, this would make vorticity component analysis a two-liner instead of an impossible task.

### Horizontal divergence

For any flow analysis, horizontal divergence (du/dx + dv/dy) is commonly needed but requires manual gradient computation and calculator assembly. A helper would be useful:

```python
div_h = compute_divergence(input=data, components=("u", "v"), result="div_horizontal")
# Or for 3D:
div_3d = compute_divergence(input=data, components=("u", "v", "w"), result="divergence")
```

---

## 10. Minor Issues

### `title()` placement

The title text rendered at the very bottom of the screen and was partially cut off in the screenshot. Would be better to default to top-center, and support named positions:

```python
title("My Title", position="top_center", padding=20)
```

### `scene_preset("dark")` changes everything

When I switched to `scene_preset("dark")`, some layers became very hard to see because the lighting changed along with the background. A way to change just the background without affecting lighting:

```python
background(0.1, 0.1, 0.15)  # This works but doesn't set up nice lighting
scene_preset("dark", preserve_lighting=True)
```

### No way to control lighting direction

For terrain or surface visualization, angled lighting helps show topographic detail. There's no way to set light direction:

```python
light(direction=(1, -1, -1), intensity=0.8, color=(1, 1, 0.95))
```

---

## 11. Ideal vs. Actual Round-Trip Comparison

Here's the actual sequence of MCP calls I made, vs. what the ideal would look like:

### Actual session: ~40 MCP calls

```
 1. list_data_files()                        # what's here?
 2. describe_data()                          # fails — no pipeline active
 3. get_examples()                           # how do I use this?
 4. quick_start("output.30000.vts")          # load data
 5. describe_data("data")                    # now it works
 6. screenshot()                             # what does it look like?
 7. get_field_summary("data", "fieldA")      # distribution of field A?
 8. get_field_summary("data", "fieldB")      # distribution of field B?
 9. get_field_summary("data", "fieldC")      # distribution of field C?
10. set_pipeline(...)                        # first real visualization
11. screenshot()                             # check it
12. suggest_camera("overview")               # bad default angle
13. set_pipeline(... with camera fix)        # rebuild
14. screenshot()                             # check
15. get_bounds("actor_name")                 # where is the feature?
16. get_statistics("data", "u")              # velocity range?
17. get_ground_z("data", x1, y1)             # terrain height?
18. get_ground_z("data", x2, y2)             # terrain height elsewhere?
19. set_pipeline(... add streamlines)        # add flow vis
20. screenshot()                             # check
21. set_pipeline(... different camera)       # adjust view
22. screenshot()                             # check
23. get_spatial_extent(field, lo, hi)         # where are interesting values?
24. get_spatial_extent(field2, lo, hi)        # where is another feature?
25. list_capabilities()                      # what filters/helpers exist?
26-30. set_pipeline + screenshot (×5)        # iterating on derived field vis
31-36. sample_point (×12, in batches)        # probing flow structure
37-42. set_pipeline + screenshot (×5+)       # final presentation views
```

### Ideal session: ~12 MCP calls

```
 1. describe_data("output.30000.vts")
    → Returns: grid info, ALL field stats with percentiles,
      grid coordinate mapping, terrain-following detection,
      suggested pipeline, AND initial screenshot
    
    After ONE call I know every field's distribution shape,
    where the data is interesting, and can plan my full approach.

 2. set_pipeline(... first visualization)
    → Returns pipeline status AND screenshot automatically
    
 3. set_pipeline(... derived field cross-sections)
    → Uses: color_by="vorticity_vec", component=0 (just works)
    → Returns screenshot automatically
    
 4. sample_points("data",
       points=[(x,y,z) for y in [y1,y2,y3] for z in [z1,z2,z3]],
       fields=["v","w","theta"])
    → Returns compact table of all 9 points in one call
    
 5. set_pipeline(... horizontal slice view)
    → Returns screenshot automatically
    
 6. set_pipeline(... multi-panel: 4 fields on same slice)
    → Returns screenshot (4 panels in one image!)

 7-12. Refinement iterations, each 1 call instead of 2
```

### Key savings:
- **Calls 1-9 → 1 call**: Rich `describe_data` with percentiles eliminates all follow-up field queries
- **Every set_pipeline+screenshot pair → 1 call**: Auto-screenshot saves ~12 calls over the session
- **12 sample_point calls → 1 sample_points call**: Batch probing
- **2 separate v/w views → 1 multi-panel call**: Linked views
- **No camera trial-and-error**: Focus-on-actor camera suggestions
- **No VOI index guessing**: Coordinate-based extraction

That's roughly **40 calls → 12 calls**, and more importantly, the *thinking* between calls is more productive because each response gives me enough context to make a fully informed next decision, rather than revealing one piece of information that prompts yet another query.

---

## Summary: Top 5 Priorities

1. **Vector component coloring in `show()`** — `color_by="field", component=0`. This is a fundamental gap for any vector field analysis.

2. **Fix `calculator()` silent failures** — errors should be reported, not swallowed. This cost the most debugging time.

3. **Rich `describe_data` + auto-screenshot from `set_pipeline`** — these two changes together would cut session round trips roughly in half. Every state-changing tool should return a screenshot.

4. **In-plane vector glyphs on slices** — `show_vectors()` on a cross-section would be worth more than any number of scalar-colored slices for understanding flow structure.

5. **Batch probing (`sample_points` / `sample_line`)** — probing one point at a time with all fields returned is extremely inefficient for building spatial understanding of a flow field.
