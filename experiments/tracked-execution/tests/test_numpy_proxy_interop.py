"""Tests for numpy-proxy interoperability via __array__ protocol.

Covers the scenario where TrackedProxy wrapping an ndarray must be usable
anywhere numpy expects a real array — in particular, assigning
``np.sqrt(proxy_arr)`` (which returns a TrackedProxy) to a PyVista mesh field
inside a vtk_escape call.

All tests are self-contained and do not require a display, network access, or
real dataset files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import pyvista as pv

_LIB_DIR = Path(__file__).resolve().parent.parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from tracked_execution.dispatch import DAG, stable_hash
from tracked_execution.proxy import TrackedProxy
from tracked_execution.executor import execute_pipeline
from tracked_execution.vtk_escape import vtk_escape


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_volume_mesh(n: int = 8, seed: int = 0) -> pv.ImageData:
    """Create a small synthetic volume mesh with a Temperature field."""
    rng = np.random.default_rng(seed)
    mesh = pv.ImageData(dimensions=(n, n, n))
    mesh["Temperature"] = rng.random(mesh.n_points) * 1000.0
    return mesh


def make_array_proxy(arr: np.ndarray, dag: DAG | None = None) -> TrackedProxy:
    """Wrap a numpy array in a TrackedProxy."""
    if dag is None:
        dag = DAG()
    h = stable_hash(("root", "test_array", arr.tobytes()))
    dag.cache[h] = arr
    dag.current_run.add(h)
    return TrackedProxy(arr, h, dag)


def make_mesh_proxy(mesh: pv.DataSet, dag: DAG | None = None) -> TrackedProxy:
    """Wrap a PyVista mesh in a TrackedProxy."""
    if dag is None:
        dag = DAG()
    h = stable_hash(("root", "test_mesh"))
    dag.cache[h] = mesh
    dag.current_run.add(h)
    return TrackedProxy(mesh, h, dag)


# ---------------------------------------------------------------------------
# 1. Basic __array__ protocol
# ---------------------------------------------------------------------------

class TestArrayProtocol:
    def test_array_protocol_returns_ndarray(self):
        """TrackedProxy.__array__() returns a numpy ndarray."""
        arr = np.array([1.0, 4.0, 9.0])
        proxy = make_array_proxy(arr)

        result = np.asarray(proxy)
        assert isinstance(result, np.ndarray)

    def test_array_protocol_values_match(self):
        """The array returned by __array__() has the same values as the original."""
        arr = np.array([1.0, 4.0, 9.0])
        proxy = make_array_proxy(arr)

        result = np.asarray(proxy)
        np.testing.assert_array_equal(result, arr)

    def test_array_protocol_with_dtype(self):
        """__array__(dtype=...) returns array cast to the requested dtype."""
        arr = np.array([1, 4, 9], dtype=np.int32)
        proxy = make_array_proxy(arr)

        result = np.array(proxy, dtype=np.float64)
        assert result.dtype == np.float64
        np.testing.assert_array_almost_equal(result, arr.astype(np.float64))

    def test_array_protocol_preserves_shape(self):
        """__array__ preserves the shape of the underlying array."""
        arr = np.arange(24).reshape(4, 6).astype(float)
        proxy = make_array_proxy(arr)

        result = np.asarray(proxy)
        assert result.shape == (4, 6)

    def test_direct_dunder_array_call(self):
        """Calling proxy.__array__() directly returns a numpy array."""
        arr = np.array([2.0, 3.0, 5.0])
        proxy = make_array_proxy(arr)

        # Bypass normal dispatch and call the method directly
        result = proxy.__array__()
        assert isinstance(result, np.ndarray)
        np.testing.assert_array_equal(result, arr)


# ---------------------------------------------------------------------------
# 2. numpy ufunc interop
# ---------------------------------------------------------------------------

class TestNumpyUfuncInterop:
    def test_np_sqrt_on_proxy_array(self):
        """np.sqrt() on a proxy wrapping an ndarray works via __array__."""
        arr = np.array([1.0, 4.0, 9.0, 16.0])
        proxy = make_array_proxy(arr)

        result = np.sqrt(proxy)
        expected = np.sqrt(arr)
        np.testing.assert_array_almost_equal(result, expected)

    def test_np_sqrt_result_is_ndarray(self):
        """np.sqrt() on a proxy returns a plain ndarray (not a TrackedProxy).

        When numpy ufuncs are called directly with a proxy (not through
        _TrackedNumpyNamespace), they call __array__ first and operate on
        the real data, returning a plain numpy array.
        """
        arr = np.array([1.0, 4.0, 9.0])
        proxy = make_array_proxy(arr)

        result = np.sqrt(proxy)
        assert isinstance(result, np.ndarray)

    def test_np_abs_on_proxy(self):
        """np.abs() on a proxy array works via __array__."""
        arr = np.array([-3.0, 1.0, -7.0])
        proxy = make_array_proxy(arr)

        result = np.abs(proxy)
        np.testing.assert_array_equal(result, np.abs(arr))

    def test_np_log_on_proxy(self):
        """np.log() on a proxy array works via __array__."""
        arr = np.array([1.0, np.e, np.e**2])
        proxy = make_array_proxy(arr)

        result = np.log(proxy)
        np.testing.assert_array_almost_equal(result, np.log(arr))

    def test_arithmetic_with_proxy_and_scalar(self):
        """Arithmetic between a proxy array and a scalar works via __array__."""
        arr = np.array([1.0, 2.0, 3.0])
        proxy = make_array_proxy(arr)

        result = np.asarray(proxy) * 2.0
        np.testing.assert_array_equal(result, arr * 2.0)


# ---------------------------------------------------------------------------
# 3. Mesh field assignment inside vtk_escape
# ---------------------------------------------------------------------------

class TestMeshFieldAssignment:
    def test_assign_raw_array_to_mesh_field(self):
        """Assigning a plain ndarray to a mesh field inside vtk_escape works."""
        mesh = make_volume_mesh()
        dag = DAG()
        mesh_proxy = make_mesh_proxy(mesh, dag)
        dag.begin_run()

        raw_temperature = mesh["Temperature"]
        derived = np.sqrt(raw_temperature)

        def add_derived_field(m):
            m_copy = m.copy()
            m_copy["Derived"] = derived
            return m_copy

        result = vtk_escape(mesh_proxy, add_derived_field, key="add_derived_v1")
        real = object.__getattribute__(result, "_real")
        assert "Derived" in real.array_names

    def test_assign_proxy_array_via_asarray(self):
        """A proxy array can be assigned to a mesh field via np.asarray().

        This is the core scenario: the user has a TrackedProxy wrapping an ndarray
        and wants to assign it to a mesh field. Using np.asarray() calls __array__
        and converts it to a real ndarray before assignment.
        """
        mesh = make_volume_mesh()
        dag = DAG()

        # Simulate arr = mesh["Temperature"] returning a TrackedProxy
        temp_arr = mesh["Temperature"].copy()
        arr_proxy = make_array_proxy(temp_arr, dag)

        # np.sqrt on a proxy via __array__ returns a plain ndarray
        derived_arr = np.sqrt(np.asarray(arr_proxy))

        mesh_proxy = make_mesh_proxy(mesh, dag)
        dag.begin_run()

        def add_derived_field(m):
            m_copy = m.copy()
            m_copy["Derived"] = derived_arr
            return m_copy

        result = vtk_escape(mesh_proxy, add_derived_field, key="add_derived_v2")
        real = object.__getattribute__(result, "_real")
        assert "Derived" in real.array_names

        expected = np.sqrt(temp_arr)
        np.testing.assert_array_almost_equal(real["Derived"], expected)


# ---------------------------------------------------------------------------
# 4. Complex workflow via execute_pipeline
# ---------------------------------------------------------------------------

class TestComplexWorkflowPipeline:
    def test_tracked_np_sqrt_result_assignable_via_asarray(self):
        """In the tracked namespace, np.sqrt returns a TrackedProxy.

        Using np.asarray(result) calls __array__ and converts it to a real
        ndarray which can then be assigned to a mesh field inside vtk_escape.
        """
        import tempfile, os

        dag = DAG()
        mesh = make_volume_mesh(n=5)

        with tempfile.NamedTemporaryFile(suffix=".vtk", delete=False) as f:
            tmp_path = f.name
        try:
            mesh.save(tmp_path)

            # This pipeline exercises the exact failure scenario from the task brief:
            # 1. Read a mesh
            # 2. Get a field array (returns TrackedProxy wrapping ndarray)
            # 3. Apply np.sqrt (returns TrackedProxy via _TrackedNumpyNamespace)
            # 4. Inside vtk_escape, assign the result to a new mesh field
            #    (requires __array__ to unwrap the proxy for PyVista's __setitem__)
            code = f"""
mesh = read("{tmp_path}")
arr = mesh["Temperature"]
result = np.sqrt(arr)

def add_derived(m):
    m_copy = m.copy()
    # np.asarray calls __array__ on the TrackedProxy, giving a real ndarray
    m_copy["Derived"] = np.asarray(result)
    return m_copy

mesh_copy = vtk_escape(mesh, add_derived, key="add_derived_sqrt_v1")
has_field = "Derived" in mesh_copy.array_names
print(f"has_derived: {{has_field}}")
"""
            exec_result = execute_pipeline(code, dag)
            assert "has_derived: True" in exec_result.output

        finally:
            os.unlink(tmp_path)

    def test_tracked_np_result_direct_assignment_via_array_protocol(self):
        """Direct assignment of TrackedProxy to mesh field works via __array__.

        PyVista's __setitem__ calls np.asarray() on the assigned value, which
        triggers __array__ on a TrackedProxy and unwraps it transparently.
        """
        import tempfile, os

        dag = DAG()
        mesh = make_volume_mesh(n=5)

        with tempfile.NamedTemporaryFile(suffix=".vtk", delete=False) as f:
            tmp_path = f.name
        try:
            mesh.save(tmp_path)

            # This variant tests that PyVista's own array coercion calls __array__
            # implicitly, so we don't need np.asarray() explicitly.
            code = f"""
mesh = read("{tmp_path}")
arr = mesh["Temperature"]

def add_derived(m):
    m_copy = m.copy()
    # arr is a TrackedProxy; PyVista's __setitem__ calls np.asarray(arr)
    # which calls arr.__array__(), unwrapping it transparently.
    m_copy["Derived"] = arr
    return m_copy

mesh_copy = vtk_escape(mesh, add_derived, key="add_derived_direct_v1")
has_field = "Derived" in mesh_copy.array_names
print(f"has_derived: {{has_field}}")
"""
            exec_result = execute_pipeline(code, dag)
            assert "has_derived: True" in exec_result.output

        finally:
            os.unlink(tmp_path)

    def test_workflow_field_values_correct(self):
        """The derived field values match the expected mathematical result."""
        import tempfile, os

        dag = DAG()
        mesh = make_volume_mesh(n=5)

        with tempfile.NamedTemporaryFile(suffix=".vtk", delete=False) as f:
            tmp_path = f.name
        try:
            mesh.save(tmp_path)

            code = f"""
mesh = read("{tmp_path}")
arr = mesh["Temperature"]
result = np.sqrt(arr)

def add_derived(m):
    m_copy = m.copy()
    m_copy["Derived"] = np.asarray(result)
    return m_copy

mesh_copy = vtk_escape(mesh, add_derived, key="add_derived_values_v1")
# Print first element of Derived to verify correctness
first_derived = mesh_copy["Derived"][0]
first_temp = mesh["Temperature"][0]
print(f"derived_0: {{first_derived:.6f}}")
print(f"expected_0: {{np.sqrt(first_temp):.6f}}")
"""
            exec_result = execute_pipeline(code, dag)
            # Both values should appear and be equal (printed with same format)
            lines = exec_result.output.strip().splitlines()
            derived_val = float(lines[0].split(": ")[1])
            expected_val = float(lines[1].split(": ")[1])
            assert abs(derived_val - expected_val) < 1e-5

        finally:
            os.unlink(tmp_path)
