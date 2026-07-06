"""Frozen value types for the SIVA phase architecture.

These records are the immutable values that flow between phases. Nothing in
this module imports the renderer, so it is safe to construct and pass these
values on any thread (including across the worker -> render-thread boundary in
hot reload).

Migration status (see design-reflections/2026-07-06-spec-as-value-design.md):
this is step 2 ("freeze the graph"). The pipeline graph is now a frozen
``Spec`` value (a tuple of frozen ``Node`` records, plus shows, scene, and
name bindings). ``compute(spec, cache)`` in ``siva/compute.py`` turns a ``Spec``
into a ``ComputeResult``; the mutable ``PipelineBuilder`` in ``dsl.py`` only
exists during ``construct`` and never escapes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, NamedTuple, Optional, Tuple


@dataclass(frozen=True)
class Ref:
    """An edge in the pipeline graph: a reference to another node's output.

    Edges live *inside* a :class:`Node`'s ``params`` as ``Ref`` values — this is
    the single edge channel. "Is this param an edge?" is a type test against
    this frozen value, never an ``isinstance`` check on a live builder handle.
    """

    node_id: int


@dataclass(frozen=True)
class Node:
    """A frozen pipeline node: an op plus its parameters.

    ``op`` is the form kind / VTK class name (e.g. ``"vtkThreshold"`` or a
    pseudo-class like ``"_extract_region"``). ``params`` maps parameter names to
    scalar values or :class:`Ref` edge values. The primary input, when present,
    is the conventionally-named ``"input"`` param (a ``Ref``); it wires to VTK
    port 0 via ``SetInputConnection`` and is the *principal operand* the
    empty-output diagnostics reason about. Secondary ``Ref`` params
    (``SeedSource``, ``GlyphSource``, ``_probe_source``) wire through the bespoke
    handlers in ``filters.py`` keyed by their param name.
    """

    node_id: int
    op: str
    params: Mapping[str, Any]

    @property
    def inputs(self) -> Tuple[int, ...]:
        """Node ids this node depends on — DERIVED from the ``Ref``-valued params.

        This is the single edge rule: there is no stored ``inputs`` field and no
        privileged structural input slot. Order follows ``params`` iteration
        order (insertion order), which is stable for the freeze.
        """
        return tuple(v.node_id for v in self.params.values() if isinstance(v, Ref))


class Show(NamedTuple):
    """A ``show()`` directive: render ``node`` as an actor named ``name``.

    Kept as an ordered list (not keyed by name) because actor add order affects
    transparency compositing and ``name`` may be ``None`` or non-unique.

    ``node`` is a frozen :class:`Ref` value pointing at the node to render.
    """

    node: "Ref"
    name: Optional[str]
    props: dict


@dataclass(frozen=True)
class CameraSpec:
    """Frozen camera settings from a ``camera()`` form.

    Any field left ``None`` is not applied (only the passed parameters change).
    """

    position: Optional[Any] = None
    focal_point: Optional[Any] = None
    up: Optional[Any] = None
    zoom: Optional[float] = None


@dataclass(frozen=True)
class TitleSpec:
    """Frozen scene-title overlay settings from a ``title()`` form."""

    text: str
    position: Any = "top"
    font_size: int = 24
    color: Any = (1, 1, 1)
    show_view_name: bool = True


@dataclass(frozen=True)
class AxesSpec:
    """Frozen cube-axes settings from an ``axes()`` form."""

    color: Any = (1, 1, 1)
    font_size: int = 14
    labels: Any = ("X", "Y", "Z")


@dataclass(frozen=True)
class Annotation:
    """Frozen 3-D billboard text annotation from an ``annotate()`` form."""

    x: float
    y: float
    z: float
    text: str
    color: Any = "white"
    font_size: int = 14


@dataclass(frozen=True)
class SceneSpec:
    """Frozen global scene settings: everything the render phase needs that is
    not a per-node show directive.

    Each singleton slot (``camera``, ``background``, ``title``, ``axes``) is
    ``None`` when its form was never called; ``annotations`` is an empty tuple.
    """

    camera: Optional[CameraSpec] = None
    background: Optional[Any] = None
    title: Optional[TitleSpec] = None
    axes: Optional[AxesSpec] = None
    annotations: tuple = ()


@dataclass(frozen=True)
class Spec:
    """The pipeline program as a frozen value.

    ``nodes`` is in declaration order, which is a valid topological order
    (inputs are declared before the forms that consume them). ``shows`` are the
    per-node display directives, ``scene`` the global scene settings, and
    ``bindings`` maps each Python variable name in the spec to the node id it
    was assigned — the name view of the graph, computed at freeze time from the
    construction namespace (it needs only names and handles, not built outputs).
    """

    nodes: Tuple[Node, ...]
    shows: Tuple[Show, ...]
    scene: SceneSpec
    bindings: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ComputeResult:
    """Result of the compute phase (``compute(spec, cache)``, no renderer touch).

    Holds live VTK data handles (their lifetime is owned by the ``BuildCache``)
    alongside the frozen :class:`Spec` that produced them. This is the value that
    crosses the worker -> render-thread boundary in hot reload: a frozen record
    instead of a live builder.

    ``outputs`` maps ``node_id -> vtk_algorithm``; ``statuses`` maps
    ``node_id -> per-node status dict``. The scene, shows, and name bindings live
    on ``spec``. Backward-compatible accessors (``vtk_objects``,
    ``node_statuses``, ``scene``, ``shows``, ``vtk_objects_by_name``) are exposed
    as properties; the by-name view is a trivial join of ``spec.bindings`` with
    ``outputs``, computed on demand rather than stored twice.
    """

    spec: Spec
    outputs: dict
    statuses: dict

    @property
    def vtk_objects(self) -> dict:
        return self.outputs

    @property
    def node_statuses(self) -> dict:
        return self.statuses

    @property
    def scene(self) -> SceneSpec:
        return self.spec.scene

    @property
    def shows(self) -> tuple:
        return self.spec.shows

    @property
    def vtk_objects_by_name(self) -> dict:
        return {
            name: self.outputs[node_id]
            for name, node_id in self.spec.bindings.items()
            if node_id in self.outputs
        }
