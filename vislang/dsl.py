"""DSL interpreter for declarative VTK pipeline specifications."""

import vtk

from .filters import create_vtk_filter, create_show


class NodeRef:
    """Reference to a pipeline node."""

    def __init__(self, builder, node_id, vtk_class, properties, input_ref=None):
        self._builder = builder
        self._node_id = node_id
        self.vtk_class = vtk_class
        self.properties = properties
        self.input_ref = input_ref


class PipelineBuilder:
    """Builds VTK pipelines from DSL declarations."""

    def __init__(self):
        self._nodes = []  # (node_id, NodeRef)
        self._shows = []  # (node_ref, show_name, display_props)
        self._camera = None
        self._background = None
        self._title = None
        self._node_counter = 0

    def source(self, vtk_class, **props):
        self._node_counter += 1
        node_id = self._node_counter
        ref = NodeRef(self, node_id, vtk_class, props)
        self._nodes.append((node_id, ref))
        return ref

    def filter(self, vtk_class, input=None, **props):
        self._node_counter += 1
        node_id = self._node_counter
        ref = NodeRef(self, node_id, vtk_class, props, input_ref=input)
        self._nodes.append((node_id, ref))
        return ref

    def contour(self, input=None, **props):
        return self.filter("vtkContourFilter", input=input, **props)

    def calculator(self, input=None, **props):
        return self.filter("vtkArrayCalculator", input=input, **props)

    def threshold(self, input=None, **props):
        return self.filter("vtkThreshold", input=input, **props)

    def extract_grid(self, input=None, **props):
        return self.filter("vtkExtractGrid", input=input, **props)

    def stream_tracer(self, input=None, **props):
        return self.filter("vtkStreamTracer", input=input, **props)

    def tube(self, input=None, **props):
        return self.filter("vtkTubeFilter", input=input, **props)

    def glyph(self, input=None, **props):
        return self.filter("vtkGlyph3D", input=input, **props)

    def warp_vector(self, input=None, **props):
        return self.filter("vtkWarpVector", input=input, **props)

    def warp_scalar(self, input=None, **props):
        return self.filter("vtkWarpScalar", input=input, **props)

    def cell_to_point(self, input=None, **props):
        """Convert cell data to point data."""
        return self.filter("vtkCellDataToPointData", input=input, **props)

    def surface(self, input=None, **props):
        """Extract the outer surface of a dataset."""
        return self.filter("vtkDataSetSurfaceFilter", input=input, **props)

    def smooth(self, input=None, iterations=20, **props):
        """Smooth a polydata surface."""
        props["NumberOfIterations"] = iterations
        return self.filter("vtkWindowedSincPolyDataFilter", input=input, **props)

    def mask_points(self, input=None, **props):
        return self.filter("vtkMaskPoints", input=input, **props)

    def gradient(self, input=None, **props):
        return self.filter("vtkGradientFilter", input=input, **props)

    def clip(self, input=None, origin=None, normal=None, inside_out=False, **props):
        """Clip data by a plane. Keeps the half on the normal side."""
        plane_dict = dict(type="Plane", Origin=origin, Normal=normal)
        props["CutFunction"] = plane_dict
        if inside_out:
            props["InsideOut"] = 1
        return self.filter("vtkClipDataSet", input=input, **props)

    def clip_sphere(self, input=None, center=None, radius=100, inside_out=True, **props):
        """Clip data by a sphere. By default keeps inside."""
        props["CutFunction"] = dict(type="Sphere", Center=center, Radius=radius)
        if inside_out:
            props["InsideOut"] = 1
        return self.filter("vtkClipDataSet", input=input, **props)

    def clip_box(self, input=None, bounds=None, inside_out=True, **props):
        """Clip data by an axis-aligned box. By default keeps inside."""
        props["CutFunction"] = dict(type="Box", Bounds=bounds)
        if inside_out:
            props["InsideOut"] = 1
        return self.filter("vtkClipDataSet", input=input, **props)

    def probe(self, input=None, source=None, **props):
        """Sample source data at input geometry points."""
        if source is not None:
            props["_probe_source"] = source
        return self.filter("vtkProbeFilter", input=input, **props)

    def resample_to_image(self, input=None, dimensions=None, **props):
        """Resample any dataset to a regular image grid."""
        if dimensions is not None:
            props["SamplingDimensions"] = dimensions
        return self.filter("vtkResampleToImage", input=input, **props)

    def fire_region(self, input=None, min_theta=340, max_theta=1200):
        """Extract the fire region by thresholding on potential temperature."""
        return self.threshold(input=input, ThresholdBy="theta",
                             ThresholdRange=[min_theta, max_theta])

    def compute_velocity(self, input=None, components=("u", "v", "w"), result="velocity"):
        """Compute a vector field from scalar components."""
        return self.filter("vtkArrayCalculator", input=input,
            AddScalarArrayName=list(components),
            Function=f"{components[0]}*iHat + {components[1]}*jHat + {components[2]}*kHat",
            ResultArrayName=result)

    def compute_vorticity(self, input=None, velocity_input=None,
                          components=("u", "v", "w"), result="vorticity_magnitude"):
        """Compute vorticity magnitude from velocity components.

        If velocity_input is provided, uses it directly. Otherwise computes
        velocity from the scalar components first.
        """
        if velocity_input is None:
            velocity_input = self.compute_velocity(input=input, components=components)
        vort = self.filter("vtkCellDerivatives", input=velocity_input,
            VectorMode="ComputeVorticity", TensorMode="PassTensors")
        vort_pts = self.filter("vtkCellDataToPointData", input=vort)
        return self.filter("vtkArrayCalculator", input=vort_pts,
            AddVectorArrayName=["Vorticity"],
            Function="mag(Vorticity)",
            ResultArrayName=result)

    def compute_gradient_magnitude(self, input=None, field="theta", result=None):
        """Compute the gradient magnitude of a scalar field.

        Useful for finding edges and boundaries in the data.
        """
        if result is None:
            result = f"{field}_gradient_mag"
        grad = self.filter("vtkGradientFilter", input=input,
            GradientField=field, ResultArrayName=f"{field}_gradient")
        return self.filter("vtkArrayCalculator", input=grad,
            AddVectorArrayName=[f"{field}_gradient"],
            Function=f"mag({field}_gradient)",
            ResultArrayName=result)

    def compute_magnitude(self, input=None, components=("u", "v", "w"), result="speed"):
        """Compute the magnitude of scalar components."""
        expr = "+".join(f"{c}*{c}" for c in components)
        return self.filter("vtkArrayCalculator", input=input,
            AddScalarArrayName=list(components),
            Function=f"sqrt({expr})",
            ResultArrayName=result)

    def slice(self, input=None, origin=None, normal=None, **props):
        props["CutFunction"] = dict(type="Plane", Origin=origin, Normal=normal)
        return self.filter("vtkCutter", input=input, **props)

    def raw_source(self, filename, dimensions=(1, 1, 1), scalar_type="unsigned_char",
                   header_size=0, num_components=1):
        """Load a raw binary volume file via vtkImageReader2.

        Args:
            filename: Path to the .raw file.
            dimensions: (nx, ny, nz) tuple of grid dimensions.
            scalar_type: Data type string ("unsigned_char", "unsigned_short",
                         "float", "double", etc.) or a VTK type constant.
            header_size: Number of bytes to skip at the start of the file.
            num_components: Number of scalar components per voxel.
        """
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
        if isinstance(scalar_type, str):
            vtk_type = _scalar_type_map.get(scalar_type)
            if vtk_type is None:
                raise ValueError(
                    f"Unknown scalar type '{scalar_type}'. "
                    f"Available: {sorted(_scalar_type_map.keys())}"
                )
        else:
            vtk_type = scalar_type

        nx, ny, nz = dimensions
        return self.source("vtkImageReader2",
                           FileName=filename,
                           DataExtent=[0, nx - 1, 0, ny - 1, 0, nz - 1],
                           DataScalarType=vtk_type,
                           FileDimensionality=3,
                           HeaderSize=header_size,
                           NumberOfScalarComponents=num_components)

    def seeds_near(self, input=None, field="theta", min_val=400, max_val=1200,
                   num_seeds=30, offset_z=10):
        """Create seed points near where a field is in a given range.

        Finds the spatial extent of the field range, then creates a line
        source through that region.
        """
        self._node_counter += 1
        node_id = self._node_counter
        ref = NodeRef(self, node_id, "_seeds_near", {
            "input_ref": input,
            "field": field, "min_val": min_val, "max_val": max_val,
            "num_seeds": num_seeds, "offset_z": offset_z
        }, input_ref=input)
        self._nodes.append((node_id, ref))
        return ref

    def show(self, node, name=None, **display_props):
        self._shows.append((node, name, display_props))

    def camera(self, position=None, focal_point=None, up=None, zoom=None):
        self._camera = {
            "position": position,
            "focal_point": focal_point,
            "up": up,
            "zoom": zoom,
        }

    def title(self, text, position="top", font_size=24, color=(1, 1, 1)):
        """Add a text annotation to the scene."""
        self._title = {"text": text, "position": position, "font_size": font_size, "color": color}

    def background(self, r, g, b):
        self._background = (r, g, b)

    def scene_preset(self, name="dark"):
        """Apply a named scene preset for background and styling.

        Presets:
          dark - Dark blue/black background (default, good for fire/glow)
          light - Light gray background (good for solid objects)
          black - Pure black background
          white - Pure white background (publication-ready)
        """
        presets = {
            "dark": (0.02, 0.02, 0.06),
            "light": (0.85, 0.85, 0.9),
            "black": (0.0, 0.0, 0.0),
            "white": (1.0, 1.0, 1.0),
        }
        if name not in presets:
            raise ValueError(f"Unknown scene preset '{name}'. Available: {sorted(presets.keys())}")
        self._background = presets[name]

    def build(self, renderer):
        """Build the VTK pipeline and add actors to the renderer."""
        renderer.clear()

        # Map node_id -> vtk_algorithm
        vtk_objects = {}
        node_names = {}  # node_id -> variable name
        node_statuses = {}

        # Build nodes in order (dependency order is insertion order since
        # inputs are always declared before dependents)
        for node_id, ref in self._nodes:
            input_alg = None
            if ref.input_ref is not None:
                input_alg = vtk_objects.get(ref.input_ref._node_id)

            # Handle _seeds_near special case
            if ref.vtk_class == "_seeds_near":
                input_alg_sn = vtk_objects.get(ref.input_ref._node_id)
                if input_alg_sn:
                    input_alg_sn.Update()
                    data = input_alg_sn.GetOutput()
                    field = ref.properties["field"]
                    min_val = ref.properties["min_val"]
                    max_val = ref.properties["max_val"]
                    num_seeds = ref.properties["num_seeds"]
                    offset_z = ref.properties["offset_z"]

                    from . import queries
                    extent_str = queries.get_spatial_extent(data, field, min_val, max_val)

                    import re
                    x_match = re.search(r'X: \[([-.0-9]+), ([-.0-9]+)\]', extent_str)
                    y_match = re.search(r'Y: \[([-.0-9]+), ([-.0-9]+)\]', extent_str)
                    z_match = re.search(r'Z: \[([-.0-9]+), ([-.0-9]+)\]', extent_str)

                    if x_match and y_match and z_match:
                        xmin, xmax = float(x_match.group(1)), float(x_match.group(2))
                        ymin, ymax = float(y_match.group(1)), float(y_match.group(2))
                        zmin, zmax = float(z_match.group(1)), float(z_match.group(2))

                        cx = (xmin + xmax) / 2
                        cy = (ymin + ymax) / 2
                        z = (zmin + zmax) / 2 + offset_z
                        dx = xmax - xmin

                        line = vtk.vtkLineSource()
                        line.SetPoint1(cx - dx, cy, z)
                        line.SetPoint2(cx + dx, cy, z)
                        line.SetResolution(num_seeds)
                        line.Update()

                        vtk_objects[node_id] = line
                        node_statuses[node_id] = {
                            "class": "vtkLineSource (auto-seed)",
                            "num_points": line.GetOutput().GetNumberOfPoints(),
                            "num_cells": line.GetOutput().GetNumberOfCells(),
                            "info": f"Seeds near {field} in [{min_val}, {max_val}], z={z:.1f}"
                        }
                    else:
                        node_statuses[node_id] = {"error": f"Could not find extent for {field} in [{min_val}, {max_val}]"}
                continue  # Skip the normal filter creation

            # Handle GlyphSource special case: if it's a NodeRef, resolve it
            props = dict(ref.properties)
            for k, v in props.items():
                if isinstance(v, NodeRef):
                    props[k] = vtk_objects[v._node_id]

            try:
                vtk_obj, status = create_vtk_filter(ref.vtk_class, input_alg, **props)
                vtk_objects[node_id] = vtk_obj
                node_statuses[node_id] = status
            except Exception as e:
                node_statuses[node_id] = {"error": str(e)}

        # Build show directives
        show_statuses = {}
        bar_count = 0  # Track scalar bars for positioning
        for node_ref, show_name, display_props in self._shows:
            vtk_alg = vtk_objects.get(node_ref._node_id)
            if vtk_alg is None:
                show_statuses[show_name or "?"] = {
                    "error": "Node not built (dependency error)"
                }
                continue
            try:
                result = create_show(vtk_alg, **display_props)
                if isinstance(result, tuple):
                    actor, bar_actor = result
                else:
                    actor, bar_actor = result, None

                actor_name = show_name or f"show_{node_ref._node_id}"
                if isinstance(actor, vtk.vtkVolume):
                    renderer.add_volume(actor_name, actor)
                else:
                    renderer.add_actor(actor_name, actor)
                if bar_actor:
                    # Position multiple scalar bars side by side
                    x_pos = 0.88 - bar_count * 0.10
                    bar_actor.SetPosition(x_pos, 0.3)
                    bar_count += 1
                    renderer.add_actor(f"{actor_name}_bar", bar_actor)
                show_statuses[actor_name] = {"status": "ok"}
            except Exception as e:
                show_statuses[show_name or "?"] = {"error": str(e)}

        # Camera and background
        if self._background:
            renderer.set_background(*self._background)

        if self._camera:
            renderer.set_camera(**{k: v for k, v in self._camera.items() if v is not None})
        else:
            renderer.reset_camera()

        if self._title:
            text_actor = vtk.vtkTextActor()
            text_actor.SetInput(self._title["text"])
            tp = text_actor.GetTextProperty()
            tp.SetFontSize(self._title["font_size"])
            tp.SetColor(*self._title["color"])
            tp.SetFontFamilyToArial()
            tp.SetBold(True)
            tp.SetShadow(True)

            pos = self._title.get("position", "top")
            if pos == "top":
                text_actor.SetPosition(20, renderer._render_window.GetSize()[1] - 50)
            elif pos == "bottom":
                text_actor.SetPosition(20, 20)
            elif isinstance(pos, tuple):
                text_actor.SetPosition(*pos)

            renderer._renderer.AddActor2D(text_actor)

        renderer.render()

        return vtk_objects, node_names, node_statuses, show_statuses


def interpret(code, renderer):
    """Interpret a DSL code string and build the pipeline.

    Returns (vtk_objects_by_name, node_statuses, show_statuses, builder).
    """
    builder = PipelineBuilder()

    # Create the restricted namespace
    namespace = {
        "source": builder.source,
        "filter": builder.filter,
        "contour": builder.contour,
        "calculator": builder.calculator,
        "threshold": builder.threshold,
        "extract_grid": builder.extract_grid,
        "stream_tracer": builder.stream_tracer,
        "tube": builder.tube,
        "glyph": builder.glyph,
        "warp_vector": builder.warp_vector,
        "warp_scalar": builder.warp_scalar,
        "cell_to_point": builder.cell_to_point,
        "surface": builder.surface,
        "smooth": builder.smooth,
        "mask_points": builder.mask_points,
        "gradient": builder.gradient,
        "fire_region": builder.fire_region,
        "compute_velocity": builder.compute_velocity,
        "compute_vorticity": builder.compute_vorticity,
        "compute_gradient_magnitude": builder.compute_gradient_magnitude,
        "compute_magnitude": builder.compute_magnitude,
        "clip": builder.clip,
        "clip_sphere": builder.clip_sphere,
        "clip_box": builder.clip_box,
        "probe": builder.probe,
        "resample_to_image": builder.resample_to_image,
        "slice": builder.slice,
        "seeds_near": builder.seeds_near,
        "raw_source": builder.raw_source,
        "show": builder.show,
        "camera": builder.camera,
        "background": builder.background,
        "scene_preset": builder.scene_preset,
        "title": builder.title,
        # Safe builtins
        "range": range,
        "zip": zip,
        "enumerate": enumerate,
        "len": len,
        "min": min,
        "max": max,
        "True": True,
        "False": False,
        "None": None,
        "dict": dict,
        "list": list,
        "tuple": tuple,
        "float": float,
        "int": int,
        "abs": abs,
        "round": round,
        "sorted": sorted,
        "sum": sum,
        "math": __import__("math"),
        "print": print,  # Allow debug output
        "__builtins__": {},
    }

    exec(code, namespace)

    # Build the pipeline
    vtk_objects, _, node_statuses, show_statuses = builder.build(renderer)

    # Extract variable names from namespace
    vtk_objects_by_name = {}
    for var_name, var_value in namespace.items():
        if isinstance(var_value, NodeRef) and var_value._node_id in vtk_objects:
            vtk_objects_by_name[var_name] = vtk_objects[var_value._node_id]
            # Update node status with the variable name
            if var_value._node_id in node_statuses:
                node_statuses[var_value._node_id]["name"] = var_name

    return vtk_objects_by_name, node_statuses, show_statuses, builder
