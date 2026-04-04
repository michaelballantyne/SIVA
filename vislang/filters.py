"""VTK filter creation with property mapping and whitelisting."""

import vtk

WHITELISTED_CLASSES = {
    # Sources
    "vtkXMLStructuredGridReader": vtk.vtkXMLStructuredGridReader,
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
}


def create_vtk_filter(vtk_class_name, input_algorithm=None, **properties):
    """Create a VTK filter/source, connect input, apply properties, update."""
    if vtk_class_name not in WHITELISTED_CLASSES:
        raise ValueError(
            f"VTK class '{vtk_class_name}' not in whitelist. "
            f"Available: {sorted(WHITELISTED_CLASSES.keys())}"
        )

    vtk_obj = WHITELISTED_CLASSES[vtk_class_name]()

    if input_algorithm is not None:
        if hasattr(input_algorithm, "GetOutputPort"):
            vtk_obj.SetInputConnection(input_algorithm.GetOutputPort())
        else:
            vtk_obj.SetInputData(input_algorithm)

    _apply_properties(vtk_obj, vtk_class_name, properties)

    # For stream tracer, ensure active vectors are set on input
    if vtk_class_name == "vtkStreamTracer" and input_algorithm is not None:
        if hasattr(input_algorithm, "GetOutput"):
            input_data = input_algorithm.GetOutput()
            pd = input_data.GetPointData()
            # Find the first 3-component array to use as vectors if active vectors not set
            for i in range(pd.GetNumberOfArrays()):
                arr = pd.GetArray(i)
                if arr and arr.GetNumberOfComponents() == 3:
                    pd.SetActiveVectors(arr.GetName())
                    break

    vtk_obj.Update()

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
        status["warning"] = "Filter produced empty output"

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
        elif key == "OrientationArray":
            vtk_obj.SetInputArrayToProcess(1, 0, 0, 0, value)
        elif key == "GlyphMode":
            modes = {
                "AllPoints": 0,
                "EveryNthPoint": 1,
                "UniformSpatialDistribution": 2,
            }
            if hasattr(vtk_obj, "SetGlyphMode"):
                vtk_obj.SetGlyphMode(modes.get(value, 0))
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
            lut = vtk.vtkLookupTable()
            lut.SetNumberOfTableValues(256)
            if "hue_range" in lut_config:
                lut.SetHueRange(*lut_config["hue_range"])
            if "saturation_range" in lut_config:
                lut.SetSaturationRange(*lut_config["saturation_range"])
            if "value_range" in lut_config:
                lut.SetValueRange(*lut_config["value_range"])
            if "alpha_range" in lut_config:
                lut.SetAlphaRange(*lut_config["alpha_range"])
            if scalar_range:
                lut.SetTableRange(*scalar_range)
            lut.Build()
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

    return actor
