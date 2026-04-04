# Challenges Encountered During VisLang Development

This documents the pain points and challenges found while building wildfire
visualizations, with ideas for how the project can address them.

## 1. Terrain-Following Grid Coordinates

**Problem**: The wildfire dataset uses a terrain-following coordinate system
where z=0 in index space does NOT correspond to z=0 in physical space. The
ground z-coordinate varies from ~0.75 to ~196 depending on (x,y) location.
This means:
- Placing streamline seed points at z=50 puts them *below* the terrain
- Seed points produce 0 streamline points with no error message
- You have to call `get_ground_z` or `get_spatial_extent` to find valid z values

**Example**: I tried placing seeds at z=50 and got empty streamlines. The
ground at (80,-10) is actually at z=159.

**Potential solutions**:
- **Default seed placement**: When creating a StreamTracer without explicit
  SeedSource, automatically generate seed points within the data bounds using
  the centroid and a reasonable offset from the ground
- **Validation in set_pipeline**: Check if seed source points are outside the
  data bounds and emit a warning with the actual bounds
- **Smart z-lookup**: A DSL helper like `ground_z(data, x, y)` that can be
  used inline in seed definitions
- **Automatic seed from spatial extent**: e.g., `seeds_near(data, "theta", 400, 1200)`
  to create seeds in the region where a field is in range

## 2. Active Vectors Not Set for StreamTracer/Glyph3D

**Problem**: vtkStreamTracer and vtkGlyph3D require active vectors to be set
on their input data, but the vtkArrayCalculator output doesn't automatically
set the result as active vectors. This means streamlines silently produce 0
output even when the velocity field exists.

**Example**: Computing `velocity = u*iHat + v*jHat + w*kHat` with
vtkArrayCalculator creates the "velocity" array, but vtkStreamTracer can't
find it as vectors unless `SetActiveVectors("velocity")` is called on the
point data.

**Current fix**: `create_vtk_filter` automatically scans for 3-component
arrays and sets the first one as active vectors for StreamTracer/Glyph3D.

**Better solutions**:
- When `Vectors="velocity"` is specified, explicitly set active vectors on
  the input data
- Validate that the named vector array exists before creating the filter
- Emit a clear error if no 3-component array is found when it's required

## 3. Colormap Calibration for Skewed Distributions

**Problem**: Most field values cluster near ambient levels (e.g., theta is
~300K everywhere with a tiny fraction at 400-1200K for fire). Using a simple
scalar_range maps 99%+ of the data to one end of the colormap, making most
of the visualization a single color.

**Example**: The O2 field ranges from 0.086 to 0.23, but almost all values
are near 0.23 (ambient). Using `scalar_range=(0.086, 0.23)` with
cool_to_warm makes the entire terrain red/white since 99%+ of values are
near 0.23.

**Potential solutions**:
- **Quantile-based scalar ranges**: Auto-compute a useful range from the
  histogram (e.g., 1st and 99th percentiles) instead of requiring manual
  specification
- **Non-linear colormaps**: Support log-scale or custom transfer functions
  that handle skewed distributions
- **Domain-specific presets**: Pre-configured colormaps with appropriate
  ranges for known fields (theta, O2, rhof_1, etc.)
- **Histogram-aware suggestions**: When `get_statistics` is called, suggest
  a scalar_range based on the distribution

## 4. VTK Property API Inconsistencies

**Problem**: VTK classes have inconsistent APIs. Some accept property-style
setters (`SetScaleFactor`), some use enum setters
(`SetVectorModeToComputeVorticity`), some use index-based APIs
(`SetValue(0, 400.0)` for contours). The DSL must handle each specially.

**Example**: `MaximumNumberOfSamplePoints` was listed in the DESIGN.md for
vtkGlyph3D but doesn't exist in the VTK 9.x API. The correct approach is
different.

**Potential solutions**:
- **Comprehensive property validation**: Before calling `Update()`, check
  that all properties were actually applied (no-ops detected)
- **VTK API introspection**: Use VTK's method list to validate property
  names at set time rather than at update time
- **ParaView XML metadata** (Phase 2): Use ParaView's server manager XML
  files to auto-map property names to correct VTK API calls

## 5. Empty Output Without Clear Cause

**Problem**: Filters can produce 0-point output for many different reasons
(wrong field name, out-of-range threshold, seed points outside data, etc.)
but VTK doesn't distinguish these cases. The only signal is 0 output points.

**Example**: A contour at theta=50 produces 0 points because all theta values
are >298. The system says "Filter produced empty output" but doesn't
explain *why*.

**Potential solutions**:
- **Smart empty-output diagnostics**: When a filter produces 0 output, check
  the input data to diagnose why:
  - For ContourFilter: check if contour values are within the field's range
  - For Threshold: check if any values fall in the range
  - For StreamTracer: check if seeds are inside the data bounds
- **Pre-flight validation**: Before running a filter, check if the parameters
  make sense given the input data
- **Suggested fixes in error messages**: "Contour at theta=50 is empty.
  theta range is [298.8, 1183.9]. Try a value in this range."

## 6. Camera Positioning

**Problem**: Finding a good camera position requires understanding the 3D
scene geometry. The terrain-following grid makes this harder because the
"center" of the scene isn't at obvious coordinates.

**Example**: The fire at x=[28,134], y=[-46,24], z=[132,221] means a good
focal point is around (80, -10, 170), but this isn't obvious without
querying spatial extent first.

**Potential solutions**:
- **Auto-camera**: Default camera that frames the visible actors
  (currently does `reset_camera()` which is a start)
- **Camera presets**: Named views like "overview", "closeup_fire",
  "top_down" that compute positions from the current actors
- **Camera from feature**: `camera_looking_at(node="fire", distance=500)`
  that computes a good view of a specific pipeline node

## 7. Pipeline Rebuild Cost

**Problem**: Every `set_pipeline` call rebuilds the entire pipeline from
scratch, including re-reading the 1GB VTS file. On real data this takes
2-5 seconds. The tear-down/rebuild design is simple but makes iteration slow.

**Potential solutions**:
- **Reader caching**: VTK's XML reader already caches, but we could
  explicitly cache the reader output and skip re-reading
- **Incremental updates** (Phase 2 reconciler): Diff the previous and new
  pipeline specs and only update changed nodes
- **Persistent data source**: Keep the reader alive across set_pipeline calls
  and only reconnect filters

## 8. Visualization Quality Defaults

**Problem**: VTK's default rendering settings (lighting, background, line
width) produce bland visualizations. Every pipeline needs manual specular,
opacity, and colormap tuning.

**Potential solutions**:
- **Smart defaults by filter type**: Contours get specular=0.3, terrain
  gets ambient=0.2, streamlines get line_width=2
- **Scene presets**: "scientific" (white background, no specular),
  "presentation" (dark background, good lighting), "publication" (clean
  white background with thin lines)
- **Automatic lighting**: Add a light kit that produces good default
  illumination from multiple angles
