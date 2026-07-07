"""Tests for the structured per-node status schema defined in siva/diagnostics.py.

For each status kind, this module builds a small pipeline that triggers it and
asserts the status dict has the correct shape:
  - "status" key present with correct value
  - "class" key always present
  - "kind" key present on non-ok statuses
  - "message" key present on non-ok statuses
  - Kind-specific structured fields present
"""

import pytest
import vtk

from siva import diagnostics as _diag
from siva.compute import evaluate
from siva.dsl import PipelineBuilder

# --- test helper: freeze a builder and run the compute phase (replaces the
# former PipelineBuilder._build_pipeline, which now lives in siva.compute) ---
from siva.compute import compute as _compute_spec
from siva.dsl import _freeze_spec as _freeze_spec_for_test


def _bp(_builder, cache=None):
    _r = _compute_spec(_freeze_spec_for_test(_builder), cache=cache)
    return _r.outputs, _r.statuses

from siva.filters import create_vtk_filter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_image_alg(dims=(8, 8, 8), field_name="temperature",
                    field_range=(0.0, 100.0)):
    """Return a vtkTrivialProducer wrapping a small ImageData with one scalar."""
    import numpy as np
    from vtk.util.numpy_support import numpy_to_vtk

    img = vtk.vtkImageData()
    img.SetDimensions(*dims)
    img.SetOrigin(0.0, 0.0, 0.0)
    img.SetSpacing(1.0, 1.0, 1.0)
    n = img.GetNumberOfPoints()
    vals = np.linspace(field_range[0], field_range[1], n)
    arr = numpy_to_vtk(vals.astype(np.float64))
    arr.SetName(field_name)
    img.GetPointData().AddArray(arr)
    img.GetPointData().SetActiveScalars(field_name)

    producer = vtk.vtkTrivialProducer()
    producer.SetOutput(img)
    producer.Update()
    return producer


# ---------------------------------------------------------------------------
# diagnostics.ok helper
# ---------------------------------------------------------------------------

class TestOkHelper:
    def test_ok_has_status_ok(self):
        s = _diag.ok("vtkContourFilter", num_points=100)
        assert s["status"] == "ok"

    def test_ok_has_class(self):
        s = _diag.ok("vtkContourFilter", num_points=100)
        assert s["class"] == "vtkContourFilter"

    def test_ok_no_kind_or_message(self):
        s = _diag.ok("vtkContourFilter")
        assert "kind" not in s
        assert "message" not in s

    def test_ok_extra_fields_forwarded(self):
        s = _diag.ok("vtkFoo", num_points=42, bounds=[0, 1, 0, 1, 0, 1])
        assert s["num_points"] == 42
        assert s["bounds"] == [0, 1, 0, 1, 0, 1]


# ---------------------------------------------------------------------------
# diagnostics.error helper
# ---------------------------------------------------------------------------

class TestErrorHelper:
    def test_error_has_status_error(self):
        s = _diag.error("vtkFoo", _diag.KIND_OTHER, "bad thing")
        assert s["status"] == "error"

    def test_error_has_class(self):
        s = _diag.error("vtkFoo", _diag.KIND_OTHER, "bad thing")
        assert s["class"] == "vtkFoo"

    def test_error_has_kind(self):
        s = _diag.error("vtkFoo", _diag.KIND_UNKNOWN_PROPERTY, "msg")
        assert s["kind"] == _diag.KIND_UNKNOWN_PROPERTY

    def test_error_has_message(self):
        s = _diag.error("vtkFoo", _diag.KIND_OTHER, "bad thing happened")
        assert s["message"] == "bad thing happened"

    def test_error_structured_fields_forwarded(self):
        s = _diag.error("vtkFoo", _diag.KIND_UNKNOWN_PROPERTY, "msg",
                        property="Foo", vtk_class="vtkFoo",
                        similar=["Bar"], valid=["Baz"])
        assert s["property"] == "Foo"
        assert s["similar"] == ["Bar"]
        assert s["valid"] == ["Baz"]


# ---------------------------------------------------------------------------
# diagnostics.skipped helper
# ---------------------------------------------------------------------------

class TestSkippedHelper:
    def test_skipped_has_status_skipped(self):
        s = _diag.skipped("vtkFoo", upstream_id=3)
        assert s["status"] == "skipped"

    def test_skipped_has_class(self):
        s = _diag.skipped("vtkFoo", upstream_id=3)
        assert s["class"] == "vtkFoo"

    def test_skipped_has_kind_upstream_failed(self):
        s = _diag.skipped("vtkFoo", upstream_id=3)
        assert s["kind"] == _diag.KIND_UPSTREAM_FAILED

    def test_skipped_has_upstream(self):
        s = _diag.skipped("vtkFoo", upstream_id=5)
        assert s["upstream"] == 5

    def test_skipped_has_message(self):
        s = _diag.skipped("vtkFoo", upstream_id=3)
        assert "message" in s
        assert "3" in s["message"]

    def test_skipped_custom_message(self):
        s = _diag.skipped("vtkFoo", upstream_id=3, message="custom msg")
        assert s["message"] == "custom msg"


# ---------------------------------------------------------------------------
# diagnostics.warning helper
# ---------------------------------------------------------------------------

class TestWarningHelper:
    def test_warning_has_status_warning(self):
        s = _diag.warning("vtkClipDataSet", _diag.KIND_EMPTY_OUTPUT, "empty")
        assert s["status"] == "warning"

    def test_warning_has_class(self):
        s = _diag.warning("vtkClipDataSet", _diag.KIND_EMPTY_OUTPUT, "empty")
        assert s["class"] == "vtkClipDataSet"

    def test_warning_has_kind(self):
        s = _diag.warning("vtkClipDataSet", _diag.KIND_EMPTY_OUTPUT, "empty")
        assert s["kind"] == _diag.KIND_EMPTY_OUTPUT

    def test_warning_has_message(self):
        s = _diag.warning("vtkClipDataSet", _diag.KIND_EMPTY_OUTPUT, "empty output")
        assert s["message"] == "empty output"

    def test_warning_extra_fields_forwarded(self):
        s = _diag.warning("vtkFoo", _diag.KIND_FIELD_OUT_OF_RANGE, "msg",
                          field="temperature", range=[0.0, 100.0], value=200.0,
                          param="ClipValue")
        assert s["field"] == "temperature"
        assert s["range"] == [0.0, 100.0]
        assert s["value"] == 200.0


# ---------------------------------------------------------------------------
# KIND_UNKNOWN_PROPERTY — triggered by a typo'd property name
# ---------------------------------------------------------------------------

class TestUnknownPropertyKind:
    def test_unknown_property_shape(self):
        """vtkContourFilter with typo'd property name -> error with kind=unknown_property."""
        b = PipelineBuilder()
        src = b.source("vtkSphereSource", Radius=1.0)
        cf = b.filter("vtkContourFilter", src, ScalarArrays="Temperature")
        _, statuses = _bp(b)

        s = statuses[cf.node_id]
        assert s["status"] == "error"
        assert s["kind"] == _diag.KIND_UNKNOWN_PROPERTY
        assert "message" in s
        assert "class" in s
        # Structured fields
        assert s["property"] == "ScalarArrays"
        assert "vtkContourFilter" in s["vtk_class"]
        assert isinstance(s["similar"], list)
        assert isinstance(s["valid"], list)
        assert len(s["valid"]) >= 5


# ---------------------------------------------------------------------------
# KIND_MISSING_REQUIRED_ARG — triggered by missing bounds in extract_region
# ---------------------------------------------------------------------------

class TestMissingRequiredArgKind:
    def test_extract_region_missing_bounds_shape(self, synthetic_vti_path):
        """extract_region without bounds -> error with kind=missing_required_arg."""
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=synthetic_vti_path)
        region = b.extract_region(input=data, bounds=[0, 1, 0, 1, 0, 1])
        # Remove bounds to trigger the error
        for ref in b._nodes:
            if ref.node_id == region.node_id:
                del ref.properties["bounds"]
                break

        _, statuses = _bp(b)
        s = statuses[region.node_id]

        assert s["status"] == "error"
        assert s["kind"] == _diag.KIND_MISSING_REQUIRED_ARG
        assert "message" in s
        assert "class" in s
        assert s["arg"] == "bounds"
        assert "expected" in s

    def test_line_probe_missing_endpoints_shape(self, synthetic_vti_path):
        """line_probe without point1/point2 -> error with kind=missing_required_arg."""
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=synthetic_vti_path)
        probe = b.line_probe(input=data)  # both None
        _, statuses = _bp(b)

        s = statuses[probe.node_id]
        assert s["status"] == "error"
        assert s["kind"] == _diag.KIND_MISSING_REQUIRED_ARG
        assert "message" in s
        assert "class" in s
        assert "arg" in s
        assert "expected" in s


# ---------------------------------------------------------------------------
# KIND_UPSTREAM_FAILED — triggered by cascade skip
# ---------------------------------------------------------------------------

class TestUpstreamFailedKind:
    def test_cascade_skip_shape(self, synthetic_vti_path):
        """Child of a failed node -> skipped with kind=upstream_failed."""
        b = PipelineBuilder()
        data = b.source("vtkXMLImageDataReader", FileName=synthetic_vti_path)
        bad = b.threshold(input=data, ThresholdBy="NONEXISTENT_XYZ",
                          ThresholdRange=[0.0, 1.0])
        child = b.filter("vtkDataSetSurfaceFilter", input=bad)

        _, statuses = _bp(b)
        s = statuses[child.node_id]

        assert s["status"] == "skipped"
        assert s["kind"] == _diag.KIND_UPSTREAM_FAILED
        assert s["upstream"] == bad.node_id
        assert "message" in s
        assert "class" in s


# ---------------------------------------------------------------------------
# KIND_EMPTY_OUTPUT — triggered by out-of-range clip value
# ---------------------------------------------------------------------------

class TestEmptyOutputKind:
    def test_clip_out_of_range_shape(self):
        """vtkClipDataSet with value above field max -> warning with kind=empty_output."""
        alg = _make_image_alg(field_range=(0.0, 100.0))
        _, s = create_vtk_filter("vtkClipDataSet", alg, Value=999.0)

        assert s["status"] == "warning"
        assert s["kind"] == _diag.KIND_EMPTY_OUTPUT
        assert "message" in s
        assert "class" in s
        assert s["class"] == "vtkClipDataSet"
        # num_points and num_cells still present
        assert "num_points" in s
        assert s["num_points"] == 0

    def test_ok_filter_has_status_ok(self):
        """vtkClipDataSet with mid-range value -> ok status."""
        alg = _make_image_alg(field_range=(0.0, 100.0))
        _, s = create_vtk_filter("vtkClipDataSet", alg, Value=50.0)

        assert s["status"] == "ok"
        assert "class" in s
        assert s["num_points"] > 0


# ---------------------------------------------------------------------------
# KIND_OTHER — generic error (e.g. runtime exception from VTK)
# ---------------------------------------------------------------------------

class TestKindOther:
    def test_kind_other_shape(self):
        """_diag.error with KIND_OTHER has the required fields."""
        s = _diag.error("vtkFoo", _diag.KIND_OTHER, "Something went wrong")
        assert s["status"] == "error"
        assert s["kind"] == _diag.KIND_OTHER
        assert s["message"] == "Something went wrong"
        assert s["class"] == "vtkFoo"
