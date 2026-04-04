"""VTK filter creation with property mapping and whitelisting."""

import vtk

# Reader cache: avoids re-reading large files on pipeline rebuild
_reader_cache = {}  # (class_name, filename) -> vtk_algorithm


def clear_reader_cache():
    """Clear the reader cache (for testing)."""
    _reader_cache.clear()


WHITELISTED_CLASSES = {
    # Sources / Readers
    "vtkXMLStructuredGridReader": vtk.vtkXMLStructuredGridReader,
    "vtkXMLImageDataReader": vtk.vtkXMLImageDataReader,
    "vtkArrowSource": vtk.vtkArrowSource,
    "vtkLineSource": vtk.vtkLineSource,
    "vtkPointSource": vtk.vtkPointSource,
    # Filters
    "vtkContourFilter": vtk.vtkContourFilter,
    "vtkArrayCalculator": vtk.vtkArrayCalculator,
    "vtkExtractGrid": vtk.vtkExtractGrid,
    "vtkThreshold": vtk.vtkThreshold,
    "vtkStreamTracer": vtk.vtkStreamTracer,
    "vtkTubeFilter": vtk.vtkTubeFilter,
    "vtkCellDataToPointData": vtk.vtkCellDataToPointData,
    "vtkCellDerivatives": vtk.vtkCellDerivatives,
    "vtkGlyph3D": vtk.vtkGlyph3D,
    "vtkWindowedSincPolyDataFilter": vtk.vtkWindowedSincPolyDataFilter,
    "vtkExtractVOI": vtk.vtkExtractVOI,
    "vtkGeometryFilter": vtk.vtkGeometryFilter,
    "vtkDataSetSurfaceFilter": vtk.vtkDataSetSurfaceFilter,
    "vtkWarpScalar": vtk.vtkWarpScalar,
    "vtkCutter": vtk.vtkCutter,
    "vtkClipDataSet": vtk.vtkClipDataSet,
    "vtkProbeFilter": vtk.vtkProbeFilter,
}


def create_vtk_filter(vtk_class_name, input_algorithm=None, **properties):
    """Create a VTK filter/source, connect input, apply properties, update."""
    if vtk_class_name not in WHITELISTED_CLASSES:
        raise ValueError(
            f"VTK class '{vtk_class_name}' not in whitelist. "
            f"Available: {sorted(WHITELISTED_CLASSES.keys())}"
        )

    # Use reader cache for file readers to avoid re-reading large files
    _cacheable_readers = {"vtkXMLStructuredGridReader", "vtkXMLImageDataReader"}
    if vtk_class_name in _cacheable_readers and "FileName" in properties:
        cache_key = (vtk_class_name, properties["FileName"])
        if cache_key in _reader_cache:
            cached = _reader_cache[cache_key]
            cached.Update()
            output = cached.GetOutput()
            status = {
                "class": vtk_class_name,
                "num_points": output.GetNumberOfPoints() if output else 0,
                "num_cells": output.GetNumberOfCells() if output else 0,
            }
            if output and output.GetNumberOfPoints() > 0:
                status["bounds"] = list(output.GetBounds())
            if output:
                arrays = []
                pd = output.GetPointData()
                for i in range(pd.GetNumberOfArrays()):
                    arrays.append(pd.GetArrayName(i))
                if arrays:
                    status["point_arrays"] = arrays
            status["cached"] = True
            return cached, status

    vtk_obj = WHITELISTED_CLASSES[vtk_class_name]()

    if input_algorithm is not None:
        if hasattr(input_algorithm, "GetOutputPort"):
            vtk_obj.SetInputConnection(input_algorithm.GetOutputPort())
        else:
            vtk_obj.SetInputData(input_algorithm)

    _apply_properties(vtk_obj, vtk_class_name, properties)

    # For filters that need active vectors, set them on input
    if vtk_class_name in ("vtkStreamTracer", "vtkGlyph3D", "vtkCellDerivatives") and input_algorithm is not None:
        if hasattr(input_algorithm, "GetOutput"):
            input_data = input_algorithm.GetOutput()
            pd = input_data.GetPointData()
            for i in range(pd.GetNumberOfArrays()):
                arr = pd.GetArray(i)
                if arr and arr.GetNumberOfComponents() == 3:
                    pd.SetActiveVectors(arr.GetName())
                    break

    vtk_obj.Update()

    # Cache readers for reuse
    if vtk_class_name in ("vtkXMLStructuredGridReader", "vtkXMLImageDataReader") and "FileName" in properties:
        cache_key = (vtk_class_name, properties["FileName"])
        _reader_cache[cache_key] = vtk_obj

    output = vtk_obj.GetOutput()
    status = {
        "class": vtk_class_name,
        "num_points": output.GetNumberOfPoints() if output else 0,
        "num_cells": output.GetNumberOfCells() if output else 0,
    }
    if output and output.GetNumberOfPoints() > 0:
        status["bounds"] = list(output.GetBounds())

    if output:
        arrays = []
        pd = output.GetPointData()
        for i in range(pd.GetNumberOfArrays()):
            arrays.append(pd.GetArrayName(i))
        if arrays:
            status["point_arrays"] = arrays
        cd = output.GetCellData()
        cell_arrays = []
        for i in range(cd.GetNumberOfArrays()):
            cell_arrays.append(cd.GetArrayName(i))
        if cell_arrays:
            status["cell_arrays"] = cell_arrays

    if status["num_points"] == 0:
        # Diagnose why output is empty
        warning = "Filter produced empty output"
        if vtk_class_name == "vtkContourFilter":
            # Check if contour values are in range
            contour_by = properties.get("ContourBy", "")
            iso_vals = properties.get("Isosurfaces", [])
            if input_algorithm and hasattr(input_algorithm, "GetOutput"):
                inp = input_algorithm.GetOutput()
                if inp and contour_by:
                    arr = inp.GetPointData().GetArray(contour_by)
                    if arr is None:
                        available = [inp.GetPointData().GetArrayName(i) for i in range(inp.GetPointData().GetNumberOfArrays())]
                        warning += f". Field '{contour_by}' not found. Available: {available}"
                    elif arr:
                        rng = arr.GetRange()
                        out_of_range = [v for v in iso_vals if v < rng[0] or v > rng[1]]
                        if out_of_range:
                            warning += (
                                f". Isosurface values {out_of_range} are outside "
                                f"'{contour_by}' range [{rng[0]:.4g}, {rng[1]:.4g}]"
                            )
                        else:
                            warning += f". Values {iso_vals} are within range [{rng[0]:.4g}, {rng[1]:.4g}] but produced no surface"
        elif vtk_class_name == "vtkThreshold":
            thresh_by = properties.get("ThresholdBy", "")
            thresh_range = properties.get("ThresholdRange", [])
            if input_algorithm and hasattr(input_algorithm, "GetOutput") and thresh_by:
                inp = input_algorithm.GetOutput()
                if inp:
                    arr = inp.GetPointData().GetArray(thresh_by)
                    if arr:
                        rng = arr.GetRange()
                        if thresh_range and (thresh_range[0] > rng[1] or thresh_range[1] < rng[0]):
                            warning += (
                                f". ThresholdRange {thresh_range} doesn't overlap "
                                f"'{thresh_by}' range [{rng[0]:.4g}, {rng[1]:.4g}]"
                            )
        elif vtk_class_name == "vtkStreamTracer":
            warning += (
                ". Check: (1) seed points are inside the data bounds "
                "(use get_ground_z to find valid z-coordinates), "
                "(2) velocity vectors exist on the input data"
            )
        status["warning"] = warning

    return vtk_obj, status


def _apply_properties(vtk_obj, vtk_class_name, properties):
    """Apply properties to a VTK object with special-case handling."""
    for key, value in properties.items():
        if key == "Isosurfaces":
            for i, v in enumerate(value):
                vtk_obj.SetValue(i, v)
            vtk_obj.SetNumberOfContours(len(value))
        elif key == "ContourBy":
            vtk_obj.SetInputArrayToProcess(0, 0, 0, 0, value)
        elif key == "Vectors":
            vtk_obj.SetInputArrayToProcess(0, 0, 0, 0, value)
        elif key == "ThresholdRange":
            # VTK 9.x threshold API
            vtk_obj.SetLowerThreshold(value[0])
            vtk_obj.SetUpperThreshold(value[1])
            vtk_obj.SetThresholdFunction(vtk.vtkThreshold.THRESHOLD_BETWEEN)
        elif key == "ThresholdBy":
            vtk_obj.SetInputArrayToProcess(0, 0, 0, 0, value)
        elif key == "AddScalarArrayName":
            for name in value:
                vtk_obj.AddScalarArrayName(name)
        elif key == "AddVectorArrayName":
            for name in value:
                vtk_obj.AddVectorArrayName(name)
        elif key == "VOI":
            vtk_obj.SetVOI(*value)
        elif key == "SampleRate":
            vtk_obj.SetSampleRate(*value)
        elif key == "IntegrationDirection":
            directions = {
                "Forward": vtk_obj.FORWARD if hasattr(vtk_obj, "FORWARD") else 0,
                "Backward": vtk_obj.BACKWARD if hasattr(vtk_obj, "BACKWARD") else 1,
                "Both": vtk_obj.BOTH if hasattr(vtk_obj, "BOTH") else 2,
            }
            vtk_obj.SetIntegrationDirection(directions.get(value, 2))
        elif key == "IntegratorType":
            integrators = {"RungeKutta2": 0, "RungeKutta4": 1, "RungeKutta45": 2}
            vtk_obj.SetIntegratorType(integrators.get(value, 2))
        elif key == "GlyphSource":
            # value is a vtk algorithm (source for glyphs)
            vtk_obj.SetSourceConnection(value.GetOutputPort())
        elif key == "ScaleArray":
            vtk_obj.SetInputArrayToProcess(0, 0, 0, 0, value)
            vtk_obj.SetScaleModeToScaleByScalar()
        elif key == "OrientationArray":
            vtk_obj.SetInputArrayToProcess(1, 0, 0, 0, value)
            vtk_obj.OrientOn()
            vtk_obj.SetVectorModeToUseVector()
        elif key == "GlyphMode":
            modes = {
                "AllPoints": 0,
                "EveryNthPoint": 1,
                "UniformSpatialDistribution": 2,
            }
            if hasattr(vtk_obj, "SetGlyphMode"):
                vtk_obj.SetGlyphMode(modes.get(value, 0))
        elif key == "VectorMode":
            mode_setter = f"SetVectorModeTo{value}"
            if hasattr(vtk_obj, mode_setter):
                getattr(vtk_obj, mode_setter)()
            else:
                raise ValueError(f"Unknown VectorMode '{value}'")
        elif key == "TensorMode":
            mode_setter = f"SetTensorModeTo{value}"
            if hasattr(vtk_obj, mode_setter):
                getattr(vtk_obj, mode_setter)()
            else:
                raise ValueError(f"Unknown TensorMode '{value}'")
        elif key == "CutFunction":
            # value is a dict like {"type": "Plane", "Origin": (x,y,z), "Normal": (x,y,z)}
            if value.get("type") == "Plane":
                plane = vtk.vtkPlane()
                if value.get("Origin") is not None:
                    plane.SetOrigin(*value["Origin"])
                if value.get("Normal") is not None:
                    plane.SetNormal(*value["Normal"])
                vtk_obj.SetCutFunction(plane)
            else:
                raise ValueError(f"Unsupported CutFunction type: {value.get('type')}")
        elif key == "SeedSource":
            # value is a vtk algorithm providing seed points
            if hasattr(value, "GetOutputPort"):
                vtk_obj.SetSourceConnection(value.GetOutputPort())
            else:
                vtk_obj.SetSourceData(value)
        else:
            # Default: try Set{Key}(value)
            setter = f"Set{key}"
            if hasattr(vtk_obj, setter):
                getattr(vtk_obj, setter)(value)
            else:
                raise ValueError(
                    f"VTK class '{vtk_class_name}' has no method '{setter}'"
                )


def create_show(vtk_algorithm, **display_props):
    """Create a vtkActor from a filter's output with display properties."""
    mapper = vtk.vtkDataSetMapper()
    if hasattr(vtk_algorithm, "GetOutputPort"):
        mapper.SetInputConnection(vtk_algorithm.GetOutputPort())
    else:
        mapper.SetInputData(vtk_algorithm)

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)

    prop = actor.GetProperty()

    color_by = display_props.get("color_by")
    scalar_range = display_props.get("scalar_range")
    color = display_props.get("color")
    opacity = display_props.get("opacity")
    specular = display_props.get("specular")
    specular_power = display_props.get("specular_power")
    representation = display_props.get("representation")
    lut_config = display_props.get("lut")
    line_width = display_props.get("line_width")

    if color_by:
        mapper.SetScalarModeToUsePointFieldData()
        mapper.SelectColorArray(color_by)
        mapper.SetColorModeToMapScalars()
        mapper.ScalarVisibilityOn()
        if scalar_range:
            mapper.SetScalarRange(*scalar_range)

        if lut_config:
            from .colormaps import build_lut
            lut = build_lut(lut_config, scalar_range=scalar_range)
            mapper.SetLookupTable(lut)
    elif color:
        mapper.ScalarVisibilityOff()
        prop.SetColor(*color)
    else:
        mapper.ScalarVisibilityOn()

    if opacity is not None:
        prop.SetOpacity(opacity)
    if specular is not None:
        prop.SetSpecular(specular)
    if specular_power is not None:
        prop.SetSpecularPower(specular_power)
    if line_width is not None:
        prop.SetLineWidth(line_width)

    if representation:
        rep_map = {
            "Surface": vtk.VTK_SURFACE,
            "Wireframe": vtk.VTK_WIREFRAME,
            "Points": vtk.VTK_POINTS,
        }
        if representation == "Volume":
            # Volume rendering needs a different approach; fallback to surface
            prop.SetRepresentation(vtk.VTK_SURFACE)
            prop.SetOpacity(display_props.get("opacity", 0.3))
        elif representation in rep_map:
            prop.SetRepresentation(rep_map[representation])

    # Scalar bar (color legend)
    scalar_bar_prop = display_props.get("scalar_bar")
    if scalar_bar_prop and color_by:
        bar = vtk.vtkScalarBarActor()
        bar.SetLookupTable(mapper.GetLookupTable())
        bar.SetTitle(scalar_bar_prop if isinstance(scalar_bar_prop, str) else color_by)
        bar.SetNumberOfLabels(5)
        bar.SetWidth(0.08)
        bar.SetHeight(0.4)
        bar.SetPosition(0.88, 0.3)
        bar.GetTitleTextProperty().SetFontSize(14)
        bar.GetTitleTextProperty().SetColor(1, 1, 1)
        bar.GetLabelTextProperty().SetColor(1, 1, 1)
        bar.GetLabelTextProperty().SetFontSize(10)
        return actor, bar
    return actor, None
