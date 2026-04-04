# VisLang Pipeline Examples

> Auto-generated from source by `python scripts/gen_docs.py`.
> Do not edit by hand — changes will be overwritten.

These patterns are generic — substitute your own file names, field names,
and value ranges.  Use `describe_data()` and `get_statistics()` to find the
right values for your dataset.

---

```python
=== Common Pipeline Patterns ===

These patterns are generic — substitute your own file names, field names,
and value ranges. Use describe_data() and get_statistics() to find the
right values for your dataset.

1. LOAD AND SHOW A FIELD:
data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
show(data, "field", color_by="fieldname", scalar_range=(lo, hi))
scene_preset("dark")

2. EXTRACT A SURFACE SLICE (e.g., ground plane of a structured grid):
surface = filter("vtkExtractGrid", input=data, VOI=[0,NX,0,NY,0,0])
show(surface, "surface", color_by="fieldname", scalar_range=(lo, hi), lut="cool_to_warm")

2b. EXTRACT A SUB-REGION BY PHYSICAL COORDINATES (no index guessing needed):
# Use describe_data() or get_bounds() to find the physical bounds, then:
region = extract_region(input=data, bounds=[xmin, xmax, ymin, ymax, zmin, zmax])
show(region, "region", color_by="fieldname", scalar_range=(lo, hi))
# Works for vtkStructuredGrid and vtkImageData; picks the right VTK filter automatically.
# Use voi= if you already know the grid indices:
# region = extract_region(input=data, voi=[imin, imax, jmin, jmax, kmin, kmax])

3. ISOSURFACE:
# Use suggest_isosurface() to find good values
iso = contour(input=data, ContourBy="fieldname", Isosurfaces=[value])
show(iso, "iso", color_by="fieldname", scalar_range=(lo, hi))

4. THRESHOLD (extract a value range):
region = threshold(input=data, ThresholdBy="fieldname", ThresholdRange=[lo, hi])
show(region, "region", color_by="fieldname", scalar_range=(lo, hi))

5. CROSS-SECTION SLICE:
cut = slice(input=data, origin=(x, y, z), normal=(1, 0, 0))
show(cut, "section", color_by="fieldname", scalar_range=(lo, hi), opacity=0.5)

6. STREAMLINES (vector field):
# First compute a vector from scalar components
velocity = compute_velocity(input=data, components=("vx", "vy", "vz"), result="velocity")
# Create seeds — use a line, plane, or seeds_near()
line_seed = source("vtkLineSource", Point1=(x1,y1,z1), Point2=(x2,y2,z2), Resolution=30)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=line_seed, Vectors="velocity", IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=500)
tubes = tube(input=streams, Radius=1.0, NumberOfSides=8)
show(tubes, "flow", color_by="velocity", opacity=0.7)

7. STREAMLINES WITH PLANAR SEED GRID:
plane_seeds = source("vtkPlaneSource",
    Origin=(x0,y0,z0), Point1=(x1,y1,z1), Point2=(x2,y2,z2),
    XResolution=10, YResolution=8)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=plane_seeds, Vectors="velocity", IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=500)
tubes = tube(input=streams, Radius=1.0, NumberOfSides=8)
show(tubes, "flow", color_by="velocity", opacity=0.6)

8. VOLUME RENDERING (with explicit opacity):
# Use suggest_opacity() to get good opacity control points for your field
region = threshold(input=data, ThresholdBy="fieldname", ThresholdRange=[lo, hi])
show(region, "volume", representation="Volume", color_by="fieldname",
    scalar_range=(lo, hi), lut="cool_to_warm",
    opacity_function=[(lo, 0.0), (mid, 0.1), (hi, 0.5)],
    volume_resolution=200)

9. VOLUME RENDERING (image data — no resampling needed):
data = source("vtkXMLImageDataReader", FileName="data/volume.vti")
show(data, "vol", representation="Volume", color_by="Scalars_",
    scalar_range=(0, 255), lut="grayscale",
    opacity_function=[(0, 0.0), (30, 0.0), (80, 0.01), (120, 0.05), (180, 0.2), (255, 0.6)],
    gradient_opacity=True)

10. RAW BINARY VOLUME:
data = raw_source("data/volume.raw",
    dimensions=(256, 256, 128), scalar_type="unsigned_short")
show(data, "vol", representation="Volume", opacity_function="ct_bone",
    gradient_opacity=True)

11. VECTOR GLYPHS (arrows):
velocity = compute_velocity(input=data, components=("vx","vy","vz"), result="velocity")
speed = compute_magnitude(input=data, components=("vx","vy","vz"), result="speed")
sub = filter("vtkExtractGrid", input=speed, VOI=[...], SampleRate=[8,8,2])
arrow = source("vtkArrowSource", TipResolution=6, ShaftResolution=6)
glyphs = filter("vtkGlyph3D", input=sub,
    GlyphSource=arrow, OrientationArray="velocity",
    ScaleArray="speed", ScaleFactor=5.0)
show(glyphs, "arrows", color_by="speed", scalar_range=(0, max_speed))

12. MULTIPLE ISOSURFACES (using loop):
values = [v1, v2, v3, v4]  # Use suggest_isosurface() to pick these
for val in values:
    iso = contour(input=data, ContourBy="fieldname", Isosurfaces=float(val))
    show(iso, f"iso_{val}", color_by="fieldname", scalar_range=(lo, hi))

13. VECTOR COMPONENT COLORING (e.g., show Z-velocity only):
# Color by a single component of a vector field instead of magnitude
# component accepts 0/1/2 or "x"/"y"/"z"
velocity = compute_velocity(input=data, components=("u", "v", "w"), result="velocity")
show(data, "vertical_wind", color_by="w", scalar_range=(-5, 5), lut="cool_to_warm")
# Or color by a component of an existing vector array:
show(velocity, "vz", color_by="velocity", component="z", lut="cool_to_warm")

14. MAKE VECTOR + CURL (general primitives):
# make_vector assembles scalars into a vector (same as compute_velocity):
vel = make_vector(input=data, components=("u", "v", "w"), result="velocity")
# curl computes the curl of any vector field:
vort = curl(vector_field=vel, result="vorticity", vector=True)         # 3-component curl
vort_mag = curl(vector_field=vel, result="vort_mag", vector=False)     # scalar magnitude
show(iso, "curl_mag", color_by="vort_mag", scalar_range=(lo, hi))
# compute_velocity and compute_vorticity are thin wrappers around these primitives.

15. SCENE ANNOTATIONS (label features with billboard text):
# After building a visualization, add text labels at 3D world positions.
# Labels always face the camera regardless of view angle.
annotate(x=500, y=300, z=50, label="Fire front", color="yellow", font_size=16)
annotate(x=200, y=400, z=10, label="Fuel bed", color="orange", font_size=14)
annotate(x=100, y=100, z=80, label="Smoke plume", color="white", font_size=14)
# Use get_bounds() or describe_data() to find coordinate ranges for placement.
# To remove all labels: clear_annotations()
# Labels persist across set_camera() calls — re-run annotate() to move a label,
# or call clear_annotations() and re-add all labels with new positions.

=== Tips ===
- Call describe_data() first for a full dataset overview
- Use get_statistics() to find field ranges before choosing scalar_range
- Use suggest_isosurface() to find meaningful contour values
- Use suggest_opacity() for histogram-guided volume rendering opacity
- Use make_vector/curl for general vector field construction and differential ops
- Use compute_velocity/vorticity/magnitude as convenient wrappers
- Use suggest_camera() for a good camera angle
- Use annotate() to label key features; clear_annotations() to start fresh
- Start simple and add layers incrementally

```
