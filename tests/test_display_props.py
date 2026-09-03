"""Tests for show() display-property validation and surface lighting/shading props.

Covers two paired behaviors:

1. **Validation** (``siva.filters.check_display_props`` /
   ``validate_display_props``): every key ``show()`` accepts is in the
   ``DISPLAY_PROPS`` registry, tagged surface-only / volume-only / both.
   An unknown key fails the show directive (with a difflib near-name
   suggestion and the accepted set); a key belonging to the other
   representation produces a build-report warning saying it was ignored.

2. **Surface lighting/shading props**: ``lighting``, ``ambient``, ``diffuse``
   reach the ``vtkProperty``, and ``smooth_shading`` sets the interpolation
   mode -- and nothing else.  ``show()`` never inserts a filter behind the
   caller's back, so Phong shading of a surface with no point normals needs an
   explicit ``filter("vtkPolyDataNormals", input=...)`` upstream.

3. **Volume rendering requires ``opacity_function``**: a
   ``representation="Volume"`` show without it fails with a paste-able ramp
   over the field range instead of silently inventing a transfer function.

Rendering isn't needed here (actor/mapper/property state is inspected
directly), but conftest starts Xvfb for the session anyway.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pytest
import vtk
from vtk.util.numpy_support import numpy_to_vtk

from siva import scene as scene_mod
from siva.filters import (
    DISPLAY_PROPS,
    REPRESENTATIONS,
    SCOPE_BOTH,
    SCOPE_SURFACE,
    SCOPE_VOLUME,
    check_display_props,
    create_show,
    display_props_for_scope,
    validate_display_props,
)
from siva.hot_reload import _build_report
from siva.spec import Ref, Show


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scalar_image(field_name="temperature", lo=0.0, hi=100.0, dims=(6, 6, 6)):
    """vtkImageData with one linearly-spaced scalar field (not polydata)."""
    img = vtk.vtkImageData()
    img.SetDimensions(*dims)
    img.SetSpacing(1.0, 1.0, 1.0)
    n = img.GetNumberOfPoints()
    arr = numpy_to_vtk(np.linspace(lo, hi, n).astype(np.float64), deep=True)
    arr.SetName(field_name)
    img.GetPointData().AddArray(arr)
    img.GetPointData().SetActiveScalars(field_name)
    return img


def _sphere_source():
    """A polydata-producing algorithm whose output carries point normals."""
    src = vtk.vtkSphereSource()
    src.SetThetaResolution(8)
    src.SetPhiResolution(8)
    return src


def _plane_no_normals():
    """A vtkPolyData with no point normals (show() must not generate any)."""
    plane = vtk.vtkPlaneSource()
    plane.SetXResolution(4)
    plane.SetYResolution(4)
    plane.Update()
    poly = vtk.vtkPolyData()
    poly.DeepCopy(plane.GetOutput())
    poly.GetPointData().SetNormals(None)
    assert poly.GetPointData().GetNormals() is None
    return poly


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------

class TestRegistry:
    """The registry itself: scopes are well-formed and cover both paths."""

    def test_every_prop_has_a_known_scope(self):
        assert set(DISPLAY_PROPS.values()) <= {SCOPE_BOTH, SCOPE_SURFACE, SCOPE_VOLUME}

    def test_representations_match_the_stub_generator(self):
        """The spec_api stub's Literal is generated from this same tuple."""
        import scripts.gen_spec_api as gen  # noqa: PLC0415

        assert sorted(gen.REPRESENTATIONS) == sorted(REPRESENTATIONS)

    def test_surface_scope_excludes_volume_only_props(self):
        surface = display_props_for_scope(SCOPE_SURFACE)
        assert "opacity_function" not in surface
        assert "color_function" not in surface
        assert "smooth_shading" in surface
        assert "color_by" in surface  # shared props are included

    def test_volume_scope_excludes_surface_only_props(self):
        volume = display_props_for_scope(SCOPE_VOLUME)
        assert "smooth_shading" not in volume
        assert "line_width" not in volume
        assert "opacity_function" in volume
        assert "color_by" in volume

    def test_new_lighting_props_are_surface_only(self):
        for key in ("lighting", "smooth_shading"):
            assert DISPLAY_PROPS[key] == SCOPE_SURFACE, key

    def test_removed_props_are_not_accepted(self):
        """Props whose only implementation was a hidden filter/mapper knob."""
        for key in ("split_sharp_edges", "feature_angle",
                    "clip_planes", "sample_distance"):
            assert key not in DISPLAY_PROPS, key

    def test_shared_lighting_coefficients(self):
        for key in ("ambient", "diffuse", "specular", "specular_power"):
            assert DISPLAY_PROPS[key] == SCOPE_BOTH, key


# ---------------------------------------------------------------------------
# Unknown keys
# ---------------------------------------------------------------------------

class TestUnknownKeys:
    """An unknown display-prop key is an error, not a silent drop."""

    def test_unknown_key_reports_error(self):
        error, warnings = check_display_props({"color_by": "t", "shiny": True})
        assert error is not None
        assert error["property"] == "shiny"
        assert warnings == []

    def test_unknown_key_suggests_near_name(self):
        error, _ = check_display_props({"ambiant": 0.3})
        assert "ambient" in error["similar"]
        assert "similar: ambient" in error["message"]

    def test_unknown_key_message_lists_available_set(self):
        error, _ = check_display_props({"totally_bogus": 1})
        assert "unknown show() display property" in error["message"]
        assert "'totally_bogus'" in error["message"]
        for key in ("color_by", "smooth_shading", "opacity_function"):
            assert key in error["message"]
        assert error["valid"] == sorted(DISPLAY_PROPS)

    def test_multiple_unknown_keys_all_named(self):
        error, _ = check_display_props({"foo": 1, "bar": 2})
        assert "'foo'" in error["message"] and "'bar'" in error["message"]
        assert error["property"] == ["foo", "bar"]

    def test_validate_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown show.. display property"):
            validate_display_props({"opactiy": 0.5})

    def test_create_show_rejects_unknown_key(self):
        data = _make_scalar_image()
        with pytest.raises(ValueError) as exc:
            create_show(data, color_by="temperature", opactiy=0.5)
        assert "opacity" in str(exc.value)

    def test_unknown_representation_value_reports_error(self):
        """An unrecognized representation would otherwise render as a surface."""
        error, warnings = check_display_props({"representation": "Volumes"})
        assert error is not None
        assert error["property"] == "representation"
        assert "Volume" in error["similar"]
        assert "unknown show() representation 'Volumes'" in error["message"]
        assert warnings == []

    def test_known_representations_pass(self):
        for rep in ("Surface", "Wireframe", "Points", "Volume"):
            error, _ = check_display_props({"representation": rep})
            assert error is None, rep

    def test_valid_keys_pass_clean(self):
        error, warnings = check_display_props({
            "color_by": "temperature", "scalar_range": (0, 1), "lut": "fire",
            "opacity": 0.5, "scalar_bar": True, "ambient": 0.2, "diffuse": 0.8,
            "specular": 0.3, "specular_power": 20, "color": (1, 0, 0),
            "component": "z", "line_width": 2.0, "lighting": True,
            "smooth_shading": True,
        })
        assert error is None
        assert warnings == []


# ---------------------------------------------------------------------------
# Mis-scoped keys
# ---------------------------------------------------------------------------

class TestScopeWarnings:
    """Known-but-inapplicable keys warn instead of vanishing."""

    def test_volume_only_key_on_surface_warns(self):
        error, warnings = check_display_props({
            "color_by": "temperature",
            "color_function": [(0, 1, 1, 1)],
        })
        assert error is None
        assert [w["property"] for w in warnings] == ["color_function"]
        msg = warnings[0]["message"]
        assert "color_function" in msg
        assert "volume-only" in msg
        assert "ignored" in msg
        assert "representation='Surface'" in msg

    def test_several_volume_only_keys_on_surface_warn(self):
        _error, warnings = check_display_props({
            "opacity_function": [(0, 0), (1, 1)],
            "gradient_opacity": True,
            "shade": False,
        })
        assert set(w["property"] for w in warnings) == {
            "opacity_function", "gradient_opacity", "shade"}

    def test_surface_only_key_on_volume_warns(self):
        _error, warnings = check_display_props({
            "representation": "Volume",
            "color_by": "temperature",
            "smooth_shading": True,
        })
        assert [w["property"] for w in warnings] == ["smooth_shading"]
        assert "surface-only" in warnings[0]["message"]
        assert "volume rendering" in warnings[0]["message"]

    def test_shared_key_never_warns_on_volume(self):
        _error, warnings = check_display_props({
            "representation": "Volume", "ambient": 0.4, "specular_power": 10,
        })
        assert warnings == []

    def test_create_show_tolerates_misscoped_key(self):
        """Mis-scoped props are warnings, so the actor still builds."""
        data = _make_scalar_image()
        actor, _bar = create_show(data, color_by="temperature", shade=True)
        assert isinstance(actor, vtk.vtkActor)


# ---------------------------------------------------------------------------
# Surface lighting / shading props reach VTK
# ---------------------------------------------------------------------------

class TestSurfaceLightingProps:
    """Each new surface prop lands on the vtkProperty or the normals filter."""

    def test_lighting_off(self):
        actor, _ = create_show(_make_scalar_image(), lighting=False)
        assert actor.GetProperty().GetLighting() == 0

    def test_lighting_on_by_default(self):
        actor, _ = create_show(_make_scalar_image())
        assert actor.GetProperty().GetLighting() == 1

    def test_ambient_and_diffuse(self):
        actor, _ = create_show(_make_scalar_image(), ambient=0.25, diffuse=0.75)
        prop = actor.GetProperty()
        assert prop.GetAmbient() == pytest.approx(0.25)
        assert prop.GetDiffuse() == pytest.approx(0.75)

    def test_smooth_shading_sets_phong(self):
        actor, _ = create_show(_sphere_source(), smooth_shading=True)
        assert actor.GetProperty().GetInterpolation() == vtk.VTK_PHONG

    def test_smooth_shading_false_sets_flat(self):
        actor, _ = create_show(_sphere_source(), smooth_shading=False)
        assert actor.GetProperty().GetInterpolation() == vtk.VTK_FLAT

    def test_no_shading_props_leaves_default_interpolation(self):
        actor, _ = create_show(_sphere_source())
        assert actor.GetProperty().GetInterpolation() == vtk.VTK_GOURAUD

    def test_no_filter_inserted_for_smooth_shading(self):
        """show() never inserts geometry filters -- the mapper input is untouched."""
        actor, _ = create_show(_sphere_source(), smooth_shading=True)
        upstream = actor.GetMapper().GetInputAlgorithm()
        assert isinstance(upstream, vtk.vtkSphereSource)

    def test_no_normals_generated_when_missing(self):
        """A surface without normals is shaded as-is; no vtkPolyDataNormals."""
        actor, _ = create_show(_plane_no_normals(), smooth_shading=True)
        mapper = actor.GetMapper()
        assert actor.GetProperty().GetInterpolation() == vtk.VTK_PHONG
        assert not isinstance(mapper.GetInputAlgorithm(), vtk.vtkPolyDataNormals)
        mapper.Update()
        assert mapper.GetInput().GetPointData().GetNormals() is None

    def test_no_geometry_filter_for_non_polydata_input(self):
        actor, _ = create_show(_make_scalar_image(), smooth_shading=True)
        upstream = actor.GetMapper().GetInputAlgorithm()
        assert not isinstance(upstream, vtk.vtkGeometryFilter)
        assert not isinstance(upstream, vtk.vtkPolyDataNormals)

    def test_shading_preserves_color_by_mapper_settings(self):
        actor, bar = create_show(_make_scalar_image(), color_by="temperature",
                                 smooth_shading=True)
        mapper = actor.GetMapper()
        assert mapper.GetScalarVisibility() == 1
        assert mapper.GetArrayName() == "temperature"
        assert bar is not None
        mapper.Update()
        assert mapper.GetInput().GetNumberOfPoints() > 0


# ---------------------------------------------------------------------------
# Volume rendering requires an opacity function
# ---------------------------------------------------------------------------

class TestVolumeOpacityFunctionRequired:
    """No silent auto-opacity: the caller must supply the transfer function."""

    def test_missing_opacity_function_raises_with_pasteable_ramp(self):
        data = _make_scalar_image(lo=12.0, hi=250.0)
        with pytest.raises(ValueError) as exc:
            create_show(data, representation="Volume", color_by="temperature")
        msg = str(exc.value)
        assert "opacity_function=[(" in msg
        assert "12.0, 0.0" in msg
        assert "250.0, 0.6" in msg

    def test_ramp_uses_explicit_scalar_range_when_given(self):
        data = _make_scalar_image(lo=0.0, hi=100.0)
        with pytest.raises(ValueError) as exc:
            create_show(data, representation="Volume", color_by="temperature",
                        scalar_range=(20.0, 80.0))
        assert "opacity_function=[(20.0, 0.0), (80.0, 0.6)]" in str(exc.value)

    def test_volume_with_opacity_function_builds(self):
        data = _make_scalar_image(lo=0.0, hi=100.0)
        volume, _bar = create_show(
            data, representation="Volume", color_by="temperature",
            opacity_function=[(0.0, 0.0), (100.0, 0.6)])
        assert isinstance(volume, vtk.vtkVolume)
        otf = volume.GetProperty().GetScalarOpacity()
        assert otf.GetValue(100.0) == pytest.approx(0.6)

    def test_missing_opacity_function_fails_the_show_directive(self):
        statuses = _run_shows(
            {"representation": "Volume", "color_by": "temperature"}, name="vol")
        assert statuses["vol"]["status"] == "error"
        assert "opacity_function=[(" in statuses["vol"]["message"]

    def test_missing_opacity_function_has_structured_kind_and_arg(self):
        statuses = _run_shows(
            {"representation": "Volume", "color_by": "temperature"}, name="vol")
        assert statuses["vol"]["kind"] == "missing_required_arg"
        assert statuses["vol"]["arg"] == "opacity_function"

    def test_missing_opacity_function_uses_placeholder_when_no_array_exists(self):
        # No point-data arrays at all -- neither an explicit color_by match
        # nor active scalars to fall back to -- so scalar_range can't be
        # resolved and the message must fall back to the generic placeholder
        # rather than a bogus (0.0, 1.0) ramp.
        img = vtk.vtkImageData()
        img.SetDimensions(3, 3, 3)
        with pytest.raises(ValueError) as exc:
            create_show(img, representation="Volume")
        assert "opacity_function=[(min, 0.0), (max, 0.6)]" in str(exc.value)


# ---------------------------------------------------------------------------
# Build-report integration
# ---------------------------------------------------------------------------

class _FakeRenderer:
    """Minimal renderer seam (mirrors tests/test_terse_report.py)."""

    camera_positioned = True

    def __init__(self):
        self.actors = {}

    def dispatch(self, fn):
        return fn()

    def clear(self):
        self.actors.clear()

    def render(self):
        pass

    def add_actor(self, name, actor):
        self.actors[name] = actor

    def add_volume(self, name, actor):
        self.actors[name] = actor

    def add_scalar_bar(self, *args, **kwargs):
        pass

    def add_overlay_actor(self, *args, **kwargs):
        pass

    def set_background(self, *args, **kwargs):
        pass

    def set_camera(self, **kwargs):
        pass

    def get_camera_state(self):
        return {"position": [0.0, 0.0, 1.0], "focal_point": [0.0, 0.0, 0.0]}


def _run_shows(props, name="surf"):
    """Run build_show_actors for a single show directive; return its status."""
    data = _make_scalar_image()
    shows = (Show(node=Ref(0), name=name, props=props),)
    statuses = scene_mod.build_show_actors(shows, {0: data}, _FakeRenderer())
    return statuses


def _report(show_statuses):
    node_statuses = {0: {"status": "ok", "class": "vtkImageData", "name": "data"}}
    return _build_report(
        node_statuses, show_statuses, version=1, t_interpret=0.01, t_total=0.02,
        cache_stats={"hits": 0, "misses": 1, "evictions": 0},
        renderer=_FakeRenderer(), verbose=False,
    )


class TestShowStatusReporting:
    """Errors and warnings surface through show_statuses and the build report."""

    def test_unknown_key_fails_the_show_directive(self):
        statuses = _run_shows({"color_by": "temperature", "ambiant": 0.3})
        assert statuses["surf"]["status"] == "error"
        assert statuses["surf"]["kind"] == "unknown_property"
        assert statuses["surf"]["property"] == "ambiant"
        assert "ambient" in statuses["surf"]["similar"]

    def test_unknown_key_error_appears_in_report(self):
        statuses = _run_shows({"ambiant": 0.3})
        report = _report(statuses)
        assert "ERRORS" in report
        assert "surf: ERROR" in report
        assert "ambiant" in report
        assert "similar: ambient" in report

    def test_volume_only_key_on_surface_warns_in_status(self):
        statuses = _run_shows({"color_by": "temperature", "opacity_function": [(0, 1)]})
        assert statuses["surf"]["status"] == "warning"
        assert statuses["surf"]["ignored"] == ["opacity_function"]
        assert "ignored" in statuses["surf"]["message"]

    def test_volume_only_key_warning_appears_in_report(self):
        statuses = _run_shows({"color_by": "temperature", "opacity_function": [(0, 1)]})
        report = _report(statuses)
        # A show warning must force the verbose path, not the terse one-liner.
        assert "built with warnings" in report
        assert "opacity_function" in report
        assert "WARNING" in report

    def test_clean_show_stays_ok(self):
        statuses = _run_shows({"color_by": "temperature", "smooth_shading": True})
        assert statuses["surf"]["status"] == "ok"
        # ok statuses go through siva.diagnostics.ok() like error/warning
        # statuses go through its error()/warning() helpers.
        assert statuses["surf"]["class"] == "surf"
        report = _report(statuses)
        assert "ok" in report
        assert "WARNING" not in report
