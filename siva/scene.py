"""Render phase: turn frozen scene values into renderer state.

These are the functions whose arguments include a ``Renderer``. They receive
frozen values (a :class:`~siva.spec.SceneSpec`, a tuple of
:class:`~siva.spec.Show` directives, and the built VTK objects) plus the
renderer handle — never the mutable ``PipelineBuilder``. They touch the
renderer only through its public interface (see
``tests/test_renderer_interface.py``), and must run on the renderer-owning
thread.

This is the split that lets an alternate backend (e.g. Trame) drop in: the
render phase is a pure function of values + the renderer seam.
"""

from __future__ import annotations

import vtk

from . import diagnostics as _diag
from .filters import create_show
from .spec import TitleSpec


def build_show_actors(shows, vtk_objects, renderer):
    """Create actors/volumes from ``show()`` directives and add them to the renderer.

    ``shows`` is a tuple of :class:`~siva.spec.Show`; ``vtk_objects`` maps
    ``node_id -> vtk_algorithm``. Returns a ``show_statuses`` dict keyed by
    actor name.
    """
    show_statuses = {}
    for directive in shows:
        vtk_alg = vtk_objects.get(directive.node.node_id)
        if vtk_alg is None:
            key = directive.name or "?"
            show_statuses[key] = _diag.error(
                key, _diag.KIND_OTHER, "Node not built (dependency error)"
            )
            continue
        try:
            result = create_show(vtk_alg, **directive.props)
            if isinstance(result, tuple):
                actor, bar_actor = result
            else:
                actor, bar_actor = result, None

            actor_name = directive.name or f"show_{directive.node.node_id}"
            if isinstance(actor, vtk.vtkVolume):
                renderer.add_volume(actor_name, actor)
            else:
                renderer.add_actor(actor_name, actor)
            if bar_actor:
                # Prefer the title already set on the bar actor (may be
                # humanized by _infer_display_defaults inside create_show);
                # fall back to the raw scalar_bar prop or the field name.
                scalar_bar_prop = directive.props.get("scalar_bar")
                if isinstance(scalar_bar_prop, str):
                    fallback_title = scalar_bar_prop
                else:
                    fallback_title = directive.props.get("color_by", "")
                title_text = bar_actor.GetTitle() or fallback_title
                title_actor = vtk.vtkTextActor()
                title_actor.SetInput(title_text)
                tp = title_actor.GetTextProperty()
                tp.SetFontSize(15)
                tp.SetColor(1, 1, 1)
                tp.SetJustificationToRight()
                tp.SetVerticalJustificationToCentered()
                tp.BoldOn()
                tp.ShadowOff()
                renderer.add_scalar_bar(actor_name, bar_actor, title_actor)
            show_statuses[actor_name] = {"status": "ok"}
        except Exception as e:
            key = directive.name or "?"
            show_statuses[key] = _diag.error(key, _diag.KIND_OTHER, str(e))
    return show_statuses


def apply_scene_settings(scene, renderer):
    """Apply background, camera, title, annotations, and axes to the renderer.

    ``scene`` is a frozen :class:`~siva.spec.SceneSpec`.
    """
    if scene.background:
        renderer.set_background(*scene.background)

    if scene.camera is not None:
        cam = scene.camera
        kwargs = {
            k: v
            for k, v in (
                ("position", cam.position),
                ("focal_point", cam.focal_point),
                ("up", cam.up),
                ("zoom", cam.zoom),
            )
            if v is not None
        }
        renderer.set_camera(**kwargs)
    elif not renderer.camera_positioned:
        result = renderer.suggest_camera("overview")
        if result:
            renderer.set_camera(**result)
            renderer.camera_positioned = True
        else:
            renderer.reset_camera()

    view_name = getattr(renderer, "view_name", None)
    title_spec = scene.title
    if title_spec is None and view_name:
        title_spec = TitleSpec(text="", position="top", font_size=18,
                               color=(1, 1, 1), show_view_name=True)
    if title_spec:
        user_text = title_spec.text
        if view_name and title_spec.show_view_name:
            rendered_text = f"{view_name}: {user_text}" if user_text else view_name
        else:
            rendered_text = user_text
        text_actor = vtk.vtkTextActor()
        text_actor.SetInput(rendered_text)
        tp = text_actor.GetTextProperty()
        tp.SetFontSize(title_spec.font_size)
        tp.SetColor(*title_spec.color)
        tp.SetFontFamilyToArial()
        tp.SetBold(True)
        tp.SetShadow(True)

        pos = title_spec.position
        if pos == "top":
            text_actor.SetPosition(20, renderer.get_size()[1] - 50)
        elif pos == "bottom":
            text_actor.SetPosition(20, 20)
        elif isinstance(pos, tuple):
            text_actor.SetPosition(*pos)

        renderer.add_overlay_actor(text_actor)

    for ann in scene.annotations:
        actor = vtk.vtkBillboardTextActor3D()
        actor.SetInput(ann.text)
        actor.SetPosition(ann.x, ann.y, ann.z)
        # Exclude annotation actors from ComputeVisiblePropBounds() so that
        # labels placed far from the data do not stretch the cube-axes bounds.
        actor.UseBoundsOff()
        tp = actor.GetTextProperty()
        r, g, b = ann.color
        tp.SetColor(r, g, b)
        tp.SetFontSize(ann.font_size)
        tp.SetBold(False)
        tp.SetItalic(False)
        tp.SetShadow(True)
        renderer.add_overlay_actor(actor)

    if scene.axes is not None:
        cube_axes = vtk.vtkCubeAxesActor()
        cube_axes.SetBounds(renderer.get_visible_bounds())
        cube_axes.SetCamera(renderer.get_active_camera())
        r, g, b = scene.axes.color
        cube_axes.GetTitleTextProperty(0).SetColor(r, g, b)
        cube_axes.GetTitleTextProperty(1).SetColor(r, g, b)
        cube_axes.GetTitleTextProperty(2).SetColor(r, g, b)
        cube_axes.GetLabelTextProperty(0).SetColor(r, g, b)
        cube_axes.GetLabelTextProperty(1).SetColor(r, g, b)
        cube_axes.GetLabelTextProperty(2).SetColor(r, g, b)
        cube_axes.GetXAxesLinesProperty().SetColor(r, g, b)
        cube_axes.GetYAxesLinesProperty().SetColor(r, g, b)
        cube_axes.GetZAxesLinesProperty().SetColor(r, g, b)
        fs = scene.axes.font_size
        for i in range(3):
            cube_axes.GetTitleTextProperty(i).SetFontSize(fs)
            cube_axes.GetLabelTextProperty(i).SetFontSize(fs)
        labels = scene.axes.labels
        cube_axes.SetXTitle(labels[0])
        cube_axes.SetYTitle(labels[1])
        cube_axes.SetZTitle(labels[2])
        cube_axes.SetFlyModeToOuterEdges()
        cube_axes.DrawXGridlinesOff()
        cube_axes.DrawYGridlinesOff()
        cube_axes.DrawZGridlinesOff()
        renderer.add_actor("__axes__", cube_axes)


def render_scene(scene, shows, vtk_objects, renderer):
    """Render phase: swap actors into the renderer and render.

    This is the cheap scene-update step that must run on the renderer-owning
    thread. It receives only frozen values + the renderer — never the builder.

    Returns ``show_statuses``.
    """
    renderer.clear()
    show_statuses = build_show_actors(shows, vtk_objects, renderer)
    apply_scene_settings(scene, renderer)
    renderer.render()
    return show_statuses
