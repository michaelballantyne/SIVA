"""Frozen value types for the SIVA phase architecture.

These records are the immutable values that flow between phases. Nothing in
this module imports the renderer, so it is safe to construct and pass these
values on any thread (including across the worker -> render-thread boundary in
hot reload).

Migration status (see design-reflections/2026-07-06-spec-as-value-design.md):
this is step 1 ("freeze the scene"). Only the scene settings and show
directives are frozen here; the pipeline graph is still owned by the mutable
``PipelineBuilder`` in ``dsl.py`` and will become a frozen ``Spec`` value in
step 2. ``ComputeResult`` therefore still holds the built VTK objects directly
rather than a ``Spec`` + outputs map.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple, Optional

if TYPE_CHECKING:  # avoid an import cycle — NodeRef lives in dsl.py
    from .dsl import NodeRef


class Show(NamedTuple):
    """A ``show()`` directive: render ``node`` as an actor named ``name``.

    Kept as an ordered list (not keyed by name) because actor add order affects
    transparency compositing and ``name`` may be ``None`` or non-unique.

    ``node`` is a construction-time ``NodeRef`` handle in step 1; step 2 will
    demote it to a frozen ``Ref`` value.
    """

    node: "NodeRef"
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
class ComputeResult:
    """Result of the compute phase (construct + build, no renderer touch).

    Holds live VTK data handles (their lifetime is owned by the ``BuildCache``)
    alongside the frozen scene/show values the render phase consumes. This is
    the value that crosses the worker -> render-thread boundary in hot reload:
    a frozen record instead of a live builder.

    ``vtk_objects`` maps ``node_id -> vtk_algorithm``; ``vtk_objects_by_name``
    maps the Python variable name a node was assigned to -> its vtk_algorithm.
    ``show_statuses`` is produced by the *render* phase, not here, so it is not
    part of this record.
    """

    vtk_objects: dict
    vtk_objects_by_name: dict
    node_statuses: dict
    scene: SceneSpec
    shows: tuple
