"""Integration tests for the VisLang system using real wildfire data."""

import os
import sys
import json

# Ensure we can import vislang
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vislang.renderer import Renderer
from vislang.dsl import interpret
from vislang import queries

DATA_FILE = "output.30000.vts"
RESULTS = {"passed": 0, "failed": 0, "errors": []}


def test(name):
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


@test("Renderer creates without error")
def test_renderer_init():
    r = Renderer(800, 600)
    r.render()
    path = r.screenshot("/tmp/test_empty.png")
    assert os.path.exists(path), "Screenshot file not created"
    assert os.path.getsize(path) > 100, "Screenshot file too small"


@test("Load real data source")
def test_load_data():
    r = Renderer(800, 600)
    code = f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")'
    objs, statuses, shows, builder = interpret(code, r)
    assert "data" in objs, "data not in objects"
    objs["data"].Update()
    output = objs["data"].GetOutput()
    assert output.GetNumberOfPoints() == 18300000, f"Expected 18.3M points, got {output.GetNumberOfPoints()}"


@test("Extract grid filter")
def test_extract_grid():
    r = Renderer(800, 600)
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
terrain = filter("vtkExtractGrid", input=data, VOI=[0,599,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1")
'''
    objs, statuses, shows, builder = interpret(code, r)
    assert "terrain" in objs
    objs["terrain"].Update()
    output = objs["terrain"].GetOutput()
    assert output.GetNumberOfPoints() > 0, "Terrain has no points"
    assert shows.get("terrain", {}).get("status") == "ok", "Show failed"


@test("Contour filter (fire isosurface)")
def test_contour_fire():
    r = Renderer(800, 600)
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


@test("Calculator + StreamTracer with seed source")
def test_streamlines():
    r = Renderer(800, 600)
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


@test("TubeFilter on streamlines")
def test_tubes():
    r = Renderer(800, 600)
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


@test("Query: get_array_info")
def test_query_array_info():
    r = Renderer(800, 600)
    code = f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")'
    objs, _, _, _ = interpret(code, r)
    objs["data"].Update()
    info = queries.get_array_info(objs["data"].GetOutput())
    assert "theta" in info
    assert "rhof_1" in info
    assert "18300000" in info


@test("Query: get_statistics")
def test_query_statistics():
    r = Renderer(800, 600)
    code = f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")'
    objs, _, _, _ = interpret(code, r)
    objs["data"].Update()
    stats = queries.get_statistics(objs["data"].GetOutput(), "theta")
    assert "298.751" in stats  # min theta
    assert "1183.94" in stats  # max theta


@test("Query: get_spatial_extent")
def test_query_spatial_extent():
    r = Renderer(800, 600)
    code = f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")'
    objs, _, _, _ = interpret(code, r)
    objs["data"].Update()
    extent = queries.get_spatial_extent(objs["data"].GetOutput(), "theta", 400.0, 1200.0)
    assert "3831 points" in extent
    assert "X:" in extent


@test("Query: get_histogram")
def test_query_histogram():
    r = Renderer(800, 600)
    code = f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")'
    objs, _, _, _ = interpret(code, r)
    objs["data"].Update()
    hist = queries.get_histogram(objs["data"].GetOutput(), "rhof_1", 10)
    assert "Histogram" in hist
    assert "█" in hist


@test("Color map presets")
def test_colormap_presets():
    r = Renderer(800, 600)
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
terrain = filter("vtkExtractGrid", input=data, VOI=[0,599,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1", scalar_range=(0.0, 0.6), lut="terrain")
'''
    objs, statuses, shows, builder = interpret(code, r)
    assert shows.get("terrain", {}).get("status") == "ok"


@test("Full wildfire demo pipeline")
def test_full_demo():
    r = Renderer(800, 600)
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
terrain = filter("vtkExtractGrid", input=data, VOI=[0,599,0,499,0,0])
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


@test("Error handling: bad VTK class")
def test_bad_vtk_class():
    r = Renderer(800, 600)
    code = 'bad = source("vtkFakeFilter", FileName="test.vts")'
    objs, statuses, shows, builder = interpret(code, r)
    # Should have an error in node_statuses
    has_error = any("error" in s for s in statuses.values())
    assert has_error, "Expected error for fake VTK class"


@test("Error handling: bad field name in query")
def test_bad_field_query():
    r = Renderer(800, 600)
    code = f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")'
    objs, _, _, _ = interpret(code, r)
    objs["data"].Update()
    result = queries.get_statistics(objs["data"].GetOutput(), "nonexistent_field")
    assert "not found" in result


@test("Version history saves correctly")
def test_version_history():
    from vislang.server import set_pipeline, _version, _history_dir
    os.makedirs(".vislang/history", exist_ok=True)
    result = set_pipeline(f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
terrain = filter("vtkExtractGrid", input=data, VOI=[0,599,0,499,0,0])
show(terrain, "t", color_by="rhof_1")
''')
    assert "Pipeline v" in result
    # Check version directory exists
    import glob
    versions = glob.glob(".vislang/history/v*")
    assert len(versions) > 0, "No version directories created"


@test("Convenience wrappers (contour, calculator, etc)")
def test_convenience_wrappers():
    r = Renderer(800, 600)
    code = f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
iso = contour(input=data, ContourBy="theta", Isosurfaces=[400.0])
show(iso, "iso", color_by="theta")
'''
    objs, statuses, shows, builder = interpret(code, r)
    assert "iso" in objs
    objs["iso"].Update()
    assert objs["iso"].GetOutput().GetNumberOfPoints() > 0


@test("Suggest camera for each style")
def test_suggest_camera():
    from vislang.server import set_pipeline, suggest_camera
    set_pipeline(f'''
data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")
terrain = filter("vtkExtractGrid", input=data, VOI=[0,599,0,499,0,0])
show(terrain, "terrain", color_by="rhof_1")
fire = filter("vtkContourFilter", input=data, ContourBy="theta", Isosurfaces=[400.0])
show(fire, "fire", color_by="theta", scalar_range=(350.0, 1200.0))
''')
    for style in ["overview", "closeup", "top_down", "side"]:
        result = suggest_camera(style)
        assert "camera(" in result, f"Style '{style}' missing camera params: {result}"
        assert "position=" in result, f"Style '{style}' missing position"
        assert "focal_point=" in result, f"Style '{style}' missing focal_point"


@test("Sample point returns field values")
def test_sample_point():
    from vislang.server import set_pipeline, sample_point
    set_pipeline(f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")')
    result = sample_point("data", 80.0, -10.0, 170.0)
    assert "theta" in result, f"Expected 'theta' in result: {result}"
    # Check there's at least one numeric value (digits with optional decimal)
    import re
    assert re.search(r"\d+\.\d+", result), f"Expected numeric values in result: {result}"


@test("List capabilities")
def test_list_capabilities():
    from vislang.server import list_capabilities
    result = list_capabilities()
    assert "vtkContourFilter" in result, "Missing vtkContourFilter"
    assert "fire" in result, "Missing fire colormap"
    assert "source" in result, "Missing source function"


@test("Slice cross section")
def test_slice_cross_section():
    r = Renderer(800, 600)
    code = f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")\ncs = slice(input=data, origin=(80, -10, 170), normal=(1, 0, 0))\nshow(cs, "cross", color_by="theta")'
    objs, statuses, shows, builder = interpret(code, r)
    assert "cs" in objs, f"cs not in objects, got: {list(objs.keys())}"
    objs["cs"].Update()
    output = objs["cs"].GetOutput()
    assert output.GetNumberOfPoints() > 0, f"Cross section has no points"


@test("Vorticity pipeline")
def test_vorticity_pipeline():
    r = Renderer(800, 600)
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


@test("Suggest scalar range")
def test_suggest_scalar_range():
    from vislang.server import set_pipeline, suggest_scalar_range
    set_pipeline(f'data = source("vtkXMLStructuredGridReader", FileName="{DATA_FILE}")')
    result = suggest_scalar_range("data", "theta")
    assert "percentile" in result.lower() or "Percentiles" in result
    assert "Suggested range" in result

@test("List data files")
def test_list_data_files():
    from vislang.server import list_data_files
    result = list_data_files()
    assert "output.30000.vts" in result


@test("Reader caching")
def test_reader_caching():
    from vislang.filters import clear_reader_cache
    clear_reader_cache()
    r = Renderer(800, 600)
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


@test("Volume rendering pipeline builds correctly")
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


@test("Volume rendering with opacity presets")
def test_volume_opacity_presets():
    from vislang.colormaps import build_opacity_function
    for preset in ["ramp_up", "gaussian", "step"]:
        otf = build_opacity_function(preset, scalar_range=(0, 100), opacity_scale=0.5)
        assert otf.GetSize() >= 2, f"Preset '{preset}' has too few control points"

    # Test custom list
    otf = build_opacity_function([(0, 0.0), (50, 0.5), (100, 1.0)], scalar_range=(0, 100))
    assert otf.GetSize() == 3, "Custom opacity function has wrong number of points"


@test("Volume rendering with scalar bar")
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


@test("Color transfer function from presets")
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


@test("Suggest opacity function for volume rendering")
def test_suggest_opacity():
    from vislang.filters import create_vtk_filter
    reader, _ = create_vtk_filter("vtkXMLStructuredGridReader", FileName=DATA_FILE)
    reader.Update()
    data = reader.GetOutput()

    result = queries.suggest_opacity_function(data, "theta", scalar_range=(350, 1200))
    assert "opacity_function=" in result, "Should contain opacity_function"
    assert "350" in result, "Should reference scalar range min"

    # Test with O2
    result2 = queries.suggest_opacity_function(data, "O2")
    assert "opacity_function=" in result2, "Should contain opacity_function for O2"
    assert "Ambient peak" in result2, "Should identify ambient peak"


@test("New VTK filter classes in whitelist")
def test_new_vtk_classes():
    from vislang.filters import WHITELISTED_CLASSES
    new_classes = ["vtkWarpVector", "vtkMaskPoints", "vtkGradientFilter",
                   "vtkResampleToImage", "vtkAppendFilter", "vtkTransformFilter"]
    for cls_name in new_classes:
        assert cls_name in WHITELISTED_CLASSES, f"{cls_name} not in whitelist"


if __name__ == "__main__":
    if not os.path.exists(DATA_FILE):
        print(f"ERROR: Data file '{DATA_FILE}' not found. Run from project root.")
        sys.exit(1)

    print(f"Running integration tests with {DATA_FILE}...")
    print()

    tests = [
        test_renderer_init,
        test_load_data,
        test_extract_grid,
        test_contour_fire,
        test_streamlines,
        test_tubes,
        test_query_array_info,
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
        test_list_capabilities,
        test_slice_cross_section,
        test_vorticity_pipeline,
        test_suggest_scalar_range,
        test_list_data_files,
        test_reader_caching,
        test_volume_rendering,
        test_volume_opacity_presets,
        test_volume_scalar_bar,
        test_color_transfer_function,
        test_suggest_opacity,
        test_new_vtk_classes,
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
