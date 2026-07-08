"""Tests for extract_component helper and curl vector mode.

Uses both in-memory synthetic data and the synthetic dataset at
datasets/synthetic/data/output.vti (a 64^3 grid with temperature, density,
and velocity fields).
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vtk
import numpy as np
from vtk.util.numpy_support import vtk_to_numpy, numpy_to_vtk

from siva.filters import extract_component


def _make_vector_data():
    """Create a small vtkImageData with a known 3-component vector field.

    velocity = rigid-body rotation about Z axis at (0.5, 0.5):
      vx = -2*pi*(y - 0.5)
      vy =  2*pi*(x - 0.5)
      vz =  0
    """
    N = 8
    img = vtk.vtkImageData()
    img.SetDimensions(N, N, N)
    img.SetOrigin(0, 0, 0)
    spacing = 1.0 / (N - 1)
    img.SetSpacing(spacing, spacing, spacing)

    omega = 2.0 * math.pi
    n_pts = img.GetNumberOfPoints()
    vel = vtk.vtkFloatArray()
    vel.SetName("velocity")
    vel.SetNumberOfComponents(3)
    vel.SetNumberOfTuples(n_pts)

    for i in range(n_pts):
        pt = img.GetPoint(i)
        vx = -omega * (pt[1] - 0.5)
        vy = omega * (pt[0] - 0.5)
        vz = 0.0
        vel.SetTuple3(i, vx, vy, vz)

    img.GetPointData().AddArray(vel)
    img.GetPointData().SetActiveVectors("velocity")

    # Also add a scalar field for testing error cases
    temp = vtk.vtkFloatArray()
    temp.SetName("temperature")
    temp.SetNumberOfComponents(1)
    temp.SetNumberOfTuples(n_pts)
    for i in range(n_pts):
        temp.SetValue(i, float(i))
    img.GetPointData().AddArray(temp)

    return img


class TestExtractComponentBasic(unittest.TestCase):
    """Test the extract_component function from filters.py."""

    def setUp(self):
        self.data = _make_vector_data()

    def test_extract_x_component(self):
        """Extract X component of velocity; values should be -2*pi*(y-0.5)."""
        _, status = extract_component(self.data, "velocity", 0, "vx")
        self.assertEqual(status["source_field"], "velocity")
        self.assertEqual(status["component"], 0)
        self.assertEqual(status["result_name"], "vx")

        arr = self.data.GetPointData().GetArray("vx")
        self.assertIsNotNone(arr, "vx array should be added to point data")
        self.assertEqual(arr.GetNumberOfComponents(), 1)

        # Check a specific value: point at (0, 0, 0) -> vx = -2*pi*(0 - 0.5) = pi
        # Point 0 is at origin (0, 0, 0)
        pt0 = self.data.GetPoint(0)
        expected_vx = -2.0 * math.pi * (pt0[1] - 0.5)
        self.assertAlmostEqual(arr.GetValue(0), expected_vx, places=4)

    def test_extract_y_component(self):
        """Extract Y component of velocity."""
        _, status = extract_component(self.data, "velocity", 1, "vy")
        arr = self.data.GetPointData().GetArray("vy")
        self.assertIsNotNone(arr)

        pt0 = self.data.GetPoint(0)
        expected_vy = 2.0 * math.pi * (pt0[0] - 0.5)
        self.assertAlmostEqual(arr.GetValue(0), expected_vy, places=4)

    def test_extract_z_component(self):
        """Extract Z component of velocity; should be all zeros."""
        _, status = extract_component(self.data, "velocity", 2, "vz")
        arr = self.data.GetPointData().GetArray("vz")
        self.assertIsNotNone(arr)

        np_arr = vtk_to_numpy(arr)
        self.assertTrue(np.allclose(np_arr, 0.0),
                        "Z component of rotation velocity should be 0")

    def test_extract_by_name_x(self):
        """component='x' should work the same as component=0."""
        _, status = extract_component(self.data, "velocity", "x", "vel_x")
        self.assertEqual(status["component"], 0)
        self.assertIsNotNone(self.data.GetPointData().GetArray("vel_x"))

    def test_extract_by_name_y(self):
        """component='y' should resolve to index 1."""
        _, status = extract_component(self.data, "velocity", "y", "vel_y")
        self.assertEqual(status["component"], 1)

    def test_extract_by_name_z_case_insensitive(self):
        """component='Z' (uppercase) should resolve to index 2."""
        _, status = extract_component(self.data, "velocity", "Z", "vel_z")
        self.assertEqual(status["component"], 2)

    def test_extract_returns_range(self):
        """Status should include the range of the extracted component."""
        _, status = extract_component(self.data, "velocity", 2, "vz")
        self.assertIn("range", status)
        self.assertAlmostEqual(status["range"][0], 0.0, places=4)
        self.assertAlmostEqual(status["range"][1], 0.0, places=4)

    def test_values_match_original_vector(self):
        """Extracted components should match the original vector columns."""
        vel_arr = self.data.GetPointData().GetArray("velocity")
        np_vel = vtk_to_numpy(vel_arr)

        for comp, name in enumerate(["cx", "cy", "cz"]):
            extract_component(self.data, "velocity", comp, name)
            extracted = vtk_to_numpy(self.data.GetPointData().GetArray(name))
            np.testing.assert_array_almost_equal(
                extracted, np_vel[:, comp],
                err_msg=f"Component {comp} mismatch")


class TestExtractComponentErrors(unittest.TestCase):
    """Test error handling in extract_component."""

    def setUp(self):
        self.data = _make_vector_data()

    def test_scalar_field_raises(self):
        """Extracting a component from a scalar field should raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            extract_component(self.data, "temperature", 0, "temp_x")
        self.assertIn("scalar", str(ctx.exception).lower())

    def test_nonexistent_field_raises(self):
        """Extracting from a field that doesn't exist should raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            extract_component(self.data, "nonexistent", 0, "result")
        self.assertIn("not found", str(ctx.exception).lower())

    def test_component_out_of_range_raises(self):
        """Component index beyond the number of components should raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            extract_component(self.data, "velocity", 5, "result")
        self.assertIn("out of range", str(ctx.exception).lower())

    def test_invalid_component_name_raises(self):
        """Invalid component name should raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            extract_component(self.data, "velocity", "w", "result")
        self.assertIn("component", str(ctx.exception).lower())


class TestExtractComponentDSL(unittest.TestCase):
    """Test extract_component flowing through the DSL pipeline."""

    def test_extract_via_dsl_builder(self):
        """extract_component in the DSL should produce a new scalar array."""
        from siva.compute import evaluate

        # Write synthetic data to a temp file
        data = _make_vector_data()
        tmp_path = "test_extract_comp_dsl.vti"
        writer = vtk.vtkXMLImageDataWriter()
        writer.SetFileName(tmp_path)
        writer.SetInputData(data)
        writer.Write()

        try:
            code = f'''
from siva.spec_api import *

data = source("vtkXMLImageDataReader", FileName="{tmp_path}")
vz = extract_component(input=data, field="velocity", component="z", result_name="vel_z")
'''
            _r = evaluate(code)
            vtk_objects, objs, node_statuses = _r.outputs, _r.outputs_by_name, _r.statuses

            # Check that the extract_component node was built successfully
            found_ec = False
            for nid, status in node_statuses.items():
                if status.get("class") == "extract_component":
                    found_ec = True
                    self.assertEqual(status["result_name"], "vel_z")
                    self.assertEqual(status["component"], 2)
                    self.assertIn("range", status)
            self.assertTrue(found_ec, "extract_component node not found in statuses")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_extract_default_result_name(self):
        """When result_name is omitted, it should default to '{field}_{comp}'."""
        from siva.dsl import PipelineBuilder
        builder = PipelineBuilder()
        ref = builder.extract_component(field="velocity", component="y")
        # The properties should have the default result_name
        self.assertEqual(ref.properties["result_name"], "velocity_y")

    def test_extract_default_result_name_int(self):
        """Integer component should produce a name like 'velocity_x'."""
        from siva.dsl import PipelineBuilder
        builder = PipelineBuilder()
        ref = builder.extract_component(field="velocity", component=1)
        self.assertEqual(ref.properties["result_name"], "velocity_y")


class TestCurlVector(unittest.TestCase):
    """Test curl with vector=True.

    For a rigid-body rotation vx = -omega*(y-0.5), vy = omega*(x-0.5), vz = 0,
    the vorticity is curl(v) = (0, 0, 2*omega) everywhere.
    omega = 2*pi, so vorticity_z = 4*pi ~ 12.566.
    """

    def _build_vorticity_pipeline(self, vector=False):
        """Build a vorticity pipeline via DSL and return objects/statuses."""
        from siva.compute import evaluate

        data = _make_vector_data()
        tmp_path = "test_vort_vector.vti"
        writer = vtk.vtkXMLImageDataWriter()
        writer.SetFileName(tmp_path)
        writer.SetInputData(data)
        writer.Write()

        try:
            if vector:
                code = f'''
from siva.spec_api import *

data = source("vtkXMLImageDataReader", FileName="{tmp_path}")
vort = curl_vector(vector_field=data, output_field="vorticity")
'''
            else:
                code = f'''
from siva.spec_api import *

data = source("vtkXMLImageDataReader", FileName="{tmp_path}")
vort = curl_magnitude(vector_field=data, output_field="vorticity_magnitude")
'''
            return evaluate(code), tmp_path
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    def test_vorticity_vector_mode(self):
        """vector=True should produce a 3-component vorticity field."""
        _r, tmp_path = self._build_vorticity_pipeline(vector=True)
        objs = _r.outputs_by_name
        try:
            # Find the last node output - should have vorticity array
            vort_alg = objs.get("vort")
            self.assertIsNotNone(vort_alg, f"vort not in objects: {list(objs.keys())}")
            vort_alg.Update()
            output = vort_alg.GetOutput()
            self.assertIsNotNone(output)

            # The vorticity vector should be in point data named "vorticity"
            vort_arr = output.GetPointData().GetArray("vorticity")
            if vort_arr is None:
                # List available arrays for debugging
                pd = output.GetPointData()
                names = [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]
                self.fail(f"'vorticity' not found in point data. Available: {names}")

            self.assertEqual(vort_arr.GetNumberOfComponents(), 3,
                             "Vorticity should be a 3-component vector")

            # For rigid-body rotation, vorticity should be approximately (0, 0, 4*pi)
            # at interior points (boundary points may have numerical issues)
            np_vort = vtk_to_numpy(vort_arr)
            N = 8
            expected_z = 4.0 * math.pi
            mid = N // 2
            center_idx = mid + mid * N + mid * N * N
            # Z component should be close to 4*pi
            self.assertAlmostEqual(np_vort[center_idx, 2], expected_z, delta=2.0,
                                   msg="Vorticity Z at center should be ~4*pi")
            # X and Y components should be near zero
            self.assertAlmostEqual(np_vort[center_idx, 0], 0.0, delta=1.0,
                                   msg="Vorticity X at center should be ~0")
            self.assertAlmostEqual(np_vort[center_idx, 1], 0.0, delta=1.0,
                                   msg="Vorticity Y at center should be ~0")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_vorticity_magnitude_mode(self):
        """vector=False (default) should produce a scalar vorticity magnitude."""
        _r, tmp_path = self._build_vorticity_pipeline(vector=False)
        objs = _r.outputs_by_name
        try:
            vort_alg = objs.get("vort")
            self.assertIsNotNone(vort_alg)
            vort_alg.Update()
            output = vort_alg.GetOutput()
            self.assertIsNotNone(output)

            # Should have "vorticity_magnitude" scalar
            mag_arr = output.GetPointData().GetArray("vorticity_magnitude")
            if mag_arr is None:
                pd = output.GetPointData()
                names = [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]
                self.fail(f"'vorticity_magnitude' not found. Available: {names}")

            self.assertEqual(mag_arr.GetNumberOfComponents(), 1,
                             "Vorticity magnitude should be scalar")

            # At interior points, magnitude should be close to 4*pi
            N = 8
            mid = N // 2
            center_idx = mid + mid * N + mid * N * N
            expected_mag = 4.0 * math.pi
            self.assertAlmostEqual(mag_arr.GetValue(center_idx), expected_mag,
                                   delta=2.0,
                                   msg="Vorticity magnitude at center ~4*pi")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_vorticity_vector_custom_output_field(self):
        """curl_vector with custom output_field should rename the array."""
        from siva.compute import evaluate

        data = _make_vector_data()
        tmp_path = "test_vort_custom.vti"
        writer = vtk.vtkXMLImageDataWriter()
        writer.SetFileName(tmp_path)
        writer.SetInputData(data)
        writer.Write()

        try:
            code = f'''
from siva.spec_api import *

data = source("vtkXMLImageDataReader", FileName="{tmp_path}")
vort = curl_vector(vector_field=data, output_field="my_vorticity")
'''
            _r = evaluate(code)
            vtk_objects, objs, node_statuses = _r.outputs, _r.outputs_by_name, _r.statuses
            vort_alg = objs.get("vort")
            self.assertIsNotNone(vort_alg)
            vort_alg.Update()
            output = vort_alg.GetOutput()

            # Should have "my_vorticity" as the result array name
            arr = output.GetPointData().GetArray("my_vorticity")
            if arr is None:
                pd = output.GetPointData()
                names = [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]
                self.fail(f"'my_vorticity' not found. Available: {names}")
            self.assertEqual(arr.GetNumberOfComponents(), 3)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


class TestExtractComponentWithSyntheticDataset(unittest.TestCase):
    """Test extract_component with the full synthetic dataset if available."""

    DATASET_PATH = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "datasets", "synthetic", "data", "output.vti"
    )

    @unittest.skipUnless(
        os.path.exists(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "datasets", "synthetic", "data", "output.vti")),
        "Synthetic dataset not available"
    )
    def test_extract_from_synthetic_dataset(self):
        """Extract velocity components from the synthetic dataset."""
        reader = vtk.vtkXMLImageDataReader()
        reader.SetFileName(self.DATASET_PATH)
        reader.Update()
        data = reader.GetOutput()

        # Extract all three components
        for comp, name in [(0, "vx"), (1, "vy"), (2, "vz")]:
            extract_component(data, "velocity", comp, name)
            arr = data.GetPointData().GetArray(name)
            self.assertIsNotNone(arr, f"Array {name} should exist")
            self.assertEqual(arr.GetNumberOfComponents(), 1)
            self.assertEqual(arr.GetNumberOfTuples(), data.GetNumberOfPoints())

        # Verify Z-component is all zeros (rotation about Z axis)
        vz = vtk_to_numpy(data.GetPointData().GetArray("vz"))
        self.assertTrue(np.allclose(vz, 0.0, atol=1e-6),
                        "Z velocity should be 0 for rotation about Z axis")

        # Verify the extracted components reconstruct the original vector
        vel = vtk_to_numpy(data.GetPointData().GetArray("velocity"))
        vx = vtk_to_numpy(data.GetPointData().GetArray("vx"))
        vy = vtk_to_numpy(data.GetPointData().GetArray("vy"))
        np.testing.assert_array_almost_equal(vx, vel[:, 0])
        np.testing.assert_array_almost_equal(vy, vel[:, 1])


if __name__ == "__main__":
    unittest.main()
