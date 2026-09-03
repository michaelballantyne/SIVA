"""Compute phase: turn a frozen :class:`~siva.spec.Spec` into a
:class:`~siva.spec.ComputeResult`.

``compute(spec, cache)`` is a free function over immutable values. It does the
expensive work — data I/O and filter execution — and it does NOT touch the
renderer, so it is safe to call from any thread (including the worker thread in
hot reload). The render phase (``siva.scene``) consumes the ``ComputeResult`` on
the renderer-owning thread.

``evaluate(code, cache=None)`` is construct-then-compute in one call
(``compute(construct(code), cache=cache)``) — the named seam between the two
phases where a future static-validation pass would slot in, operating on the
frozen ``Spec`` before ``compute`` touches VTK.

Everything here operates on the single edge rule: a node's dependencies are the
``Ref``-valued entries of its ``params`` (``Node.inputs``). The primary input is
the conventionally-named ``"input"`` param — it wires to VTK port 0 via
``SetInputConnection`` (the one universally-free connection on every
``vtkAlgorithm``) and is the principal operand the empty-output diagnostics
reason about. Secondary ``Ref`` params (``SeedSource``, ``GlyphSource``,
``_probe_source``) are resolved to their VTK objects and passed through as
properties, where the bespoke handlers in ``filters.py`` wire them by name.
"""

from __future__ import annotations

import hashlib
import logging

from . import diagnostics as _diag
from .build_cache import _file_fingerprint, stable_hash
from .dsl import construct
from .filters import (
    _UnknownPropertyError,
    create_vtk_filter,
    extract_component,
    physical_bounds_to_voi,
)
from .spec import ComputeResult, Ref, Spec

# The conventional name of the primary-input param: wired to VTK port 0 and
# treated as the principal operand by the empty-output diagnostics.
INPUT_PARAM = "input"


# ---------------------------------------------------------------------------
# Content hashing for incremental caching
# ---------------------------------------------------------------------------

def _compute_content_hash(node, node_hash_map):
    """Return a stable sha256 hex string for a pipeline node.

    Hashes the node op, its params, and — via the single edge rule — the hashes
    of every ``Ref``-valued param (including the primary ``"input"``). Because
    edges are hashed by their parent's content hash, changing any node changes
    its hash and, transitively, the hashes of everything downstream, so the
    cache rebuilds exactly the affected cone. An unchanged spec produces
    identical hashes and fully cache-hits.
    """
    def _hash_props(params):
        parts = {}
        for k, v in params.items():
            if isinstance(v, Ref):
                parts[k] = node_hash_map.get(v.node_id, "missing")
            elif k == "FileName" and isinstance(v, str):
                parts[k] = _file_fingerprint(v)
            else:
                parts[k] = stable_hash(v)
        return stable_hash(parts)

    kind_hash = stable_hash(node.op)
    props_hash = _hash_props(node.params)
    raw = f"kind:{kind_hash}|props:{props_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Per-node builders: one function per special node type
# ---------------------------------------------------------------------------

def _build_extract_region_node(node, input_alg, outputs, statuses):
    """Handle the _extract_region pseudo-class.

    Picks vtkExtractVOI or vtkExtractGrid based on the input data type,
    converts physical bounds to grid indices automatically. ``node.params``
    holds ``"bounds"`` plus any additional filter properties (e.g.
    ``SampleRate``) directly — no separate ``"extra_props"`` nesting.
    """
    node_id = node.node_id
    bounds = node.params.get("bounds")
    if bounds is None:
        msg = (
            "extract_region: missing required 'bounds' argument; "
            "expected (xmin, xmax, ymin, ymax, zmin, zmax)"
        )
        statuses[node_id] = _diag.error(
            "_extract_region",
            _diag.KIND_MISSING_REQUIRED_ARG,
            msg,
            arg="bounds",
            expected="(xmin, xmax, ymin, ymax, zmin, zmax)",
        )
        return

    if input_alg is None:
        statuses[node_id] = _diag.error(
            "_extract_region",
            _diag.KIND_OTHER,
            "Input node not built",
        )
        return

    try:
        input_alg.Update()
        input_data = input_alg.GetOutput()

        cls_name = input_data.GetClassName()
        if cls_name in ("vtkImageData", "vtkUniformGrid"):
            filter_class = "vtkExtractVOI"
        else:
            filter_class = "vtkExtractGrid"

        extra_props = {
            k: v for k, v in node.params.items() if k not in (INPUT_PARAM, "bounds")
        }
        voi = physical_bounds_to_voi(input_data, bounds)
        extra_props["VOI"] = voi

        vtk_obj, status = create_vtk_filter(filter_class, input_alg, **extra_props)
        status["extract_filter"] = filter_class
        status["physical_bounds"] = bounds
        status["computed_voi"] = voi
        outputs[node_id] = vtk_obj
        statuses[node_id] = status
    except Exception as e:
        statuses[node_id] = _diag.error(
            "_extract_region", _diag.KIND_OTHER, str(e)
        )


def _build_extract_component_node(node, input_alg, outputs, statuses):
    """Handle the _extract_component pseudo-class."""
    node_id = node.node_id
    if input_alg is None:
        statuses[node_id] = _diag.error(
            "_extract_component", _diag.KIND_OTHER, "Input node not built"
        )
        return

    try:
        result_alg, status = extract_component(
            input_alg,
            field=node.params["field"],
            component=node.params["component"],
            result_name=node.params["result_name"],
        )
        outputs[node_id] = result_alg
        statuses[node_id] = status
    except Exception as e:
        statuses[node_id] = _diag.error(
            "_extract_component", _diag.KIND_OTHER, str(e)
        )


def _build_line_probe_node(node, input_alg, outputs, statuses):
    """Handle the _line_probe pseudo-class.

    Validates point1/point2, then creates a vtkLineSource + vtkProbeFilter
    pipeline inline, recording a node error status if endpoints are missing.
    """
    node_id = node.node_id
    point1 = node.params.get("point1")
    point2 = node.params.get("point2")

    if point1 is None or point2 is None:
        missing = []
        if point1 is None:
            missing.append("point1")
        if point2 is None:
            missing.append("point2")
        msg = (
            f"line_probe: missing required argument(s) {missing}; "
            "expected point1=[x, y, z] and point2=[x, y, z]"
        )
        statuses[node_id] = _diag.error(
            "_line_probe",
            _diag.KIND_MISSING_REQUIRED_ARG,
            msg,
            arg=missing[0] if len(missing) == 1 else "point1,point2",
            expected="[x, y, z]",
        )
        return

    if input_alg is None:
        statuses[node_id] = _diag.error(
            "_line_probe", _diag.KIND_OTHER, "Input node not built"
        )
        return

    try:
        resolution = node.params.get("resolution", 100)
        line_alg, _ = create_vtk_filter(
            "vtkLineSource",
            None,
            Point1=list(point1),
            Point2=list(point2),
            Resolution=resolution,
        )
        probe_alg, status = create_vtk_filter(
            "vtkProbeFilter",
            line_alg,
            _probe_source=input_alg,
        )
        status["point1"] = list(point1)
        status["point2"] = list(point2)
        status["resolution"] = resolution
        outputs[node_id] = probe_alg
        statuses[node_id] = status
    except Exception as e:
        statuses[node_id] = _diag.error(
            "_line_probe", _diag.KIND_OTHER, str(e)
        )


def _build_generic_node(node, input_alg, outputs, statuses):
    """Handle all standard VTK filter/source nodes.

    The ``"input"`` param is wired as the port-0 input (``input_alg``); every
    other ``Ref``-valued param is resolved to its built VTK object and passed
    through as a property, where ``filters.py``'s handlers wire it by name.
    """
    node_id = node.node_id
    try:
        props = {}
        for k, v in node.params.items():
            if k == INPUT_PARAM:
                continue  # wired as the port-0 input, not a property
            if isinstance(v, Ref):
                if v.node_id not in outputs:
                    raise ValueError(
                        f"Property '{k}' references node that failed to build"
                    )
                props[k] = outputs[v.node_id]
            else:
                props[k] = v

        vtk_obj, status = create_vtk_filter(node.op, input_alg, **props)
        outputs[node_id] = vtk_obj
        statuses[node_id] = status
    except _UnknownPropertyError as e:
        s = e.structured
        statuses[node_id] = _diag.error(
            node.op,
            _diag.KIND_UNKNOWN_PROPERTY,
            s["message"],
            property=s["property"],
            vtk_class=s["vtk_class"],
            similar=s["similar"],
            valid=s["valid"],
        )
    except Exception as e:
        statuses[node_id] = _diag.error(
            node.op, _diag.KIND_OTHER, str(e)
        )


# ---------------------------------------------------------------------------
# Pseudo-class dispatch registry
# ---------------------------------------------------------------------------

# Maps a pseudo-class node op to its dedicated builder. Every other op falls
# back to _build_generic_node, which wires the conventional "input" param to
# VTK port 0 and passes everything else through as a property. This table is
# also where per-form metadata would live if a form ever needs to diverge
# from that universal INPUT_PARAM convention — e.g. a wiring table or a
# principal-operand override for the empty-output diagnostics — rather than
# threading more special cases through the builders themselves.
_NODE_BUILDERS = {
    "_extract_region": _build_extract_region_node,
    "_extract_component": _build_extract_component_node,
    "_line_probe": _build_line_probe_node,
}


# ---------------------------------------------------------------------------
# Main compute entry point
# ---------------------------------------------------------------------------

def compute(spec: Spec, cache=None) -> ComputeResult:
    """Build the VTK filter graph for *spec* and run ``Update()`` on all nodes.

    This is the expensive compute step (data I/O, filter execution). It does NOT
    touch the renderer and is safe to call from any thread.

    When *cache* is provided (a :class:`~siva.build_cache.BuildCache`), nodes
    whose content hash matches a previous run are reused without rebuilding.

    Returns a :class:`~siva.spec.ComputeResult` carrying the frozen ``spec``, the
    built VTK objects (``outputs``), and per-node ``statuses``.
    """
    outputs = {}          # node_id -> vtk_algorithm
    statuses = {}         # node_id -> status dict
    node_hash_map = {}    # node_id -> content hash (for cache + child hashing)
    failed_nodes = {}     # node_id -> first upstream failure id (or self)

    if cache is not None:
        cache.begin_run()

    # Build nodes in declaration order (inputs always precede dependents).
    for node in spec.nodes:
        node_id = node.node_id

        # Resolve the primary input (port 0), if any.
        input_ref = node.params.get(INPUT_PARAM)
        input_alg = None
        if isinstance(input_ref, Ref):
            input_alg = outputs.get(input_ref.node_id)

        # Compute content hash for this node (used regardless of cache).
        h = _compute_content_hash(node, node_hash_map)
        node_hash_map[node_id] = h

        if cache is not None:
            cached = cache.get(h)
            if cached is not None:
                outputs[node_id] = cached
                cache.touch(h)
                cache.hits += 1
                cached_status = {"cached": True, "class": node.op}
                try:
                    cached_output = cached.GetOutput() if hasattr(cached, "GetOutput") else None
                    if cached_output is not None:
                        cached_status["num_points"] = cached_output.GetNumberOfPoints()
                        cached_status["num_cells"] = cached_output.GetNumberOfCells()
                        if hasattr(cached_output, "GetNumberOfLines"):
                            n_lines = cached_output.GetNumberOfLines()
                            n_polys = cached_output.GetNumberOfPolys()
                            if n_lines:
                                cached_status["num_lines"] = n_lines
                            if n_polys:
                                cached_status["num_polys"] = n_polys
                except Exception:
                    pass
                statuses[node_id] = cached_status
                continue
            cache.misses += 1

        # --- Cascade-skip: if any direct dependency failed, skip this node. ---
        # The single edge rule (Node.inputs) covers the primary input and every
        # secondary Ref param (SeedSource / GlyphSource / probe source) at once.
        failed_upstream_id = None
        for dep_id in node.inputs:
            if dep_id in failed_nodes:
                failed_upstream_id = dep_id
                break
        if failed_upstream_id is not None:
            statuses[node_id] = _diag.skipped(node.op, failed_upstream_id)
            failed_nodes[node_id] = failed_upstream_id
            continue

        builder_fn = _NODE_BUILDERS.get(node.op, _build_generic_node)
        builder_fn(node, input_alg, outputs, statuses)

        # If the node failed (error recorded but no vtk object produced), track it.
        if node_id not in outputs and node_id in statuses:
            if statuses[node_id].get("status") == _diag.STATUS_ERROR:
                failed_nodes[node_id] = node_id

        if cache is not None and node_id in outputs:
            cache.put(h, outputs[node_id])
            cache.touch(h)

    if cache is not None:
        stats = cache.end_run()
        logging.getLogger("siva").info(
            "Cache: %d hits, %d misses, %d evicted, %d kept",
            stats["hits"], stats["misses"], stats["evictions"], stats["kept"],
        )

    # Inject the variable name each node was bound to into its status, so the
    # build reports can label nodes by name. Names come from spec.bindings.
    for name, node_id in spec.bindings.items():
        if node_id in statuses:
            statuses[node_id]["name"] = name

    # For nodes with no explicit binding name (auto-generated "node_N" labels
    # in reports), fall back to the show() name they feed, when exactly one
    # show() directive names that node — so a verbose report can say
    # "node_7 [shown as 'skin']" instead of the opaque "node_7". Nodes bound
    # to a variable name keep that name; this is purely a fallback for the
    # unbound case (e.g. show(threshold(data, ...), name="skin") with no
    # intermediate variable).
    shown_by: dict = {}
    for show in spec.shows:
        if show.name:
            shown_by.setdefault(show.node.node_id, set()).add(show.name)
    for node_id, names in shown_by.items():
        if node_id in statuses and "name" not in statuses[node_id] and len(names) == 1:
            statuses[node_id]["shown_as"] = next(iter(names))

    return ComputeResult(spec=spec, outputs=outputs, statuses=statuses)


def evaluate(code, cache=None) -> ComputeResult:
    """Parse DSL code and run the VTK pipeline: construct, then compute.

    This is construct-then-compute (``compute(construct(code), cache=cache)``),
    named as its own function because the boundary between the two phases is
    the seam where a future static-validation pass would slot in — operating
    on the frozen :class:`~siva.spec.Spec` that ``construct`` produces, before
    ``compute`` ever touches VTK.

    Does NOT touch the renderer, so it is safe to call from any thread; the
    caller applies the result to the renderer on the renderer-owning thread via
    ``siva.scene.render_scene(result.scene, result.shows, result.outputs,
    renderer)``.

    Returns a :class:`~siva.spec.ComputeResult`.
    """
    return compute(construct(code), cache=cache)
