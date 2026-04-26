"""Tests for wrapper validation surfaced as node error status (pillar 2).

Contract:
  - Validation errors in extract_region, extract_component, and line_probe
    are recorded as {"error": ...} in node_statuses, NOT raised as Python exceptions.
  - Sibling nodes in Y-shaped pipelines are unaffected by a validation failure.
  - Descendant nodes of a validation-failed node receive cascade-skip status.
  - Error messages mention the wrapper name, what was wrong, and expected form.

All tests use PipelineBuilder directly (no renderer, no Xvfb needed).
The synthetic dataset is used where a real data source is required.
"""

import os
import pytest

from vislang.dsl import PipelineBuilder, interpret_build

SYNTHETIC_VTI = os.path.join(
    os.path.dirname(__file__), "..", "datasets", "synthetic", "data", "output.vti"
)


def _ensure_synthetic():
    if not os.path.exists(SYNTHETIC_VTI):
        pytest.skip("Synthetic dataset not present — run datasets/synthetic/generate.py")


# ---------------------------------------------------------------------------
# extract_region — missing bounds
# ---------------------------------------------------------------------------

class TestExtractRegionValidation:
    """Missing 'bounds' arg in extract_region records error, does not raise."""

    def test_missing_bounds_records_error_not_raises(self):
        """extract_region without bounds should produce error status, not raise."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        region = b.extract_region(input=data, bounds=[0, 1, 0, 1, 0, 1])
        # Sabotage: remove bounds from the node's properties
        for node_id, ref in b._nodes:
            if node_id == region._node_id:
                del ref.properties["bounds"]
                break

        try:
            vtk_objs, statuses = b._build_pipeline()
        except Exception as e:
            pytest.fail(f"extract_region validation should not raise, got: {e}")

        region_status = statuses[region._node_id]
        assert "error" in region_status, (
            f"Missing bounds should produce error status, got: {region_status}"
        )

    def test_missing_bounds_error_message_quality(self):
        """Error from missing bounds mentions 'extract_region' and 'bounds'."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        region = b.extract_region(input=data, bounds=[0, 1, 0, 1, 0, 1])
        for node_id, ref in b._nodes:
            if node_id == region._node_id:
                del ref.properties["bounds"]
                break

        _, statuses = b._build_pipeline()
        msg = statuses[region._node_id]["error"]
        assert "extract_region" in msg, f"Error should mention 'extract_region': {msg}"
        assert "bounds" in msg, f"Error should mention 'bounds': {msg}"

    def test_missing_bounds_sibling_still_builds(self):
        """Y-shape: one branch missing bounds; sibling branch builds successfully."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)

        # Bad branch: extract_region with bounds removed
        bad_region = b.extract_region(input=data, bounds=[0, 1, 0, 1, 0, 1])
        for node_id, ref in b._nodes:
            if node_id == bad_region._node_id:
                del ref.properties["bounds"]
                break

        # Good branch: valid threshold on real field
        good_thresh = b.threshold(input=data, ThresholdBy="temperature",
                                  ThresholdRange=[0.0, 1000.0])

        vtk_objs, statuses = b._build_pipeline()

        assert good_thresh._node_id in vtk_objs, (
            "Good sibling should build when extract_region fails validation"
        )
        assert "error" not in statuses[good_thresh._node_id], (
            f"Good sibling should have no error: {statuses[good_thresh._node_id]}"
        )

    def test_missing_bounds_descendant_cascade_skipped(self):
        """Child of a validation-failed extract_region is cascade-skipped."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        bad_region = b.extract_region(input=data, bounds=[0, 1, 0, 1, 0, 1])
        for node_id, ref in b._nodes:
            if node_id == bad_region._node_id:
                del ref.properties["bounds"]
                break
        child = b.filter("vtkDataSetSurfaceFilter", input=bad_region)

        _, statuses = b._build_pipeline()

        child_status = statuses[child._node_id]
        assert child_status.get("status") == "skipped", (
            f"Child of failed extract_region should be skipped: {child_status}"
        )
        assert child_status.get("upstream") == bad_region._node_id, (
            f"Skipped child should reference failed extract_region: {child_status}"
        )


# ---------------------------------------------------------------------------
# extract_component — validation errors (bad field, scalar field, out-of-range)
# ---------------------------------------------------------------------------

class TestExtractComponentValidation:
    """extract_component validation errors surface as node status, not exceptions."""

    def _write_temp_vti(self, tmp_path="/tmp/test_wrapper_validation.vti"):
        """Write a small VTI with velocity (3-comp) and temperature (1-comp)."""
        import math
        import vtk
        N = 8
        img = vtk.vtkImageData()
        img.SetDimensions(N, N, N)
        img.SetOrigin(0, 0, 0)
        img.SetSpacing(1.0 / (N - 1), 1.0 / (N - 1), 1.0 / (N - 1))
        n_pts = img.GetNumberOfPoints()

        vel = vtk.vtkFloatArray()
        vel.SetName("velocity")
        vel.SetNumberOfComponents(3)
        vel.SetNumberOfTuples(n_pts)
        omega = 2.0 * math.pi
        for i in range(n_pts):
            pt = img.GetPoint(i)
            vel.SetTuple3(i, -omega * (pt[1] - 0.5), omega * (pt[0] - 0.5), 0.0)
        img.GetPointData().AddArray(vel)

        temp = vtk.vtkFloatArray()
        temp.SetName("temperature")
        temp.SetNumberOfComponents(1)
        temp.SetNumberOfTuples(n_pts)
        for i in range(n_pts):
            temp.SetValue(i, float(i))
        img.GetPointData().AddArray(temp)

        import vtk as _vtk
        writer = _vtk.vtkXMLImageDataWriter()
        writer.SetFileName(tmp_path)
        writer.SetInputData(img)
        writer.Write()
        return tmp_path

    def test_nonexistent_field_records_error(self):
        """extract_component with a missing field records error, not raises."""
        path = self._write_temp_vti()
        code = f'''
data = source("vtkXMLImageDataReader", FileName="{path}")
c = extract_component(input=data, field="NONEXISTENT", component=0, result_name="out")
'''
        try:
            builder, vtk_objs, objs, statuses = interpret_build(code)
        except Exception as e:
            pytest.fail(f"interpret_build should not raise for missing field: {e}")

        c_status = statuses.get(max(statuses.keys()))
        assert "error" in c_status, (
            f"Missing field should produce error status, got: {c_status}"
        )

    def test_nonexistent_field_error_mentions_wrapper(self):
        """Error from missing field mentions 'extract_component' and field name."""
        path = self._write_temp_vti()
        code = f'''
data = source("vtkXMLImageDataReader", FileName="{path}")
c = extract_component(input=data, field="BADFIELD", component=0, result_name="out")
'''
        _, _, _, statuses = interpret_build(code)
        # Find the extract_component node (will have the highest node id)
        ec_status = next(
            (s for s in statuses.values() if "extract_component" in s.get("class", "")),
            None
        )
        # It might be an error status (class not set) — check the error key
        error_statuses = [s for s in statuses.values() if "error" in s]
        assert error_statuses, f"Should have at least one error status: {statuses}"
        msg = error_statuses[-1]["error"]
        assert "extract_component" in msg.lower(), (
            f"Error should mention 'extract_component': {msg}"
        )
        assert "BADFIELD" in msg or "not found" in msg.lower(), (
            f"Error should mention the field name or 'not found': {msg}"
        )

    def test_scalar_field_records_error_not_raises(self):
        """extract_component on a scalar field records error, not raises."""
        path = self._write_temp_vti()
        code = f'''
data = source("vtkXMLImageDataReader", FileName="{path}")
c = extract_component(input=data, field="temperature", component=0, result_name="out")
'''
        try:
            builder, vtk_objs, objs, statuses = interpret_build(code)
        except Exception as e:
            pytest.fail(f"interpret_build should not raise for scalar field: {e}")

        error_statuses = [s for s in statuses.values() if "error" in s]
        assert error_statuses, f"Should have an error status for scalar field: {statuses}"
        msg = error_statuses[-1]["error"]
        assert "scalar" in msg.lower(), (
            f"Error should mention 'scalar': {msg}"
        )

    def test_out_of_range_component_records_error(self):
        """extract_component with out-of-range index records error, not raises."""
        path = self._write_temp_vti()
        code = f'''
data = source("vtkXMLImageDataReader", FileName="{path}")
c = extract_component(input=data, field="velocity", component=99, result_name="out")
'''
        try:
            builder, vtk_objs, objs, statuses = interpret_build(code)
        except Exception as e:
            pytest.fail(f"interpret_build should not raise for out-of-range component: {e}")

        error_statuses = [s for s in statuses.values() if "error" in s]
        assert error_statuses, f"Should have an error status: {statuses}"
        msg = error_statuses[-1]["error"]
        assert "out of range" in msg.lower(), (
            f"Error should mention 'out of range': {msg}"
        )

    def test_sibling_builds_when_extract_component_fails(self):
        """Y-shape: extract_component fails validation; sibling threshold builds."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)

        # Bad branch: extract scalar field (velocity is 3-comp, temperature is 1-comp)
        bad_ec = b.extract_component(input=data, field="temperature",
                                     component=0, result_name="out")
        # Good branch
        good_thresh = b.threshold(input=data, ThresholdBy="temperature",
                                  ThresholdRange=[0.0, 1000.0])

        vtk_objs, statuses = b._build_pipeline()

        assert good_thresh._node_id in vtk_objs, (
            "Good sibling should build when extract_component fails validation"
        )
        assert "error" in statuses[bad_ec._node_id], (
            f"Bad extract_component should have error status: {statuses[bad_ec._node_id]}"
        )

    def test_descendant_cascade_skipped_after_extract_component_fails(self):
        """Child of a validation-failed extract_component is cascade-skipped."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)

        # extract_component on scalar field — will fail validation
        bad_ec = b.extract_component(input=data, field="temperature",
                                     component=0, result_name="out")
        # child of the bad node
        child = b.filter("vtkDataSetSurfaceFilter", input=bad_ec)

        _, statuses = b._build_pipeline()

        child_status = statuses[child._node_id]
        assert child_status.get("status") == "skipped", (
            f"Child of failed extract_component should be skipped: {child_status}"
        )
        assert child_status.get("upstream") == bad_ec._node_id, (
            f"Skipped child should reference failed extract_component: {child_status}"
        )


# ---------------------------------------------------------------------------
# line_probe — missing point1/point2
# ---------------------------------------------------------------------------

class TestLineProbeValidation:
    """Missing point1/point2 in line_probe records error, not raises."""

    def test_missing_both_endpoints_records_error(self):
        """line_probe without point1 and point2 records error, not raises."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        # Both endpoints missing (default is None)
        probe = b.line_probe(input=data)

        try:
            vtk_objs, statuses = b._build_pipeline()
        except Exception as e:
            pytest.fail(f"line_probe missing endpoints should not raise: {e}")

        probe_status = statuses[probe._node_id]
        assert "error" in probe_status, (
            f"Missing endpoints should produce error status: {probe_status}"
        )

    def test_missing_point1_records_error(self):
        """line_probe without point1 records error mentioning 'point1'."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        probe = b.line_probe(input=data, point2=[1.0, 0.5, 0.5])

        _, statuses = b._build_pipeline()

        probe_status = statuses[probe._node_id]
        assert "error" in probe_status, (
            f"Missing point1 should produce error status: {probe_status}"
        )
        msg = probe_status["error"]
        assert "point1" in msg, f"Error should mention 'point1': {msg}"

    def test_missing_point2_records_error(self):
        """line_probe without point2 records error mentioning 'point2'."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        probe = b.line_probe(input=data, point1=[0.0, 0.5, 0.5])

        _, statuses = b._build_pipeline()

        probe_status = statuses[probe._node_id]
        assert "error" in probe_status, (
            f"Missing point2 should produce error status: {probe_status}"
        )
        msg = probe_status["error"]
        assert "point2" in msg, f"Error should mention 'point2': {msg}"

    def test_missing_endpoints_error_message_quality(self):
        """Error from missing endpoints mentions 'line_probe' and expected form."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        probe = b.line_probe(input=data)  # both None

        _, statuses = b._build_pipeline()

        msg = statuses[probe._node_id]["error"]
        assert "line_probe" in msg, f"Error should mention 'line_probe': {msg}"
        # Should say something about expected form
        assert "point1" in msg and "point2" in msg, (
            f"Error should mention both point1 and point2: {msg}"
        )

    def test_sibling_builds_when_line_probe_missing_endpoints(self):
        """Y-shape: line_probe fails; sibling threshold builds successfully."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)

        # Bad branch: missing endpoints
        bad_probe = b.line_probe(input=data)
        # Good branch
        good_thresh = b.threshold(input=data, ThresholdBy="temperature",
                                  ThresholdRange=[0.0, 1000.0])

        vtk_objs, statuses = b._build_pipeline()

        assert good_thresh._node_id in vtk_objs, (
            "Good sibling should build when line_probe fails validation"
        )
        assert "error" in statuses[bad_probe._node_id], (
            f"Bad line_probe should have error status: {statuses[bad_probe._node_id]}"
        )

    def test_descendant_cascade_skipped_after_line_probe_fails(self):
        """Children of failed line_probe nodes are cascade-skipped."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)

        # Missing endpoints: validation error
        bad_probe = b.line_probe(input=data)
        # Add a filter downstream of the probe — should get skipped
        child = b.filter("vtkDataSetSurfaceFilter", input=bad_probe)

        _, statuses = b._build_pipeline()

        child_status = statuses[child._node_id]
        assert child_status.get("status") == "skipped", (
            f"Child of failed line_probe should be skipped: {child_status}"
        )
        assert child_status.get("upstream") == bad_probe._node_id, (
            f"Skipped child should reference failed line_probe: {child_status}"
        )


# ---------------------------------------------------------------------------
# interpret_build contract — no Python exceptions out of validation failures
# ---------------------------------------------------------------------------

class TestInterpretBuildValidationContract:
    """interpret_build must not raise Python exceptions for user-facing validation errors."""

    def test_extract_region_missing_bounds_no_exception_from_interpret_build(self):
        """interpret_build does not raise when extract_region has no bounds."""
        _ensure_synthetic()
        # We can't easily drop bounds via DSL string; use builder directly
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        region = b.extract_region(input=data, bounds=[0, 1, 0, 1, 0, 1])
        for node_id, ref in b._nodes:
            if node_id == region._node_id:
                del ref.properties["bounds"]
                break

        try:
            vtk_objs, statuses = b._build_pipeline()
        except Exception as e:
            pytest.fail(f"_build_pipeline should not raise: {e}")

        assert "error" in statuses[region._node_id]

    def test_all_independent_errors_appear_in_one_pass(self):
        """All three wrappers can fail in a single build; all errors appear at once."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)

        # extract_region failure (missing bounds)
        bad_region = b.extract_region(input=data, bounds=[0, 1, 0, 1, 0, 1])
        for node_id, ref in b._nodes:
            if node_id == bad_region._node_id:
                del ref.properties["bounds"]
                break

        # extract_component failure (scalar field)
        bad_ec = b.extract_component(input=data, field="temperature",
                                     component=0, result_name="out")

        # line_probe failure (missing endpoints)
        bad_probe = b.line_probe(input=data)

        # This good node should still succeed
        good = b.threshold(input=data, ThresholdBy="temperature",
                           ThresholdRange=[0.0, 1000.0])

        try:
            vtk_objs, statuses = b._build_pipeline()
        except Exception as e:
            pytest.fail(f"Build should not raise even with 3 failing wrappers: {e}")

        # All three failing wrappers have error status
        assert "error" in statuses[bad_region._node_id], "extract_region should have error"
        assert "error" in statuses[bad_ec._node_id], "extract_component should have error"
        assert "error" in statuses[bad_probe._node_id], "line_probe should have error"

        # Good node still built
        assert good._node_id in vtk_objs, "Independent good node should build"
