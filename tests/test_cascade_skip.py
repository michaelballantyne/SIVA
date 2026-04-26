"""Tests for cascade-skip behavior in _build_pipeline.

Contract enforced:
  - A node either succeeds (vtk_output is set) or fails (status has "error").
  - Descendants of any failed node are skipped automatically.
  - Skipped status: {"status": "skipped", "upstream": <failed_node_id>, "class": ...}
  - No AttributeError or VTK crash propagates from cascade.
  - Independent siblings of a failed branch still succeed.

Upstream tracking is *immediate-parent*: a transitive skip records the
id of the immediately-preceding skipped/failed node, not the root failure.

All tests use PipelineBuilder directly (no renderer, no Xvfb needed).
The synthetic dataset is used for tests that require a real data source.
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
# 1. Direct child of a failed node is skipped
# ---------------------------------------------------------------------------

class TestDirectChildSkipped:
    """When a threshold fails (non-existent field), its direct child is skipped."""

    def test_direct_child_status_is_skipped(self):
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        # Use a field name that doesn't exist — threshold will fail
        bad_thresh = b.threshold(input=data, ThresholdBy="NONEXISTENT_FIELD_XYZ",
                                 ThresholdRange=[0.0, 1.0])
        surf = b.filter("vtkDataSetSurfaceFilter", input=bad_thresh)

        _, statuses = b._build_pipeline()

        assert statuses[surf._node_id]["status"] == "skipped", (
            f"Direct child of failed node should be 'skipped', got: {statuses[surf._node_id]}"
        )

    def test_direct_child_upstream_references_failed_node(self):
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        bad_thresh = b.threshold(input=data, ThresholdBy="NONEXISTENT_FIELD_XYZ",
                                 ThresholdRange=[0.0, 1.0])
        surf = b.filter("vtkDataSetSurfaceFilter", input=bad_thresh)

        _, statuses = b._build_pipeline()

        surf_status = statuses[surf._node_id]
        assert surf_status.get("upstream") == bad_thresh._node_id, (
            f"skipped status should reference failed node id={bad_thresh._node_id}, "
            f"got upstream={surf_status.get('upstream')!r}"
        )

    def test_failed_node_has_error_key(self):
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        bad_thresh = b.threshold(input=data, ThresholdBy="NONEXISTENT_FIELD_XYZ",
                                 ThresholdRange=[0.0, 1.0])
        b.filter("vtkDataSetSurfaceFilter", input=bad_thresh)

        _, statuses = b._build_pipeline()

        thresh_status = statuses[bad_thresh._node_id]
        assert "error" in thresh_status, (
            f"Failed threshold should have 'error' key, got: {thresh_status}"
        )


# ---------------------------------------------------------------------------
# 2. Transitive descendants (4-node chain, node 2 fails)
# ---------------------------------------------------------------------------

class TestTransitiveDescendantsSkipped:
    """In a 4-node chain A->B->C->D, if B fails, both C and D are skipped."""

    def test_chain_descendants_all_skipped(self):
        _ensure_synthetic()
        b = PipelineBuilder()
        # A: real source
        node_a = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        # B: bad threshold — will fail
        node_b = b.threshold(input=node_a, ThresholdBy="NONEXISTENT_FIELD_XYZ",
                              ThresholdRange=[0.0, 1.0])
        # C: downstream of B
        node_c = b.filter("vtkDataSetSurfaceFilter", input=node_b)
        # D: downstream of C
        node_d = b.filter("vtkSmoothPolyDataFilter", input=node_c)

        _, statuses = b._build_pipeline()

        for node, label in [(node_c, "C"), (node_d, "D")]:
            s = statuses[node._node_id]
            assert s.get("status") == "skipped", (
                f"Node {label} should be 'skipped', got: {s}"
            )

    def test_transitive_child_upstream_is_immediate_parent(self):
        """Transitive skip records the immediate parent's id, not the root failure."""
        _ensure_synthetic()
        b = PipelineBuilder()
        node_a = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        node_b = b.threshold(input=node_a, ThresholdBy="NONEXISTENT_FIELD_XYZ",
                              ThresholdRange=[0.0, 1.0])
        node_c = b.filter("vtkDataSetSurfaceFilter", input=node_b)
        node_d = b.filter("vtkSmoothPolyDataFilter", input=node_c)

        _, statuses = b._build_pipeline()

        # C's upstream is B (the direct failed node)
        assert statuses[node_c._node_id]["upstream"] == node_b._node_id, (
            "C's upstream should reference B (the direct failure)"
        )
        # D's upstream is C (the immediate skipped parent)
        assert statuses[node_d._node_id]["upstream"] == node_c._node_id, (
            "D's upstream should reference C (its immediate skipped parent)"
        )


# ---------------------------------------------------------------------------
# 3. Independent siblings still succeed
# ---------------------------------------------------------------------------

class TestIndependentSiblingsSucceed:
    """Y-shaped pipeline: one branch fails, sibling branch builds successfully."""

    def test_good_branch_builds_when_sibling_fails(self):
        _ensure_synthetic()
        b = PipelineBuilder()
        # Shared source
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)

        # Bad branch
        bad_thresh = b.threshold(input=data, ThresholdBy="NONEXISTENT_FIELD_XYZ",
                                 ThresholdRange=[0.0, 1.0])
        bad_surf = b.filter("vtkDataSetSurfaceFilter", input=bad_thresh)

        # Good branch — uses a real field ("temperature")
        good_thresh = b.threshold(input=data, ThresholdBy="temperature",
                                  ThresholdRange=[0.0, 1000.0])

        vtk_objs, statuses = b._build_pipeline()

        # Good branch node must be in vtk_objects (built successfully)
        assert good_thresh._node_id in vtk_objs, (
            "Good branch should build successfully when sibling fails"
        )
        # Sanity: bad branch is indeed marked skipped
        assert statuses[bad_surf._node_id].get("status") == "skipped"

    def test_good_branch_has_no_error_in_status(self):
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)

        b.threshold(input=data, ThresholdBy="NONEXISTENT_FIELD_XYZ",
                    ThresholdRange=[0.0, 1.0])
        good_thresh = b.threshold(input=data, ThresholdBy="temperature",
                                  ThresholdRange=[0.0, 1000.0])

        _, statuses = b._build_pipeline()

        good_status = statuses[good_thresh._node_id]
        assert "error" not in good_status, (
            f"Good sibling should have no error, got: {good_status}"
        )
        assert good_status.get("status") != "skipped", (
            "Good sibling should not be marked skipped"
        )


# ---------------------------------------------------------------------------
# 4. No AttributeError from extract_region / extract_component on bad upstream
# ---------------------------------------------------------------------------

class TestNoCrashOnExtractNodes:
    """extract_region and extract_component on a None/skipped upstream must not crash."""

    def test_extract_region_downstream_of_failed_node_no_exception(self):
        """Cascade into extract_region pseudo-node must not raise AttributeError.

        Uses a threshold on a non-existent field (which records an error status)
        as the failing upstream node, then extract_region is downstream of it.
        A non-existent-file source only produces empty output (warning), not an
        error status, so a threshold failure is the reliable way to create a
        truly-failed upstream node.
        """
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        # Bad threshold — will record "error" in status
        bad_thresh = b.threshold(input=data, ThresholdBy="NONEXISTENT_FIELD_XYZ",
                                 ThresholdRange=[0.0, 1.0])
        # extract_region downstream — should be cascade-skipped, not crash
        region = b.extract_region(input=bad_thresh, bounds=[0, 1, 0, 1, 0, 1])

        try:
            vtk_objs, statuses = b._build_pipeline()
        except AttributeError as e:
            pytest.fail(f"AttributeError leaked from extract_region cascade: {e}")
        except Exception as e:
            pytest.fail(f"Unexpected exception from extract_region cascade: {e}")

        assert statuses[region._node_id].get("status") == "skipped", (
            f"extract_region downstream of failure should be 'skipped', "
            f"got: {statuses[region._node_id]}"
        )

    def test_extract_component_downstream_of_failed_node_no_exception(self):
        """Cascade into extract_component pseudo-node must not raise AttributeError.

        Same approach as extract_region: uses a bad threshold as upstream failure.
        """
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        bad_thresh = b.threshold(input=data, ThresholdBy="NONEXISTENT_FIELD_XYZ",
                                 ThresholdRange=[0.0, 1.0])
        comp = b.extract_component(input=bad_thresh, field="temperature",
                                   component=0, result_name="temp_x")

        try:
            vtk_objs, statuses = b._build_pipeline()
        except AttributeError as e:
            pytest.fail(f"AttributeError leaked from extract_component cascade: {e}")
        except Exception as e:
            pytest.fail(f"Unexpected exception from extract_component cascade: {e}")

        assert statuses[comp._node_id].get("status") == "skipped", (
            f"extract_component downstream of failure should be 'skipped', "
            f"got: {statuses[comp._node_id]}"
        )

    def test_extract_region_missing_bounds_records_error(self):
        """extract_region with missing 'bounds' should record an error, not raise."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        # Force a missing-bounds scenario by constructing NodeRef manually
        # We patch via the internal node to drop the 'bounds' key
        region = b.extract_region(input=data, bounds=[0, 1, 0, 1, 0, 1])
        # Sabotage: remove bounds from the node's properties
        for node_id, ref in b._nodes:
            if node_id == region._node_id:
                del ref.properties["bounds"]
                break

        try:
            vtk_objs, statuses = b._build_pipeline()
        except Exception as e:
            pytest.fail(f"Exception should be caught internally, not raised: {e}")

        region_status = statuses[region._node_id]
        assert "error" in region_status, (
            f"Missing bounds should produce an error status, got: {region_status}"
        )

    def test_extract_component_on_none_input_records_error_not_crash(self):
        """extract_component with no valid upstream records error or skip, no crash.

        Note: vtkXMLImageDataReader with a non-existent file produces empty
        output with a warning (not an error status), so extract_component will
        attempt to run and fail internally with an error — not a cascade-skip.
        Both outcomes (error or skipped) are acceptable; what must not happen
        is an unhandled AttributeError/exception propagating out.
        """
        b = PipelineBuilder()
        # Non-existent file: source succeeds (empty output, warning), not cascaded
        bad_source = b.source("vtkXMLImageDataReader", FileName="/nonexistent.vti")
        comp = b.extract_component(input=bad_source, field="temperature",
                                   component=0, result_name="temp_x")

        # Should not raise
        try:
            vtk_objs, statuses = b._build_pipeline()
        except Exception as e:
            pytest.fail(f"Should not raise, got: {e}")

        # Either skipped (cascade) or has error — not a crash
        comp_status = statuses[comp._node_id]
        assert "error" in comp_status or comp_status.get("status") == "skipped", (
            f"Should have error or skipped status, got: {comp_status}"
        )


# ---------------------------------------------------------------------------
# 5. Status report from run_pipeline is readable
# ---------------------------------------------------------------------------

class TestStatusReportReadable:
    """The node_statuses dict and formatted report clearly show skipped chains."""

    def test_skipped_status_has_upstream_key(self):
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        bad = b.threshold(input=data, ThresholdBy="NONEXISTENT_FIELD_XYZ",
                          ThresholdRange=[0.0, 1.0])
        child = b.filter("vtkDataSetSurfaceFilter", input=bad)

        _, statuses = b._build_pipeline()

        child_status = statuses[child._node_id]
        assert "upstream" in child_status, (
            f"Skipped node status must have 'upstream' key, got: {child_status}"
        )

    def test_skipped_status_dict_has_expected_shape(self):
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        bad = b.threshold(input=data, ThresholdBy="NONEXISTENT_FIELD_XYZ",
                          ThresholdRange=[0.0, 1.0])
        child = b.filter("vtkDataSetSurfaceFilter", input=bad)

        _, statuses = b._build_pipeline()

        child_status = statuses[child._node_id]
        # Must have at minimum: status, upstream, class
        assert child_status.get("status") == "skipped"
        assert child_status.get("upstream") == bad._node_id
        assert "class" in child_status

    def test_interpret_build_report_shows_skipped_with_upstream(self):
        """interpret_build returns statuses readable enough for an agent to trace chain."""
        _ensure_synthetic()
        code = f"""\
data = source("vtkXMLImageDataReader", FileName="{SYNTHETIC_VTI}")
bad = threshold(input=data, ThresholdBy="NONEXISTENT_FIELD_XYZ", ThresholdRange=[0.0, 1.0])
surf = filter("vtkDataSetSurfaceFilter", input=bad)
"""
        builder, vtk_objs, vtk_objs_by_name, statuses = interpret_build(code)

        # Find the surf node status
        surf_status = None
        for nid, s in statuses.items():
            if s.get("status") == "skipped":
                surf_status = s
                break

        assert surf_status is not None, (
            "At least one skipped node should appear in statuses"
        )
        assert "upstream" in surf_status, (
            "Skipped node in interpret_build result must carry 'upstream' key"
        )

    def test_skipped_chain_readable_report_format(self):
        """Manually format the node_statuses the way server.py does and verify 'skipped (upstream:...)' appears."""
        _ensure_synthetic()
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=SYNTHETIC_VTI)
        bad = b.threshold(input=data, ThresholdBy="NONEXISTENT_FIELD_XYZ",
                          ThresholdRange=[0.0, 1.0])
        child = b.filter("vtkDataSetSurfaceFilter", input=bad)

        _, statuses = b._build_pipeline()

        # Replicate the server.py report-formatting logic
        report_lines = []
        for node_id, status in sorted(statuses.items()):
            name = status.get("name", f"node_{node_id}")
            if "error" in status:
                report_lines.append(f"  {name}: ERROR - {status['error']}")
            elif status.get("status") == "skipped":
                upstream_id = status.get("upstream", "?")
                upstream_name = statuses.get(upstream_id, {}).get("name", f"node_{upstream_id}")
                report_lines.append(f"  {name}: skipped (upstream: {upstream_name})")
            else:
                report_lines.append(f"  {name}: ok")

        report = "\n".join(report_lines)
        assert "skipped (upstream:" in report, (
            f"Report should contain 'skipped (upstream:...)' line, got:\n{report}"
        )
