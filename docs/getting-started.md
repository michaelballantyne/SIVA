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

The bridge is wait_for_pipeline(): it executes a DSL pipeline file and renders the result.

TYPICAL WORKFLOW:
  1. list_data_files()          — see what's available
  2. load("mydata.vts")         — load the dataset; already returns full describe_data() output
  3. describe_data(node=, field=) — only needed for derived nodes (after threshold, contour, etc.)
  4. Write a pipeline file (see patterns below), then call wait_for_pipeline()
  5. The first wait_for_pipeline() auto-applies an overview camera. Call
     set_suggested_camera() only to reset or switch style. Camera is preserved
     across all subsequent wait_for_pipeline() calls.
  6. Iterate: edit the file, call wait_for_pipeline() again

PIPELINE FILE STRUCTURE:
  # Load data
  data = source("vtkXMLStructuredGridReader", FileName="mydata.vts")
  # Filter chain
  region = threshold(input=data, ThresholdBy="field", ThresholdRange=[lo, hi])
  # Display
  show(region, "name", color_by="field", scalar_range=(lo, hi))
  # Background defaults to dark; call background("white"|"light"|"black") to change.
  # (Camera is set via set_camera() MCP tool, not in the pipeline file.)

--- KEY PATTERNS ---

1a. SURFACE COLORING — flat/regular grid (vtkImageData, vtkRectilinearGrid):
data = source("vtkXMLImageDataReader", FileName="mydata.vti")
surface = extract_region(input=data, bounds=[xmin, xmax, ymin, ymax, zmin, zmin])
show(surface, "ground", color_by="fieldname", scalar_range=(lo, hi), lut="cool_to_warm")

1b. SURFACE COLORING — terrain-following structured grid (vtkStructuredGrid):
#   Use grid index k=0, NOT spatial z bounds (ground z varies across the domain)
#   Check dimensions with describe_data() first
ground = extract_grid(input=data, VOI=[0, ni_max, 0, nj_max, 0, 0])
show(ground, "ground", color_by="fieldname", scalar_range=(lo, hi), lut="cool_to_warm")

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
- The first wait_for_pipeline() auto-applies an overview camera. Call set_suggested_camera()
  only to reset or try a different style ("overview", "top_down", "side")
- Start simple and add layers incrementally — debug one layer at a time
- COORDINATE SYSTEMS: slice(), extract_region(), and clip_box() use physical (world)
  coordinates. extract_grid() uses absolute structured-grid indices from the file's
  extent (which may NOT start at 0). describe_data() shows the valid index extent.
  get_spatial_extent() returns BOTH physical bounds and grid indices for a feature.
  Mixing physical coords and grid indices silently produces wrong selections.
- VTK boolean properties: use the direct setter (e.g. Splitting=False), not VTK's
  C++ macro form (SplittingOff=True). Only the underlying SetX(bool) is exposed;
  XOn()/XOff() aren't.
- Algebra vs. mesh: calculator() does math on existing field values (arithmetic,
  vector ops, conditional masking). For quantities that depend on the *mesh* itself
  — surface normals, cell shape, connectivity, neighborhood derivatives — use a
  filter and scan the filter list first. Tell: if you're reconstructing a geometric
  quantity from point coordinates, you're probably rebuilding what a filter already
  computes.

--- DSL FORMS (used in pipeline .py files, executed by wait_for_pipeline()) ---

=== Data Sources ===
  source(class_name, **props)       — load a file or create geometry using any whitelisted VTK class
  raw_source(filename, dimensions, scalar_type, ...)  — load raw binary volume data
  filter(class_name, input=, **props) — apply any whitelisted VTK filter directly

=== Data Prep ===
  threshold(input=, ThresholdBy=, ThresholdRange=[min,max])  — keep cells in a value range
  extract_region(input=, bounds=[xmin,xmax,ymin,ymax,zmin,zmax])  — crop by spatial bounds (or voi= for grid indices)
  extract_grid(input=, VOI=[i0,i1,j0,j1,k0,k1])  — extract a sub-grid by absolute index extent (NOT physical coords; check describe_data() for valid range)
  calculator(input=, Function=, ResultArrayName=, AddScalarArrayName=[], AddVectorArrayName=[])  — derived scalar or vector field; vector algebra is first-class (dot/cross/mag/norm, vector arithmetic). See get_dsl_reference(form="calculator") for the syntax.
  cell_to_point(input=)   — promote cell arrays to point arrays (required before contouring)
  point_to_cell(input=)   — demote point arrays to cell arrays
  resample_to_image(input=, dimensions=(nx,ny,nz))  — resample to a regular grid
  probe(input=, source=node)  — sample one dataset at the points of another
  elevation(input=, low_point=, high_point=)  — add an Elevation scalar field by Z height

=== Derived Fields ===
  make_vector(input=, components=('cx','cy','cz'), result='velocity')  — assemble vector from scalar components
  compute_magnitude(input=, components=('u','v','w'), result='speed')  — compute vector magnitude as a scalar
  curl_vector(vector_field=node, output_field='vorticity')  — compute 3-component curl (vorticity vector)
  curl_magnitude(vector_field=node, output_field='vorticity_magnitude')  — compute scalar curl magnitude
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
  background('dark'|'light'|'black'|'white') | background(r, g, b)  — set background color
  title(text, position=, font_size=, color=)  — add a text overlay
  annotate(x, y, z, text, color=, font_size=)  — 3-D billboard label at a world-space position
  axes(color=, font_size=, labels=)  — add labeled X/Y/Z axes with tick marks (physical coords)

=== Sources/Readers (for use with source()) ===
vtkArrowSource, vtkConeSource, vtkCubeSource, vtkCylinderSource, vtkDiskSource, vtkFrustumSource, vtkGenericDataObjectReader, vtkImageReader2, vtkLineSource, vtkNrrdReader, vtkOBJReader, vtkOutlineSource, vtkPLYReader, vtkParametricFunctionSource, vtkPlaneSource, vtkPointSource, vtkRegularPolygonSource, vtkSTLReader, vtkSphereSource, vtkSuperquadricSource, vtkTessellatedBoxSource, vtkTexturedSphereSource, vtkXMLImageDataReader, vtkXMLPolyDataReader, vtkXMLRectilinearGridReader, vtkXMLStructuredGridReader, vtkXMLUnstructuredGridReader

=== Filters (for use with filter()) ===
  vtkAppendFilter — appends one or more datasets together into a single unstructured grid
  vtkAppendPolyData — appends one or more polygonal datasets together
  vtkArrayCalculator — perform mathematical operations on data in field data arrays
  vtkBooleanOperationPolyDataFilter — Computes the boundary of the union, intersection, or difference volume computed from the volumes defined by two input surfaces.
  vtkButterflySubdivisionFilter — generate a subdivision surface using the Butterfly Scheme
  vtkCellDataToPointData — map cell data to point data
  vtkCellDerivatives — compute derivatives of scalars and vectors
  vtkCleanPolyData — merge duplicate points, and/or remove unused points and/or remove degenerate cells
  vtkClipDataSet — clip any dataset with user-specified implicit function or input scalar data
  vtkClipPolyData — clip polygonal data with user-specified implicit function or input scalar data
  vtkConnectivityFilter — extract data based on geometric connectivity
  vtkContourFilter — generate isosurfaces/isolines from scalar values
  vtkCutter — Cut vtkDataSet with user-specified implicit function
  vtkDataSetSurfaceFilter — Extracts outer surface (as vtkPolyData) of any dataset
  vtkDecimatePro — reduce the number of triangles in a mesh
  vtkDelaunay2D — create 2D Delaunay triangulation of input points
  vtkDelaunay3D — create 3D Delaunay triangulation of input points
  vtkElevationFilter — generate scalars along a specified direction
  vtkExtractCells — subset a vtkDataSet to create a vtkUnstructuredGrid
  vtkExtractEdges — extract cell edges from any type of dataset
  vtkExtractGeometry — extract cells that lie either entirely inside or outside of a specified implicit function
  vtkExtractGrid — select piece (e.g., volume of interest) and/or subsample structured grid dataset
  vtkExtractVOI — select piece (e.g., volume of interest) and/or subsample structured points dataset
  vtkFeatureEdges — extract interior, boundary, non-manifold, and/or sharp edges from polygonal data
  vtkFillHolesFilter — identify and fill holes in meshes
  vtkFlyingEdges3D — generate isosurface from 3D image data (volume)
  vtkGaussianSplatter — splat points into a volume with an elliptical, Gaussian distribution
  vtkGeometryFilter — extract boundary geometry from dataset (or convert data to polygonal type)
  vtkGlyph3D — copy oriented and scaled glyph geometry to every input point
  vtkGradientFilter — A general filter for gradient estimation.
  vtkHull — produce an n-sided convex hull
  vtkImageCast — Image Data type Casting Filter
  vtkImageClip — Reduces the image extent of the input.
  vtkImageExtractComponents — Outputs a single component
  vtkImageFlip — This flips an axis of an image.
  vtkImageGaussianSmooth — Performs a gaussian convolution.
  vtkImageGradient — Computes the gradient vector.
  vtkImageGradientMagnitude — Computes magnitude of the gradient.
  vtkImageMathematics — Add, subtract, multiply, divide, invert, sin, cos, exp, log.
  vtkImageMedian3D — Median Filter
  vtkImageNormalize — Normalizes that scalar components for each point.
  vtkImageResample — Resamples an image to be larger or smaller.
  vtkImageReslice — Reslices a volume along a new set of axes.
  vtkImageShiftScale — shift and scale an input image
  vtkImplicitModeller — compute distance from input geometry on structured point dataset
  vtkIntersectionPolyDataFilter — vtkIntersectionPolyDataFilter computes the intersection between two vtkPolyData objects.
  vtkLinearSubdivisionFilter — generate a subdivision surface using the Linear Scheme
  vtkLoopSubdivisionFilter — generate a subdivision surface using the Loop Scheme
  vtkMarchingCubes — generate isosurface(s) from volume
  vtkMaskPoints — selectively filter points
  vtkMassProperties — estimate volume, area, shape index of triangle mesh
  vtkOutlineFilter — create wireframe outline for an arbitrary data set or composite dataset
  vtkPassArrays — Passes a subset of arrays to the output
  vtkPointDataToCellData — map point data to cell data
  vtkPointInterpolator — interpolate over point cloud using various kernels
  vtkPoissonDiskSampler — generate point normals using local tangent planes
  vtkPolyDataConnectivityFilter — extract polygonal data based on geometric connectivity
  vtkPolyDataNormals — compute normals for polygonal mesh
  vtkProbeFilter — sample data values at specified point locations
  vtkProjectSphereFilter — A filter to 'unroll' a sphere.
  vtkQuadricDecimation — reduce the number of triangles in a mesh
  vtkRadiusOutlierRemoval — remove isolated points
  vtkRandomAttributeGenerator — generate and create random data attributes
  vtkRectilinearGridGeometryFilter — extract geometry for a rectilinear grid
  vtkRectilinearGridToTetrahedra — create a Tetrahedral mesh from a RectilinearGrid
  vtkResampleToImage — sample dataset on a uniform grid
  vtkResampleWithDataSet — sample point and cell data of a dataset on points from another dataset.
  vtkReverseSense — reverse the ordering of polygonal cells and/or vertex normals
  vtkRibbonFilter — create oriented ribbons from lines defined in polygonal dataset
  vtkSPHInterpolator — interpolate over point cloud using SPH kernels
  vtkSampleImplicitFunctionFilter — sample an implicit function over a dataset, generating scalar values and optional gradient vectors
  vtkSelectEnclosedPoints — mark points as to whether they are inside a closed surface
  vtkShrinkFilter — shrink cells composing an arbitrary data set
  vtkShrinkPolyData — shrink cells composing PolyData
  vtkSmoothPolyDataFilter — adjust point positions using Laplacian smoothing
  vtkStatisticalOutlierRemoval — remove sparse outlier points
  vtkStreamTracer — Streamline generator
  vtkStripper — create triangle strips and/or poly-lines
  vtkStructuredGridGeometryFilter — extract geometry for structured grid
  vtkTableBasedClipDataSet — Clip any dataset with a user-specified implicit function or an input scalar point data array.
  vtkTableToPolyData — filter used to convert a vtkTable to a vtkPolyData consisting of vertices.
  vtkThreshold — extracts cells where scalar value in cell satisfies threshold criterion
  vtkThresholdPoints — extracts points whose scalar value satisfies threshold criterion
  vtkTransformFilter — transform points and associated normals and vectors
  vtkTransformPolyDataFilter — transform points and associated normals and vectors for polygonal dataset
  vtkTriangleFilter — convert input polygons and strips to triangles
  vtkTubeFilter — filter that generates tubes around lines
  vtkVertexGlyphFilter — Make a vtkPolyData with a vertex on each point.
  vtkVoxelGrid — subsample points using uniform binning
  vtkWarpScalar — deform geometry with scalar data
  vtkWarpVector — deform geometry with vector data
  vtkWindowedSincPolyDataFilter — adjust point positions using a windowed sinc function interpolation kernel

=== Colormaps (for lut= parameter of show()) ===
"blue_to_red", "cool_to_warm", "fire", "grayscale", "heat", "oxygen", "terrain", "wind"

Use get_dsl_reference(form="form-name") for full parameter docs on any form above.
```

---

## Further Reading

- [dsl-reference.md](dsl-reference.md) — Complete DSL form reference
- [mcp-reference.md](mcp-reference.md) — Complete MCP tool reference
- [instructions.md](instructions.md) — MCP server guidance string
