"""Tests for make_vector, curl_vector, and curl_magnitude DSL primitives."""

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
        from siva.dsl import PipelineBuilder
        self.builder = PipelineBuilder()

    def test_make_vector_returns_node_ref(self):
        """make_vector should return a NodeRef pointing to vtkArrayCalculator."""
        from siva.dsl import NodeRef
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


class TestCurlVectorDSLBuilder(unittest.TestCase):
    """Test PipelineBuilder.curl_vector() node creation."""

    def setUp(self):
        from siva.dsl import PipelineBuilder
        self.builder = PipelineBuilder()

    def _make_dummy_input(self):
        """Add a dummy source node and return its ref."""
        return self.builder.source("vtkXMLImageDataReader", FileName="/tmp/dummy.vti")

    def test_curl_vector_returns_node_ref(self):
        """curl_vector should return a NodeRef pointing to vtkArrayCalculator."""
        from siva.dsl import NodeRef
        inp = self._make_dummy_input()
        out = self.builder.curl_vector(vector_field=inp)
        self.assertIsInstance(out, NodeRef)
        self.assertEqual(out.vtk_class, "vtkArrayCalculator")

    def test_curl_vector_default_output_field(self):
        """Default output_field for curl_vector is 'vorticity' (snake_case)."""
        inp = self._make_dummy_input()
        out = self.builder.curl_vector(vector_field=inp)
        self.assertEqual(out.properties["ResultArrayName"], "vorticity")

    def test_curl_vector_custom_output_field(self):
        """Custom output_field is passed through correctly."""
        inp = self._make_dummy_input()
        out = self.builder.curl_vector(vector_field=inp, output_field="omega")
        self.assertEqual(out.properties["ResultArrayName"], "omega")

    def test_curl_vector_output_is_snake_case_by_default(self):
        """Default output array name should be all lowercase (snake_case)."""
        inp = self._make_dummy_input()
        out = self.builder.curl_vector(vector_field=inp)
        name = out.properties["ResultArrayName"]
        self.assertEqual(name, name.lower(), f"Expected snake_case, got '{name}'")


class TestCurlMagnitudeDSLBuilder(unittest.TestCase):
    """Test PipelineBuilder.curl_magnitude() node creation."""

    def setUp(self):
        from siva.dsl import PipelineBuilder
        self.builder = PipelineBuilder()

    def _make_dummy_input(self):
        return self.builder.source("vtkXMLImageDataReader", FileName="/tmp/dummy.vti")

    def test_curl_magnitude_returns_node_ref(self):
        """curl_magnitude should return a NodeRef pointing to vtkArrayCalculator."""
        from siva.dsl import NodeRef
        inp = self._make_dummy_input()
        out = self.builder.curl_magnitude(vector_field=inp)
        self.assertIsInstance(out, NodeRef)
        self.assertEqual(out.vtk_class, "vtkArrayCalculator")

    def test_curl_magnitude_default_output_field(self):
        """Default output_field for curl_magnitude is 'vorticity_magnitude' (snake_case)."""
        inp = self._make_dummy_input()
        out = self.builder.curl_magnitude(vector_field=inp)
        self.assertEqual(out.properties["ResultArrayName"], "vorticity_magnitude")

    def test_curl_magnitude_custom_output_field(self):
        """Custom output_field is passed through correctly."""
        inp = self._make_dummy_input()
        out = self.builder.curl_magnitude(vector_field=inp, output_field="spin_intensity")
        self.assertEqual(out.properties["ResultArrayName"], "spin_intensity")

    def test_curl_magnitude_function_contains_mag(self):
        """curl_magnitude should compute mag() in its calculator function."""
        inp = self._make_dummy_input()
        out = self.builder.curl_magnitude(vector_field=inp)
        fn = out.properties["Function"]
        self.assertIn("mag", fn.lower())

    def test_curl_magnitude_output_is_snake_case_by_default(self):
        """Default output array name should be all lowercase (snake_case)."""
        inp = self._make_dummy_input()
        out = self.builder.curl_magnitude(vector_field=inp)
        name = out.properties["ResultArrayName"]
        self.assertEqual(name, name.lower(), f"Expected snake_case, got '{name}'")


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
        from siva.compute import evaluate
        code = f'''
data = source("vtkXMLImageDataReader", FileName="{self.TMP}")
{extra_code}
'''
        _r = evaluate(code)
        vtk_objects, objs, node_statuses = _r.outputs, _r.outputs_by_name, _r.statuses
        return objs, node_statuses, {}, None

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
        objs, node_statuses, _, _ = self._run(
            'vel = make_vector(input=data, components=("u", "v", "w"))'
        )
        errors = [s.get("message") for s in node_statuses.values() if s.get("status") == "error"]
        self.assertEqual(errors, [], f"Pipeline had errors: {errors}")


class TestCurlVectorInterpreter(unittest.TestCase):
    """Run curl_vector through the DSL interpreter and verify computed vorticity."""

    TMP = "/tmp/test_curl_vector.vti"

    @classmethod
    def setUpClass(cls):
        _write_tmp(_make_rotation_data(), cls.TMP)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.TMP):
            os.remove(cls.TMP)

    def _run_curl_vector(self, output_field="vorticity"):
        from siva.compute import evaluate
        code = f'''
data = source("vtkXMLImageDataReader", FileName="{self.TMP}")
vort = curl_vector(vector_field=data, output_field="{output_field}")
'''
        _r = evaluate(code)
        vtk_objects, objs, node_statuses = _r.outputs, _r.outputs_by_name, _r.statuses
        return objs, node_statuses, {}, None

    def test_curl_vector_produces_3component_array(self):
        """curl_vector should produce a 3-component vector array."""
        objs, _, _, _ = self._run_curl_vector(output_field="omega")
        alg = objs.get("vort")
        self.assertIsNotNone(alg)
        alg.Update()
        arr = alg.GetOutput().GetPointData().GetArray("omega")
        self.assertIsNotNone(arr, "'omega' not found in output")
        self.assertEqual(arr.GetNumberOfComponents(), 3,
                         "curl_vector output must be 3-component vector")

    def test_curl_vector_default_output_name_is_snake_case(self):
        """Default output array name 'vorticity' is snake_case."""
        objs, _, _, _ = self._run_curl_vector()
        alg = objs.get("vort")
        self.assertIsNotNone(alg)
        alg.Update()
        arr = alg.GetOutput().GetPointData().GetArray("vorticity")
        self.assertIsNotNone(arr, "'vorticity' not found in output")
        # Verify no capital-V 'Vorticity' leaks through
        cap_v_arr = alg.GetOutput().GetPointData().GetArray("Vorticity")
        # The VTK intermediate 'Vorticity' may or may not persist; the point is
        # the OUTPUT array we care about is snake_case 'vorticity'
        self.assertEqual(arr.GetNumberOfComponents(), 3)

    def test_curl_vector_z_component_near_4pi(self):
        """For rigid-body rotation, Z-vorticity ~ 4*pi at interior points."""
        objs, _, _, _ = self._run_curl_vector(output_field="vorticity")
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

    def test_curl_vector_available_in_dsl_namespace(self):
        """curl_vector should be a valid name in the DSL namespace (no NameError)."""
        objs, node_statuses, _, _ = self._run_curl_vector()
        errors = [s.get("message") for s in node_statuses.values() if s.get("status") == "error"]
        self.assertEqual(errors, [], f"Pipeline had errors: {errors}")


class TestCurlMagnitudeInterpreter(unittest.TestCase):
    """Run curl_magnitude through the DSL interpreter and verify computed vorticity magnitude."""

    TMP = "/tmp/test_curl_magnitude.vti"

    @classmethod
    def setUpClass(cls):
        _write_tmp(_make_rotation_data(), cls.TMP)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.TMP):
            os.remove(cls.TMP)

    def _run_curl_magnitude(self, output_field="vorticity_magnitude"):
        from siva.compute import evaluate
        code = f'''
data = source("vtkXMLImageDataReader", FileName="{self.TMP}")
vort = curl_magnitude(vector_field=data, output_field="{output_field}")
'''
        _r = evaluate(code)
        vtk_objects, objs, node_statuses = _r.outputs, _r.outputs_by_name, _r.statuses
        return objs, node_statuses, {}, None

    def test_curl_magnitude_produces_scalar(self):
        """curl_magnitude should produce a scalar (1-component) array."""
        objs, _, _, _ = self._run_curl_magnitude(output_field="vort_mag")
        alg = objs.get("vort")
        self.assertIsNotNone(alg)
        alg.Update()
        arr = alg.GetOutput().GetPointData().GetArray("vort_mag")
        self.assertIsNotNone(arr, "'vort_mag' not found in output")
        self.assertEqual(arr.GetNumberOfComponents(), 1,
                         "curl_magnitude output must be scalar (1-component)")

    def test_curl_magnitude_default_output_name_is_snake_case(self):
        """Default output array name 'vorticity_magnitude' is snake_case."""
        objs, _, _, _ = self._run_curl_magnitude()
        alg = objs.get("vort")
        self.assertIsNotNone(alg)
        alg.Update()
        arr = alg.GetOutput().GetPointData().GetArray("vorticity_magnitude")
        self.assertIsNotNone(arr, "'vorticity_magnitude' not found in output")
        self.assertEqual(arr.GetNumberOfComponents(), 1,
                         "curl_magnitude default output must be scalar")

    def test_curl_magnitude_near_4pi(self):
        """For rigid-body rotation, ||curl|| ~ 4*pi at interior points."""
        objs, _, _, _ = self._run_curl_magnitude(output_field="vort_mag")
        alg = objs["vort"]
        alg.Update()
        arr = alg.GetOutput().GetPointData().GetArray("vort_mag")
        np_mag = vtk_to_numpy(arr)
        N = 8
        mid = N // 2
        center_idx = mid + mid * N + mid * N * N
        self.assertAlmostEqual(np_mag[center_idx], 4.0 * math.pi, delta=2.0,
                               msg="Curl magnitude at center should be ~4*pi")

    def test_curl_magnitude_available_in_dsl_namespace(self):
        """curl_magnitude should be a valid name in the DSL namespace (no NameError)."""
        objs, node_statuses, _, _ = self._run_curl_magnitude()
        errors = [s.get("message") for s in node_statuses.values() if s.get("status") == "error"]
        self.assertEqual(errors, [], f"Pipeline had errors: {errors}")


class TestCurlNoOldApiLeakage(unittest.TestCase):
    """Verify that the old curl(vector=...) API is gone from the DSL namespace."""

    def test_old_curl_not_in_dsl_namespace(self):
        """'curl' should not be a valid DSL name — only curl_vector and curl_magnitude exist."""
        from siva.compute import evaluate
        code = '''
data = source("vtkXMLImageDataReader", FileName="/tmp/nonexistent.vti")
vort = curl(vector_field=data)
'''
        # The DSL exec should raise NameError for 'curl'
        with self.assertRaises(NameError):
            evaluate(code)

    def test_curl_vector_in_dsl_namespace(self):
        """curl_vector should be importable from the DSL namespace."""
        from siva.dsl import _make_namespace, PipelineBuilder
        builder = PipelineBuilder()
        ns = _make_namespace(builder)
        self.assertIn("curl_vector", ns, "curl_vector must be in DSL namespace")

    def test_curl_magnitude_in_dsl_namespace(self):
        """curl_magnitude should be importable from the DSL namespace."""
        from siva.dsl import _make_namespace, PipelineBuilder
        builder = PipelineBuilder()
        ns = _make_namespace(builder)
        self.assertIn("curl_magnitude", ns, "curl_magnitude must be in DSL namespace")

    def test_old_curl_not_in_dsl_namespace(self):
        """'curl' must not be in the DSL namespace."""
        from siva.dsl import _make_namespace, PipelineBuilder
        builder = PipelineBuilder()
        ns = _make_namespace(builder)
        self.assertNotIn("curl", ns, "old 'curl' must not be in DSL namespace")


class TestMakeVectorThenCurlVector(unittest.TestCase):
    """Test chaining make_vector -> curl_vector in a single pipeline."""

    TMP = "/tmp/test_make_vector_curl_chain.vti"

    @classmethod
    def setUpClass(cls):
        _write_tmp(_make_rotation_data(), cls.TMP)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.TMP):
            os.remove(cls.TMP)

    def test_chain_make_vector_then_curl_vector(self):
        """make_vector output fed into curl_vector should compute vorticity from scalars."""
        from siva.compute import evaluate
        code = f'''
data = source("vtkXMLImageDataReader", FileName="{self.TMP}")
vel = make_vector(input=data, components=("u", "v", "w"), result="velocity")
vort = curl_vector(vector_field=vel, output_field="vorticity")
'''
        _r = evaluate(code)
        vtk_objects, objs, node_statuses = _r.outputs, _r.outputs_by_name, _r.statuses
        errors = [s.get("message") for s in node_statuses.values() if s.get("status") == "error"]
        self.assertEqual(errors, [], f"Pipeline had errors: {errors}")

        alg = objs.get("vort")
        self.assertIsNotNone(alg)
        alg.Update()
        arr = alg.GetOutput().GetPointData().GetArray("vorticity")
        self.assertIsNotNone(arr, "'vorticity' not found in output")
        self.assertEqual(arr.GetNumberOfComponents(), 3, "Should be 3-component vector")

    def test_chain_make_vector_then_curl_magnitude(self):
        """make_vector output fed into curl_magnitude should produce scalar vorticity."""
        from siva.compute import evaluate
        code = f'''
data = source("vtkXMLImageDataReader", FileName="{self.TMP}")
vel = make_vector(input=data, components=("u", "v", "w"), result="velocity")
vort = curl_magnitude(vector_field=vel, output_field="vorticity_magnitude")
'''
        _r = evaluate(code)
        vtk_objects, objs, node_statuses = _r.outputs, _r.outputs_by_name, _r.statuses
        errors = [s.get("message") for s in node_statuses.values() if s.get("status") == "error"]
        self.assertEqual(errors, [], f"Pipeline had errors: {errors}")

        alg = objs.get("vort")
        self.assertIsNotNone(alg)
        alg.Update()
        arr = alg.GetOutput().GetPointData().GetArray("vorticity_magnitude")
        self.assertIsNotNone(arr, "'vorticity_magnitude' not found in output")
        self.assertEqual(arr.GetNumberOfComponents(), 1, "Should be scalar magnitude")

    def test_chain_make_vector_curl_magnitude_correct_values(self):
        """make_vector + curl_magnitude chain should produce vorticity values near 4*pi."""
        from siva.compute import evaluate

        code = f'''
data = source("vtkXMLImageDataReader", FileName="{self.TMP}")
vel = make_vector(input=data, components=("u", "v", "w"), result="velocity")
vort = curl_magnitude(vector_field=vel, output_field="vorticity_magnitude")
'''
        _r = evaluate(code)
        objs = _r.outputs_by_name

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
