"""Integration tests for the VisLang system using real wildfire data."""

import os
import sys
import json

# Ensure we can import vislang
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vislang.renderer import Renderer, RenderMode
from vislang.dsl import interpret
from vislang import queries

DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "datasets", "wildfire", "data", "output.30000.vts")
RESULTS = {"passed": 0, "failed": 0, "errors": []}


def _register(name):
    """Decorator factory that wraps a test function with pass/fail tracking.

    Named _register (not 'test') so pytest does not try to collect it as a
    fixture or test case.
    """
    def decorator(fn):
        def wrapper():
            try:
                fn()
                RESULTS["passed"] += 1
                print(f"  PASS: {name}")
            except AssertionError as e:
                RESULTS["failed"] += 1
                RESULTS["errors"].append(f"{name}: {e}")
                print(f"  FAIL: {name} - {e}")
            except Exception as e:
                RESULTS["failed"] += 1
                RESULTS["errors"].append(f"{name}: {type(e).__name__}: {e}")
                print(f"  ERROR: {name} - {type(e).__name__}: {e}")
        return wrapper
    return decorator


@_register("Renderer creates without error")
def test_renderer_init():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    r.render()
    path = r.screenshot("/tmp/test_empty.png")
    assert os.path.exists(path), "Screenshot file not created"
    assert os.path.getsize(path) > 100, "Screenshot file too small"


@_register("Load real data source")
def test_load_data():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")'
    objs, statuses, shows, builder = interpret(code, r)
    assert "data" in objs, "data not in objects"
    objs["data"].Update()
    output = objs["data"].GetOutput()
    assert output.GetNumberOfPoints() == 18300000, f"Expected 18.3M points, got {output.GetNumberOfPoints()}"


@_register("Extract grid filter")
def test_extract_grid():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1")
'''
    objs, statuses, shows, builder = interpret(code, r)
    assert "terrain" in objs
    objs["terrain"].Update()
    output = objs["terrain"].GetOutput()
    assert output.GetNumberOfPoints() > 0, "Terrain has no points"
    assert shows.get("terrain", {}).get("status") == "ok", "Show failed"


@_register("Contour filter (fire isosurface)")
def test_contour_fire():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta", scalar_range=(350.0, 1200.0))
'''
    objs, statuses, shows, builder = interpret(code, r)
    assert "fire" in objs
    objs["fire"].Update()
    output = objs["fire"].GetOutput()
    assert output.GetNumberOfPoints() > 0, "Fire contour has no points"
    assert output.GetNumberOfPoints() < 100000, f"Too many contour points: {output.GetNumberOfPoints()}"


@_register("Calculator + StreamTracer with seed source")
def test_streamlines():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat",
    ResultArrayName="velocity")
seeds = source("vtkLineSource", Point1=(-100, -10, 175), Point2=(200, -10, 175), Resolution=20)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=seeds,
    Vectors="velocity",
    IntegrationDirection="Both",
    MaximumNumberOfSteps=1000,
    MaximumPropagation=300,
    InitialIntegrationStep=0.5)
'''
    objs, statuses, shows, builder = interpret(code, r)
    assert "streams" in objs
    objs["streams"].Update()
    output = objs["streams"].GetOutput()
    assert output.GetNumberOfPoints() > 100, f"Too few streamline points: {output.GetNumberOfPoints()}"


@_register("TubeFilter on streamlines")
def test_tubes():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat",
    ResultArrayName="velocity")
seeds = source("vtkLineSource", Point1=(-100, -10, 175), Point2=(200, -10, 175), Resolution=20)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=seeds, Vectors="velocity",
    IntegrationDirection="Both",
    MaximumNumberOfSteps=1000, MaximumPropagation=300, InitialIntegrationStep=0.5)
tubes = filter("vtkTubeFilter", input=streams, Radius=2.0, NumberOfSides=8)
show(tubes, "wind", color_by="u", scalar_range=(-5, 20))
'''
    objs, statuses, shows, builder = interpret(code, r)
    assert "tubes" in objs
    objs["tubes"].Update()
    assert objs["tubes"].GetOutput().GetNumberOfPoints() > 0


@_register("Query: get_statistics")
def test_query_statistics():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")'
    objs, _, _, _ = interpret(code, r)
    objs["data"].Update()
    stats = queries.get_statistics(objs["data"].GetOutput(), "theta")
    assert "298.751" in stats  # min theta
    assert "1183.94" in stats  # max theta


@_register("Query: get_spatial_extent")
def test_query_spatial_extent():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")'
    objs, _, _, _ = interpret(code, r)
    objs["data"].Update()
    extent = queries.get_spatial_extent(objs["data"].GetOutput(), "theta", 400.0, 1200.0)
    assert "3831 points" in extent
    assert "X:" in extent


@_register("Query: get_histogram")
def test_query_histogram():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")'
    objs, _, _, _ = interpret(code, r)
    objs["data"].Update()
    hist = queries.get_histogram(objs["data"].GetOutput(), "rhof_1", 10)
    assert "Histogram" in hist
    assert "█" in hist


@_register("Color map presets")
def test_colormap_presets():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain")
'''
    objs, statuses, shows, builder = interpret(code, r)
    assert shows.get("terrain", {}).get("status") == "ok"


@_register("Full wildfire demo pipeline")
def test_full_demo():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta", scalar_range=(350.0, 1200.0), lut="fire")
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat",
    ResultArrayName="velocity")
seeds = source("vtkLineSource", Point1=(-100, -10, 175), Point2=(200, -10, 175), Resolution=25)
streams = filter("vtkStreamTracer", input=velocity,
    SeedSource=seeds, Vectors="velocity",
    IntegrationDirection="Both",
    MaximumNumberOfSteps=2000, MaximumPropagation=600, InitialIntegrationStep=0.3)
tubes = filter("vtkTubeFilter", input=streams, Radius=1.5, NumberOfSides=8)
show(tubes, "wind", color_by="u", scalar_range=(-5, 25), lut="wind", opacity=0.8)
camera(position=(80, -600, 500), focal_point=(80, -10, 160), up=(0, 0, 1))
background(0.08, 0.08, 0.15)
'''
    objs, statuses, shows, builder = interpret(code, r)
    # Check all shows succeeded
    for name, status in shows.items():
        assert status.get("status") == "ok", f"Show '{name}' failed: {status}"
    # Check key nodes produced output
    for name in ["terrain", "fire", "streams", "tubes"]:
        assert name in objs, f"Missing node: {name}"
    # Screenshot
    path = r.screenshot("/tmp/test_full_demo.png")
    assert os.path.exists(path)
    assert os.path.getsize(path) > 10000, "Demo screenshot too small"


@_register("Error handling: bad VTK class")
def test_bad_vtk_class():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = 'bad = source("vtkFakeFilter", FileName="test.vts")'
    objs, statuses, shows, builder = interpret(code, r)
    # Should have an error in node_statuses
    has_error = any("error" in s for s in statuses.values())
    assert has_error, "Expected error for fake VTK class"


@_register("Error handling: bad field name in query")
def test_bad_field_query():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")'
    objs, _, _, _ = interpret(code, r)
    objs["data"].Update()
    result = queries.get_statistics(objs["data"].GetOutput(), "nonexistent_field")
    assert "not found" in result


@_register("Version history saves correctly")
def test_version_history():
    from pathlib import Path
    from vislang.server import run_pipeline, _current_ctx
    os.makedirs(".vislang/history", exist_ok=True)
    Path(_current_ctx().pipeline_file).write_text(f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "t", color_by="rhof_1")
''')
    result = run_pipeline()
    first = result if isinstance(result, str) else result[0]
    assert "Pipeline v" in first
    # Check version directory exists
    import glob
    versions = glob.glob(".vislang/history/v*")
    assert len(versions) > 0, "No version directories created"


@_register("Convenience wrappers (contour, calculator, etc)")
def test_convenience_wrappers():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
iso = contour(input=data, ContourBy="theta", Isosurfaces=[400.0])
show(iso, "iso", color_by="theta")
'''
    objs, statuses, shows, builder = interpret(code, r)
    assert "iso" in objs
    objs["iso"].Update()
    assert objs["iso"].GetOutput().GetNumberOfPoints() > 0


@_register("Suggest camera for each style")
def test_suggest_camera():
    from pathlib import Path
    from vislang.server import run_pipeline, set_suggested_camera, _current_ctx
    Path(_current_ctx().pipeline_file).write_text(f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta", scalar_range=(350.0, 1200.0))
''')
    run_pipeline()
    for style in ["overview", "top_down", "side"]:
        result = set_suggested_camera(style)
        first = result if isinstance(result, str) else result[0]
        assert "Camera set" in first, f"Style '{style}' unexpected result: {first}"


@_register("Sample point returns field values")
def test_sample_point():
    from pathlib import Path
    from vislang.server import run_pipeline, sample_points, _current_ctx
    Path(_current_ctx().pipeline_file).write_text(f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")')
    run_pipeline()
    result = sample_points("data", [[80.0, -10.0, 170.0]])
    assert "theta" in result, f"Expected 'theta' in result: {result}"
    # Check there's at least one numeric value (digits with optional decimal)
    import re
    assert re.search(r"\d+\.\d+", result), f"Expected numeric values in result: {result}"


@_register("DSL overview")
def test_dsl_overview():
    from vislang.server import get_dsl_overview
    result = get_dsl_overview()
    assert "vtkContourFilter" in result, "Missing vtkContourFilter"
    assert "fire" in result, "Missing fire colormap"
    assert "source" in result, "Missing source function"


@_register("Slice cross section")
def test_slice_cross_section():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")\ncs = slice(input=data, origin=(80, -10, 170), normal=(1, 0, 0))\nshow(cs, "cross", color_by="theta")'
    objs, statuses, shows, builder = interpret(code, r)
    assert "cs" in objs, f"cs not in objects, got: {list(objs.keys())}"
    objs["cs"].Update()
    output = objs["cs"].GetOutput()
    assert output.GetNumberOfPoints() > 0, f"Cross section has no points"


@_register("Vorticity pipeline")
def test_vorticity_pipeline():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
velocity = filter("vtkArrayCalculator", input=data,
    AddScalarArrayName=["u", "v", "w"],
    Function="u*iHat + v*jHat + w*kHat",
    ResultArrayName="velocity")
derivs = filter("vtkCellDerivatives", input=velocity,
    VectorMode="ComputeVorticity", TensorMode="PassTensors")
to_point = filter("vtkCellDataToPointData", input=derivs)
vort_mag = filter("vtkArrayCalculator", input=to_point,
    AddVectorArrayName=["Vorticity"],
    Function="mag(Vorticity)",
    ResultArrayName="vort_mag")
vort_iso = filter("vtkContourFilter", input=vort_mag, ContourBy="vort_mag", Isosurfaces=[1.5])
'''
    objs, statuses, shows, builder = interpret(code, r)
    assert "vort_iso" in objs, f"vort_iso not in objects, got: {list(objs.keys())}"
    objs["vort_iso"].Update()
    output = objs["vort_iso"].GetOutput()
    assert output.GetNumberOfPoints() > 0, f"Vorticity isosurface has no points"


@_register("List data files")
def test_list_data_files():
    from vislang.server import list_data_files
    result = list_data_files()
    assert "output.30000.vts" in result


@_register("Reader caching")
def test_reader_caching():
    from vislang.filters import clear_reader_cache
    clear_reader_cache()
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    # First build - populates cache
    code = f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")'
    objs1, statuses1, shows1, builder1 = interpret(code, r)
    # Second build - should use cache
    objs2, statuses2, shows2, builder2 = interpret(code, r)
    # Find the data node status - look for cached key
    found_cached = False
    for node_id, status in statuses2.items():
        if status.get("name") == "data" and status.get("cached"):
            found_cached = True
            break
    assert found_cached, f"Expected 'cached' key in data node status. Statuses: {statuses2}"


@_register("Volume rendering pipeline builds correctly")
def test_volume_rendering():
    import vtk
    from vislang.filters import create_show, create_vtk_filter

    # Load data and threshold
    reader, _ = create_vtk_filter("vtkXMLStructuredGridReader", FileName=DATA_FILE)
    thresh, _ = create_vtk_filter("vtkThreshold", reader,
        ThresholdBy="theta", ThresholdRange=[350.0, 1200.0])

    # Create volume
    vol, bar = create_show(thresh,
        representation="Volume",
        color_by="theta",
        scalar_range=(350.0, 1200.0),
        lut="fire",
        opacity_function=[(350, 0.0), (400, 0.05), (700, 0.3), (1200, 0.8)],
        volume_resolution=100)

    assert isinstance(vol, vtk.vtkVolume), f"Expected vtkVolume, got {type(vol).__name__}"
    mapper = vol.GetMapper()
    assert mapper is not None, "Volume has no mapper"
    mapper.Update()
    inp = mapper.GetInput()
    assert inp is not None, "Mapper has no input"
    assert inp.GetClassName() == "vtkImageData", f"Expected vtkImageData, got {inp.GetClassName()}"
    assert inp.GetNumberOfPoints() > 0, "Resampled data has no points"
    assert inp.GetPointData().GetArray("theta") is not None, "theta array lost during resampling"


@_register("Volume rendering with opacity presets")
def test_volume_opacity_presets():
    from vislang.colormaps import build_opacity_function
    for preset in ["ramp_up", "gaussian", "step"]:
        otf = build_opacity_function(preset, scalar_range=(0, 100), opacity_scale=0.5)
        assert otf.GetSize() >= 2, f"Preset '{preset}' has too few control points"

    # Test custom list
    otf = build_opacity_function([(0, 0.0), (50, 0.5), (100, 1.0)], scalar_range=(0, 100))
    assert otf.GetSize() == 3, "Custom opacity function has wrong number of points"


@_register("Volume rendering with scalar bar")
def test_volume_scalar_bar():
    import vtk
    from vislang.filters import create_show, create_vtk_filter

    reader, _ = create_vtk_filter("vtkXMLStructuredGridReader", FileName=DATA_FILE)
    thresh, _ = create_vtk_filter("vtkThreshold", reader,
        ThresholdBy="theta", ThresholdRange=[350.0, 1200.0])

    vol, bar = create_show(thresh,
        representation="Volume",
        color_by="theta",
        scalar_range=(350.0, 1200.0),
        lut="fire",
        scalar_bar="Temperature (K)",
        volume_resolution=64)

    assert isinstance(vol, vtk.vtkVolume), "Expected vtkVolume"
    assert bar is not None, "Expected scalar bar"
    assert isinstance(bar, vtk.vtkScalarBarActor), f"Expected vtkScalarBarActor, got {type(bar).__name__}"


@_register("Color transfer function from presets")
def test_color_transfer_function():
    import vtk
    from vislang.colormaps import build_color_transfer_function
    for preset in ["fire", "cool_to_warm", "terrain", "wind"]:
        ctf = build_color_transfer_function(preset, scalar_range=(0, 100))
        assert isinstance(ctf, vtk.vtkColorTransferFunction), f"Preset '{preset}' failed"
        assert ctf.GetSize() >= 2, f"Preset '{preset}' has too few control points"

    # Test HSV config
    ctf = build_color_transfer_function(
        dict(hue_range=(0.0, 0.67), saturation_range=(0.5, 1.0), value_range=(0.3, 1.0)),
        scalar_range=(0, 100))
    assert ctf.GetSize() >= 2, "HSV color transfer function failed"


@_register("New VTK filter classes in whitelist")
def test_new_vtk_classes():
    from vislang.filters import WHITELISTED_CLASSES
    new_classes = ["vtkWarpVector", "vtkMaskPoints", "vtkGradientFilter",
                   "vtkResampleToImage", "vtkAppendFilter", "vtkTransformFilter",
                   # Readers
                   "vtkPLYReader", "vtkSTLReader", "vtkOBJReader",
                   # Sources
                   "vtkFrustumSource", "vtkOutlineSource", "vtkTessellatedBoxSource",
                   # Filters -- geometry
                   "vtkClipPolyData", "vtkTransformPolyDataFilter",
                   "vtkLoopSubdivisionFilter", "vtkButterflySubdivisionFilter",
                   "vtkLinearSubdivisionFilter", "vtkReverseSense",
                   "vtkMarchingCubes", "vtkFlyingEdges3D",
                   "vtkBooleanOperationPolyDataFilter", "vtkIntersectionPolyDataFilter",
                   "vtkHull", "vtkShrinkFilter", "vtkShrinkPolyData",
                   "vtkExtractCells", "vtkExtractGeometry",
                   "vtkTableBasedClipDataSet", "vtkRectilinearGridToTetrahedra",
                   "vtkMassProperties", "vtkTableToPolyData",
                   "vtkRectilinearGridGeometryFilter", "vtkStructuredGridGeometryFilter",
                   "vtkProjectSphereFilter", "vtkRandomAttributeGenerator",
                   "vtkSampleImplicitFunctionFilter", "vtkImplicitModeller",
                   # Filters -- point cloud and sampling
                   "vtkPointInterpolator", "vtkSPHInterpolator",
                   "vtkStatisticalOutlierRemoval", "vtkRadiusOutlierRemoval",
                   "vtkVoxelGrid", "vtkPoissonDiskSampler",
                   # Filters -- image processing
                   "vtkImageResample", "vtkImageReslice", "vtkImageFlip",
                   "vtkImageExtractComponents", "vtkImageNormalize", "vtkImageClip",
                   "vtkImageMedian3D", "vtkImageGradient", "vtkImageGradientMagnitude"]
    for cls_name in new_classes:
        assert cls_name in WHITELISTED_CLASSES, f"{cls_name} not in whitelist"


@_register("Volume rendering auto-opacity")
def test_volume_auto_opacity():
    import vtk
    from vislang.filters import create_show, create_vtk_filter

    reader, _ = create_vtk_filter("vtkXMLStructuredGridReader", FileName=DATA_FILE)
    thresh, _ = create_vtk_filter("vtkThreshold", reader,
        ThresholdBy="theta", ThresholdRange=[350.0, 1200.0])

    # No explicit opacity_function — should auto-generate from histogram
    vol, _ = create_show(thresh,
        representation="Volume",
        color_by="theta",
        scalar_range=(350.0, 1200.0),
        lut="fire",
        volume_resolution=64)

    assert isinstance(vol, vtk.vtkVolume), "Expected vtkVolume"
    otf = vol.GetProperty().GetScalarOpacity()
    assert otf.GetSize() >= 2, "Auto-opacity should generate multiple control points"

    # First point should have low opacity (common ambient values)
    node = [0.0] * 4
    otf.GetNodeValue(0, node)
    assert node[1] < 0.1, f"First opacity should be low (ambient), got {node[1]}"


@_register("Volume rendering gradient opacity")
def test_volume_gradient_opacity():
    import vtk
    from vislang.filters import create_show, create_vtk_filter

    reader, _ = create_vtk_filter("vtkXMLImageDataReader", FileName="datasets/synthetic/data/output.vti")
    vol, _ = create_show(reader,
        representation="Volume",
        color_by="temperature",
        scalar_range=(0, 996),
        gradient_opacity=True)

    assert isinstance(vol, vtk.vtkVolume), "Expected vtkVolume"
    gotf = vol.GetProperty().GetGradientOpacity()
    assert gotf.GetSize() >= 2, "Should have gradient opacity control points"


@_register("Raw binary reader via vtkImageReader2")
def test_raw_reader():
    import struct
    raw_path = "/tmp/test_vol_integration.raw"
    with open(raw_path, "wb") as f:
        for i in range(8*8*8):
            f.write(struct.pack("B", i % 256))

    from vislang.filters import create_vtk_filter
    reader, status = create_vtk_filter("vtkImageReader2",
        FileName=raw_path,
        DataExtent=[0, 7, 0, 7, 0, 7],
        DataScalarType="unsigned_char",
        FileDimensionality=3)
    reader.Update()
    output = reader.GetOutput()
    assert output.GetNumberOfPoints() == 512, f"Expected 512 points, got {output.GetNumberOfPoints()}"
    assert output.GetDimensions() == (8, 8, 8), f"Expected (8,8,8) dims"


@_register("Clip and resample_to_image DSL functions")
def test_clip_and_resample():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    # Patch render to avoid segfault
    r.render = lambda: None
    r.screenshot = lambda path="x.png": path
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
clipped = clip(input=data, origin=(100, 0, 0), normal=(1, 0, 0))
show(clipped, "clipped", color_by="theta")
'''
    objs, statuses, shows, builder = interpret(code, r)
    assert "clipped" in objs, "clipped not in objects"
    objs["clipped"].Update()
    out = objs["clipped"].GetOutput()
    assert out.GetNumberOfPoints() > 0, "Clipped output should have points"
    assert out.GetNumberOfPoints() < 18300000, "Clipped should have fewer points than full data"


@_register("Volume rendering with clipping planes")
def test_volume_clipping():
    import vtk
    from vislang.filters import create_show, create_vtk_filter
    reader, _ = create_vtk_filter("vtkXMLImageDataReader", FileName="datasets/synthetic/data/output.vti")
    vol, _ = create_show(reader,
        representation="Volume",
        color_by="temperature",
        scalar_range=(0, 996),
        clip_planes=[{"origin": (0.5, 0.5, 0.5), "normal": (1, 0, 0)}])

    assert isinstance(vol, vtk.vtkVolume), "Expected vtkVolume"
    planes = vol.GetMapper().GetClippingPlanes()
    assert planes is not None, "Should have clipping planes"
    assert planes.GetNumberOfItems() == 1, "Should have 1 clipping plane"


@_register("FIELD_DEFAULTS is empty (domain-neutral)")
def test_field_defaults_empty():
    from vislang.colormaps import FIELD_DEFAULTS

    # FIELD_DEFAULTS should be empty — domain-specific defaults belong in
    # domain documentation files, not hardcoded in the MCP server.
    assert len(FIELD_DEFAULTS) == 0, \
        f"FIELD_DEFAULTS should be empty, has: {list(FIELD_DEFAULTS.keys())}"


@_register("Scene preset sets correct background")
def test_scene_preset():
    from vislang.dsl import PipelineBuilder
    builder = PipelineBuilder()
    builder.scene_preset("dark")
    assert builder._background == (0.02, 0.02, 0.06), \
        f"Expected (0.02, 0.02, 0.06), got {builder._background}"
    builder.scene_preset("light")
    assert builder._background == (0.85, 0.85, 0.9), \
        f"Expected (0.85, 0.85, 0.9), got {builder._background}"
    builder.scene_preset("white")
    assert builder._background == (1.0, 1.0, 1.0), \
        f"Expected (1.0, 1.0, 1.0), got {builder._background}"
    # Invalid preset should raise ValueError
    try:
        builder.scene_preset("nonexistent")
        assert False, "Expected ValueError for unknown preset"
    except ValueError:
        pass


@_register("Multiple scalar bars positioned at different x coords")
def test_multiple_scalar_bars():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    r.render = lambda: None
    r.screenshot = lambda path="x.png": path
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain", scalar_bar="Fuel")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta", scalar_range=(350.0, 1200.0), lut="fire", scalar_bar="Temp")
'''
    objs, statuses, shows, builder = interpret(code, r)
    assert shows.get("terrain", {}).get("status") == "ok", f"terrain show failed: {shows}"
    assert shows.get("fire", {}).get("status") == "ok", f"fire show failed: {shows}"
    # Check that scalar bars exist at different positions
    actors = r._renderer.GetActors2D()
    actors.InitTraversal()
    bar_x_positions = []
    for i in range(actors.GetNumberOfItems()):
        actor = actors.GetNextActor2D()
        if hasattr(actor, "GetTitle"):
            pos = actor.GetPosition()
            bar_x_positions.append(pos[0])
    assert len(bar_x_positions) >= 2, \
        f"Expected at least 2 scalar bars, found {len(bar_x_positions)}"
    assert len(set(bar_x_positions)) == len(bar_x_positions), \
        f"Scalar bars should have different x positions, got {bar_x_positions}"


@_register("Volume shade control")
def test_volume_shade_control():
    import vtk
    from vislang.filters import create_show, create_vtk_filter

    reader, _ = create_vtk_filter("vtkXMLImageDataReader", FileName="datasets/synthetic/data/output.vti")

    # shade=True (default)
    vol_on, _ = create_show(reader,
        representation="Volume",
        color_by="temperature",
        scalar_range=(0, 996),
        shade=True)
    assert isinstance(vol_on, vtk.vtkVolume), "Expected vtkVolume"
    assert vol_on.GetProperty().GetShade() == 1, "Shade should be on"

    # shade=False
    vol_off, _ = create_show(reader,
        representation="Volume",
        color_by="temperature",
        scalar_range=(0, 996),
        shade=False)
    assert isinstance(vol_off, vtk.vtkVolume), "Expected vtkVolume"
    assert vol_off.GetProperty().GetShade() == 0, "Shade should be off"


@_register("raw_source DSL function")
def test_raw_source_dsl():
    import struct
    raw_path = "/tmp/test_raw_source_dsl.raw"
    nx, ny, nz = 4, 4, 4
    with open(raw_path, "wb") as f:
        for i in range(nx * ny * nz):
            f.write(struct.pack("B", i % 256))

    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    r.render = lambda: None
    r.screenshot = lambda path="x.png": path
    code = f'''
vol = raw_source("{raw_path}", dimensions=(4, 4, 4), scalar_type="unsigned_char")
'''
    objs, statuses, shows, builder = interpret(code, r)
    assert "vol" in objs, f"vol not in objects, got: {list(objs.keys())}"
    objs["vol"].Update()
    output = objs["vol"].GetOutput()
    assert output.GetNumberOfPoints() == 64, \
        f"Expected 64 points, got {output.GetNumberOfPoints()}"
    assert output.GetDimensions() == (4, 4, 4), \
        f"Expected (4,4,4) dims, got {output.GetDimensions()}"
    os.remove(raw_path)


@_register("All convenience functions together")
def test_all_convenience_functions():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    r.render = lambda: None
    r.screenshot = lambda path="x.png": path
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")

# make_vector, curl, compute_magnitude
vel = make_vector(input=data)
vel_for_vort = make_vector(input=data)
vort = curl(vector_field=vel_for_vort, result="vorticity_magnitude", vector=False)
spd = compute_magnitude(input=data)

# contour, threshold, extract_grid
iso = contour(input=data, ContourBy="theta", Isosurfaces=[400.0])
hot = threshold(input=data, ThresholdBy="theta", ThresholdRange=[350.0, 1200.0])
terrain = extract_grid(input=data, VOI=[0,599,0,499,0,0])

# stream_tracer + seeds_near + tube
seeds = seeds_near(input=data, field="theta", min_val=400, max_val=1200, num_seeds=10, offset_z=10)
streams = stream_tracer(input=vel, SeedSource=seeds, Vectors="velocity",
    IntegrationDirection="Both", MaximumNumberOfSteps=500, MaximumPropagation=200,
    InitialIntegrationStep=0.5)
tubes = tube(input=streams, Radius=2.0, NumberOfSides=6)

# slice, clip
sl = slice(input=data, origin=(80, 0, 170), normal=(1, 0, 0))
cl = clip(input=data, origin=(100, 0, 0), normal=(1, 0, 0))

# scene_preset, title
scene_preset("dark")
title("All Convenience Functions Test", font_size=18)

# show with field defaults (color_by without lut/scalar_range)
show(terrain, "terrain", color_by="rhof_1")

# show with representation="Volume"
show(hot, "hot_vol", representation="Volume", color_by="theta",
    scalar_range=(350.0, 1200.0), lut="fire", volume_resolution=32)

# other shows
show(iso, "fire", color_by="theta", scalar_range=(350, 1200), lut="fire")
show(tubes, "wind", color_by="u", scalar_range=(-5, 20), opacity=0.7)
show(sl, "cross", color_by="theta", scalar_range=(298, 600))
show(cl, "clipped", color_by="theta")
show(vort, "vorticity", color_by="vorticity_magnitude", scalar_range=(0, 5))
show(spd, "speed_field", color_by="speed", scalar_range=(0, 20))
'''
    objs, statuses, shows, builder = interpret(code, r)
    # All key nodes should exist
    for name in ["vel", "vel_for_vort", "vort", "spd", "iso", "hot", "terrain",
                 "seeds", "streams", "tubes", "sl", "cl"]:
        assert name in objs, f"Node '{name}' not in objects, got: {list(objs.keys())}"
    # All shows should be ok
    for name, status in shows.items():
        assert status.get("status") == "ok", f"Show '{name}' failed: {status}"
    # Verify scene preset applied
    assert builder._background == (0.02, 0.02, 0.06), \
        f"scene_preset('dark') not applied, got {builder._background}"
    # Verify title was set
    assert builder._title is not None, "title() not applied"
    assert builder._title["text"] == "All Convenience Functions Test"


@_register("Volume rendering empty data raises ValueError")
def test_empty_volume_error():
    from vislang.filters import create_show, create_vtk_filter

    # Create threshold with impossible range to get empty data
    reader, _ = create_vtk_filter("vtkXMLStructuredGridReader", FileName=DATA_FILE)
    thresh, _ = create_vtk_filter("vtkThreshold", reader,
        ThresholdBy="theta", ThresholdRange=[99999.0, 100000.0])

    try:
        vol, _ = create_show(thresh,
            representation="Volume",
            color_by="theta",
            scalar_range=(99999.0, 100000.0),
            lut="fire",
            volume_resolution=32)
        assert False, "Expected ValueError for empty volume data"
    except ValueError as e:
        assert "0 points" in str(e), f"Expected '0 points' in error message, got: {e}"


@_register("Compute helpers (make_vector, curl, magnitude, gradient_magnitude)")
def test_compute_helpers():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    r.render = lambda: None
    r.screenshot = lambda path="x.png": path
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
vel = make_vector(input=data)
speed = compute_magnitude(input=data, result="speed")
vort = curl(vector_field=vel, result="vorticity_magnitude", vector=False)
grad = compute_gradient_magnitude(input=data, field="theta")
'''
    objs, statuses, shows, _ = interpret(code, r)
    # Check all nodes built successfully (no errors)
    for nid, st in statuses.items():
        assert "error" not in st, f"Node {st.get('name', nid)}: {st.get('error')}"
    # Check that specific arrays exist on the outputs
    objs["speed"].Update()
    assert objs["speed"].GetOutput().GetPointData().GetArray("speed") is not None
    objs["vort"].Update()
    assert objs["vort"].GetOutput().GetPointData().GetArray("vorticity_magnitude") is not None


@_register("Describe data returns useful info")
def test_describe_data():
    from vislang.filters import create_vtk_filter
    reader, _ = create_vtk_filter("vtkXMLStructuredGridReader", FileName=DATA_FILE)
    reader.Update()
    data = reader.GetOutput()
    # Simulate describe_data output
    lines = []
    lines.append(f"Points: {data.GetNumberOfPoints()}")
    assert data.GetNumberOfPoints() == 18300000


@_register("Suggest isosurface returns useful values")
def test_suggest_isosurface():
    from vislang.filters import create_vtk_filter
    reader, _ = create_vtk_filter("vtkXMLStructuredGridReader", FileName=DATA_FILE)
    reader.Update()
    data = reader.GetOutput()
    result = queries.suggest_isosurface(data, "theta", 3)
    assert "Isosurfaces=" in result
    assert "theta" in result


@_register("Math module available in DSL")
def test_math_in_dsl():
    r = Renderer(800, 600, mode=RenderMode.OFFSCREEN)
    r.render = lambda: None
    r.screenshot = lambda path="x.png": path
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
radius = math.sqrt(2) * 3
pi = math.pi
terrain = extract_grid(input=data, VOI=[251,850,0,499,0,0])
show(terrain, "t", color_by="rhof_1")
'''
    objs, _, shows, _ = interpret(code, r)
    assert "error" not in shows.get("t", {})


if __name__ == "__main__":
    if not os.path.exists(DATA_FILE):
        print(f"ERROR: Data file '{DATA_FILE}' not found. Run datasets/wildfire/download.sh.")
        sys.exit(1)

    # Initialize server context for tests that use server-layer tools
    import vislang.server as _srv
    from vislang.renderer import Renderer, RenderMode
    _srv._init_for_test(Renderer(640, 800, mode=RenderMode.OFFSCREEN))

    print(f"Running integration tests with {DATA_FILE}...")
    print()

    tests = [
        test_renderer_init,
        test_load_data,
        test_extract_grid,
        test_contour_fire,
        test_streamlines,
        test_tubes,
        test_query_statistics,
        test_query_spatial_extent,
        test_query_histogram,
        test_colormap_presets,
        test_full_demo,
        test_bad_vtk_class,
        test_bad_field_query,
        test_version_history,
        test_convenience_wrappers,
        test_suggest_camera,
        test_sample_point,
        test_dsl_overview,
        test_slice_cross_section,
        test_vorticity_pipeline,
        test_list_data_files,
        test_reader_caching,
        test_volume_rendering,
        test_volume_opacity_presets,
        test_volume_scalar_bar,
        test_color_transfer_function,
        test_new_vtk_classes,
        test_volume_auto_opacity,
        test_volume_gradient_opacity,
        test_raw_reader,
        test_clip_and_resample,
        test_volume_clipping,
        test_field_defaults_empty,
        test_scene_preset,
        test_multiple_scalar_bars,
        test_volume_shade_control,
        test_raw_source_dsl,
        test_all_convenience_functions,
        test_empty_volume_error,
        test_compute_helpers,
        test_describe_data,
        test_suggest_isosurface,
        test_math_in_dsl,
    ]

    for t in tests:
        t()

    print()
    print(f"Results: {RESULTS['passed']} passed, {RESULTS['failed']} failed")
    if RESULTS["errors"]:
        print("Failures:")
        for e in RESULTS["errors"]:
            print(f"  - {e}")
    sys.exit(0 if RESULTS["failed"] == 0 else 1)
