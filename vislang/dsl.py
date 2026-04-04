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
                actor = create_show(vtk_alg, **display_props)
                actor_name = show_name or f"show_{node_ref._node_id}"
                renderer.add_actor(actor_name, actor)
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
