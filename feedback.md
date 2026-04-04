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

Feedback from a session exploring vorticity-driven lateral fire spread in a FIRETEC wildfire simulation (`output.30000.vts`, 18.3M points, 600x500x61 structured grid).

---

## 1. Vector Component Extraction is Broken/Missing

**This was the single biggest obstacle in the session.** I needed to extract the x-component of the vorticity vector (streamwise vorticity, omega_x) to visualize counter-rotating vortex pairs. This should be trivial but I could not make it work.

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

`vtkExtractGrid` uses grid indices for `VOI`, but the data is in physical coordinates. I wasted two iterations extracting the wrong region because I guessed indices incorrectly. The grid mapping (600x500x61 grid spanning X=[-498,700], Y=[-500,498]) isn't obvious.

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

### Describing data requires loading it first

```python
# This fails:
describe_data()  # "No pipeline is active"

# Must do this first:
quick_start("output.30000.vts")  # loads data
describe_data()  # now works
```

I'd prefer `describe_data("output.30000.vts")` to work directly without requiring a pipeline. Or have `quick_start()` return the description alongside the suggested pipeline.

### Field statistics require separate calls per field

I needed statistics for theta, rhof_1, O2, and the velocity components. That's 6 separate `get_statistics()` calls or 6 `get_field_summary()` calls. `describe_data()` returns field ranges but not percentiles or distribution info.

**Wish:** `describe_data()` should include percentile summaries (p1, p25, p50, p75, p99) for each field. The full range alone is often misleading (e.g., theta ranges 299-1184K but 99% of values are 300-301K). Having percentiles upfront would have saved me 3 separate `get_field_summary()` calls.

### Spatial extent + ground_z + statistics = 3 tools to position one seed line

To place streamline seeds correctly, I needed:
1. `get_spatial_extent()` to find where the fire is
2. `get_ground_z()` to find valid z-coordinates
3. `get_statistics()` to understand the velocity field

A combined tool or smarter `seeds_near()` would help:

```python
# This exists but I didn't try it — does it handle terrain-following grids?
seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200, num_seeds=20, offset_z=10)

# What I'd really want for flank analysis:
seeds = seeds_ring(input=data, field="theta", isoval=350, offset=20, num_seeds=30)
# Places seeds in a ring around the theta=350 isosurface, offset outward by 20m
```

---

## 5. Missing Visualization Primitives

### Vector glyphs on slices

I wanted to show v/w velocity vectors on the Y-Z cross-section to directly illustrate the vortex circulation. This would have been far more intuitive than coloring by v and w separately. VTK supports `vtkGlyph3D` with arrow sources, but applying it to a slice with in-plane components is awkward:

```python
# What I wish I could write:
cut = slice(input=data, origin=(80, -15, 200), normal=(1, 0, 0))
show_vectors(cut, "vortex_arrows",
    components=("v", "w"),        # project velocity into the slice plane
    scale=5.0, every_nth=3,       # subsample for clarity
    color_by="speed",
    scalar_range=(0, 10))
```

This single visualization would have replaced my two separate v/w cross-section images and been much easier for the user to interpret.

### Annotations and markers

I probed specific points to build the velocity table in my analysis. It would have been powerful to mark these on the visualization:

```python
# Mark probed points with labels
mark_point(x=80, y=30, z=165, label="v=-0.8, w=-0.5", color="white")
mark_point(x=80, y=-60, z=165, label="v=+0.6, w=-0.3", color="white")

# Or annotate regions
annotate_arrow(start=(80, 30, 165), end=(80, -15, 165), label="inflow", color="cyan")
annotate_arrow(start=(80, -60, 165), end=(80, -15, 165), label="inflow", color="cyan")
annotate_arrow(start=(80, -15, 230), end=(80, 50, 230), label="outflow", color="orange")
```

### Schematic overlays

For explaining vortex structures, I resorted to ASCII art in my text response. Being able to overlay schematic elements (circles with rotation arrows, flow direction arrows) on the screenshot would have been far more effective:

```python
# Overlay a schematic vortex pair on the YZ cross-section
overlay_circle(center_yz=(-40, 200), radius=30, rotation="ccw", color="cyan", label="south vortex")
overlay_circle(center_yz=(20, 200), radius=30, rotation="cw", color="orange", label="north vortex")
overlay_arrow(from_yz=(-70, 170), to_yz=(-15, 170), color="red", label="entrainment")
```

---

## 6. Colormap and Color Bar Issues

### `cool_to_warm` for diverging data is good, but the white midpoint is hard to see on a light background

When I switched to `scene_preset("dark")`, the white midpoint of `cool_to_warm` became very visible against the dark background — good. But on the default light background, zero-values are invisible. A diverging colormap with a dark midpoint (like ParaView's "Blue-Orange Diverging") would be useful.

### Color bars overlap when multiple are shown

With 3 scalar bars (fuel, temperature, wind speed), they stacked up on the right side and overlapped. There's no way to control their placement or reduce their size:

```python
# Wish: control color bar position and size
show(data, "field", scalar_bar="Temperature (K)", 
    scalar_bar_position=(0.85, 0.1), scalar_bar_height=0.3)
```

---

## 7. Report Generation and Image Export

If I wanted to create a final PDF/HTML report with all the visualizations and analysis, the current workflow is painful: screenshots are returned inline in the conversation but there's no way to save them to files with names, organize them, or reference them later.

### What would help

```python
# Save named screenshots to disk
screenshot(save_as="fig1_overview.png", resolution=(1920, 1080))
screenshot(save_as="fig2_vorticity_yz.png", resolution=(1920, 1080))

# Or batch export of the current view at multiple angles
export_turntable(prefix="fire_plume", frames=8, resolution=(1920, 1080))

# Export a report with all saved figures
export_report("fire_analysis.html", figures=["fig1_overview.png", "fig2_vorticity_yz.png"],
    captions=["Overview of fire plume", "Y-Z vorticity cross-section"])
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

Rather than calling `sample_point()` six times with manually chosen coordinates, I'd want:

```python
# Click-to-probe mode: user clicks on the visualization and gets field values
enable_probe_mode()  # Returns coordinates and field values for each click
```

### Animation / time series

This is timestep 30000. To understand the *evolution* of lateral spread, I'd need multiple timesteps:

```python
# Load a time series and animate
data = source("vtkXMLStructuredGridReader", FileName="output.*.vts")
animate(data, field="theta", timesteps=range(25000, 35000, 1000))

# Or compare two timesteps side-by-side
compare_timesteps("output.25000.vts", "output.30000.vts", field="rhof_1",
    title_left="t=25000", title_right="t=30000")
```

### Linked multi-view

The most effective way to present vortex structure would be synchronized views:

```python
# Four linked panels showing the same region
layout = multi_view(rows=2, cols=2)
layout[0,0].show(cut, color_by="v", scalar_range=(-8,8), lut="cool_to_warm")
layout[0,1].show(cut, color_by="w", scalar_range=(-8,8), lut="cool_to_warm")
layout[1,0].show(cut, color_by="theta", scalar_range=(298,400), lut="fire")
layout[1,1].show(cut, color_by="speed", scalar_range=(0,15), lut="wind")
# All four panels share the same camera, zoom, etc.
```

This would have replaced my sequential "now let me show the same slice colored by w" workflow and given the user a single image showing all four fields simultaneously.

### Adjustable slice position

For exploring where the CRVP structure is strongest:

```python
# Interactive slider to move a slice through the domain
interactive_slice(input=data, normal=(1,0,0), range=(0, 200), 
    color_by="v", scalar_range=(-8,8))
# User can drag the slice position and see the vortex structure evolve spatially
```

---

## 9. Compute Pipeline Suggestions

### Derived quantities

Common fire-atmosphere analysis quantities that could be built-in:

```python
# Fire-relevant derived fields
fire_intensity = compute_fire_intensity(input=data, 
    fuel="rhof_1", temperature="theta", velocity=("u","v","w"))

# Horizontal divergence (important for fire spread analysis)  
div_h = compute_horizontal_divergence(input=data, u="u", v="v")

# Vorticity components (not just magnitude!)
vort = compute_vorticity_vector(input=data, velocity="velocity", result="vorticity")
# Then: show(..., color_by="vorticity", component=0)  # omega_x
```

### The `compute_vorticity()` helper internally uses `vtkCellDerivatives` + `vtkCellDataToPointData`

I noticed from the pipeline report that `compute_vorticity()` goes through cell derivatives. The `gradient()` helper uses `vtkGradientFilter` which also computes vorticity but as a vector. These two paths should be unified — ideally `compute_vorticity()` should return the vector and optionally add the magnitude:

```python
vort = compute_vorticity(input=vel, result="vorticity")  
# Returns vector field "vorticity" AND scalar "vorticity_magnitude"
```

---

## 10. Minor Issues

### `title()` placement

The title text rendered at the very bottom of the screen and was cut off in the screenshot. Would be better to default to top-center, and support padding/margin:

```python
title("My Title", position="top_center", padding=20)
```

### `scene_preset("dark")` changes everything

When I switched to `scene_preset("dark")`, the ground surface and several translucent layers became very hard to see because the default lighting changed. A preset that only changes the background color (not lighting) would be useful:

```python
background(0.1, 0.1, 0.15)  # This works but doesn't set up nice lighting
scene_preset("dark", preserve_lighting=True)
```

### No way to control lighting direction

For terrain visualization with the ground plane, angled lighting helps show topography. There's no way to set light direction:

```python
light(direction=(1, -1, -1), intensity=0.8, color=(1, 1, 0.95))
```

---

## Summary: Top 5 Priorities

1. **Vector component coloring in `show()`** — `color_by="field", component=0`. This is a fundamental gap for any vector field analysis.

2. **Fix `calculator()` silent failures** — errors should be reported, not swallowed. This cost the most debugging time.

3. **Multi-panel / linked views** — showing the same slice colored by v, w, theta, and speed simultaneously would transform the presentation quality.

4. **In-plane vector glyphs on slices** — `show_vectors()` on a cross-section would be worth more than any number of scalar-colored slices for understanding flow structure.

5. **Named screenshot export** — `screenshot(save_as="filename.png")` for report generation.
