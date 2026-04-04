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
    "vtkXMLPolyDataReader": vtk.vtkXMLPolyDataReader,
    "vtkXMLUnstructuredGridReader": vtk.vtkXMLUnstructuredGridReader,
    "vtkXMLRectilinearGridReader": vtk.vtkXMLRectilinearGridReader,
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
    "vtkResampleToImage": vtk.vtkResampleToImage,
    "vtkWarpVector": vtk.vtkWarpVector,
    "vtkMaskPoints": vtk.vtkMaskPoints,
    "vtkPassArrays": vtk.vtkPassArrays,
    "vtkAppendFilter": vtk.vtkAppendFilter,
    "vtkTransformFilter": vtk.vtkTransformFilter,
    "vtkGradientFilter": vtk.vtkGradientFilter,
    "vtkImageReader2": vtk.vtkImageReader2,
}


def create_vtk_filter(vtk_class_name, input_algorithm=None, **properties):
    """Create a VTK filter/source, connect input, apply properties, update."""
    if vtk_class_name not in WHITELISTED_CLASSES:
        raise ValueError(
            f"VTK class '{vtk_class_name}' not in whitelist. "
            f"Available: {sorted(WHITELISTED_CLASSES.keys())}"
        )

    # Use reader cache for file readers to avoid re-reading large files
    _cacheable_readers = {"vtkXMLStructuredGridReader", "vtkXMLImageDataReader",
                          "vtkXMLPolyDataReader", "vtkXMLUnstructuredGridReader",
                          "vtkXMLRectilinearGridReader"}
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
    if vtk_class_name in _cacheable_readers and "FileName" in properties:
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
            # Accept single value or list
            if isinstance(value, (int, float)):
                value = [value]
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
                # vtkCutter uses SetCutFunction, vtkClipDataSet uses SetClipFunction
                if hasattr(vtk_obj, "SetCutFunction"):
                    vtk_obj.SetCutFunction(plane)
                elif hasattr(vtk_obj, "SetClipFunction"):
                    vtk_obj.SetClipFunction(plane)
            else:
                raise ValueError(f"Unsupported CutFunction type: {value.get('type')}")
        elif key == "_probe_source":
            # Internal: set source for vtkProbeFilter
            if hasattr(value, "GetOutputPort"):
                vtk_obj.SetSourceConnection(value.GetOutputPort())
            elif hasattr(value, "GetOutput"):
                vtk_obj.SetSourceData(value.GetOutput())
        elif key == "SamplingDimensions":
            vtk_obj.SetSamplingDimensions(*value)
        elif key == "SeedSource":
            # value is a vtk algorithm providing seed points
            if hasattr(value, "GetOutputPort"):
                vtk_obj.SetSourceConnection(value.GetOutputPort())
            else:
                vtk_obj.SetSourceData(value)
        elif key == "OnRatio":
            vtk_obj.SetOnRatio(value)
        elif key == "RandomMode":
            if value:
                vtk_obj.RandomModeOn()
            else:
                vtk_obj.RandomModeOff()
        elif key == "GradientField":
            vtk_obj.SetInputArrayToProcess(0, 0, 0, 0, value)
        elif key == "DataExtent":
            vtk_obj.SetDataExtent(*value)
        elif key == "DataScalarType":
            _scalar_type_map = {
                "unsigned_char": vtk.VTK_UNSIGNED_CHAR,
                "char": vtk.VTK_CHAR,
                "unsigned_short": vtk.VTK_UNSIGNED_SHORT,
                "short": vtk.VTK_SHORT,
                "unsigned_int": vtk.VTK_UNSIGNED_INT,
                "int": vtk.VTK_INT,
                "float": vtk.VTK_FLOAT,
                "double": vtk.VTK_DOUBLE,
            }
            if isinstance(value, str):
                scalar_type = _scalar_type_map.get(value)
                if scalar_type is None:
                    raise ValueError(
                        f"Unknown scalar type '{value}'. "
                        f"Available: {sorted(_scalar_type_map.keys())}"
                    )
                vtk_obj.SetDataScalarType(scalar_type)
            else:
                # Assume it's already a VTK type constant (int)
                vtk_obj.SetDataScalarType(value)
        elif key == "FileDimensionality":
            vtk_obj.SetFileDimensionality(value)
        elif key == "NumberOfScalarComponents":
            vtk_obj.SetNumberOfScalarComponents(value)
        elif key == "HeaderSize":
            vtk_obj.SetHeaderSize(value)
        else:
            # Default: try Set{Key}(value)
            setter = f"Set{key}"
            if hasattr(vtk_obj, setter):
                getattr(vtk_obj, setter)(value)
            else:
                raise ValueError(
                    f"VTK class '{vtk_class_name}' has no method '{setter}'"
                )


def _auto_opacity(arr, scalar_range, num_bins=50, num_points=8, max_opacity=0.6):
    """Generate histogram-guided opacity control points.

    Makes common (ambient) values transparent and rare (feature) values opaque.
    """
    lo, hi = scalar_range
    if hi <= lo:
        return None

    n = arr.GetNumberOfTuples()
    bin_width = (hi - lo) / num_bins
    counts = [0] * num_bins

    step = max(1, n // 20000)
    for i in range(0, n, step):
        v = arr.GetValue(i)
        if lo <= v <= hi:
            idx = min(int((v - lo) / bin_width), num_bins - 1)
            counts[idx] += 1

    max_count = max(counts) if counts else 1

    # Generate control points spaced evenly across the range
    points = []
    step_size = max(1, num_bins // (num_points - 1))
    for i in range(0, num_bins, step_size):
        val = lo + (i + 0.5) * bin_width
        fraction = counts[i] / max_count if max_count > 0 else 0
        opacity = max_opacity * (1.0 - fraction)
        points.append((round(val, 6), round(max(0.0, opacity), 4)))

    # Ensure endpoints
    if points and points[-1][0] < hi:
        last_frac = counts[-1] / max_count if max_count > 0 else 0
        points.append((round(hi, 6), round(max_opacity * (1.0 - last_frac), 4)))

    return points


def _create_volume(vtk_algorithm, **display_props):
    """Create a vtkVolume for volume rendering.

    Returns (vtkVolume, scalar_bar_or_None).
    """
    from .colormaps import build_color_transfer_function, build_opacity_function

    color_by = display_props.get("color_by")
    scalar_range = display_props.get("scalar_range")
    lut_config = display_props.get("lut")
    opacity = display_props.get("opacity", 1.0)
    opacity_function = display_props.get("opacity_function")
    volume_resolution = display_props.get("volume_resolution", 256)

    # Cap volume resolution to prevent OOM
    max_res = 512
    if isinstance(volume_resolution, (int, float)) and volume_resolution > max_res:
        import logging
        logging.getLogger("vislang").warning(
            f"volume_resolution={volume_resolution} capped to {max_res}")
        volume_resolution = max_res

    # Get the output data to check its type
    if hasattr(vtk_algorithm, "GetOutput"):
        vtk_algorithm.Update()
        data = vtk_algorithm.GetOutput()
    else:
        data = vtk_algorithm

    # Guard: if the data has 0 points, volume rendering will fail
    if data is None or data.GetNumberOfPoints() == 0:
        raise ValueError(
            "Volume rendering input has 0 points. "
            "Check your threshold/filter - the data may be empty. "
            "Use get_statistics() to verify field ranges."
        )

    # Set active scalars if color_by is specified
    if color_by and data:
        pd = data.GetPointData()
        if pd.GetArray(color_by) is not None:
            pd.SetActiveScalars(color_by)

    # Determine scalar range from data if not provided
    if scalar_range is None and data and color_by:
        arr = data.GetPointData().GetArray(color_by)
        if arr:
            scalar_range = arr.GetRange()
    if scalar_range is None and data:
        pd = data.GetPointData()
        if pd.GetScalars():
            scalar_range = pd.GetScalars().GetRange()
    if scalar_range is None:
        scalar_range = (0.0, 1.0)

    # Volume mappers require vtkImageData. If the data is not image data,
    # resample it using vtkResampleToImage.
    need_resample = True
    if data is not None:
        data_class = data.GetClassName()
        if data_class in ("vtkImageData", "vtkUniformGrid"):
            need_resample = False

    if need_resample:
        resampler = vtk.vtkResampleToImage()
        if hasattr(vtk_algorithm, "GetOutputPort"):
            resampler.SetInputConnection(vtk_algorithm.GetOutputPort())
        else:
            resampler.SetInputDataObject(vtk_algorithm)

        # Set sampling dimensions
        if isinstance(volume_resolution, (list, tuple)):
            resampler.SetSamplingDimensions(*volume_resolution)
        else:
            # Use the resolution as max dimension, scale others proportionally
            if data is not None:
                bounds = data.GetBounds()
                dx = bounds[1] - bounds[0]
                dy = bounds[3] - bounds[2]
                dz = bounds[5] - bounds[4]
                max_dim = max(dx, dy, dz)
                if max_dim > 0:
                    nx = max(2, int(volume_resolution * dx / max_dim))
                    ny = max(2, int(volume_resolution * dy / max_dim))
                    nz = max(2, int(volume_resolution * dz / max_dim))
                    resampler.SetSamplingDimensions(nx, ny, nz)
                else:
                    resampler.SetSamplingDimensions(volume_resolution, volume_resolution, volume_resolution)
            else:
                resampler.SetSamplingDimensions(volume_resolution, volume_resolution, volume_resolution)

        resampler.Update()
        image_data = resampler.GetOutput()

        # Set active scalars on resampled data
        if color_by and image_data:
            rpd = image_data.GetPointData()
            if rpd.GetArray(color_by) is not None:
                rpd.SetActiveScalars(color_by)

        mapper = vtk.vtkSmartVolumeMapper()
        mapper.SetInputConnection(resampler.GetOutputPort())
    else:
        mapper = vtk.vtkSmartVolumeMapper()
        if hasattr(vtk_algorithm, "GetOutputPort"):
            mapper.SetInputConnection(vtk_algorithm.GetOutputPort())
        else:
            mapper.SetInputData(vtk_algorithm)

    if color_by:
        mapper.SetScalarModeToUsePointFieldData()
        mapper.SelectScalarArray(color_by)

    # Build color transfer function
    if lut_config is not None:
        ctf = build_color_transfer_function(lut_config, scalar_range=scalar_range)
    else:
        # Default: grayscale ramp
        ctf = vtk.vtkColorTransferFunction()
        ctf.AddRGBPoint(scalar_range[0], 0.0, 0.0, 0.0)
        ctf.AddRGBPoint(scalar_range[1], 1.0, 1.0, 1.0)

    # Build opacity transfer function
    # When no opacity_function is specified, generate a histogram-guided one
    # that makes common values transparent and rare values opaque
    if opacity_function is None and data and color_by:
        arr = data.GetPointData().GetArray(color_by)
        if arr is not None:
            opacity_function = _auto_opacity(arr, scalar_range)

    opacity_scale = opacity if opacity is not None else 1.0
    otf = build_opacity_function(opacity_function, scalar_range=scalar_range, opacity_scale=opacity_scale)

    # Create volume property
    vol_prop = vtk.vtkVolumeProperty()
    vol_prop.SetColor(ctf)
    vol_prop.SetScalarOpacity(otf)
    vol_prop.SetInterpolationTypeToLinear()
    vol_prop.SetAmbient(display_props.get("ambient", 0.3))
    vol_prop.SetDiffuse(display_props.get("diffuse", 0.6))
    vol_prop.SetSpecular(display_props.get("specular", 0.2))
    if display_props.get("specular_power") is not None:
        vol_prop.SetSpecularPower(display_props["specular_power"])

    # Gradient opacity: enhances edges/surfaces in volume rendering
    # by modulating opacity based on the gradient magnitude
    gradient_opacity = display_props.get("gradient_opacity")
    if gradient_opacity is True:
        # Auto gradient opacity: ramp from 0 to 1 over data gradient range
        gotf = vtk.vtkPiecewiseFunction()
        gotf.AddPoint(0.0, 0.0)
        gotf.AddPoint(0.5, 0.1)
        gotf.AddPoint(1.0, 1.0)
        vol_prop.SetGradientOpacity(gotf)
    elif isinstance(gradient_opacity, list):
        # Custom control points: [(grad_value, opacity), ...]
        gotf = vtk.vtkPiecewiseFunction()
        for gval, gop in gradient_opacity:
            gotf.AddPoint(gval, gop)
        vol_prop.SetGradientOpacity(gotf)

    # Shading control
    shade = display_props.get("shade", True)
    if shade:
        vol_prop.ShadeOn()
    else:
        vol_prop.ShadeOff()

    # Sample distance for ray marching (lower = higher quality, slower)
    sample_distance = display_props.get("sample_distance")
    if sample_distance is not None:
        mapper.SetSampleDistance(sample_distance)

    # Clipping planes for volume cropping
    clip_planes = display_props.get("clip_planes")
    if clip_planes:
        planes = vtk.vtkPlaneCollection()
        for cp in clip_planes:
            plane = vtk.vtkPlane()
            if "origin" in cp:
                plane.SetOrigin(*cp["origin"])
            if "normal" in cp:
                plane.SetNormal(*cp["normal"])
            planes.AddItem(plane)
        mapper.SetClippingPlanes(planes)

    # Create volume
    volume = vtk.vtkVolume()
    volume.SetMapper(mapper)
    volume.SetProperty(vol_prop)

    # Scalar bar
    scalar_bar_prop = display_props.get("scalar_bar")
    if scalar_bar_prop and color_by:
        # Build a LUT from the color transfer function for the scalar bar
        from .colormaps import build_lut
        if lut_config is not None:
            lut = build_lut(lut_config, scalar_range=scalar_range)
        else:
            lut = vtk.vtkLookupTable()
            lut.SetNumberOfTableValues(256)
            lut.SetTableRange(*scalar_range)
            lut.Build()

        bar = vtk.vtkScalarBarActor()
        bar.SetLookupTable(lut)
        bar.SetTitle(scalar_bar_prop if isinstance(scalar_bar_prop, str) else color_by)
        bar.SetNumberOfLabels(5)
        bar.SetWidth(0.08)
        bar.SetHeight(0.4)
        bar.SetPosition(0.88, 0.3)
        bar.GetTitleTextProperty().SetFontSize(14)
        bar.GetTitleTextProperty().SetColor(1, 1, 1)
        bar.GetLabelTextProperty().SetColor(1, 1, 1)
        bar.GetLabelTextProperty().SetFontSize(10)
        return volume, bar

    return volume, None


def create_show(vtk_algorithm, **display_props):
    """Create a vtkActor (or vtkVolume) from a filter's output with display properties.

    Returns (renderable, scalar_bar_or_None) where renderable is either a
    vtkActor or a vtkVolume (for representation="Volume").
    """
    representation = display_props.get("representation")

    # Apply field-specific defaults if no lut/scalar_range provided
    color_by_field = display_props.get("color_by")
    if color_by_field and (display_props.get("lut") is None or display_props.get("scalar_range") is None):
        from .colormaps import FIELD_DEFAULTS
        defaults = FIELD_DEFAULTS.get(color_by_field, {})
        if display_props.get("lut") is None and "lut" in defaults:
            display_props = dict(display_props, lut=defaults["lut"])
        if display_props.get("scalar_range") is None and "scalar_range" in defaults:
            display_props = dict(display_props, scalar_range=defaults["scalar_range"])

    # Volume rendering path
    if representation == "Volume":
        return _create_volume(vtk_algorithm, **display_props)

    # Standard actor path
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
        if representation in rep_map:
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
