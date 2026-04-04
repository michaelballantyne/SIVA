"""DSL interpreter for declarative VTK pipeline specifications."""

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

    def slice(self, input=None, origin=None, normal=None, **props):
        props["CutFunction"] = dict(type="Plane", Origin=origin, Normal=normal)
        return self.filter("vtkCutter", input=input, **props)

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

    def background(self, r, g, b):
        self._background = (r, g, b)

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
                        import vtk
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
                renderer.add_actor(actor_name, actor)
                if bar_actor:
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
        "slice": builder.slice,
        "seeds_near": builder.seeds_near,
        "show": builder.show,
        "camera": builder.camera,
        "background": builder.background,
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
        "abs": abs,
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
