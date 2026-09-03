"""Tests for the error-message improvements to VTK **props kwarg validation.

Covers five related behaviors of ``create_vtk_filter``/``_apply_properties``/
``_validate_vtk_kwargs_structured`` in ``siva/filters.py``:

  1. The ``valid:`` list in an unknown-property error is trimmed to
     class-specific properties -- generic vtkObject/vtkAlgorithm plumbing
     (``InputData``, ``GlobalWarningDisplay``, ``ProgressText``, ...) and
     zero-arg ``SetXxxToYyy`` enum shortcuts are excluded.
  2. The ``similar:`` suggestion pool also includes the literal ``"input"``
     (the DSL-level primary-input kwarg every wrapper form accepts), so a
     typo'd ``input`` kwarg gets a suggestion even though no VTK property is
     named that.
  3. ``XOn``/``XOff`` VTK BoolMacro kwargs (e.g. ``ComputeNormalsOn=True``)
     get a dedicated "that's a method, not a property" message.
  4. An unrecognized ``vtk_class`` in ``source()``/``filter()`` gets a short
     message with near-name suggestions instead of the entire whitelist.
  5. A near-empty VTK setter TypeError is enhanced with the expected
     argument type read off the setter's docstring signature. A list value
     forwarded to a setter that raises an arity error (e.g.
     ``vtkFlyingEdges3D.Value=[500]`` -> ``SetValue()`` takes one value) gets
     a redirect hint instead of being silently retried per-element.

All unit tests operate on real VTK instances with no renderer or Xvfb.
Integration tests go through the DSL/compute phase (no renderer).
"""

import vtk
import pytest

from siva.filters import (
    _validate_vtk_kwargs_structured,
    _display_setter_names,
    _get_vtk_valid_setters,
    _special_extra_keys,
    _supports_cut_function,
    _apply_properties,
    _enhance_setter_type_error,
    _unknown_class_message,
    create_vtk_filter,
    SIVA_FILTER_EXTRAS,
    WHITELISTED_CLASSES,
)
from siva.dsl import PipelineBuilder, _freeze_spec
from siva.compute import compute as _compute_spec


def _bp(_builder, cache=None):
    _r = _compute_spec(_freeze_spec(_builder), cache=cache)
    return _r.outputs, _r.statuses


# ---------------------------------------------------------------------------
# 1. Trimmed 'valid:' list
# ---------------------------------------------------------------------------

class TestTrimmedValidList:
    def test_generic_algorithm_plumbing_excluded(self):
        """Generic vtkObject/vtkAlgorithm-family names don't show in 'valid'."""
        f = vtk.vtkContourFilter()
        displayed = _display_setter_names(f)
        for noisy in ("GlobalWarningDisplay", "ProgressText", "InputConnection",
                      "InputData", "Debug", "ReferenceCount", "ObjectName"):
            assert noisy not in displayed, f"{noisy!r} should be excluded from display list"

    def test_enum_shortcut_excluded(self):
        """Zero-arg SetXxxToYyy shortcuts don't show in 'valid' (vtkThreshold)."""
        t = vtk.vtkThreshold()
        displayed = _display_setter_names(t)
        assert "ComponentModeToUseAll" not in displayed
        assert "ComponentModeToUseAny" not in displayed
        assert "ComponentModeToUseSelected" not in displayed
        # The real assignable property is still there
        assert "ComponentMode" in displayed

    def test_class_specific_properties_still_shown(self):
        """Real, class-specific properties remain in the trimmed list."""
        f = vtk.vtkContourFilter()
        displayed = _display_setter_names(f)
        for real in ("ComputeNormals", "ComputeScalars", "GenerateTriangles", "NumberOfContours"):
            assert real in displayed

    def test_membership_check_unaffected_by_trimming(self):
        """Trimming only affects display; a trimmed-out name is still a *valid* kwarg."""
        f = vtk.vtkContourFilter()
        valid = _get_vtk_valid_setters(f)
        displayed = _display_setter_names(f)
        assert "InputConnection" in valid
        assert "InputConnection" not in displayed

    def test_error_message_valid_list_is_trimmed(self):
        """The rendered error's 'valid:' section excludes generic plumbing."""
        f = vtk.vtkContourFilter()
        result = _validate_vtk_kwargs_structured(f, {"Bogus": 1}, "vtkContourFilter")
        assert result is not None
        assert "GlobalWarningDisplay" not in result["message"]
        assert "InputConnection" not in result["message"]
        assert "ComputeNormals" in result["message"]

    def test_still_has_enough_entries(self):
        """Trimming shouldn't gut the list below usefulness for a typical filter."""
        f = vtk.vtkContourFilter()
        result = _validate_vtk_kwargs_structured(f, {"Bogus": 1}, "vtkContourFilter")
        assert len(result["valid"]) >= 5


# ---------------------------------------------------------------------------
# 2. The literal "input" as a 'similar:' candidate
# ---------------------------------------------------------------------------

class TestInputSuggestion:
    def test_typo_of_input_suggests_input(self):
        """A kwarg close to 'input' should suggest it even though no VTK
        property is named that -- it's the DSL-level primary-input kwarg."""
        f = vtk.vtkContourFilter()
        result = _validate_vtk_kwargs_structured(f, {"inpt": "whatever"}, "vtkContourFilter")
        assert result is not None
        assert "input" in result["similar"]

    def test_filter_typo_of_input_suggests_input_end_to_end(self):
        """filter('vtkX', inpt=...) should suggest 'input' through the DSL."""
        b = PipelineBuilder()
        n = b.filter("vtkSphereSource", inpt="whatever")
        _, statuses = _bp(b)
        status = statuses[n.node_id]
        assert status["status"] == "error"
        assert "input" in status["similar"]

    def test_source_has_no_stray_candidates_when_valid(self):
        """A well-formed source() call succeeds with no error."""
        b = PipelineBuilder()
        n = b.source("vtkSphereSource", Radius=1.0)
        _, statuses = _bp(b)
        assert statuses[n.node_id]["status"] != "error"

    def test_unrelated_typo_does_not_suggest_input(self):
        """A typo unrelated to 'input' doesn't spuriously suggest it."""
        f = vtk.vtkContourFilter()
        result = _validate_vtk_kwargs_structured(f, {"Bogus": 1}, "vtkContourFilter")
        assert result is not None
        assert "input" not in result["similar"]


# ---------------------------------------------------------------------------
# 3. VTK BoolMacro On/Off kwargs
# ---------------------------------------------------------------------------

class TestBoolMacroHint:
    def test_compute_normals_on_gives_dedicated_message(self):
        f = vtk.vtkContourFilter()
        result = _validate_vtk_kwargs_structured(f, {"ComputeNormalsOn": True}, "vtkContourFilter")
        assert result is not None
        assert "ComputeNormalsOn" in result["message"]
        assert "not a property" in result["message"]
        assert "ComputeNormals=True" in result["message"]
        assert "ComputeNormals=False" in result["message"]
        assert result["similar"] == ["ComputeNormals"]

    def test_compute_normals_off_gives_dedicated_message(self):
        f = vtk.vtkContourFilter()
        result = _validate_vtk_kwargs_structured(f, {"ComputeNormalsOff": True}, "vtkContourFilter")
        assert result is not None
        assert "ComputeNormalsOff" in result["message"]
        assert "ComputeNormals=True" in result["message"]

    def test_only_triggers_when_base_property_exists(self):
        """'FooOn' where 'Foo' isn't a real property falls through to the
        generic unknown-property path, not the bool-macro message."""
        f = vtk.vtkContourFilter()
        result = _validate_vtk_kwargs_structured(f, {"NonsenseOn": True}, "vtkContourFilter")
        assert result is not None
        assert "not a property" not in result["message"]

    def test_dsl_integration_bool_macro(self):
        """Through the full DSL: ComputeNormalsOn=True on contour()."""
        b = PipelineBuilder()
        src = b.source("vtkSphereSource", Radius=1.0)
        n = b.filter("vtkContourFilter", src, ComputeNormalsOn=True)
        _, statuses = _bp(b)
        status = statuses[n.node_id]
        assert status["status"] == "error"
        assert "ComputeNormals=True" in status["message"]


# ---------------------------------------------------------------------------
# 4. Unknown VTK class message
# ---------------------------------------------------------------------------

class TestUnknownClassMessage:
    def test_short_message_no_full_whitelist_dump(self):
        msg = _unknown_class_message("vtkTotallyMadeUpClass")
        assert "vtkTotallyMadeUpClass" in msg
        assert "not whitelisted" in msg
        # The old behavior dumped the entire ~2.4 KB whitelist; the new
        # message should be dramatically shorter.
        assert len(msg) < 400
        # Doesn't enumerate the whole class list inline
        assert msg.count("vtk") < 10

    def test_points_to_get_dsl_overview(self):
        msg = _unknown_class_message("vtkTotallyMadeUpClass")
        assert "get_dsl_overview()" in msg

    def test_near_name_suggestion(self):
        msg = _unknown_class_message("vtkContureFilter")  # typo of vtkContourFilter
        assert "vtkContourFilter" in msg

    def test_create_vtk_filter_raises_trimmed_message(self):
        with pytest.raises(ValueError) as exc_info:
            create_vtk_filter("vtkNotWhitelisted")
        assert "not whitelisted" in str(exc_info.value)
        assert len(str(exc_info.value)) < 400

    def test_no_suggestions_for_wildly_unrelated_name(self):
        """A name close to nothing gets no 'Did you mean' clause, but is
        still short and points at get_dsl_overview()."""
        msg = _unknown_class_message("zzz_not_even_vtk_shaped")
        assert "get_dsl_overview()" in msg


# ---------------------------------------------------------------------------
# 5. Setter TypeError enhancement and list-arity redirect
# ---------------------------------------------------------------------------

class TestSetterTypeErrorEnhancement:
    def test_near_empty_message_enhanced_with_docstring_type(self):
        """A near-empty TypeError from a real setter is enhanced with the
        expected argument type read from its VTK-generated docstring."""
        f = vtk.vtkThresholdPoints()
        err = TypeError("")  # simulates a near-empty VTK TypeError
        enhanced = _enhance_setter_type_error(f, "vtkThresholdPoints", "ThresholdFunction", "x", err)
        assert isinstance(enhanced, ValueError)
        msg = str(enhanced)
        assert "ThresholdFunction" in msg
        assert "int" in msg
        assert "SetThresholdFunction" in msg

    def test_non_empty_message_left_unchanged(self):
        """When the original error already says something, it's returned as-is."""
        f = vtk.vtkThresholdPoints()
        err = TypeError("some specific complaint")
        enhanced = _enhance_setter_type_error(f, "vtkThresholdPoints", "ThresholdFunction", "x", err)
        assert enhanced is err

    def test_unknown_property_left_unchanged(self):
        """When the expected type can't be determined (bogus key), the
        original error is returned unchanged even if its message is empty."""
        f = vtk.vtkThresholdPoints()
        err = TypeError("")
        enhanced = _enhance_setter_type_error(f, "vtkThresholdPoints", "NotARealProperty", "x", err)
        assert enhanced is err


class TestListArityRedirect:
    def _image_data(self):
        img = vtk.vtkImageData()
        img.SetDimensions(5, 5, 5)
        img.AllocateScalars(vtk.VTK_FLOAT, 1)
        for i in range(img.GetNumberOfPoints()):
            img.GetPointData().GetScalars().SetTuple1(i, float(i))
        return img

    def test_list_value_on_single_arg_setter_fails_with_hint(self):
        """Value=[500] must now fail (not silently succeed via a retry),
        with a hint pointing at contour()/Isosurfaces."""
        fe = vtk.vtkFlyingEdges3D()
        fe.SetInputData(self._image_data())
        with pytest.raises(ValueError) as exc_info:
            _apply_properties(fe, "vtkFlyingEdges3D", {"Value": [500]})
        msg = str(exc_info.value)
        assert "SetValue" in msg
        assert "takes one value per call" in msg
        assert "contour()" in msg
        assert "Isosurfaces" in msg
        # And it really didn't apply the list -- default (unset) state remains.
        assert fe.GetNumberOfContours() == 1
        assert fe.GetValue(0) == 0.0

    def test_list_value_on_multi_component_setter_also_fails_with_hint(self):
        """A setter that takes several distinct positional components (e.g.
        vtkPlaneSource.SetResolution(xR, yR)) also gets the same generic
        redirect hint on a list-arity mismatch -- SIVA no longer tries to
        distinguish indexed accessors from multi-component setters."""
        p = vtk.vtkPlaneSource()
        default_x, default_y = p.GetXResolution(), p.GetYResolution()
        with pytest.raises(ValueError) as exc_info:
            _apply_properties(p, "vtkPlaneSource", {"Resolution": [10, 10]})
        assert "takes one value per call" in str(exc_info.value)
        # Values must be untouched, not partially/mismatched-applied.
        assert (p.GetXResolution(), p.GetYResolution()) == (default_x, default_y)

    def test_dsl_integration_isovalue_list_still_errors_cleanly(self):
        """Through the DSL: a list Value on a class with no data still
        surfaces as a clean node error, not an unhandled exception."""
        b = PipelineBuilder()
        n = b.filter("vtkFlyingEdges3D", None, Value=[500.0])
        _, statuses = _bp(b)
        status = statuses[n.node_id]
        assert status["status"] in ("error", "warning", "ok")

    def test_dsl_integration_seed_node_bad_resolution_still_errors(self):
        """End-to-end regression: the wrong-arity Resolution=[10, 10] on
        vtkPlaneSource must still surface as a node error."""
        b = PipelineBuilder()
        seeds = b.source(
            "vtkPlaneSource",
            Origin=(0, 0, 0), Point1=(1, 0, 0), Point2=(0, 1, 0),
            Resolution=[10, 10],
        )
        _, statuses = _bp(b)
        assert statuses[seeds.node_id]["status"] == "error"


# ---------------------------------------------------------------------------
# 6. Per-class special-case keys (SIVA_FILTER_EXTRAS) -- runtime/generator sync
# ---------------------------------------------------------------------------

class TestSpecialCaseKeysArePerClass:
    """SIVA_FILTER_EXTRAS gates special-case property keys per VTK class.

    A key like 'Vectors' or 'GlyphSource' has no plain VTK Set<key> setter --
    it's dispatched specially in _apply_properties -- so it must be exempted
    from typo checking only on the classes that actually implement it, not
    globally. See the SIVA_FILTER_EXTRAS docstring in siva/filters.py.
    """

    def test_extras_valid_on_their_own_class(self):
        """Every SIVA_FILTER_EXTRAS key passes validation on its own class."""
        for vtk_class, extras in SIVA_FILTER_EXTRAS.items():
            instance = WHITELISTED_CLASSES[vtk_class]()
            for key in extras:
                result = _validate_vtk_kwargs_structured(
                    instance, {key: "placeholder"}, vtk_class
                )
                assert result is None, (
                    f"{key!r} should be valid on {vtk_class} but got: {result}"
                )

    def test_extras_rejected_on_unrelated_class(self):
        """A special-case key from one class is rejected as unknown on another."""
        sphere = vtk.vtkSphereSource()
        for key in ("Vectors", "GlyphSource", "ScaleArray", "OrientationArray",
                    "ContourBy", "Isosurfaces", "ThresholdBy", "ThresholdRange",
                    "SeedSource", "GradientField"):
            result = _validate_vtk_kwargs_structured(
                sphere, {key: "placeholder"}, "vtkSphereSource"
            )
            assert result is not None, (
                f"{key!r} has no meaning on vtkSphereSource and should be rejected"
            )

    def test_glyph_mode_is_not_a_valid_property(self):
        """GlyphMode was removed: vtkGlyph3D has no SetGlyphMode in this VTK
        build, so it must no longer silently no-op -- it should be reported
        as a plain unknown property."""
        glyph = vtk.vtkGlyph3D()
        assert not hasattr(glyph, "SetGlyphMode")
        result = _validate_vtk_kwargs_structured(
            glyph, {"GlyphMode": "AllPoints"}, "vtkGlyph3D"
        )
        assert result is not None
        assert "GlyphMode" in result["message"]


def test_special_extra_keys_matches_gen_spec_api_table():
    """The runtime's per-class special-case table is the *same object* the
    generator imports -- see scripts/gen_spec_api.py's import of
    SIVA_FILTER_EXTRAS from siva.filters -- so a per-class special-case key
    can never drift between the runtime validator and the generated editor
    stub. Also cross-checks the CutFunction-availability helper the two
    modules share.
    """
    import importlib.util
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    gen_script_path = repo_root / "scripts" / "gen_spec_api.py"
    spec = importlib.util.spec_from_file_location("gen_spec_api", gen_script_path)
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)

    assert gen.SIVA_FILTER_EXTRAS is SIVA_FILTER_EXTRAS
    assert gen._supports_cut_function is _supports_cut_function

    for name, cls in WHITELISTED_CLASSES.items():
        instance = cls()
        assert _special_extra_keys(name, instance) == (
            set(SIVA_FILTER_EXTRAS.get(name, ()))
            | ({"CutFunction"} if gen._supports_cut_function(instance) else set())
        )
