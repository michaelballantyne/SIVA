"""Tests for make_vector and curl DSL primitives."""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vtk
import numpy as np
from vtk.util.numpy_support import vtk_to_numpy


def _make_rotation_data(N=8):
    """Create a small vtkImageData with three scalar fields (u, v, w) and a
    known 3-component rotation velocity:
      u = -2*pi*(y - 0.5)
      v =  2*pi*(x - 0.5)
      w =  0
    Also includes the assembled 'velocity' vector for convenience.
    """
    img = vtk.vtkImageData()
    img.SetDimensions(N, N, N)
    img.SetOrigin(0, 0, 0)
    spacing = 1.0 / (N - 1)
    img.SetSpacing(spacing, spacing, spacing)

    omega = 2.0 * math.pi
    n_pts = img.GetNumberOfPoints()

    u_arr = vtk.vtkFloatArray()
    u_arr.SetName("u")
    u_arr.SetNumberOfTuples(n_pts)

    v_arr = vtk.vtkFloatArray()
    v_arr.SetName("v")
    v_arr.SetNumberOfTuples(n_pts)

    w_arr = vtk.vtkFloatArray()
    w_arr.SetName("w")
    w_arr.SetNumberOfTuples(n_pts)

    vel_arr = vtk.vtkFloatArray()
    vel_arr.SetName("velocity")
    vel_arr.SetNumberOfComponents(3)
    vel_arr.SetNumberOfTuples(n_pts)

    for i in range(n_pts):
        pt = img.GetPoint(i)
        uu = -omega * (pt[1] - 0.5)
        vv = omega * (pt[0] - 0.5)
        ww = 0.0
        u_arr.SetValue(i, uu)
        v_arr.SetValue(i, vv)
        w_arr.SetValue(i, ww)
        vel_arr.SetTuple3(i, uu, vv, ww)

    img.GetPointData().AddArray(u_arr)
    img.GetPointData().AddArray(v_arr)
    img.GetPointData().AddArray(w_arr)
    img.GetPointData().AddArray(vel_arr)
    img.GetPointData().SetActiveVectors("velocity")
    return img


def _write_tmp(data, path):
    writer = vtk.vtkXMLImageDataWriter()
    writer.SetFileName(path)
    writer.SetInputData(data)
    writer.Write()


# ---------------------------------------------------------------------------
# DSL builder tests (PipelineBuilder methods, no full renderer needed)
# ---------------------------------------------------------------------------

class TestMakeVectorDSLBuilder(unittest.TestCase):
    """Test PipelineBuilder.make_vector() node creation."""

    def setUp(self):
        from vislang.dsl import PipelineBuilder
        self.builder = PipelineBuilder()

    def test_make_vector_returns_node_ref(self):
        """make_vector should return a NodeRef pointing to vtkArrayCalculator."""
        from vislang.dsl import NodeRef
        ref = self.builder.make_vector(components=("u", "v", "w"), result="velocity")
        self.assertIsInstance(ref, NodeRef)
        self.assertEqual(ref.vtk_class, "vtkArrayCalculator")

    def test_make_vector_result_name_in_props(self):
        """ResultArrayName should match the result argument."""
        ref = self.builder.make_vector(components=("a", "b", "c"), result="myVec")
        self.assertEqual(ref.properties["ResultArrayName"], "myVec")

    def test_make_vector_function_string(self):
        """Function property should use iHat/jHat/kHat with the component names."""
        ref = self.builder.make_vector(components=("px", "py", "pz"), result="momentum")
        fn = ref.properties["Function"]
        self.assertIn("px", fn)
        self.assertIn("py", fn)
        self.assertIn("pz", fn)
        self.assertIn("iHat", fn)
        self.assertIn("jHat", fn)
        self.assertIn("kHat", fn)

    def test_make_vector_components_in_props(self):
        """AddScalarArrayName should list all three component names."""
        ref = self.builder.make_vector(components=("u", "v", "w"))
        self.assertEqual(ref.properties["AddScalarArrayName"], ["u", "v", "w"])

    def test_make_vector_default_result(self):
        """Default result name is 'velocity'."""
        ref = self.builder.make_vector(components=("u", "v", "w"))
        self.assertEqual(ref.properties["ResultArrayName"], "velocity")


class TestCurlDSLBuilder(unittest.TestCase):
    """Test PipelineBuilder.curl() node creation."""

    def setUp(self):
        from vislang.dsl import PipelineBuilder
        self.builder = PipelineBuilder()

    def _make_dummy_input(self):
        """Add a dummy source node and return its ref."""
        return self.builder.source("vtkXMLImageDataReader", FileName="/tmp/dummy.vti")

    def test_curl_vector_mode_creates_calculator(self):
        """curl(vector=True) with custom result should produce a vtkArrayCalculator."""
        from vislang.dsl import NodeRef
        inp = self._make_dummy_input()
        out = self.builder.curl(vector_field=inp, result="omega", vector=True)
        self.assertIsInstance(out, NodeRef)
        self.assertEqual(out.vtk_class, "vtkArrayCalculator")
        self.assertEqual(out.properties["ResultArrayName"], "omega")

    def test_curl_magnitude_mode_creates_calculator(self):
        """curl(vector=False) should produce a vtkArrayCalculator computing magnitude."""
        from vislang.dsl import NodeRef
        inp = self._make_dummy_input()
        out = self.builder.curl(vector_field=inp, result="vort_mag", vector=False)
        self.assertIsInstance(out, NodeRef)
        self.assertEqual(out.vtk_class, "vtkArrayCalculator")
        fn = out.properties["Function"]
        self.assertIn("mag", fn.lower())

    def test_curl_default_result_name(self):
        """Default result name for curl is 'vorticity'."""
        inp = self._make_dummy_input()
        out = self.builder.curl(vector_field=inp)
        # vector=True by default, custom name != "Vorticity" -> calculator
        self.assertEqual(out.properties["ResultArrayName"], "vorticity")

    def test_curl_vorticity_passthrough(self):
        """curl with result='Vorticity' and vector=True skips the rename calc."""
        inp = self._make_dummy_input()
        out = self.builder.curl(vector_field=inp, result="Vorticity", vector=True)
        # Should be vtkCellDataToPointData, not vtkArrayCalculator
        self.assertEqual(out.vtk_class, "vtkCellDataToPointData")


# ---------------------------------------------------------------------------
# Interpreter / end-to-end tests (full DSL execution)
# ---------------------------------------------------------------------------

class TestMakeVectorInterpreter(unittest.TestCase):
    """Run make_vector through the DSL interpreter and verify the output array."""

    TMP = "/tmp/test_make_vector.vti"

    @classmethod
    def setUpClass(cls):
        _write_tmp(_make_rotation_data(), cls.TMP)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.TMP):
            os.remove(cls.TMP)

    def _run(self, extra_code=""):
        from vislang.dsl import interpret_build
        code = f'''
data = source("vtkXMLImageDataReader", FileName="{self.TMP}")
{extra_code}
'''
        builder, vtk_objects, objs, node_statuses = interpret_build(code)
        return objs, node_statuses, {}, builder

    def test_make_vector_produces_vector_array(self):
        """make_vector should create a 3-component array on the output."""
        objs, node_statuses, _, _ = self._run(
            'vel = make_vector(input=data, components=("u", "v", "w"), result="velocity")'
        )
        vel_alg = objs.get("vel")
        self.assertIsNotNone(vel_alg, f"'vel' not in objects: {list(objs.keys())}")
        vel_alg.Update()
        out = vel_alg.GetOutput()
        arr = out.GetPointData().GetArray("velocity")
        self.assertIsNotNone(arr, "'velocity' array not found on output")
        self.assertEqual(arr.GetNumberOfComponents(), 3)

    def test_make_vector_values_match_original(self):
        """Assembled vector should match the original scalar fields."""
        objs, _, _, _ = self._run(
            'vel = make_vector(input=data, components=("u", "v", "w"), result="myvel")'
        )
        vel_alg = objs["vel"]
        vel_alg.Update()
        out = vel_alg.GetOutput()

        np_myvel = vtk_to_numpy(out.GetPointData().GetArray("myvel"))
        np_u = vtk_to_numpy(out.GetPointData().GetArray("u"))
        np_v = vtk_to_numpy(out.GetPointData().GetArray("v"))
        np_w = vtk_to_numpy(out.GetPointData().GetArray("w"))

        np.testing.assert_array_almost_equal(np_myvel[:, 0], np_u, decimal=5,
                                             err_msg="X component mismatch")
        np.testing.assert_array_almost_equal(np_myvel[:, 1], np_v, decimal=5,
                                             err_msg="Y component mismatch")
        np.testing.assert_array_almost_equal(np_myvel[:, 2], np_w, decimal=5,
                                             err_msg="Z component mismatch")

    def test_make_vector_custom_result_name(self):
        """result parameter should control the output array name."""
        objs, _, _, _ = self._run(
            'out = make_vector(input=data, components=("u", "v", "w"), result="momentum")'
        )
        alg = objs["out"]
        alg.Update()
        arr = alg.GetOutput().GetPointData().GetArray("momentum")
        self.assertIsNotNone(arr, "'momentum' array not found")

    def test_make_vector_available_in_dsl_namespace(self):
        """make_vector should be a valid name in the DSL namespace (no NameError)."""
        # This just checks it can be called without error
        objs, node_statuses, _, _ = self._run(
            'vel = make_vector(input=data, components=("u", "v", "w"))'
        )
        errors = [s.get("message") for s in node_statuses.values() if s.get("status") == "error"]
        self.assertEqual(errors, [], f"Pipeline had errors: {errors}")


class TestCurlInterpreter(unittest.TestCase):
    """Run curl through the DSL interpreter and verify computed vorticity."""

    TMP = "/tmp/test_curl.vti"

    @classmethod
    def setUpClass(cls):
        _write_tmp(_make_rotation_data(), cls.TMP)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.TMP):
            os.remove(cls.TMP)

    def _run_curl(self, vector=True, result="vorticity"):
        from vislang.dsl import interpret_build
        vec_str = "True" if vector else "False"
        code = f'''
data = source("vtkXMLImageDataReader", FileName="{self.TMP}")
vort = curl(vector_field=data, result="{result}", vector={vec_str})
'''
        builder, vtk_objects, objs, node_statuses = interpret_build(code)
        return objs, node_statuses, {}, builder

    def test_curl_vector_produces_3component_array(self):
        """curl(vector=True) should produce a 3-component array."""
        objs, _, _, _ = self._run_curl(vector=True, result="omega")
        alg = objs.get("vort")
        self.assertIsNotNone(alg)
        alg.Update()
        arr = alg.GetOutput().GetPointData().GetArray("omega")
        self.assertIsNotNone(arr, "'omega' not found in output")
        self.assertEqual(arr.GetNumberOfComponents(), 3)

    def test_curl_magnitude_produces_scalar(self):
        """curl(vector=False) should produce a scalar (1-component) array."""
        objs, _, _, _ = self._run_curl(vector=False, result="vort_mag")
        alg = objs.get("vort")
        self.assertIsNotNone(alg)
        alg.Update()
        arr = alg.GetOutput().GetPointData().GetArray("vort_mag")
        self.assertIsNotNone(arr, "'vort_mag' not found in output")
        self.assertEqual(arr.GetNumberOfComponents(), 1)

    def test_curl_z_component_near_4pi(self):
        """For rigid-body rotation, Z-vorticity ~ 4*pi at interior points."""
        objs, _, _, _ = self._run_curl(vector=True, result="vorticity")
        alg = objs["vort"]
        alg.Update()
        arr = alg.GetOutput().GetPointData().GetArray("vorticity")
        np_vort = vtk_to_numpy(arr)
        N = 8
        mid = N // 2
        center_idx = mid + mid * N + mid * N * N
        expected_z = 4.0 * math.pi
        self.assertAlmostEqual(np_vort[center_idx, 2], expected_z, delta=2.0,
                               msg="Z vorticity at center should be ~4*pi")

    def test_curl_magnitude_near_4pi(self):
        """For rigid-body rotation, ||curl|| ~ 4*pi at interior points."""
        objs, _, _, _ = self._run_curl(vector=False, result="vort_mag")
        alg = objs["vort"]
        alg.Update()
        arr = alg.GetOutput().GetPointData().GetArray("vort_mag")
        np_mag = vtk_to_numpy(arr)
        N = 8
        mid = N // 2
        center_idx = mid + mid * N + mid * N * N
        self.assertAlmostEqual(np_mag[center_idx], 4.0 * math.pi, delta=2.0,
                               msg="Curl magnitude at center should be ~4*pi")

    def test_curl_available_in_dsl_namespace(self):
        """curl should be a valid name in the DSL namespace (no NameError)."""
        objs, node_statuses, _, _ = self._run_curl(vector=True)
        errors = [s.get("message") for s in node_statuses.values() if s.get("status") == "error"]
        self.assertEqual(errors, [], f"Pipeline had errors: {errors}")


class TestMakeVectorThenCurl(unittest.TestCase):
    """Test chaining make_vector -> curl in a single pipeline."""

    TMP = "/tmp/test_make_vector_curl_chain.vti"

    @classmethod
    def setUpClass(cls):
        _write_tmp(_make_rotation_data(), cls.TMP)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.TMP):
            os.remove(cls.TMP)

    def test_chain_make_vector_then_curl(self):
        """make_vector output fed into curl should compute vorticity from scalars."""
        from vislang.dsl import interpret_build
        code = f'''
data = source("vtkXMLImageDataReader", FileName="{self.TMP}")
vel = make_vector(input=data, components=("u", "v", "w"), result="velocity")
vort = curl(vector_field=vel, result="vorticity", vector=False)
'''
        builder, vtk_objects, objs, node_statuses = interpret_build(code)
        errors = [s.get("message") for s in node_statuses.values() if s.get("status") == "error"]
        self.assertEqual(errors, [], f"Pipeline had errors: {errors}")

        alg = objs.get("vort")
        self.assertIsNotNone(alg)
        alg.Update()
        arr = alg.GetOutput().GetPointData().GetArray("vorticity")
        self.assertIsNotNone(arr, "'vorticity' not found in output")
        self.assertEqual(arr.GetNumberOfComponents(), 1, "Should be scalar magnitude")

    def test_chain_make_vector_curl_produces_correct_values(self):
        """make_vector + curl chain should produce vorticity values near 4*pi."""
        from vislang.dsl import interpret_build

        code = f'''
data = source("vtkXMLImageDataReader", FileName="{self.TMP}")
vel = make_vector(input=data, components=("u", "v", "w"), result="velocity")
vort = curl(vector_field=vel, result="vorticity_magnitude", vector=False)
'''
        _, _, objs, _ = interpret_build(code)

        alg = objs["vort"]
        alg.Update()

        arr = vtk_to_numpy(alg.GetOutput().GetPointData().GetArray("vorticity_magnitude"))
        N = 8
        mid = N // 2
        center_idx = mid + mid * N + mid * N * N
        self.assertAlmostEqual(arr[center_idx], 4.0 * math.pi, delta=2.0,
            msg="Vorticity magnitude at center should be ~4*pi")


if __name__ == "__main__":
    unittest.main()
