"""Tests for pre-execution field name validation in create_vtk_filter.

Verifies that field name typos are caught BEFORE the expensive Update() call,
using the _validate_field_names / _get_output_array_names infrastructure added
to siva/filters.py.
"""

import os
import sys
import unittest

import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.filters import (
    create_vtk_filter,
    _get_output_array_names,
    _validate_field_names,
    _FIELD_NAME_PROPERTIES,
    clear_reader_cache,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_image_source(dims=(8, 8, 8), field_name="temperature",
                       field_range=(0.0, 100.0)):
    """Return a vtkTrivialProducer wrapping a vtkImageData with one scalar field."""
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


def _make_cell_source(field_name="pressure"):
    """Return a source with a cell-data array."""
    img = vtk.vtkImageData()
    img.SetDimensions(5, 5, 5)
    img.SetOrigin(0.0, 0.0, 0.0)
    img.SetSpacing(1.0, 1.0, 1.0)
    n = img.GetNumberOfCells()
    vals = np.linspace(0.0, 1.0, n)
    arr = numpy_to_vtk(vals.astype(np.float64))
    arr.SetName(field_name)
    img.GetCellData().AddArray(arr)

    producer = vtk.vtkTrivialProducer()
    producer.SetOutput(img)
    producer.Update()
    return producer


# ---------------------------------------------------------------------------
# _get_output_array_names unit tests
# ---------------------------------------------------------------------------

class TestGetOutputArrayNames(unittest.TestCase):
    """Unit tests for the _get_output_array_names helper."""

    def test_returns_point_arrays(self):
        src = _make_image_source(field_name="temperature")
        point_arrays, cell_arrays = _get_output_array_names(src)
        self.assertIn("temperature", point_arrays)
        self.assertEqual(cell_arrays, [])

    def test_returns_cell_arrays(self):
        src = _make_cell_source(field_name="pressure")
        point_arrays, cell_arrays = _get_output_array_names(src)
        self.assertEqual(point_arrays, [])
        self.assertIn("pressure", cell_arrays)

    def test_none_input_returns_empty(self):
        point_arrays, cell_arrays = _get_output_array_names(None)
        self.assertEqual(point_arrays, [])
        self.assertEqual(cell_arrays, [])

    def test_direct_dataset_input(self):
        img = vtk.vtkImageData()
        img.SetDimensions(3, 3, 3)
        n = img.GetNumberOfPoints()
        arr = numpy_to_vtk(np.zeros(n))
        arr.SetName("density")
        img.GetPointData().AddArray(arr)
        point_arrays, cell_arrays = _get_output_array_names(img)
        self.assertIn("density", point_arrays)

    def test_updates_algorithm_before_querying(self):
        """_get_output_array_names calls Update() so un-executed sources work."""
        src = vtk.vtkSphereSource()
        # Sphere source has no data arrays, but it should not crash
        point_arrays, cell_arrays = _get_output_array_names(src)
        # Just verify it didn't raise and returned lists
        self.assertIsInstance(point_arrays, list)
        self.assertIsInstance(cell_arrays, list)


# ---------------------------------------------------------------------------
# _validate_field_names unit tests
# ---------------------------------------------------------------------------

class TestValidateFieldNames(unittest.TestCase):
    """Unit tests for _validate_field_names before Update() is called."""

    def _src(self, field="temperature"):
        return _make_image_source(field_name=field)

    def test_no_error_for_valid_contour_field(self):
        src = self._src("temperature")
        # Should not raise
        _validate_field_names(
            "vtkContourFilter",
            {"ContourBy": "temperature", "Isosurfaces": [50.0]},
            src,
        )

    def test_raises_for_typo_in_contour_field(self):
        src = self._src("temperature")
        with self.assertRaises(ValueError) as ctx:
            _validate_field_names(
                "vtkContourFilter",
                {"ContourBy": "temperatur"},  # typo: missing 'e'
                src,
            )
        err = str(ctx.exception)
        self.assertIn("temperatur", err)
        self.assertIn("temperature", err)  # available field listed

    def test_raises_for_typo_in_threshold_field(self):
        src = self._src("velocity")
        with self.assertRaises(ValueError) as ctx:
            _validate_field_names(
                "vtkThreshold",
                {"ThresholdBy": "velosity"},  # typo
                src,
            )
        err = str(ctx.exception)
        self.assertIn("velosity", err)
        self.assertIn("velocity", err)

    def test_no_error_when_no_input(self):
        """Validation is skipped when input_algorithm is None (source nodes)."""
        _validate_field_names(
            "vtkContourFilter",
            {"ContourBy": "nonexistent_field"},
            None,  # no input
        )

    def test_no_error_for_unregistered_filter_class(self):
        """Filters not in _FIELD_NAME_PROPERTIES are not validated."""
        src = self._src("temperature")
        _validate_field_names(
            "vtkGeometryFilter",  # not in _FIELD_NAME_PROPERTIES
            {"SomeProperty": "nonexistent_field"},
            src,
        )

    def test_no_error_when_property_absent(self):
        """If the property isn't set, validation is skipped."""
        src = self._src("temperature")
        # No ContourBy in props -> no validation for that key
        _validate_field_names(
            "vtkContourFilter",
            {"Isosurfaces": [50.0]},
            src,
        )

    def test_raises_for_bad_array_name_in_list(self):
        """AddScalarArrayName as a list: any bad name should raise."""
        src = self._src("temperature")
        with self.assertRaises(ValueError) as ctx:
            _validate_field_names(
                "vtkArrayCalculator",
                {
                    "AddScalarArrayName": ["temperature", "nonexistent"],
                    "Function": "temperature + nonexistent",
                    "ResultArrayName": "result",
                },
                src,
            )
        self.assertIn("nonexistent", str(ctx.exception))

    def test_threshold_by_cell_array_accepted(self):
        """ThresholdBy with scope 'both' should accept cell arrays."""
        src = _make_cell_source(field_name="pressure")
        # Should not raise — pressure is a cell array and scope is 'both'
        _validate_field_names(
            "vtkThreshold",
            {"ThresholdBy": "pressure"},
            src,
        )

    def test_contour_by_cell_array_raises(self):
        """ContourBy requires a point array; a cell array should raise."""
        src = _make_cell_source(field_name="pressure")
        with self.assertRaises(ValueError) as ctx:
            _validate_field_names(
                "vtkContourFilter",
                {"ContourBy": "pressure"},  # pressure is cell data, not point data
                src,
            )
        self.assertIn("pressure", str(ctx.exception))


# ---------------------------------------------------------------------------
# Integration: create_vtk_filter raises before Update()
# ---------------------------------------------------------------------------

class TestCreateVtkFilterEarlyValidation(unittest.TestCase):
    """Verify create_vtk_filter raises ValueError for bad field names."""

    def setUp(self):
        clear_reader_cache()
        self.src = _make_image_source(field_name="temperature", field_range=(0.0, 100.0))

    def test_contour_bad_field_raises_before_processing(self):
        """A typo in ContourBy should raise ValueError from create_vtk_filter."""
        with self.assertRaises(ValueError) as ctx:
            create_vtk_filter(
                "vtkContourFilter",
                self.src,
                ContourBy="temperatur",  # typo
                Isosurfaces=[50.0],
            )
        err = str(ctx.exception)
        self.assertIn("temperatur", err)
        self.assertIn("temperature", err)  # available field listed
        self.assertIn("vtkContourFilter", err)

    def test_threshold_bad_field_raises(self):
        with self.assertRaises(ValueError) as ctx:
            create_vtk_filter(
                "vtkThreshold",
                self.src,
                ThresholdBy="not_a_field",
                ThresholdRange=[0.0, 50.0],
            )
        err = str(ctx.exception)
        self.assertIn("not_a_field", err)

    def test_contour_correct_field_succeeds(self):
        vtk_obj, status = create_vtk_filter(
            "vtkContourFilter",
            self.src,
            ContourBy="temperature",
            Isosurfaces=[50.0],
        )
        self.assertIsNotNone(vtk_obj)
        # Valid contour at midpoint should produce geometry
        self.assertGreater(status.get("num_points", 0), 0)

    def test_threshold_correct_field_succeeds(self):
        vtk_obj, status = create_vtk_filter(
            "vtkThreshold",
            self.src,
            ThresholdBy="temperature",
            ThresholdRange=[20.0, 80.0],
        )
        self.assertIsNotNone(vtk_obj)
        self.assertGreater(status.get("num_points", 0), 0)

    def test_calculator_bad_scalar_array_raises(self):
        with self.assertRaises(ValueError) as ctx:
            create_vtk_filter(
                "vtkArrayCalculator",
                self.src,
                AddScalarArrayName=["bad_field"],
                Function="bad_field * 2",
                ResultArrayName="result",
            )
        self.assertIn("bad_field", str(ctx.exception))

    def test_calculator_correct_field_succeeds(self):
        vtk_obj, status = create_vtk_filter(
            "vtkArrayCalculator",
            self.src,
            AddScalarArrayName=["temperature"],
            Function="temperature * 2",
            ResultArrayName="doubled",
        )
        self.assertIsNotNone(vtk_obj)

    def test_error_message_includes_available_arrays(self):
        """The error message must list the available field names."""
        with self.assertRaises(ValueError) as ctx:
            create_vtk_filter(
                "vtkContourFilter",
                self.src,
                ContourBy="wrong_name",
                Isosurfaces=[50.0],
            )
        err = str(ctx.exception)
        # Must list 'temperature' as an available array
        self.assertIn("temperature", err)
        self.assertIn("Available", err)

    def test_no_false_positive_for_multiple_fields(self):
        """Valid field among multiple fields on source should not raise."""
        # Add a second field to the source
        img = vtk.vtkImageData()
        img.SetDimensions(8, 8, 8)
        img.SetOrigin(0.0, 0.0, 0.0)
        img.SetSpacing(1.0, 1.0, 1.0)
        n = img.GetNumberOfPoints()
        for name in ["temperature", "pressure", "density"]:
            arr = numpy_to_vtk(np.linspace(0, 1, n).astype(np.float64))
            arr.SetName(name)
            img.GetPointData().AddArray(arr)
        img.GetPointData().SetActiveScalars("temperature")

        src = vtk.vtkTrivialProducer()
        src.SetOutput(img)
        src.Update()

        # Contour on 'pressure' (valid) must not raise
        vtk_obj, status = create_vtk_filter(
            "vtkContourFilter", src, ContourBy="pressure", Isosurfaces=[0.5]
        )
        self.assertIsNotNone(vtk_obj)


# ---------------------------------------------------------------------------
# _FIELD_NAME_PROPERTIES coverage
# ---------------------------------------------------------------------------

class TestFieldNamePropertiesMap(unittest.TestCase):
    """Verify that all expected filter types are in the map."""

    def test_contour_filter_in_map(self):
        self.assertIn("vtkContourFilter", _FIELD_NAME_PROPERTIES)

    def test_threshold_in_map(self):
        self.assertIn("vtkThreshold", _FIELD_NAME_PROPERTIES)

    def test_gradient_in_map(self):
        self.assertIn("vtkGradientFilter", _FIELD_NAME_PROPERTIES)

    def test_calculator_in_map(self):
        self.assertIn("vtkArrayCalculator", _FIELD_NAME_PROPERTIES)

    def test_stream_tracer_in_map(self):
        self.assertIn("vtkStreamTracer", _FIELD_NAME_PROPERTIES)

    def test_all_specs_are_list_of_tuples(self):
        for cls, spec in _FIELD_NAME_PROPERTIES.items():
            self.assertIsInstance(spec, list, f"{cls} spec should be a list")
            for item in spec:
                self.assertIsInstance(item, tuple, f"{cls} spec item should be a tuple")
                self.assertEqual(len(item), 2, f"{cls} spec tuple should have 2 elements")
                prop_key, scope = item
                self.assertIsInstance(prop_key, str)
                self.assertIn(scope, ("point", "cell", "both"),
                              f"{cls}.{prop_key} scope must be 'point', 'cell', or 'both'")


if __name__ == "__main__":
    unittest.main()
