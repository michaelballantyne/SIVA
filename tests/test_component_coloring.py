"""Tests for vector component coloring in show().

These tests create synthetic VTK data in memory so they don't require
any external dataset files.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vtk
from siva.filters import create_show


def _make_vector_data():
    """Create a small vtkImageData with a 3-component vector field."""
    img = vtk.vtkImageData()
    img.SetDimensions(4, 4, 4)
    img.SetOrigin(0, 0, 0)
    img.SetSpacing(1, 1, 1)

    arr = vtk.vtkFloatArray()
    arr.SetName("velocity")
    arr.SetNumberOfComponents(3)
    n = img.GetNumberOfPoints()
    arr.SetNumberOfTuples(n)
    for i in range(n):
        # X component: 0..63, Y component: -10..10, Z component: 100..163
        arr.SetTuple3(i, float(i), -10.0 + 20.0 * i / (n - 1), 100.0 + float(i))
    img.GetPointData().AddArray(arr)
    img.GetPointData().SetActiveScalars("velocity")
    return img


class TestComponentColoring(unittest.TestCase):
    """Test that the component parameter on create_show works correctly."""

    def setUp(self):
        self.data = _make_vector_data()

    def test_component_by_integer(self):
        """component=2 should set vector mode to component and select component 2."""
        actor, _ = create_show(self.data,
            color_by="velocity", component=2)
        lut = actor.GetMapper().GetLookupTable()
        self.assertEqual(lut.GetVectorMode(), vtk.vtkScalarsToColors.COMPONENT)
        self.assertEqual(lut.GetVectorComponent(), 2)

    def test_component_by_name_x(self):
        """component='x' should resolve to component index 0."""
        actor, _ = create_show(self.data,
            color_by="velocity", component="x")
        lut = actor.GetMapper().GetLookupTable()
        self.assertEqual(lut.GetVectorMode(), vtk.vtkScalarsToColors.COMPONENT)
        self.assertEqual(lut.GetVectorComponent(), 0)

    def test_component_by_name_y(self):
        """component='y' should resolve to component index 1."""
        actor, _ = create_show(self.data,
            color_by="velocity", component="y")
        lut = actor.GetMapper().GetLookupTable()
        self.assertEqual(lut.GetVectorComponent(), 1)

    def test_component_by_name_z(self):
        """component='z' should resolve to component index 2."""
        actor, _ = create_show(self.data,
            color_by="velocity", component="Z")  # case-insensitive
        lut = actor.GetMapper().GetLookupTable()
        self.assertEqual(lut.GetVectorComponent(), 2)

    def test_no_component_default_mode(self):
        """Without component, vector mode should remain at VTK default (not explicitly set)."""
        actor, _ = create_show(self.data,
            color_by="velocity")
        lut = actor.GetMapper().GetLookupTable()
        # When component is not specified, we do NOT call SetVectorModeToComponent,
        # so the LUT retains VTK's default vector mode.
        # The key distinction is: with component=N, SetVectorModeToComponent() is
        # explicitly called and SetVectorComponent(N) selects the component.
        self.assertNotEqual(
            (lut.GetVectorMode(), lut.GetVectorComponent()),
            (vtk.vtkScalarsToColors.COMPONENT, 2),
            "Without component param, should not be set to component 2"
        )

    def test_component_auto_scalar_range(self):
        """When component is set and scalar_range is None, range should be auto-detected."""
        # Z component range should be [100, 163]
        actor, _ = create_show(self.data,
            color_by="velocity", component=2)
        mapper = actor.GetMapper()
        sr = mapper.GetScalarRange()
        self.assertAlmostEqual(sr[0], 100.0, places=1)
        self.assertAlmostEqual(sr[1], 163.0, places=1)

    def test_component_explicit_scalar_range(self):
        """Explicit scalar_range should override auto-detection."""
        actor, _ = create_show(self.data,
            color_by="velocity", component=0, scalar_range=(0, 10))
        mapper = actor.GetMapper()
        sr = mapper.GetScalarRange()
        self.assertAlmostEqual(sr[0], 0.0)
        self.assertAlmostEqual(sr[1], 10.0)

    def test_invalid_component_name_raises(self):
        """Invalid component name should raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            create_show(self.data,
                color_by="velocity", component="w")
        self.assertIn("Unknown component name", str(ctx.exception))

    def test_component_with_scalar_bar(self):
        """component should work together with scalar_bar."""
        actor, bar = create_show(self.data,
            color_by="velocity", component=1, scalar_bar="Vy")
        self.assertIsNotNone(bar)
        self.assertIsInstance(bar, vtk.vtkScalarBarActor)
        # The scalar bar LUT should also be in component mode
        lut = actor.GetMapper().GetLookupTable()
        self.assertEqual(lut.GetVectorMode(), vtk.vtkScalarsToColors.COMPONENT)

    def test_component_with_lut(self):
        """component should work together with a named colormap."""
        actor, _ = create_show(self.data,
            color_by="velocity", component=0, lut="cool_to_warm")
        lut = actor.GetMapper().GetLookupTable()
        self.assertEqual(lut.GetVectorMode(), vtk.vtkScalarsToColors.COMPONENT)
        self.assertEqual(lut.GetVectorComponent(), 0)


class TestComponentColoringDSL(unittest.TestCase):
    """Test component parameter flows through the DSL interpret() path."""

    def test_component_via_dsl(self):
        """component= in show() DSL should produce correct LUT settings."""
        from siva.compute import evaluate

        # Write synthetic data to a temp file
        writer = vtk.vtkXMLImageDataWriter()
        tmp_path = "/tmp/test_component_dsl.vti"
        data = _make_vector_data()
        writer.SetFileName(tmp_path)
        writer.SetInputData(data)
        writer.Write()

        try:
            code = f'''
data = source("vtkXMLImageDataReader", FileName="{tmp_path}")
show(data, "vz", color_by="velocity", component="z", lut="cool_to_warm")
'''
            _r = evaluate(code)
            vtk_objects, objs, node_statuses = _r.outputs, _r.outputs_by_name, _r.statuses
            errors = [s.get("message") for s in node_statuses.values() if s.get("status") == "error"]
            self.assertEqual(errors, [], f"Pipeline had errors: {errors}")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()
