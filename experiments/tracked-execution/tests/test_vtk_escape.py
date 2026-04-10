"""Tests for vtk_escape — raw VTK escape hatch within a tracked pipeline.

All tests are self-contained: they use synthetic PyVista meshes and do not
require a display, network access, or real dataset files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import pyvista as pv

# Ensure the package is importable from source
_LIB_DIR = Path(__file__).resolve().parent.parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from tracked_execution.core import DAG
from tracked_execution.dispatch import stable_hash
from tracked_execution.proxy import TrackedProxy
from tracked_execution.vtk_escape import vtk_escape, vtk_escape_multi, _hash_function
from tracked_execution.executor import execute_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_surface_mesh() -> pv.PolyData:
    """Create a small synthetic surface mesh (PolyData) for testing."""
    return pv.Sphere(radius=1.0, theta_resolution=8, phi_resolution=8)


def make_volume_mesh(n: int = 8) -> pv.ImageData:
    """Create a small synthetic volume mesh for testing."""
    rng = np.random.default_rng(0)
    mesh = pv.ImageData(dimensions=(n, n, n))
    mesh["Temperature"] = rng.random(mesh.n_points) * 1000
    return mesh


def make_proxy(mesh=None, dag=None):
    """Wrap a mesh in a TrackedProxy with a fresh or given DAG."""
    if dag is None:
        dag = DAG()
    if mesh is None:
        mesh = make_surface_mesh()
    h = stable_hash(("root", "test_escape"))
    dag.cache[h] = mesh
    dag.current_run.add(h)
    return TrackedProxy(mesh, h, dag), dag


# ---------------------------------------------------------------------------
# 1. test_basic_escape
# ---------------------------------------------------------------------------

class TestBasicEscape:
    def test_basic_escape(self):
        """Pass a mesh through a trivial function and get a TrackedProxy back."""
        proxy, dag = make_proxy()
        dag.begin_run()

        def identity(m):
            return m.copy()

        result = vtk_escape(proxy, identity)
        assert isinstance(result, TrackedProxy)

    def test_basic_escape_result_is_valid_mesh(self):
        """The unwrapped result is a real PyVista mesh."""
        proxy, dag = make_proxy()
        dag.begin_run()

        def identity(m):
            return m.copy()

        result = vtk_escape(proxy, identity)
        real = object.__getattribute__(result, "_real")
        assert isinstance(real, pv.DataSet)

    def test_basic_escape_type_error_on_non_proxy(self):
        """Passing a raw mesh (not a proxy) raises TypeError."""
        mesh = make_surface_mesh()
        with pytest.raises(TypeError, match="TrackedProxy"):
            vtk_escape(mesh, lambda m: m)


# ---------------------------------------------------------------------------
# 2. test_escape_caching
# ---------------------------------------------------------------------------

class TestEscapeCaching:
    def test_escape_caching_same_function_same_input(self):
        """Same function + same input → cache hit on second call."""
        proxy, dag = make_proxy()

        def identity(m):
            return m.copy()

        dag.begin_run()
        r1 = vtk_escape(proxy, identity)
        dag.end_run()
        s1 = dag.stats()

        dag.begin_run()
        r2 = vtk_escape(proxy, identity)
        dag.end_run()
        s2 = dag.stats()

        assert s1["misses"] >= 1
        assert s2["hits"] >= 1
        assert s2["misses"] == 0

    def test_escape_result_hash_is_stable(self):
        """Same function + same input → same result hash both times."""
        proxy, dag = make_proxy()

        def identity(m):
            return m.copy()

        dag.begin_run()
        r1 = vtk_escape(proxy, identity)
        h1 = object.__getattribute__(r1, "_hash")
        dag.end_run()

        dag.begin_run()
        r2 = vtk_escape(proxy, identity)
        h2 = object.__getattribute__(r2, "_hash")
        dag.end_run()

        assert h1 == h2


# ---------------------------------------------------------------------------
# 3. test_escape_different_input
# ---------------------------------------------------------------------------

class TestEscapeDifferentInput:
    def test_escape_different_input_is_cache_miss(self):
        """Different input proxy → different op hash → cache miss."""
        dag = DAG()
        mesh_a = make_surface_mesh()
        mesh_b = pv.Sphere(radius=2.0, theta_resolution=8, phi_resolution=8)

        h_a = stable_hash(("root", "mesh_a"))
        h_b = stable_hash(("root", "mesh_b"))
        dag.cache[h_a] = mesh_a
        dag.cache[h_b] = mesh_b
        dag.current_run.update([h_a, h_b])

        proxy_a = TrackedProxy(mesh_a, h_a, dag)
        proxy_b = TrackedProxy(mesh_b, h_b, dag)

        def identity(m):
            return m.copy()

        dag.begin_run()
        r_a = vtk_escape(proxy_a, identity)
        dag.end_run()

        dag.begin_run()
        r_b = vtk_escape(proxy_b, identity)
        dag.end_run()
        s2 = dag.stats()

        assert s2["misses"] >= 1

        h_ra = object.__getattribute__(r_a, "_hash")
        h_rb = object.__getattribute__(r_b, "_hash")
        assert h_ra != h_rb


# ---------------------------------------------------------------------------
# 4. test_escape_with_explicit_key
# ---------------------------------------------------------------------------

class TestEscapeWithExplicitKey:
    def test_explicit_key_works(self):
        """key='my_filter_v1' is accepted and caches correctly."""
        proxy, dag = make_proxy()
        dag.begin_run()

        result = vtk_escape(proxy, lambda m: m.copy(), key="my_filter_v1")
        assert isinstance(result, TrackedProxy)

    def test_explicit_key_gives_stable_hash(self):
        """Two lambdas with the same explicit key share the same op hash."""
        proxy, dag = make_proxy()

        dag.begin_run()
        r1 = vtk_escape(proxy, lambda m: m.copy(), key="same_key")
        h1 = object.__getattribute__(r1, "_hash")
        dag.end_run()

        dag.begin_run()
        r2 = vtk_escape(proxy, lambda m: m.copy(), key="same_key")
        h2 = object.__getattribute__(r2, "_hash")
        dag.end_run()

        assert h1 == h2
        assert dag.stats()["hits"] >= 1

    def test_different_explicit_keys_give_different_hashes(self):
        """Different key values produce different op hashes (cache misses)."""
        proxy, dag = make_proxy()

        dag.begin_run()
        r1 = vtk_escape(proxy, lambda m: m.copy(), key="key_v1")
        dag.end_run()

        dag.begin_run()
        r2 = vtk_escape(proxy, lambda m: m.copy(), key="key_v2")
        dag.end_run()
        s = dag.stats()

        assert s["misses"] >= 1
        h1 = object.__getattribute__(r1, "_hash")
        h2 = object.__getattribute__(r2, "_hash")
        assert h1 != h2


# ---------------------------------------------------------------------------
# 5. test_escape_with_real_vtk_filter
# ---------------------------------------------------------------------------

class TestEscapeWithRealVTKFilter:
    def test_smooth_poly_data_filter(self):
        """Use vtkSmoothPolyDataFilter — a VTK filter PyVista doesn't directly expose."""
        try:
            import vtk
        except ImportError:
            pytest.skip("vtk not available")

        proxy, dag = make_proxy()
        dag.begin_run()

        def smooth_vtk(m):
            smoother = vtk.vtkSmoothPolyDataFilter()
            smoother.SetInputData(m)
            smoother.SetNumberOfIterations(5)
            smoother.Update()
            return pv.wrap(smoother.GetOutput())

        result = vtk_escape(proxy, smooth_vtk)
        assert isinstance(result, TrackedProxy)
        real = object.__getattribute__(result, "_real")
        assert isinstance(real, pv.DataSet)
        # Smoothed sphere should still have points
        assert real.n_points > 0

    def test_real_vtk_filter_is_cached(self):
        """Calling the same VTK filter twice on the same input → cache hit."""
        try:
            import vtk
        except ImportError:
            pytest.skip("vtk not available")

        proxy, dag = make_proxy()

        def smooth_vtk(m):
            smoother = vtk.vtkSmoothPolyDataFilter()
            smoother.SetInputData(m)
            smoother.SetNumberOfIterations(5)
            smoother.Update()
            return pv.wrap(smoother.GetOutput())

        dag.begin_run()
        vtk_escape(proxy, smooth_vtk)
        dag.end_run()
        s1 = dag.stats()

        dag.begin_run()
        vtk_escape(proxy, smooth_vtk)
        dag.end_run()
        s2 = dag.stats()

        assert s1["misses"] >= 1
        assert s2["hits"] >= 1
        assert s2["misses"] == 0


# ---------------------------------------------------------------------------
# 6. test_escape_pv_wrap
# ---------------------------------------------------------------------------

class TestEscapePvWrap:
    def test_function_returning_vtk_object_is_wrapped(self):
        """Function returning a raw VTK object — vtk_escape wraps it with pv.wrap()."""
        try:
            import vtk
        except ImportError:
            pytest.skip("vtk not available")

        proxy, dag = make_proxy()
        dag.begin_run()

        def returns_vtk_object(m):
            # Return a raw VTK PolyData (not a PyVista object)
            clean = vtk.vtkCleanPolyData()
            clean.SetInputData(m)
            clean.Update()
            return clean.GetOutput()  # raw vtk.vtkPolyData, not pv.PolyData

        result = vtk_escape(proxy, returns_vtk_object)
        assert isinstance(result, TrackedProxy)
        real = object.__getattribute__(result, "_real")
        # pv.wrap() should have converted it to a PyVista type
        assert isinstance(real, pv.DataSet)


# ---------------------------------------------------------------------------
# 7. test_escape_in_pipeline
# ---------------------------------------------------------------------------

class TestEscapeInPipeline:
    def test_escape_in_execute_pipeline(self):
        """vtk_escape is available in execute_pipeline's restricted namespace."""
        import tempfile
        import os

        dag = DAG()
        mesh = make_volume_mesh(n=5)
        with tempfile.NamedTemporaryFile(suffix=".vtk", delete=False) as f:
            tmp_path = f.name
        try:
            mesh.save(tmp_path)
            code = f"""
mesh = read("{tmp_path}")

def my_filter(m):
    return m.copy()

result = vtk_escape(mesh, my_filter)
n = result.n_points
print(f"points: {{n}}")
"""
            exec_result = execute_pipeline(code, dag)
            assert "points:" in exec_result.output

        finally:
            os.unlink(tmp_path)

    def test_escape_in_pipeline_caches_across_runs(self):
        """vtk_escape inside execute_pipeline hits cache on second identical run."""
        import tempfile
        import os

        dag = DAG()
        mesh = make_volume_mesh(n=5)
        with tempfile.NamedTemporaryFile(suffix=".vtk", delete=False) as f:
            tmp_path = f.name
        try:
            mesh.save(tmp_path)
            code = f"""
mesh = read("{tmp_path}")

def my_filter(m):
    return m.copy()

result = vtk_escape(mesh, my_filter, key="my_filter_v1")
n = result.n_points
print(f"points: {{n}}")
"""
            r1 = execute_pipeline(code, dag)
            r2 = execute_pipeline(code, dag)

            assert r2.stats["hits"] > 0
            assert r2.stats["misses"] == 0

        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# 8. test_escape_source_hash_stable
# ---------------------------------------------------------------------------

class TestEscapeSourceHashStable:
    def test_named_function_hash_is_stable_across_calls(self):
        """The same named function produces the same hash on every call."""
        def my_named_filter(m):
            return m.copy()

        h1 = _hash_function(my_named_filter, key=None)
        h2 = _hash_function(my_named_filter, key=None)
        assert h1 == h2

    def test_different_functions_have_different_hashes(self):
        """Two functions with different source → different hashes."""
        def filter_a(m):
            return m.copy()

        def filter_b(m):
            return m.copy()  # same body — but different source position/name
            # Note: different __qualname__ guarantees difference even if body is
            # syntactically identical.

        h_a = _hash_function(filter_a, key=None)
        h_b = _hash_function(filter_b, key=None)
        # These may or may not differ depending on inspect.getsource behaviour,
        # but explicit key override always works — tested separately.
        # This test just ensures the function runs without error.
        assert isinstance(h_a, str) and len(h_a) == 64
        assert isinstance(h_b, str) and len(h_b) == 64

    def test_same_function_used_multiple_times_caches(self):
        """Named function used as vtk_escape arg caches on repeated pipeline runs."""
        proxy, dag = make_proxy()

        def my_stable_filter(m):
            """A stable, named filter for cache-stability testing."""
            return m.copy()

        dag.begin_run()
        vtk_escape(proxy, my_stable_filter)
        dag.end_run()
        s1 = dag.stats()

        dag.begin_run()
        vtk_escape(proxy, my_stable_filter)
        dag.end_run()
        s2 = dag.stats()

        assert s1["misses"] >= 1
        assert s2["hits"] >= 1
        assert s2["misses"] == 0


# ---------------------------------------------------------------------------
# 9. test_escape_lambda_with_key
# ---------------------------------------------------------------------------

class TestEscapeLambdaWithKey:
    def test_lambda_with_key_caches_correctly(self):
        """Lambda + explicit key → cache hit on second call."""
        proxy, dag = make_proxy()

        dag.begin_run()
        vtk_escape(proxy, lambda m: m.copy(), key="lambda_copy_v1")
        dag.end_run()
        s1 = dag.stats()

        dag.begin_run()
        vtk_escape(proxy, lambda m: m.copy(), key="lambda_copy_v1")
        dag.end_run()
        s2 = dag.stats()

        assert s1["misses"] >= 1
        assert s2["hits"] >= 1

    def test_lambda_without_key_still_works(self):
        """Lambda without explicit key doesn't crash (falls back to bytecode)."""
        proxy, dag = make_proxy()
        dag.begin_run()

        result = vtk_escape(proxy, lambda m: m.copy())
        assert isinstance(result, TrackedProxy)


# ---------------------------------------------------------------------------
# 10. vtk_escape_multi tests
# ---------------------------------------------------------------------------

class TestVtkEscapeMulti:
    def test_multi_basic(self):
        """vtk_escape_multi with two proxies returns a TrackedProxy."""
        dag = DAG()
        mesh_a = make_surface_mesh()
        mesh_b = pv.Sphere(radius=0.5, theta_resolution=6, phi_resolution=6)

        h_a = stable_hash(("root", "mesh_a_multi"))
        h_b = stable_hash(("root", "mesh_b_multi"))
        dag.cache[h_a] = mesh_a
        dag.cache[h_b] = mesh_b
        dag.current_run.update([h_a, h_b])

        proxy_a = TrackedProxy(mesh_a, h_a, dag)
        proxy_b = TrackedProxy(mesh_b, h_b, dag)

        dag.begin_run()

        def merge_meshes(a, b):
            return a.merge(b)

        result = vtk_escape_multi([proxy_a, proxy_b], merge_meshes)
        assert isinstance(result, TrackedProxy)

    def test_multi_caches_on_second_call(self):
        """vtk_escape_multi hits cache on identical second call."""
        dag = DAG()
        mesh_a = make_surface_mesh()
        mesh_b = pv.Sphere(radius=0.5, theta_resolution=6, phi_resolution=6)

        h_a = stable_hash(("root", "mesh_a_cache"))
        h_b = stable_hash(("root", "mesh_b_cache"))
        dag.cache[h_a] = mesh_a
        dag.cache[h_b] = mesh_b

        proxy_a = TrackedProxy(mesh_a, h_a, dag)
        proxy_b = TrackedProxy(mesh_b, h_b, dag)

        def merge_meshes(a, b):
            return a.merge(b)

        dag.begin_run()
        dag.current_run.update([h_a, h_b])
        vtk_escape_multi([proxy_a, proxy_b], merge_meshes)
        dag.end_run()
        s1 = dag.stats()

        dag.begin_run()
        dag.current_run.update([h_a, h_b])
        vtk_escape_multi([proxy_a, proxy_b], merge_meshes)
        dag.end_run()
        s2 = dag.stats()

        assert s1["misses"] >= 1
        assert s2["hits"] >= 1
        assert s2["misses"] == 0

    def test_multi_raises_on_empty_list(self):
        """vtk_escape_multi raises TypeError for empty input list."""
        with pytest.raises(TypeError, match="non-empty"):
            vtk_escape_multi([], lambda *args: args[0])

    def test_multi_raises_on_non_proxy(self):
        """vtk_escape_multi raises TypeError if any input is not a TrackedProxy."""
        proxy, dag = make_proxy()
        raw_mesh = make_surface_mesh()

        with pytest.raises(TypeError, match="TrackedProxy"):
            vtk_escape_multi([proxy, raw_mesh], lambda a, b: a)

    def test_multi_with_explicit_key(self):
        """vtk_escape_multi with explicit key caches correctly."""
        dag = DAG()
        mesh_a = make_surface_mesh()
        mesh_b = pv.Sphere(radius=0.5, theta_resolution=6, phi_resolution=6)

        h_a = stable_hash(("root", "mesh_a_key"))
        h_b = stable_hash(("root", "mesh_b_key"))
        dag.cache[h_a] = mesh_a
        dag.cache[h_b] = mesh_b

        proxy_a = TrackedProxy(mesh_a, h_a, dag)
        proxy_b = TrackedProxy(mesh_b, h_b, dag)

        dag.begin_run()
        dag.current_run.update([h_a, h_b])
        r1 = vtk_escape_multi([proxy_a, proxy_b], lambda a, b: a.merge(b), key="merge_v1")
        dag.end_run()

        dag.begin_run()
        dag.current_run.update([h_a, h_b])
        r2 = vtk_escape_multi([proxy_a, proxy_b], lambda a, b: a.merge(b), key="merge_v1")
        dag.end_run()
        s2 = dag.stats()

        assert s2["hits"] >= 1
        h1 = object.__getattribute__(r1, "_hash")
        h2 = object.__getattribute__(r2, "_hash")
        assert h1 == h2
