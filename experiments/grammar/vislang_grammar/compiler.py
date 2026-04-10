"""Compiler: translates grammar specs into VTK pipelines.

This is the bridge between the declarative grammar and VTK. It takes the
lazy spec objects (DataRef, Transform, RepSpec, Encoding, ShowResult,
LayerSpec) and builds actual VTK objects: readers, filters, mappers,
actors, and volumes.

The compiler is the ONLY module that imports VTK. Everything else in the
grammar is pure Python data.
"""

import vtk


# ---------------------------------------------------------------------------
# Reader inference
# ---------------------------------------------------------------------------

_READER_MAP = {
    ".vts": "vtkXMLStructuredGridReader",
    ".vti": "vtkXMLImageDataReader",
    ".vtp": "vtkXMLPolyDataReader",
    ".vtu": "vtkXMLUnstructuredGridReader",
    ".vtr": "vtkXMLRectilinearGridReader",
    ".vtk": "vtkGenericDataObjectReader",
    ".nrrd": "vtkNrrdReader",
    ".nhdr": "vtkNrrdReader",
    ".stl": "vtkSTLReader",
    ".obj": "vtkOBJReader",
    ".ply": "vtkPLYReader",
}


def _create_reader(filename):
    """Create a VTK reader for the given file, inferred from extension."""
    import os
    ext = os.path.splitext(filename)[1].lower()
    reader_class_name = _READER_MAP.get(ext)
    if reader_class_name is None:
        raise ValueError(
            f"Cannot infer VTK reader for extension '{ext}'. "
            f"Supported: {', '.join(sorted(_READER_MAP.keys()))}"
        )
    reader_class = getattr(vtk, reader_class_name)
    reader = reader_class()
    reader.SetFileName(filename)
    reader.Update()
    return reader


# ---------------------------------------------------------------------------
# Transform compilation
# ---------------------------------------------------------------------------

def _compile_transform(transform, input_alg):
    """Compile a single Transform into a VTK filter, connected to input_alg.

    Returns the VTK algorithm (already Updated).
    """
    kind = transform.kind
    params = transform.params

    if kind == "where":
        thresh = vtk.vtkThreshold()
        thresh.SetInputConnection(input_alg.GetOutputPort())
        thresh.SetInputArrayToProcess(
            0, 0, 0, vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS,
            params["field"]
        )
        thresh.SetLowerThreshold(params["lo"])
        thresh.SetUpperThreshold(params["hi"])
        thresh.SetThresholdFunction(vtk.vtkThreshold.THRESHOLD_BETWEEN)
        thresh.Update()
        return thresh

    elif kind == "derive":
        calc = vtk.vtkArrayCalculator()
        calc.SetInputConnection(input_alg.GetOutputPort())
        calc.SetAttributeTypeToPointData()
        comps = params["components"]
        for comp_name in comps:
            calc.AddScalarArrayName(comp_name)
        # Build expression: comp0*iHat + comp1*jHat + comp2*kHat
        expr = f"{comps[0]}*iHat + {comps[1]}*jHat + {comps[2]}*kHat"
        calc.SetFunction(expr)
        calc.SetResultArrayName(params["name"])
        calc.Update()
        return calc

    elif kind == "gradient":
        grad = vtk.vtkGradientFilter()
        grad.SetInputConnection(input_alg.GetOutputPort())
        grad.SetInputScalars(
            vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS,
            params["field"]
        )
        grad.SetResultArrayName("Gradient")
        grad.Update()
        return grad

    elif kind == "slice_grid":
        # Get the input's extent
        input_alg.Update()
        extent = list(input_alg.GetOutput().GetExtent())
        if "k" in params:
            extent[4] = extent[5] = params["k"]
        elif "j" in params:
            extent[2] = extent[3] = params["j"]
        elif "i" in params:
            extent[0] = extent[1] = params["i"]
        ext = vtk.vtkExtractGrid()
        ext.SetInputConnection(input_alg.GetOutputPort())
        ext.SetVOI(*extent)
        ext.Update()
        return ext

    elif kind == "clip":
        plane = vtk.vtkPlane()
        plane.SetOrigin(*params["origin"])
        plane.SetNormal(*params["normal"])
        clipper = vtk.vtkClipDataSet()
        clipper.SetInputConnection(input_alg.GetOutputPort())
        clipper.SetClipFunction(plane)
        clipper.Update()
        return clipper

    elif kind == "subsample":
        mask = vtk.vtkMaskPoints()
        mask.SetInputConnection(input_alg.GetOutputPort())
        mask.SetOnRatio(params["every_nth"])
        mask.Update()
        return mask

    else:
        raise ValueError(f"Unknown transform kind: {kind!r}")


# ---------------------------------------------------------------------------
# Representation compilation
# ---------------------------------------------------------------------------

def _compile_rep(rep_spec, input_alg, encoding):
    """Compile a RepSpec into a VTK renderable (actor or volume).

    Returns (renderable, scalar_bar_or_None).
    """
    kind = rep_spec.kind
    params = rep_spec.params

    if kind == "surface":
        return _compile_surface(input_alg, encoding)
    elif kind == "outline":
        return _compile_outline(input_alg, encoding)
    elif kind == "isosurface":
        return _compile_isosurface(input_alg, params, encoding)
    elif kind == "volume":
        return _compile_volume(input_alg, params, encoding)
    elif kind == "streamlines":
        return _compile_streamlines(input_alg, params, encoding)
    elif kind == "glyphs":
        return _compile_glyphs(input_alg, params, encoding)
    else:
        raise ValueError(f"Unknown representation kind: {kind!r}")


def _compile_surface(input_alg, encoding):
    """Surface representation: extract outer surface."""
    surf = vtk.vtkDataSetSurfaceFilter()
    surf.SetInputConnection(input_alg.GetOutputPort())
    surf.Update()

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(surf.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)

    _apply_surface_encoding(mapper, actor, encoding)

    bar = _maybe_scalar_bar(mapper, encoding)
    return actor, bar


def _compile_outline(input_alg, encoding):
    """Outline representation: bounding box wireframe."""
    outline = vtk.vtkOutlineFilter()
    outline.SetInputConnection(input_alg.GetOutputPort())
    outline.Update()

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(outline.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)

    _apply_surface_encoding(mapper, actor, encoding)
    return actor, None


def _compile_isosurface(input_alg, params, encoding):
    """Isosurface representation: contour extraction."""
    contour = vtk.vtkContourFilter()
    contour.SetInputConnection(input_alg.GetOutputPort())
    contour.SetInputArrayToProcess(
        0, 0, 0, vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS,
        params["field"]
    )
    for i, val in enumerate(params["at"]):
        contour.SetValue(i, val)
    contour.Update()

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(contour.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)

    _apply_surface_encoding(mapper, actor, encoding)

    bar = _maybe_scalar_bar(mapper, encoding)
    return actor, bar


def _compile_volume(input_alg, params, encoding):
    """Volume representation: direct volume rendering."""
    from .core import ScaleColor, ScaleOpacity

    # Volume rendering requires ImageData. If input is not ImageData,
    # resample to image.
    input_alg.Update()
    input_data = input_alg.GetOutput()

    if not isinstance(input_data, vtk.vtkImageData):
        resample = vtk.vtkResampleToImage()
        resample.SetInputConnection(input_alg.GetOutputPort())
        resample.SetSamplingDimensions(256, 256, 256)
        resample.Update()
        vol_input = resample
    else:
        vol_input = input_alg

    mapper = vtk.vtkSmartVolumeMapper()
    mapper.SetInputConnection(vol_input.GetOutputPort())

    vol_prop = vtk.vtkVolumeProperty()

    # Color transfer function
    color_tf = vtk.vtkColorTransferFunction()
    if isinstance(encoding.color, ScaleColor):
        sc = encoding.color
        lut = _build_color_transfer_function(sc)
        color_tf = lut
    else:
        # Default gray ramp
        color_tf.AddRGBPoint(0, 0.5, 0.5, 0.5)
        color_tf.AddRGBPoint(255, 1.0, 1.0, 1.0)
    vol_prop.SetColor(color_tf)

    # Opacity transfer function
    opacity_tf = vtk.vtkPiecewiseFunction()
    if isinstance(encoding.opacity, ScaleOpacity):
        so = encoding.opacity
        if so.control_points:
            for val, op in so.control_points:
                opacity_tf.AddPoint(val, op)
        else:
            # Default ramp
            opacity_tf.AddPoint(0, 0.0)
            opacity_tf.AddPoint(255, 1.0)
        if so.gradient_modulation:
            grad_tf = vtk.vtkPiecewiseFunction()
            grad_tf.AddPoint(0, 0.0)
            grad_tf.AddPoint(50, 0.5)
            grad_tf.AddPoint(100, 1.0)
            vol_prop.SetGradientOpacity(grad_tf)
    elif isinstance(encoding.opacity, (int, float)):
        opacity_tf.AddPoint(0, 0.0)
        opacity_tf.AddPoint(255, encoding.opacity)
    else:
        opacity_tf.AddPoint(0, 0.0)
        opacity_tf.AddPoint(255, 0.5)
    vol_prop.SetScalarOpacity(opacity_tf)

    if encoding.shade:
        vol_prop.ShadeOn()
    vol_prop.SetInterpolationTypeToLinear()

    volume = vtk.vtkVolume()
    volume.SetMapper(mapper)
    volume.SetProperty(vol_prop)

    # No scalar bar for volumes (could add later)
    return volume, None


def _compile_streamlines(input_alg, params, encoding):
    """Streamline representation: vector field integration."""
    input_alg.Update()

    # Create seed source
    seeds = params.get("seeds")
    if seeds is None:
        # Default: point source in the center of the dataset
        bounds = input_alg.GetOutput().GetBounds()
        center = [(bounds[i*2] + bounds[i*2+1]) / 2 for i in range(3)]
        seed_source = vtk.vtkPointSource()
        seed_source.SetCenter(*center)
        seed_source.SetRadius(min(bounds[i*2+1] - bounds[i*2] for i in range(3)) * 0.3)
        seed_source.SetNumberOfPoints(20)
        seed_source.Update()
    elif isinstance(seeds, dict) and seeds.get("kind") == "near":
        # Seeds near field values — use threshold + mask to pick points
        thresh = vtk.vtkThreshold()
        thresh.SetInputConnection(input_alg.GetOutputPort())
        thresh.SetInputArrayToProcess(
            0, 0, 0, vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS, seeds["field"]
        )
        thresh.SetLowerThreshold(seeds["lo"])
        thresh.SetUpperThreshold(seeds["hi"])
        thresh.SetThresholdFunction(vtk.vtkThreshold.THRESHOLD_BETWEEN)
        thresh.Update()
        mask = vtk.vtkMaskPoints()
        mask.SetInputConnection(thresh.GetOutputPort())
        n_points = thresh.GetOutput().GetNumberOfPoints()
        ratio = max(1, n_points // seeds["n"]) if n_points > 0 else 1
        mask.SetOnRatio(ratio)
        mask.SetRandomMode(True)
        mask.Update()
        seed_source = mask
    else:
        raise ValueError(f"Unsupported seed specification: {seeds}")

    # Stream tracer
    tracer = vtk.vtkStreamTracer()
    tracer.SetInputConnection(input_alg.GetOutputPort())
    tracer.SetSourceConnection(seed_source.GetOutputPort())
    direction_map = {
        "forward": vtk.vtkStreamTracer.FORWARD,
        "backward": vtk.vtkStreamTracer.BACKWARD,
        "both": vtk.vtkStreamTracer.BOTH,
    }
    tracer.SetIntegrationDirection(direction_map.get(params["direction"], vtk.vtkStreamTracer.BOTH))
    tracer.SetMaximumNumberOfSteps(params["max_steps"])
    tracer.Update()

    # Optionally wrap in tubes
    if params.get("tube_radius", 0) > 0:
        tube = vtk.vtkTubeFilter()
        tube.SetInputConnection(tracer.GetOutputPort())
        tube.SetRadius(params["tube_radius"])
        tube.SetNumberOfSides(8)
        tube.Update()
        final_alg = tube
    else:
        final_alg = tracer

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(final_alg.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)

    _apply_surface_encoding(mapper, actor, encoding)

    bar = _maybe_scalar_bar(mapper, encoding)
    return actor, bar


def _compile_glyphs(input_alg, params, encoding):
    """Glyph representation: one shape per sample point."""
    # Subsample if needed
    if params.get("every_nth", 1) > 1:
        mask = vtk.vtkMaskPoints()
        mask.SetInputConnection(input_alg.GetOutputPort())
        mask.SetOnRatio(params["every_nth"])
        mask.SetRandomMode(True)
        mask.Update()
        glyph_input = mask
    else:
        glyph_input = input_alg

    # Create glyph source
    shape = params.get("shape", "arrow")
    shape_sources = {
        "arrow": vtk.vtkArrowSource,
        "sphere": vtk.vtkSphereSource,
        "cone": vtk.vtkConeSource,
        "cube": vtk.vtkCubeSource,
    }
    source_class = shape_sources.get(shape, vtk.vtkArrowSource)
    source = source_class()
    source.Update()

    glyph = vtk.vtkGlyph3D()
    glyph.SetInputConnection(glyph_input.GetOutputPort())
    glyph.SetSourceConnection(source.GetOutputPort())
    glyph.SetScaleFactor(params.get("scale_factor", 1.0))
    glyph.Update()

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(glyph.GetOutputPort())
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)

    _apply_surface_encoding(mapper, actor, encoding)

    bar = _maybe_scalar_bar(mapper, encoding)
    return actor, bar


# ---------------------------------------------------------------------------
# Encoding application helpers
# ---------------------------------------------------------------------------

def _apply_surface_encoding(mapper, actor, encoding):
    """Apply encoding to a surface actor's mapper and property."""
    from .core import ScaleColor

    prop = actor.GetProperty()

    # Color
    if isinstance(encoding.color, ScaleColor):
        sc = encoding.color
        mapper.SetScalarModeToUsePointFieldData()
        mapper.SelectColorArray(sc.field)
        mapper.SetColorModeToMapScalars()
        mapper.ScalarVisibilityOn()
        if sc.range:
            mapper.SetScalarRange(*sc.range)
        lut = _build_lookup_table(sc)
        mapper.SetLookupTable(lut)
    elif isinstance(encoding.color, tuple) and len(encoding.color) == 3:
        mapper.ScalarVisibilityOff()
        prop.SetColor(*encoding.color)
    else:
        mapper.ScalarVisibilityOn()

    # Opacity
    if isinstance(encoding.opacity, (int, float)):
        prop.SetOpacity(encoding.opacity)

    # Material
    if encoding.specular > 0:
        prop.SetSpecular(encoding.specular)
        prop.SetSpecularPower(encoding.specular_power)

    # Line width
    if encoding.line_width != 1.0:
        prop.SetLineWidth(encoding.line_width)


def _build_lookup_table(scale_color):
    """Build a vtkLookupTable from a ScaleColor spec."""
    try:
        from vislang.colormaps import build_lut
        lut = build_lut(scale_color.colormap, scalar_range=scale_color.range)
        return lut
    except ImportError:
        # Fallback: simple rainbow
        lut = vtk.vtkLookupTable()
        lut.SetHueRange(0.667, 0.0)
        if scale_color.range:
            lut.SetTableRange(*scale_color.range)
        lut.Build()
        return lut


def _build_color_transfer_function(scale_color):
    """Build a vtkColorTransferFunction from a ScaleColor spec (for volumes)."""
    try:
        from vislang.colormaps import build_lut
        lut = build_lut(scale_color.colormap, scalar_range=scale_color.range)
        # Convert LUT to color transfer function
        ctf = vtk.vtkColorTransferFunction()
        n = lut.GetNumberOfTableValues()
        lo = scale_color.range[0] if scale_color.range else 0
        hi = scale_color.range[1] if scale_color.range else 255
        for i in range(n):
            t = lo + (hi - lo) * i / max(n - 1, 1)
            rgba = lut.GetTableValue(i)
            ctf.AddRGBPoint(t, rgba[0], rgba[1], rgba[2])
        return ctf
    except ImportError:
        # Fallback
        ctf = vtk.vtkColorTransferFunction()
        lo = scale_color.range[0] if scale_color.range else 0
        hi = scale_color.range[1] if scale_color.range else 255
        ctf.AddRGBPoint(lo, 0.0, 0.0, 1.0)
        ctf.AddRGBPoint((lo + hi) / 2, 0.0, 1.0, 0.0)
        ctf.AddRGBPoint(hi, 1.0, 0.0, 0.0)
        return ctf


def _maybe_scalar_bar(mapper, encoding):
    """Create a scalar bar actor if the encoding has a legend."""
    if encoding.legend is None:
        return None

    bar = vtk.vtkScalarBarActor()
    bar.SetLookupTable(mapper.GetLookupTable())
    bar.SetTitle(encoding.legend)
    bar.SetNumberOfLabels(5)
    bar.SetWidth(0.08)
    bar.SetHeight(0.4)
    bar.SetPosition(0.88, 0.3)
    bar.GetTitleTextProperty().SetFontSize(14)
    bar.GetTitleTextProperty().SetColor(1, 1, 1)
    bar.GetLabelTextProperty().SetColor(1, 1, 1)
    bar.GetLabelTextProperty().SetFontSize(10)
    return bar


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compile_scene(spec, renderer=None):
    """Compile a grammar spec (ShowResult or LayerSpec) into VTK objects.

    If renderer is provided, actors are added to it. Otherwise, a dict of
    compiled objects is returned for manual scene management.

    Args:
        spec: A ShowResult or LayerSpec from the grammar.
        renderer: Optional vtkRenderer to add actors to.

    Returns:
        List of (renderable, scalar_bar_or_None) tuples.
    """
    from .core import ShowResult, LayerSpec

    if isinstance(spec, ShowResult):
        shows = [spec]
    elif isinstance(spec, LayerSpec):
        shows = spec.shows
    else:
        raise TypeError(
            f"compile_scene expects ShowResult or LayerSpec, got {type(spec).__name__}"
        )

    results = []
    for show_result in shows:
        rep = show_result.rep
        enc = show_result.encoding

        if rep.source is None:
            raise ValueError(
                "RepSpec has no data source. Did you forget to pipe data into it? "
                "Example: data('file.vts') | rep_surface()"
            )

        # 1. Create reader
        reader = _create_reader(rep.source.filename)

        # 2. Apply transforms
        current = reader
        for transform in rep.transforms:
            current = _compile_transform(transform, current)

        # 3. Build representation
        renderable, bar = _compile_rep(rep, current, enc)
        results.append((renderable, bar))

        # 4. Add to renderer if provided
        if renderer is not None:
            if isinstance(renderable, vtk.vtkVolume):
                renderer.AddVolume(renderable)
            else:
                renderer.AddActor(renderable)
            if bar is not None:
                renderer.AddActor2D(bar)

    return results
