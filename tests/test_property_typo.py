"""Tests for property-typo detection in create_vtk_filter.

Contract enforced:
  - _validate_vtk_kwargs returns None for valid kwargs.
  - _validate_vtk_kwargs returns an error string for unknown properties.
  - The error string includes the typo'd name, the VTK class name,
    similar property names, and the full (capped) list of valid properties.
  - Case-sensitivity: 'contourvalues' (wrong case) is caught.
  - Empty kwargs passes without error.
  - Via the DSL: a typo'd kwarg produces a node with "error" status.
  - Descendants of a typo-errored node are cascade-skipped.
  - The error message is helpful enough to list at least 5 valid properties.

All unit tests operate on mock/real VTK instances with no renderer or Xvfb.
Integration tests use interpret_build (no renderer).
"""

import vtk
import pytest

from siva.filters import _validate_vtk_kwargs, _get_vtk_valid_setters
from siva.dsl import PipelineBuilder, interpret_build


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestValidateVtkKwargsUnit:
    """Pure unit tests for _validate_vtk_kwargs."""

    def test_typo_property_is_detected(self):
        """ScalarArrays is not a valid property on vtkContourFilter."""
        f = vtk.vtkContourFilter()
        result = _validate_vtk_kwargs(f, {"ScalarArrays": "Temperature"}, "vtkContourFilter")
        assert result is not None, "Expected error for typo'd property 'ScalarArrays'"
        assert "ScalarArrays" in result
        assert "vtkContourFilter" in result

    def test_typo_suggests_similar_property(self):
        """ScalarArrays should suggest a close match (e.g. ScalarTree)."""
        f = vtk.vtkContourFilter()
        result = _validate_vtk_kwargs(f, {"ScalarArrays": "Temperature"}, "vtkContourFilter")
        assert result is not None
        # Should have a 'similar:' line with at least one suggestion
        assert "similar:" in result.lower() or "similar" in result

    def test_valid_property_passes(self):
        """ContourValues is a real property — should return None."""
        f = vtk.vtkContourFilter()
        result = _validate_vtk_kwargs(f, {"ComputeNormals": 1}, "vtkContourFilter")
        assert result is None, f"Expected None for valid property, got: {result!r}"

    def test_empty_kwargs_passes(self):
        """Empty dict should never raise or return an error."""
        f = vtk.vtkContourFilter()
        result = _validate_vtk_kwargs(f, {}, "vtkContourFilter")
        assert result is None

    def test_case_sensitivity_wrong_case_is_caught(self):
        """'computenormals' (all-lowercase) is not a valid setter name."""
        f = vtk.vtkContourFilter()
        result = _validate_vtk_kwargs(f, {"computenormals": 1}, "vtkContourFilter")
        assert result is not None, (
            "Expected error for wrong-case property 'computenormals' "
            "(VTK setters are PascalCase)"
        )

    def test_valid_property_list_has_many_entries(self):
        """The valid-property list in the error should include at least 5 names."""
        f = vtk.vtkContourFilter()
        result = _validate_vtk_kwargs(f, {"NonExistentProp": 1}, "vtkContourFilter")
        assert result is not None
        # The 'valid:' section should list multiple entries
        assert "valid:" in result
        # Count comma-separated entries
        valid_section = result.split("valid:")[1]
        entries = [e.strip() for e in valid_section.split(",") if e.strip()]
        assert len(entries) >= 5, (
            f"Expected at least 5 valid properties in error, got: {entries}"
        )

    def test_special_case_keys_are_exempt(self):
        """Keys in _SPECIAL_CASE_KEYS (e.g. 'Isosurfaces') are exempt from checking."""
        from siva.filters import _SPECIAL_CASE_KEYS
        f = vtk.vtkContourFilter()
        # All special-case keys should pass validation (they're handled separately)
        exempt_key = next(iter(_SPECIAL_CASE_KEYS))  # grab one
        result = _validate_vtk_kwargs(f, {exempt_key: "whatever"}, "vtkContourFilter")
        assert result is None, (
            f"Special-case key '{exempt_key}' should be exempt from typo checking"
        )

    def test_multiple_kwargs_first_unknown_is_reported(self):
        """With one good and one bad kwarg, the bad one is caught."""
        f = vtk.vtkContourFilter()
        result = _validate_vtk_kwargs(
            f,
            {"ComputeNormals": 1, "ScalarArrays": "Temperature"},
            "vtkContourFilter",
        )
        assert result is not None
        assert "ScalarArrays" in result

    def test_sphere_source_valid_radius(self):
        """Radius is a valid property on vtkSphereSource."""
        s = vtk.vtkSphereSource()
        result = _validate_vtk_kwargs(s, {"Radius": 5.0}, "vtkSphereSource")
        assert result is None

    def test_sphere_source_typo(self):
        """Radiuss (extra 's') is not a valid property on vtkSphereSource."""
        s = vtk.vtkSphereSource()
        result = _validate_vtk_kwargs(s, {"Radiuss": 5.0}, "vtkSphereSource")
        assert result is not None
        assert "Radiuss" in result
        assert "similar:" in result.lower()


class TestGetVtkValidSetters:
    """Tests for the _get_vtk_valid_setters helper."""

    def test_returns_frozenset(self):
        f = vtk.vtkContourFilter()
        valid = _get_vtk_valid_setters(f)
        assert isinstance(valid, frozenset)

    def test_contains_known_properties(self):
        f = vtk.vtkContourFilter()
        valid = _get_vtk_valid_setters(f)
        # These are known-stable properties on vtkContourFilter
        assert "ComputeNormals" in valid
        assert "ComputeScalars" in valid
        assert "NumberOfContours" in valid

    def test_result_is_cached(self):
        """Second call should return the same frozenset object (identity check)."""
        from siva.filters import _vtk_setter_cache
        f1 = vtk.vtkContourFilter()
        f2 = vtk.vtkContourFilter()
        r1 = _get_vtk_valid_setters(f1)
        r2 = _get_vtk_valid_setters(f2)
        # Should be the same object from the cache
        assert r1 is r2


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestPropertyTypoDSLIntegration:
    """Test that typo'd kwargs surface as node-status errors in the DSL."""

    def test_typo_kwarg_produces_error_status(self):
        """A filter node with a typo'd kwarg should have error in its status."""
        b = PipelineBuilder()
        src = b.source("vtkSphereSource", Radius=1.0)
        cf = b.filter("vtkContourFilter", src, ScalarArrays="Temperature")

        _, statuses = b._build_pipeline()

        assert cf._node_id in statuses
        status = statuses[cf._node_id]
        assert status.get("status") == "error", f"Expected status=='error', got: {status}"

    def test_typo_error_message_mentions_property_name(self):
        """The error message should name the typo'd property."""
        b = PipelineBuilder()
        src = b.source("vtkSphereSource", Radius=1.0)
        cf = b.filter("vtkContourFilter", src, ScalarArrays="Temperature")

        _, statuses = b._build_pipeline()

        status = statuses[cf._node_id]
        assert "ScalarArrays" in status["message"]

    def test_typo_error_message_mentions_class_name(self):
        """The error message should name the VTK class."""
        b = PipelineBuilder()
        src = b.source("vtkSphereSource", Radius=1.0)
        cf = b.filter("vtkContourFilter", src, ScalarArrays="Temperature")

        _, statuses = b._build_pipeline()

        status = statuses[cf._node_id]
        assert "vtkContourFilter" in status["message"]

    def test_typo_error_message_includes_valid_properties(self):
        """The error message should include a 'valid:' section."""
        b = PipelineBuilder()
        src = b.source("vtkSphereSource", Radius=1.0)
        cf = b.filter("vtkContourFilter", src, ScalarArrays="Temperature")

        _, statuses = b._build_pipeline()

        status = statuses[cf._node_id]
        assert "valid:" in status["message"]
        # Structured field also available
        assert "valid" in status and len(status["valid"]) >= 5

    def test_typo_error_message_has_enough_valid_properties(self):
        """The valid-property list in the error should include at least 5 names."""
        b = PipelineBuilder()
        src = b.source("vtkSphereSource", Radius=1.0)
        cf = b.filter("vtkContourFilter", src, ScalarArrays="Temperature")

        _, statuses = b._build_pipeline()

        status = statuses[cf._node_id]
        # Check structured field
        assert len(status.get("valid", [])) >= 5, (
            f"Expected at least 5 valid properties in structured field, got: {status.get('valid')}"
        )

    def test_downstream_node_is_cascade_skipped(self):
        """A node downstream of a typo error should be cascade-skipped."""
        b = PipelineBuilder()
        src = b.source("vtkSphereSource", Radius=1.0)
        bad = b.filter("vtkContourFilter", src, ScalarArrays="Temperature")
        surf = b.filter("vtkDataSetSurfaceFilter", bad)

        _, statuses = b._build_pipeline()

        # bad node should have error status
        assert statuses[bad._node_id].get("status") == "error"
        # surf should be skipped
        surf_status = statuses[surf._node_id]
        assert surf_status.get("status") == "skipped", (
            f"Expected surf to be skipped, got: {surf_status}"
        )

    def test_sibling_of_typo_node_still_succeeds(self):
        """An independent node (no dependency on the bad node) should succeed."""
        b = PipelineBuilder()
        src = b.source("vtkSphereSource", Radius=1.0)
        bad = b.filter("vtkContourFilter", src, ScalarArrays="Temperature")
        # Independent node: not downstream of bad
        good = b.source("vtkSphereSource", Radius=2.0)

        _, statuses = b._build_pipeline()

        good_status = statuses[good._node_id]
        assert good_status.get("status") != "error", (
            f"Independent node should not be affected by sibling error, got: {good_status}"
        )

    def test_valid_kwargs_succeed(self):
        """A vtkContourFilter with valid kwargs should produce a success status."""
        b = PipelineBuilder()
        src = b.source("vtkSphereSource", Radius=1.0)
        cf = b.filter("vtkContourFilter", src, ComputeNormals=1)

        _, statuses = b._build_pipeline()

        status = statuses[cf._node_id]
        assert status.get("status") != "error", f"Valid kwargs should not produce error, got: {status}"

    def test_interpret_build_typo_integration(self):
        """interpret_build with a typo'd kwarg surfaces the error in node_statuses."""
        code = """
src = filter('vtkSphereSource', Radius=1.0)
bad = filter('vtkContourFilter', src, BadProperty='xyz')
child = filter('vtkDataSetSurfaceFilter', bad)
"""
        builder, vtk_objs, named, statuses = interpret_build(code)

        # Find bad node by kind==unknown_property and message containing BadProperty
        bad_status = None
        child_status = None
        for nid, st in statuses.items():
            if st.get("status") == "error" and "BadProperty" in st.get("message", ""):
                bad_status = st
            if st.get("class") == "vtkDataSetSurfaceFilter":
                child_status = st

        assert bad_status is not None, (
            f"Expected an error status mentioning 'BadProperty', got statuses: {statuses}"
        )
        assert "vtkContourFilter" in bad_status["message"]
        assert bad_status.get("kind") == "unknown_property", (
            f"Expected kind='unknown_property', got: {bad_status.get('kind')}"
        )
        assert child_status is not None, (
            f"Expected a status entry for vtkDataSetSurfaceFilter, got statuses: {statuses}"
        )
        assert child_status.get("status") == "skipped", (
            f"Child of failed node should be skipped, got: {child_status}"
        )
