# Getting Started with VisLang

> Auto-generated from source by `python scripts/gen_docs.py`.
> Do not edit by hand — changes will be overwritten.

---

```
=== VisLang DSL Overview ===

TWO-LAYER ARCHITECTURE:
  MCP tools  — interactive operations called by you/an AI: load data, query statistics,
               execute pipelines, adjust the scene, take screenshots.
  DSL forms  — declarative pipeline language used in pipeline .py files: source(),
               filter(), threshold(), contour(), show(), camera(), background().

The bridge is run_pipeline(): it executes a DSL pipeline file and renders the result.

TYPICAL WORKFLOW:
  1. list_data_files()          — see what's available
  2. load("mydata.vts")         — load the dataset; already returns full describe_data() output
  3. describe_data(node=, field=) — only needed for derived nodes (after threshold, contour, etc.)
  4. Write a pipeline file (see patterns below), then call run_pipeline()
  5. The first run_pipeline() auto-applies an overview camera. Call
     set_suggested_camera() only to reset or switch style. Camera is preserved
     across all subsequent run_pipeline() calls.
  6. Iterate: edit the file, call run_pipeline() again

PIPELINE FILE STRUCTURE:
  # Load data
  data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
  # Filter chain
  region = threshold(input=data, ThresholdBy="field", ThresholdRange=[lo, hi])
  # Display
  show(region, "name", color_by="field", scalar_range=(lo, hi))
  # Scene setup (camera is set via set_camera() MCP tool, not in the pipeline file)
  scene_preset("dark")

--- KEY PATTERNS ---

1a. SURFACE COLORING — flat/regular grid (vtkImageData, vtkRectilinearGrid):
data = source("vtkXMLImageDataReader", FileName="mydata.vti")
surface = extract_region(input=data, bounds=[xmin, xmax, ymin, ymax, zmin, zmin])
show(surface, "ground", color_by="fieldname", scalar_range=(lo, hi), lut="cool_to_warm")
scene_preset("dark")

1b. SURFACE COLORING — terrain-following structured grid (vtkStructuredGrid):
#   Use grid index k=0, NOT spatial z bounds (ground z varies across the domain)
#   Check dimensions with describe_data() first
ground = extract_grid(input=data, VOI=[0, ni_max, 0, nj_max, 0, 0])
show(ground, "ground", color_by="fieldname", scalar_range=(lo, hi), lut="cool_to_warm")
scene_preset("dark")

2. ISOSURFACE (one or more nested values):
data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
# Isosurfaces accepts a list of one or more values; suggest_isosurface() finds meaningful ones
iso = contour(input=data, ContourBy="fieldname",
              Isosurfaces=[v_low, v_mid, v_high])
show(iso, "iso", color_by="fieldname", scalar_range=(v_low, v_high),
     lut="cool_to_warm", opacity=0.35)

3. THRESHOLD + VOLUME RENDERING:
data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
region = threshold(input=data, ThresholdBy="fieldname", ThresholdRange=[lo, hi])
show(region, "vol", representation="Volume", color_by="fieldname",
    scalar_range=(lo, hi), lut="cool_to_warm",
    opacity_function=[(lo, 0.0), (mid, 0.05), (hi, 0.5)],
    gradient_opacity=True, volume_resolution=200)

4. STREAMLINES:
data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
velocity = make_vector(input=data, components=("u", "v", "w"), result="velocity")
# Use source("vtkLineSource") or source("vtkPlaneSource") for seed points
# On terrain-following / curvilinear grids, call the get_ground_z MCP tool to get ground z at (x,y) before choosing seed z
seeds = source("vtkLineSource", Point1=[x0, y0, z0], Point2=[x1, y0, z0], Resolution=30)
streams = stream_tracer(input=velocity, SeedSource=seeds, Vectors="velocity",
    IntegrationDirection="Both", MaximumNumberOfSteps=2000, MaximumPropagation=500)
show(streams, "flow", color_by="velocity", opacity=0.8)

--- TIPS ---
- Use describe_data(node=, field=) to find field ranges before choosing scalar_range or threshold values
- Use suggest_isosurface() to find meaningful contour values
- The first run_pipeline() auto-applies an overview camera. Call set_suggested_camera()
  only to reset or try a different style ("overview", "top_down", "side")
- Start simple and add layers incrementally — debug one layer at a time
- COORDINATE SYSTEMS: slice(), extract_region(), and clip_box() use physical (world)
  coordinates. extract_grid() uses absolute structured-grid indices from the file's
  extent (which may NOT start at 0). describe_data() shows the valid index extent.
  get_spatial_extent() returns BOTH physical bounds and grid indices for a feature.
  Mixing physical coords and grid indices silently produces wrong selections.

--- DSL FORMS (used in pipeline .py files, executed by run_pipeline()) ---

=== Data Sources ===
  source(class_name, **props)       — load a file or create geometry using any whitelisted VTK class
  raw_source(filename, dimensions, scalar_type, ...)  — load raw binary volume data
  filter(class_name, input=, **props) — apply any whitelisted VTK filter directly

=== Data Prep ===
  threshold(input=, ThresholdBy=, ThresholdRange=[min,max])  — keep cells in a value range
  extract_region(input=, bounds=[xmin,xmax,ymin,ymax,zmin,zmax])  — crop by spatial bounds (or voi= for grid indices)
  extract_grid(input=, VOI=[i0,i1,j0,j1,k0,k1])  — extract a sub-grid by absolute index extent (NOT physical coords; check describe_data() for valid range)
  calculator(input=, Function=, ResultArrayName=, AddScalarArrayName=[])  — compute derived scalar fields
  cell_to_point(input=)   — promote cell arrays to point arrays (required before contouring)
  point_to_cell(input=)   — demote point arrays to cell arrays
  resample_to_image(input=, dimensions=(nx,ny,nz))  — resample to a regular grid
  probe(input=, source=node)  — sample one dataset at the points of another
  elevation(input=, low_point=, high_point=)  — add an Elevation scalar field by Z height

=== Derived Fields ===
  make_vector(input=, components=('cx','cy','cz'), result='velocity')  — assemble vector from scalar components
  compute_magnitude(input=, components=('u','v','w'), result='speed')  — compute vector magnitude as a scalar
  curl(vector_field=node, result=, vector=True)  — compute 3-component or scalar curl of a vector field
  gradient(input=, GradientField=, ResultArrayName=)  — compute 3-component gradient vector
  compute_gradient_magnitude(input=, field=, result=)  — scalar magnitude of gradient (edge detection)
  extract_component(input=, field=, component=0, result_name=)  — isolate one component of a vector

=== Geometry ===
  contour(input=, ContourBy=, Isosurfaces=[])  — extract isosurfaces
  slice(input=, origin=(x,y,z), normal=(nx,ny,nz))  — planar cross-section
  clip(input=, origin=, normal=, inside_out=False)  — half-space clip by plane
  clip_box(input=, bounds=(xmin,xmax,ymin,ymax,zmin,zmax))  — rectangular crop
  clip_sphere(input=, center=, radius=, inside_out=True)  — spherical crop
  surface(input=)  — extract outer boundary as a polygonal mesh
  smooth(input=, iterations=20)  — Laplacian smoothing of a surface mesh
  warp_vector(input=, ScaleFactor=)  — displace points along a vector field
  warp_scalar(input=, ScaleFactor=)  — displace points along surface normal by a scalar
  outline(input=)  — bounding-box wireframe

=== Flow / Particles ===
  stream_tracer(input=, SeedSource=, Vectors=, ...)  — trace streamlines through a vector field
  tube(input=, Radius=, NumberOfSides=)  — wrap streamlines as 3D tubes; lines (default) usually look better — only use if the human asks
  glyph(input=, GlyphSource=, OrientationArray=, ScaleArray=, ScaleFactor=)  — place oriented glyphs
  mask_points(input=, OnRatio=, RandomMode=)  — subsample point cloud for glyphs/seeds
  line_probe(input=, point1=, point2=, resolution=)  — sample values along a line

=== Display ===
  show(node, name, color_by=, scalar_range=, lut=, opacity=, component=0/1/2)  — add node to scene
  show(..., representation='Volume', opacity_function=[(val,opacity),...],
       volume_resolution=256, gradient_opacity=True, shade=True)  — volume rendering
  Volume opacity presets: "ramp_up", "gaussian", "step", "ct_bone", "ct_tissue", "fire", "o2_depletion", "vorticity"
  camera(position=, focal_point=, up=, zoom=)  — embed camera in pipeline (for reproducible
    exports only; camera is otherwise managed via set_suggested_camera()/set_camera())
  background(r, g, b)  — set background color
  scene_preset('dark'|'light'|'black'|'white')  — apply a scene color scheme
  title(text, position=, font_size=, color=)  — add a text overlay
  axes(color=, font_size=, labels=)  — add labeled X/Y/Z axes with tick marks (physical coords)

=== Sources/Readers (for use with source()) ===
vtkArrowSource, vtkConeSource, vtkCubeSource, vtkCylinderSource, vtkDiskSource, vtkFrustumSource, vtkGenericDataObjectReader, vtkImageReader2, vtkLineSource, vtkNrrdReader, vtkOBJReader, vtkOutlineSource, vtkPLYReader, vtkParametricFunctionSource, vtkPlaneSource, vtkPointSource, vtkRegularPolygonSource, vtkSTLReader, vtkSphereSource, vtkSuperquadricSource, vtkTessellatedBoxSource, vtkTexturedSphereSource, vtkXMLImageDataReader, vtkXMLPolyDataReader, vtkXMLRectilinearGridReader, vtkXMLStructuredGridReader, vtkXMLUnstructuredGridReader

=== Filters (for use with filter()) ===
vtkAppendFilter, vtkAppendPolyData, vtkArrayCalculator, vtkBooleanOperationPolyDataFilter, vtkButterflySubdivisionFilter, vtkCellDataToPointData, vtkCellDerivatives, vtkCleanPolyData, vtkClipDataSet, vtkClipPolyData, vtkConnectivityFilter, vtkContourFilter, vtkCutter, vtkDataSetSurfaceFilter, vtkDecimatePro, vtkDelaunay2D, vtkDelaunay3D, vtkElevationFilter, vtkExtractCells, vtkExtractEdges, vtkExtractGeometry, vtkExtractGrid, vtkExtractVOI, vtkFeatureEdges, vtkFillHolesFilter, vtkFlyingEdges3D, vtkGaussianSplatter, vtkGeometryFilter, vtkGlyph3D, vtkGradientFilter, vtkHull, vtkImageCast, vtkImageClip, vtkImageExtractComponents, vtkImageFlip, vtkImageGaussianSmooth, vtkImageGradient, vtkImageGradientMagnitude, vtkImageMathematics, vtkImageMedian3D, vtkImageNormalize, vtkImageResample, vtkImageReslice, vtkImageShiftScale, vtkImplicitModeller, vtkIntersectionPolyDataFilter, vtkLinearSubdivisionFilter, vtkLoopSubdivisionFilter, vtkMarchingCubes, vtkMaskPoints, vtkMassProperties, vtkOutlineFilter, vtkPassArrays, vtkPointDataToCellData, vtkPointInterpolator, vtkPoissonDiskSampler, vtkPolyDataConnectivityFilter, vtkPolyDataNormals, vtkProbeFilter, vtkProjectSphereFilter, vtkQuadricDecimation, vtkRadiusOutlierRemoval, vtkRandomAttributeGenerator, vtkRectilinearGridGeometryFilter, vtkRectilinearGridToTetrahedra, vtkResampleToImage, vtkResampleWithDataSet, vtkReverseSense, vtkRibbonFilter, vtkSPHInterpolator, vtkSampleImplicitFunctionFilter, vtkSelectEnclosedPoints, vtkShrinkFilter, vtkShrinkPolyData, vtkSmoothPolyDataFilter, vtkStatisticalOutlierRemoval, vtkStreamTracer, vtkStripper, vtkStructuredGridGeometryFilter, vtkTableBasedClipDataSet, vtkTableToPolyData, vtkThreshold, vtkThresholdPoints, vtkTransformFilter, vtkTransformPolyDataFilter, vtkTriangleFilter, vtkTubeFilter, vtkVertexGlyphFilter, vtkVoxelGrid, vtkWarpScalar, vtkWarpVector, vtkWindowedSincPolyDataFilter

=== Colormaps (for lut= parameter of show()) ===
"blue_to_red", "cool_to_warm", "fire", "grayscale", "heat", "oxygen", "terrain", "wind"

Use get_dsl_reference('form_name') for full parameter docs on any form above.
```

---

## Further Reading

- [dsl-reference.md](dsl-reference.md) — Complete DSL form reference
- [mcp-reference.md](mcp-reference.md) — Complete MCP tool reference
- [instructions.md](instructions.md) — MCP server guidance string
