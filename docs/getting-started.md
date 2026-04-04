# Getting Started with VisLang

> Auto-generated from source by `python gen_docs.py`.
> Do not edit by hand — changes will be overwritten.

---

```
=== VisLang Getting Started ===

TWO-LAYER ARCHITECTURE:
  MCP tools  — interactive operations called by you/an AI: load data, query statistics,
               execute pipelines, adjust the scene, take screenshots.
  DSL forms  — declarative pipeline language used in pipeline .py files: source(),
               filter(), threshold(), contour(), show(), camera(), background().

The bridge is set_pipeline(): it executes a DSL pipeline file and renders the result.

TYPICAL WORKFLOW:
  1. list_data_files()          — see what's available
  2. load("mydata.vts")         — load the dataset (returns describe_data() output)
  3. get_statistics("field")    — find value ranges before choosing thresholds/isovalues
  4. Write a pipeline file (see patterns below), then call set_pipeline("pipeline.py")
  5. Iterate: edit the file, call set_pipeline() again; use get_pipeline() to inspect current code

PIPELINE FILE STRUCTURE:
  # Load data
  data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
  # Filter chain
  region = threshold(input=data, ThresholdBy="field", ThresholdRange=[lo, hi])
  # Display
  show(region, "name", color_by="field", scalar_range=(lo, hi))
  # Scene setup
  camera(position=(x,y,z), focal_point=(fx,fy,fz))
  scene_preset("dark")

--- KEY PATTERNS ---

1. SURFACE COLORING (color a ground slice by a scalar field):
data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
surface = extract_region(input=data, bounds=[xmin, xmax, ymin, ymax, zmin, zmin])
show(surface, "ground", color_by="fieldname", scalar_range=(lo, hi), lut="cool_to_warm")
scene_preset("dark")

2. ISOSURFACE:
data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
# Use suggest_isosurface() to find a meaningful isovalue
iso = contour(input=data, ContourBy="fieldname", Isosurfaces=[value])
show(iso, "iso", color_by="fieldname", scalar_range=(lo, hi), lut="hot")
camera(position=(x,y,z), focal_point=(fx,fy,fz))

3. THRESHOLD + VOLUME RENDERING:
data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
# Use suggest_opacity() to get histogram-guided opacity control points
region = threshold(input=data, ThresholdBy="fieldname", ThresholdRange=[lo, hi])
show(region, "vol", representation="Volume", color_by="fieldname",
    scalar_range=(lo, hi), lut="cool_to_warm",
    opacity_function=[(lo, 0.0), (mid, 0.05), (hi, 0.5)],
    gradient_opacity=True, volume_resolution=200)

4. STREAMLINES:
data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
velocity = compute_velocity(input=data, components=("u", "v", "w"), result="velocity")
# Use seeds_near() to auto-place seeds where a field is active
seeds = seeds_near(input=data, field="fieldname", min_val=lo, max_val=hi, num_seeds=40)
streams = stream_tracer(input=velocity, SeedSource=seeds, Vectors="velocity",
    IntegrationDirection="Both", MaximumNumberOfSteps=2000, MaximumPropagation=500)
tubes = tube(input=streams, Radius=1.0, NumberOfSides=8)
show(tubes, "flow", color_by="velocity", opacity=0.8)

--- TIPS ---
- Use get_statistics() to find field ranges before choosing scalar_range or threshold values
- Use suggest_isosurface() to find meaningful contour values
- Use suggest_opacity() for histogram-guided volume opacity
- Use suggest_camera() for a good initial camera angle
- Use get_dsl_reference("form_name") for detailed docs on any DSL form
- Use list_capabilities() to see all available DSL forms and colormap presets
- Start simple and add layers incrementally — debug one layer at a time

```

---

## Further Reading

- [dsl-reference.md](dsl-reference.md) — Complete DSL form reference
- [mcp-reference.md](mcp-reference.md) — Complete MCP tool reference
- [instructions.md](instructions.md) — MCP server guidance string
