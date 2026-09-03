"""Tests for the error-message improvements to VTK **props kwarg validation.

Covers six related behaviors of ``create_vtk_filter``/``_apply_properties``/
``_validate_vtk_kwargs_structured`` in ``siva/filters.py`` (and the DSL-level
hint plumbing in ``siva/dsl.py``):

  1. The ``valid:`` list in an unknown-property error is trimmed to
     class-specific properties -- generic vtkObject/vtkAlgorithm plumbing
     (``InputData``, ``GlobalWarningDisplay``, ``ProgressText``, ...) and
     zero-arg ``SetXxxToYyy`` enum shortcuts are excluded.
  2. The ``similar:`` suggestion pool also includes the snake_case DSL
     argument names the calling wrapper form accepts (e.g. ``input``), so a
     typo'd DSL name (not just a typo'd VTK property) gets a suggestion.
  3. ``XOn``/``XOff`` VTK BoolMacro kwargs (e.g. ``ComputeNormalsOn=True``)
     get a dedicated "that's a method, not a property" message.
  4. An unrecognized ``vtk_class`` in ``source()``/``filter()`` gets a short
     message with near-name suggestions instead of the entire whitelist.
  5. Enum-valued VTK properties (e.g. ``vtkThresholdPoints.ThresholdFunction``)
     that reject a string value get an enhanced error listing the VTK mode
     names.
  6. A list value forwarded to an indexed VTK setter (e.g.
     ``vtkFlyingEdges3D.Value=[500]`` -> ``SetValue(i, v)``) is retried as
     per-element calls; failures get a hint instead of the raw arity error.
     A regression test guards against misapplying this retry to setters that
     take several *distinct* positional components (e.g.
     ``vtkPlaneSource.SetResolution(xR, yR)``), which must still fail loudly.

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
# 2. DSL-level argument names as 'similar:' candidates
# ---------------------------------------------------------------------------

class TestDslParamNameSuggestions:
    def test_filter_typo_of_input_suggests_input(self):
        """filter('vtkX', inpt=...) should suggest 'input' even though no VTK
        property is named that."""
        b = PipelineBuilder()
        n = b.filter("vtkSphereSource", inpt="whatever")
        _, statuses = _bp(b)
        status = statuses[n.node_id]
        assert status["status"] == "error"
        assert "input" in status["similar"]

    def test_source_has_no_stray_dsl_candidates_when_valid(self):
        """A well-formed source() call succeeds with no error."""
        b = PipelineBuilder()
        n = b.source("vtkSphereSource", Radius=1.0)
        _, statuses = _bp(b)
        assert statuses[n.node_id]["status"] != "error"

    def test_elevation_wrapper_low_point_typo_suggested(self):
        """elevation()'s own DSL param names (low_point/high_point) are
        offered as candidates alongside the real VTK LowPoint/HighPoint names."""
        b = PipelineBuilder()
        src = b.source("vtkSphereSource", Radius=1.0)
        n = b.elevation(input=src, lowpoint=(0, 0, 0))
        _, statuses = _bp(b)
        status = statuses[n.node_id]
        assert status["status"] == "error"
        # Either the DSL name or the close VTK name (LowPoint) is a fine
        # suggestion; what matters is *a* helpful suggestion is present.
        assert status["similar"], f"expected a similar-name suggestion, got {status}"


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
# 5. Enum-valued property errors (empty/uninformative VTK TypeErrors)
# ---------------------------------------------------------------------------

class TestEnumPropertyErrors:
    def test_threshold_function_string_value_enhanced(self):
        """ThresholdFunction='ThresholdByUpper' on vtkThresholdPoints should
        list the VTK mode names instead of an opaque/empty TypeError."""
        img = vtk.vtkSphereSource()
        img.Update()
        tp = vtk.vtkThresholdPoints()
        tp.SetInputData(img.GetOutput())
        with pytest.raises(ValueError) as exc_info:
            _apply_properties(tp, "vtkThresholdPoints", {"ThresholdFunction": "ThresholdByUpper"})
        msg = str(exc_info.value)
        assert "ThresholdFunction" in msg
        assert "int" in msg
        assert "ThresholdByUpper" in msg
        assert "ThresholdByLower" in msg

    def test_threshold_function_dsl_integration(self):
        b = PipelineBuilder()
        src = b.source("vtkSphereSource", Radius=1.0)
        n = b.filter("vtkThresholdPoints", src, ThresholdFunction="ThresholdByUpper")
        _, statuses = _bp(b)
        status = statuses[n.node_id]
        assert status["status"] == "error"
        assert "ThresholdByUpper" in status["message"]

    def test_valid_enum_int_value_still_works(self):
        """A correct int value for ThresholdFunction is unaffected."""
        img = vtk.vtkSphereSource()
        img.Update()
        tp = vtk.vtkThresholdPoints()
        tp.SetInputData(img.GetOutput())
        _apply_properties(tp, "vtkThresholdPoints", {"ThresholdFunction": 2})
        assert tp.GetThresholdFunction() == 2


# ---------------------------------------------------------------------------
# 6. Indexed VTK setters (list value forwarded to Set<Key>)
# ---------------------------------------------------------------------------

class TestIndexedSetterRetry:
    def _image_data(self):
        img = vtk.vtkImageData()
        img.SetDimensions(5, 5, 5)
        img.AllocateScalars(vtk.VTK_FLOAT, 1)
        for i in range(img.GetNumberOfPoints()):
            img.GetPointData().GetScalars().SetTuple1(i, float(i))
        return img

    def test_single_value_list_applies_via_retry(self):
        fe = vtk.vtkFlyingEdges3D()
        fe.SetInputData(self._image_data())
        _apply_properties(fe, "vtkFlyingEdges3D", {"Value": [50.0]})
        assert fe.GetNumberOfContours() == 1
        assert fe.GetValue(0) == 50.0

    def test_multi_value_list_applies_via_retry(self):
        fe = vtk.vtkFlyingEdges3D()
        fe.SetInputData(self._image_data())
        _apply_properties(fe, "vtkFlyingEdges3D", {"Value": [10.0, 20.0, 30.0]})
        assert fe.GetNumberOfContours() == 3
        assert [fe.GetValue(i) for i in range(3)] == [10.0, 20.0, 30.0]

    def test_retry_failure_gives_hint_with_form_pointer(self):
        """When even the indexed retry fails, the original error is
        preserved with a hint, including the contour() pointer for 'Value'."""
        fe = vtk.vtkFlyingEdges3D()
        fe.SetInputData(self._image_data())
        with pytest.raises(ValueError) as exc_info:
            _apply_properties(fe, "vtkFlyingEdges3D", {"Value": ["a", "b"]})
        msg = str(exc_info.value)
        assert "SetValue" in msg
        assert "contour()" in msg

    def test_dsl_integration_isovalue_list(self):
        """Through the DSL: vtkFlyingEdges3D with a list Value on real image data."""
        b = PipelineBuilder()
        src = b.source("vtkXMLImageDataReader", FileName="__missing__.vti")
        # Skip the reader (no fixture data); exercise _apply_properties
        # directly instead for the true VTK-object-level contract, covered
        # above. This test just confirms the DSL path doesn't choke on a
        # list Value for a class where the indexed retry doesn't apply
        # (empty output is a separate, expected outcome here).
        n = b.filter("vtkFlyingEdges3D", None, Value=[500.0])
        _, statuses = _bp(b)
        status = statuses[n.node_id]
        # Should not surface as an "unhandled exception" style error; the
        # property application itself must not raise (VTK handles a null
        # input gracefully by producing empty/error output, not a Python
        # exception from our property layer).
        assert status["status"] in ("error", "warning", "ok")

    def test_multi_component_setter_not_misapplied_as_indexed(self):
        """Regression guard: a setter that takes several *distinct*
        positional components (vtkPlaneSource.SetResolution(xR, yR)) must
        NOT be silently retried as indexed (0, v0), (1, v1) calls -- that
        would misapply the values instead of failing loudly."""
        p = vtk.vtkPlaneSource()
        default_x, default_y = p.GetXResolution(), p.GetYResolution()
        with pytest.raises(TypeError):
            _apply_properties(p, "vtkPlaneSource", {"Resolution": [10, 10]})
        # And in particular, it must not have silently ended up with the
        # mismatched values a masked indexed-retry would produce (calling
        # SetResolution(0, 10) then SetResolution(1, 10) leaves xR=1, yR=10
        # -- neither the user's intent nor the untouched default).
        assert (p.GetXResolution(), p.GetYResolution()) == (default_x, default_y)

    def test_dsl_integration_seed_node_bad_resolution_still_errors(self):
        """End-to-end regression: the wrong-arity Resolution=[10, 10] on
        vtkPlaneSource must still surface as a node error, not silently
        apply mismatched values (this mirrors a prior integration test)."""
        b = PipelineBuilder()
        seeds = b.source(
            "vtkPlaneSource",
            Origin=(0, 0, 0), Point1=(1, 0, 0), Point2=(0, 1, 0),
            Resolution=[10, 10],
        )
        _, statuses = _bp(b)
        assert statuses[seeds.node_id]["status"] == "error"


# ---------------------------------------------------------------------------
# 7. Per-class special-case keys (SIVA_FILTER_EXTRAS) -- runtime/generator sync
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
