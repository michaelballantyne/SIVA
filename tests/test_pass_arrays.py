"""Tests for vtkPassArrays array selection via PointDataArrays/CellDataArrays.

vtkPassArrays selects which arrays reach the output, but VTK exposes that
selection only through ``AddPointDataArray(name)``/``AddCellDataArray(name)``
calls -- there is no ``SetPointDataArrays(list)`` setter for the generic
``**props`` model to dispatch to. ``PointDataArrays``/``CellDataArrays`` are
special-cased in ``siva.filters._apply_properties`` (and listed in
``SIVA_FILTER_EXTRAS["vtkPassArrays"]``) to expand a list of
names into the repeated ``Add*DataArray`` calls vtkPassArrays actually wants.
"""

import vtk
import pytest

from siva.filters import create_vtk_filter, _apply_properties, SIVA_FILTER_EXTRAS
from siva.dsl import PipelineBuilder, _freeze_spec
from siva.compute import compute as _compute_spec
from siva._vtk_introspect import get_algorithm_output


def _bp(builder, cache=None):
    result = _compute_spec(_freeze_spec(builder), cache=cache)
    return result.outputs, result.statuses


def _point_array_names(dataset):
    pd = dataset.GetPointData()
    return [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]


def _cell_array_names(dataset):
    cd = dataset.GetCellData()
    return [cd.GetArrayName(i) for i in range(cd.GetNumberOfArrays())]


def _make_polydata_with_arrays():
    """A tiny two-point, one-cell polydata carrying two point arrays and
    two cell arrays."""
    pd = vtk.vtkPolyData()

    pts = vtk.vtkPoints()
    pts.InsertNextPoint(0, 0, 0)
    pts.InsertNextPoint(1, 0, 0)
    pd.SetPoints(pts)

    line = vtk.vtkCellArray()
    line.InsertNextCell(2)
    line.InsertCellPoint(0)
    line.InsertCellPoint(1)
    pd.SetLines(line)

    def _float_array(name, values):
        arr = vtk.vtkFloatArray()
        arr.SetName(name)
        arr.SetNumberOfTuples(len(values))
        for i, v in enumerate(values):
            arr.SetValue(i, v)
        return arr

    pd.GetPointData().AddArray(_float_array("temperature", [1.0, 2.0]))
    pd.GetPointData().AddArray(_float_array("pressure", [3.0, 4.0]))
    pd.GetCellData().AddArray(_float_array("density", [5.0]))
    pd.GetCellData().AddArray(_float_array("velocity_mag", [6.0]))

    return pd


class TestSpecialCaseKeys:
    def test_keys_registered_as_special_case(self):
        assert "PointDataArrays" in SIVA_FILTER_EXTRAS["vtkPassArrays"]
        assert "CellDataArrays" in SIVA_FILTER_EXTRAS["vtkPassArrays"]


class TestPointDataArrays:
    def test_keeps_only_named_point_array(self):
        pd = _make_polydata_with_arrays()
        filt, diag = create_vtk_filter(
            "vtkPassArrays", input_algorithm=pd, PointDataArrays=["temperature"]
        )
        filt.Update()
        out = filt.GetOutput()
        assert _point_array_names(out) == ["temperature"]
        assert diag["status"] == "ok"

    def test_keeps_only_named_point_array_docstring_example(self):
        """Mirrors the documented example in siva/dsl.py and siva/server.py:
        filter("vtkPassArrays", input=data, PointDataArrays=["temperature", "pressure"]).
        """
        pd = _make_polydata_with_arrays()
        filt, _diag = create_vtk_filter(
            "vtkPassArrays",
            input_algorithm=pd,
            PointDataArrays=["temperature", "pressure"],
        )
        filt.Update()
        out = filt.GetOutput()
        assert sorted(_point_array_names(out)) == ["pressure", "temperature"]

    def test_rebuild_does_not_accumulate_on_cached_filter_object(self):
        """A cached vtkPassArrays instance re-configured with a smaller
        PointDataArrays selection must not still emit the old, larger
        selection -- _apply_properties must clear before re-adding."""
        pd = _make_polydata_with_arrays()
        vtk_obj = vtk.vtkPassArrays()
        vtk_obj.SetInputData(pd)

        _apply_properties(
            vtk_obj, "vtkPassArrays",
            {"PointDataArrays": ["temperature", "pressure"]},
        )
        vtk_obj.Update()
        assert sorted(_point_array_names(vtk_obj.GetOutput())) == [
            "pressure", "temperature",
        ]

        # Re-apply with a narrower selection, as would happen on a pipeline
        # rebuild against the same cached filter object.
        _apply_properties(vtk_obj, "vtkPassArrays", {"PointDataArrays": ["pressure"]})
        vtk_obj.Update()
        assert _point_array_names(vtk_obj.GetOutput()) == ["pressure"]


class TestCellDataArrays:
    def test_keeps_only_named_cell_array(self):
        pd = _make_polydata_with_arrays()
        filt, diag = create_vtk_filter(
            "vtkPassArrays", input_algorithm=pd, CellDataArrays=["density"]
        )
        filt.Update()
        out = filt.GetOutput()
        assert _cell_array_names(out) == ["density"]
        assert diag["status"] == "ok"

    def test_rebuild_does_not_accumulate_on_cached_filter_object(self):
        pd = _make_polydata_with_arrays()
        vtk_obj = vtk.vtkPassArrays()
        vtk_obj.SetInputData(pd)

        _apply_properties(
            vtk_obj, "vtkPassArrays",
            {"CellDataArrays": ["density", "velocity_mag"]},
        )
        vtk_obj.Update()
        assert sorted(_cell_array_names(vtk_obj.GetOutput())) == [
            "density", "velocity_mag",
        ]

        _apply_properties(vtk_obj, "vtkPassArrays", {"CellDataArrays": ["density"]})
        vtk_obj.Update()
        assert _cell_array_names(vtk_obj.GetOutput()) == ["density"]


class TestPointAndCellDataArraysCombined:
    def test_both_selections_apply_together(self):
        pd = _make_polydata_with_arrays()
        filt, diag = create_vtk_filter(
            "vtkPassArrays",
            input_algorithm=pd,
            PointDataArrays=["temperature"],
            CellDataArrays=["velocity_mag"],
        )
        filt.Update()
        out = filt.GetOutput()
        assert _point_array_names(out) == ["temperature"]
        assert _cell_array_names(out) == ["velocity_mag"]
        assert diag["status"] == "ok"


class TestDslIntegration:
    def test_filter_pass_arrays_through_pipeline_builder(self):
        """End-to-end through PipelineBuilder/compute, mirroring the
        filter("vtkPassArrays", ...) DSL form documented in dsl.py/server.py."""
        b = PipelineBuilder()
        src = b.source("vtkSphereSource")  # point array: "Normals"
        elev = b.elevation(input=src, low_point=(0, 0, 0), high_point=(0, 0, 1))
        trimmed = b.filter("vtkPassArrays", input=elev, PointDataArrays=["Elevation"])

        outputs, statuses = _bp(b)
        assert statuses[trimmed.node_id]["status"] == "ok", statuses[trimmed.node_id]

        vtk_obj = outputs[trimmed.node_id]
        data = get_algorithm_output(vtk_obj)
        assert _point_array_names(data) == ["Elevation"]
